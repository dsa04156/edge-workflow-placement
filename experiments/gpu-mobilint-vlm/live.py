"""Collector: one cache + one Mobilint worker (ser0004) + one Jetson CUDA worker (dev0009); smoke, then three inputs.

  python3 live.py --frozen <dir> --print-release-draft
  python3 live.py --frozen <dir> --plan                     (pure manifests, no kubectl)
  python3 live.py --frozen <dir> --runs <dir> --release <json> --stage probe|qualification [--arm mobilint|cuda]

--arm selects ONE arm explicitly (arm-only qualification, labeled in selection.json; never a pair result): only that arm's
node is preflighted, only the common + that recipe's manifest groups are bundled into the cache (the frozen manifest
itself is served unchanged), only that worker is created, and only that arm's rows of the frozen schedule are called.
The release must name the same arms (a release without `arms` is the pair).

Prepared CUDA mode (opt-in; without it every manifest, the ConfigMap and the release are the old mode's):
  python3 live.py --frozen <dir> --prepared <prepared dir> --cuda-cache-pvc <binding json> --print-release-draft | --plan | ...
<prepared dir> is prepare_runtime.py output (prepared-manifest.json + runtime.tar). The binding json names the pre-existing
PVC the coordinator verified: {name, uid, pv, path, backing_device, verified_by}; backing_device must be a non-SD/eMMC block
device, except the SD profile below. The release binds both exactly. Preflight checks the PVC (uid, Bound, capacity, access mode, unused) and its PV
(uid claimRef, exact path, required node affinity to dev0009 only); the CUDA Pod mounts only that claim at /cache and runs
vlm_prepared_bootstrap.py (no pip; the Pod refuses any other backing device). The cache bundle carries the prepared
manifest + tar instead of the CUDA wheels/model group (the frozen asset manifest is still served unchanged). The PVC is
never created, adopted or deleted here: cleanup touches only the UID records of objects this run created.
SD profile (explicit `--io-profile sd-adaptive-v1`, same binding shape): backing_device must then be a partition of an
mmcblk disk (e.g. mmcblk0p1; whole disk mmcblk0 derived from the name), and the release binds vlm_runtime_cache.SD_PROFILE
in full (`io_profile`) with the deadlines (`prepared_deadlines`). The Pod verifies partition + whole disk against sysfs and
fills with the diskstats-driven AdaptiveWriter inside the explicit SD_PROFILE fill budget; only then are the prepared run's
Pod (both arms) and cache lifetimes extended past the frozen ones by that budget. An mmcblk binding without it is refused.

Reused, unchanged: multi-edge-sensors collect.Session (create-only with server dry-run, API UID records also on partial
create, unresolved-object accounting, UID-precondition delete, readiness wait, per-call stdout/stderr/event files),
collect.protected_snapshot, the quality-network-heterogeneous bootstrap.py/worker.py/stages.py/proc.py in the Pods, and
real-topology-live http_runtime framing (sent through bounded_http: absolute deadline, reply byte cap). kubectl exec is
used once, into the owned server cache (bundle upload); every allowed server stream is preceded by a fresh GET binding the
creation UID, the server node and this run's labels; edge-node Pods are never streamed (logs are skipped and recorded).
Every successful receipt is checked against the identity verified at readiness; the first failed call stops the run.
Every stage ends with UID-only cleanup and absence verification.
"""
import argparse
from datetime import datetime, timezone
import json
import math
import os
import re
from pathlib import Path
import subprocess
import sys
import tarfile
import time
import urllib.error

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
HET, LIVE, MES, QNB = (ROOT/'experiments'/d for d in ('quality-network-heterogeneous', 'real-topology-live', 'multi-edge-sensors',
                                                       'quality-network-baselines'))
sys.path[:0] = [str(HERE), str(HET), str(LIVE), str(MES), str(QNB)]
import protocol as P  # noqa: E402
import vlm_bootstrap as VB  # noqa: E402
import vlm_runtime_cache as C  # noqa: E402
import prepare  # noqa: E402
import collect  # noqa: E402
import bounded_http  # noqa: E402

STREAM_VERBS = ('exec', 'attach', 'logs', 'cp', 'port-forward', 'top', 'debug')
SHIPPED = {'bootstrap.py': HET/'bootstrap.py', 'vlm_bootstrap.py': HERE/'vlm_bootstrap.py', 'worker.py': HET/'worker.py', 'stages.py': HET/'stages.py', 'proc.py': HET/'proc.py',
           'http_runtime.py': LIVE/'http_runtime.py', 'protocol.py': HERE/'protocol.py', 'vlm_stage.py': HERE/'vlm_stage.py',
           'vlm_worker.py': HERE/'vlm_worker.py'}
SHIPPED_PREPARED = {'vlm_prepared_bootstrap.py': HERE/'vlm_prepared_bootstrap.py', 'vlm_runtime_cache.py': HERE/'vlm_runtime_cache.py'}
BUILD_CODE = (HERE/'prepare_runtime.py',)
PREPARED_WORK = '2Gi'  # /work in prepared mode: contract copy, model link, logs (the runtime lives on the PVC)
EXPECTED_DEVICE = {'mobilint': 'npu:aries-rb', 'cuda': 'cuda:0'}
# CUDA arm: the shared bootstrap plus an fsync of each fetched file (vlm_bootstrap.py; dev0009 eMMC writeback stall).
BOOTSTRAP = {'mobilint': 'bootstrap.py', 'cuda': 'vlm_bootstrap.py'}
RUN_REPLY_MAX = 4*1024*1024  # /run reply cap (caption JSON + receipt); error evidence kept to ERROR_KEEP bytes
ERROR_KEEP = 20000


def meta(run, name, component):
    return dict(name=name, namespace=P.NS, labels={'experiment': P.EXPERIMENT, 'run-id': run, 'component': component})


def affinity(node):
    return dict(nodeAffinity=dict(requiredDuringSchedulingIgnoredDuringExecution=dict(nodeSelectorTerms=[
        dict(matchFields=[dict(key='metadata.name', operator='In', values=[node])])])))


def cache_size(footprint):
    return f"{math.ceil(footprint['total']/2**30)+2}Gi"


def config_map(run, prepared=None):
    shipped = dict(SHIPPED, **(SHIPPED_PREPARED if prepared else {}))
    return dict(apiVersion='v1', kind='ConfigMap', metadata=meta(run, run+'-code', 'code'),
                data={name: path.read_text() for name, path in shipped.items()})


def cache_pod(run, footprint, lifetime=None):
    size = cache_size(footprint)
    container = dict(name='worker', image=P.PY_IMAGE, imagePullPolicy='IfNotPresent',
                     command=['python3', '-u', '-m', 'http.server', str(P.PORT), '--directory', '/work'],
                     resources=dict(requests={'cpu': '250m', 'memory': '256Mi', 'ephemeral-storage': size},
                                    limits={'cpu': '1', 'memory': '1Gi', 'ephemeral-storage': size}),
                     readinessProbe=dict(httpGet=dict(path='/', port=P.PORT), periodSeconds=5, timeoutSeconds=3),
                     volumeMounts=[dict(name='work', mountPath='/work')],
                     securityContext=dict(runAsUser=65532, runAsGroup=65532, runAsNonRoot=True, allowPrivilegeEscalation=False,
                                          readOnlyRootFilesystem=True, capabilities=dict(drop=['ALL'])))
    spec = dict(restartPolicy='Never', activeDeadlineSeconds=lifetime or P.DEADLINES['cache_lifetime_s'], terminationGracePeriodSeconds=3,
                automountServiceAccountToken=False, enableServiceLinks=False,
                securityContext=dict(fsGroup=65532, seccompProfile=dict(type='RuntimeDefault')), affinity=affinity(P.SERVER),
                containers=[container], volumes=[dict(name='work', emptyDir=dict(sizeLimit=size))])
    return dict(apiVersion='v1', kind='Pod', metadata=meta(run, run+'-cache', 'cache'), spec=spec)


def worker_pod(run, arm, cache_url, manifest_sha, prepared=None):
    a = P.ARMS[arm]
    lifetime = prepared['deadlines']['pod_lifetime_s'] if prepared else P.DEADLINES['pod_lifetime_s']
    prepared = prepared if arm == 'cuda' else None
    env = [dict(name='NODE_NAME', valueFrom=dict(fieldRef=dict(fieldPath='spec.nodeName'))),
           dict(name='POD_UID', valueFrom=dict(fieldRef=dict(fieldPath='metadata.uid'))),
           *(dict(name=k, value=v) for k, v in (
               ('RECIPE', a['recipe']), ('BACKEND', arm), ('STAGES', P.STAGE), ('WORKER_MODULE', 'vlm_worker'), ('CACHE_URL', cache_url),
               ('ASSET_MANIFEST_SHA256', manifest_sha), ('HOME', '/work'), ('TMPDIR', '/work/tmp'), ('PYTHONDONTWRITEBYTECODE', '1'),
               ('HF_HUB_OFFLINE', '1'), ('TRANSFORMERS_OFFLINE', '1'), ('OMP_NUM_THREADS', str(P.CONTRACT['host_threads'])),
               ('INSTALL_TIMEOUT_S', str(P.DEADLINES['install_s'])), ('FETCH_DEADLINE_S', str(P.DEADLINES['fetch_s']))))]
    storage = PREPARED_WORK if prepared else a['storage']
    if prepared:
        b, d = prepared['binding'], prepared['deadlines']
        env += [dict(name=k, value=v) for k, v in (
            ('PREPARED_MANIFEST_SHA256', b['manifest_sha256']), ('PREPARED_CONTENT_SHA256', b['content_sha256']), ('CACHE_DIR', '/cache'),
            ('CACHE_BACKING_DEVICE', prepared['pvc']['backing_device']), ('PREPARED_FETCH_DEADLINE_S', str(d['fetch_s'])),
            ('CACHE_VERIFY_DEADLINE_S', str(d['verify_s'])))]
        if prepared['io_profile']:
            env += [dict(name='CACHE_IO_PROFILE', value=json.dumps(prepared['io_profile'], sort_keys=True)),
                    dict(name='CACHE_BACKING_DISK', value=prepared['backing_disk'])]
    units = {k: str(v) for k, v in a['device_resource'].items()}
    resources = dict(requests=dict(cpu=a['cpu'], memory=a['memory'], **{'ephemeral-storage': storage}, **units),
                     limits=dict(cpu=a['cpu'], memory=a['memory_limit'], **{'ephemeral-storage': storage}, **units))
    security = dict(allowPrivilegeEscalation=False, capabilities=dict(drop=['ALL']))
    pod_security = dict(seccompProfile=dict(type='RuntimeDefault'))
    if not a['image_user']:
        security['readOnlyRootFilesystem'] = True
        pod_security['runAsUser'] = 0
    bootstrap = 'vlm_prepared_bootstrap.py' if prepared else BOOTSTRAP[arm]
    container = dict(name='worker', image=a['image'], imagePullPolicy='IfNotPresent', command=['python3', '-u', '/config/'+bootstrap],
                     env=env, resources=resources, ports=[dict(containerPort=P.PORT)], securityContext=security,
                     readinessProbe=dict(httpGet=dict(path='/ready', port=P.PORT), periodSeconds=5, timeoutSeconds=3),
                     volumeMounts=[dict(name='work', mountPath='/work'), dict(name='tmp', mountPath='/tmp'),
                                   dict(name='config', mountPath='/config', readOnly=True)])
    spec = dict(restartPolicy='Never', activeDeadlineSeconds=lifetime, terminationGracePeriodSeconds=3,
                automountServiceAccountToken=False, enableServiceLinks=False, securityContext=pod_security, affinity=affinity(a['node']),
                containers=[container], volumes=[dict(name='work', emptyDir=dict(sizeLimit=storage)), dict(name='tmp', emptyDir=dict(sizeLimit='2Gi')),
                                                 dict(name='config', configMap=dict(name=run+'-code'))])
    if prepared:
        container['volumeMounts'].append(dict(name='cache', mountPath='/cache'))
        spec['volumes'].append(dict(name='cache', persistentVolumeClaim=dict(claimName=prepared['pvc']['name'])))
    return dict(apiVersion='v1', kind='Pod', metadata=meta(run, f'{run}-{arm}', arm), spec=spec)


def selection(arms):
    """Explicit arm selection -> (arms in P.ARMS order, manifest groups to bundle). None is the pair."""
    if arms is not None and (not arms or set(arms) - set(P.ARMS)):
        raise ValueError(f'unknown arm selection {arms!r}')
    arms = tuple(a for a in P.ARMS if arms is None or a in arms)
    return arms, {'common'} | {P.ARMS[a]['recipe'] for a in arms}


def bundle_groups(arms, prepared=None):
    """Manifest groups bundled into the server cache; in prepared mode the CUDA recipe group is replaced by the tar."""
    groups = selection(arms)[1]
    return groups - {P.ARMS['cuda']['recipe']} if prepared else groups


def selected_footprint(footprint, arms, prepared=None):
    """The pair keeps the frozen total; one arm sizes the cache for common + its recipe group only; prepared mode counts
    the actual prepared tar + manifest bytes instead of the CUDA group."""
    if prepared:
        b = prepared['binding']
        return dict(total=sum(footprint[g] for g in bundle_groups(arms, prepared)) + b['tar_bytes'] + b['manifest_bytes'])
    arms, groups = selection(arms)
    return footprint if arms == tuple(P.ARMS) else dict(total=sum(footprint[g] for g in groups))


def plan(run, cache_url, manifest_sha, footprint, arms=None, prepared=None):
    arms, _ = selection(arms)
    return [config_map(run, prepared), cache_pod(run, selected_footprint(footprint, arms, prepared),
                                                 prepared and prepared['deadlines']['cache_lifetime_s'])] + [
        worker_pod(run, arm, cache_url, manifest_sha, prepared) for arm in arms]


PVC_KEYS = ('name', 'uid', 'pv', 'path', 'backing_device', 'verified_by')
PREPARED_LIFETIME_MAX_S = 6 * 3600  # SD profile: upper bound of the extended prepared Pod lifetime


def prepared_deadlines(binding, io_profile=None):
    """From the actual prepared bytes: paced transfer (+ per-file fsync), paced re-hash on a hit, then the CUDA load.
    Without io_profile the frozen Pod lifetimes must fit. With SD_PROFILE the fill gets its explicit budget, which must
    hold the whole tar at the start rate; the run's Pod/cache lifetimes then grow by the same post-ready window as frozen."""
    verify_s = math.ceil(binding['total_bytes'] / C.VERIFY_RATE_BPS * 1.25) + 300
    if io_profile:
        fetch_s = io_profile['fill_budget_s']
        if math.ceil(binding['tar_bytes'] / io_profile['start_bps'] + binding['file_count'] * 0.02) > fetch_s:
            raise ValueError(f"prepared tar does not fit the {fetch_s}s SD fill budget at the start rate")
    else:
        fetch_s = math.ceil(binding['tar_bytes'] / VB.FETCH_RATE_BPS * 1.25 + binding['file_count'] * 0.02) + 300
    ready_s = max(fetch_s, verify_s) + P.DEADLINES['load_s']['cuda'] + 300
    worker_ready_s = max(ready_s, P.DEADLINES['worker_ready_s'])
    pod_s, cache_s = P.DEADLINES['pod_lifetime_s'], P.DEADLINES['cache_lifetime_s']
    if io_profile:
        pod_s = max(pod_s, worker_ready_s + pod_s - P.DEADLINES['worker_ready_s'])
        cache_s = pod_s + cache_s - P.DEADLINES['pod_lifetime_s']
        if pod_s > PREPARED_LIFETIME_MAX_S:
            raise ValueError(f'prepared SD Pod lifetime {pod_s}s exceeds {PREPARED_LIFETIME_MAX_S}s')
    if ready_s + 4 * P.DEADLINES['call_s'] > pod_s or ready_s + P.DEADLINES['upload_s'] > cache_s:
        raise ValueError(f'prepared deadlines {fetch_s}/{verify_s}/{ready_s}s do not fit the frozen Pod lifetimes')
    return dict(fetch_s=fetch_s, verify_s=verify_s, worker_ready_s=worker_ready_s, pod_lifetime_s=pod_s, cache_lifetime_s=cache_s)


def load_prepared(directory, frozen, pvc, hash_tar=True, io_profile_name=None):
    """Prepared manifest + tar bound to the frozen contract/asset manifest, and the coordinator's PVC binding; with
    io_profile_name (only SD_PROFILE's) the backing device must be an mmcblk partition."""
    directory, frozen = Path(directory), Path(frozen)
    raw = (directory/'prepared-manifest.json').read_bytes()
    manifest = json.loads(raw)
    expect = dict(recipe=P.ARMS['cuda']['recipe'], machine='aarch64', python='3.11', asset_manifest_sha256=P.file_sha(frozen/'assets-manifest.json'),
                  content_sha256=manifest.get('content_sha256'))
    problems = C.manifest_problems(manifest, expect)
    if manifest.get('frozen', {}).get('contract.json') != P.file_sha(frozen/'contract.json'):
        problems.append('prepared manifest is not bound to the frozen contract')
    tar = directory/manifest['tar']['name']
    if tar.stat().st_size != manifest['tar']['bytes'] or (hash_tar and P.file_sha(tar) != manifest['tar']['sha256']):
        problems.append(f'{tar} differs from the prepared manifest')
    io_profile = disk = None
    if sorted(pvc) != sorted(PVC_KEYS) or not all(isinstance(pvc[k], str) and pvc[k] for k in PVC_KEYS):
        problems.append(f'cuda cache PVC binding needs exactly {PVC_KEYS}')
    elif io_profile_name is not None:
        sd = re.fullmatch(r'(mmcblk\d+)p\d+', pvc['backing_device'])
        if io_profile_name != C.SD_PROFILE['name'] or not sd:
            problems.append(f"io profile {io_profile_name!r} needs {C.SD_PROFILE['name']!r} and an mmcblk partition, not {pvc['backing_device']!r}")
        else:
            io_profile, disk = dict(C.SD_PROFILE), sd.group(1)
    elif pvc['backing_device'].startswith('mmcblk'):
        problems.append(f"cuda cache backing device {pvc['backing_device']!r} is SD/eMMC without the SD profile: refused")
    if problems:
        raise ValueError('prepared runtime rejected: '+'; '.join(problems))
    binding = dict(manifest_sha256=P.sha256(raw), manifest_bytes=len(raw), content_sha256=manifest['content_sha256'],
                   tar_sha256=manifest['tar']['sha256'], tar_bytes=manifest['tar']['bytes'], total_bytes=manifest['total_bytes'],
                   file_count=manifest['file_count'])
    return dict(dir=directory, tar=tar, manifest_path=directory/'prepared-manifest.json', binding=binding, pvc=dict(pvc),
                io_profile=io_profile, backing_disk=disk, deadlines=prepared_deadlines(binding, io_profile))


def quantity(text):
    units = dict(Ki=2**10, Mi=2**20, Gi=2**30, Ti=2**40, k=10**3, K=10**3, M=10**6, G=10**9, T=10**12)
    for suffix, factor in units.items():
        if text.endswith(suffix):
            return int(float(text[:-len(suffix)]) * factor)
    return int(text)


def pvc_problems(pvc, pv, node, pods, want, need_bytes):
    """The exact pre-existing claim (uid), Bound to the named PV, unused, large enough; the PV local to dev0009 only."""
    problems = []
    m, spec, status = pvc.get('metadata', {}), pvc.get('spec', {}), pvc.get('status', {})
    if (m.get('name'), m.get('namespace'), m.get('uid')) != (want['name'], P.NS, want['uid']) or m.get('deletionTimestamp'):
        problems.append(f"pvc {m.get('namespace')}/{m.get('name')} uid {m.get('uid')} is not the bound {want['name']} {want['uid']} (or is deleting)")
    if status.get('phase') != 'Bound' or spec.get('volumeName') != want['pv']:
        problems.append(f"pvc phase {status.get('phase')} volume {spec.get('volumeName')} (want Bound to {want['pv']})")
    if not {'ReadWriteOnce', 'ReadWriteOncePod'} & set(spec.get('accessModes') or []):
        problems.append(f"pvc access modes {spec.get('accessModes')}")
    capacity = quantity((status.get('capacity') or {}).get('storage', '0'))
    if capacity < need_bytes:
        problems.append(f'pvc capacity {capacity} < {need_bytes} bytes')
    pm, ps = pv.get('metadata', {}), pv.get('spec', {})
    ref = ps.get('claimRef') or {}
    if pm.get('name') != want['pv'] or (ref.get('namespace'), ref.get('name'), ref.get('uid')) != (P.NS, want['name'], want['uid']) or \
            pv.get('status', {}).get('phase') != 'Bound':
        problems.append(f"pv {pm.get('name')} claimRef {ref.get('namespace')}/{ref.get('name')} {ref.get('uid')} is not bound to the claim")
    path = (ps.get('local') or ps.get('hostPath') or {}).get('path')
    if path != want['path']:
        problems.append(f"pv path {path!r} is not the verified {want['path']!r}")
    hostname = node['metadata'].get('labels', {}).get('kubernetes.io/hostname')
    terms = (((ps.get('nodeAffinity') or {}).get('required') or {}).get('nodeSelectorTerms')) or []
    pinned = [t for t in terms if any(e.get('key') == 'kubernetes.io/hostname' and e.get('operator') == 'In' and e.get('values') == [hostname]
                                      for e in t.get('matchExpressions') or []) or
              any(e.get('key') == 'metadata.name' and e.get('operator') == 'In' and e.get('values') == [P.JETSON] for e in t.get('matchFields') or [])]
    if not terms or len(pinned) != len(terms):
        problems.append(f'pv node affinity {terms} does not pin {P.JETSON} only')
    users = [f"{p['metadata']['namespace']}/{p['metadata']['name']}" for p in pods if p['metadata'].get('namespace') == P.NS and
             p.get('status', {}).get('phase') not in ('Succeeded', 'Failed') and
             any((v.get('persistentVolumeClaim') or {}).get('claimName') == want['name'] for v in p['spec'].get('volumes') or [])]
    if users:
        problems.append(f'pvc in use by {users}')
    return problems


def prepared_event_problem(health, prepared):
    """The loaded CUDA worker's bootstrap must report the bound prepared content (cache_hit or cache_fill)."""
    events = (health.get('bootstrap') or {}).get('events') or []
    if not any(e.get('kind') in ('cache_hit', 'cache_fill') and e.get('content_sha256') == prepared['binding']['content_sha256'] for e in events):
        return 'cuda bootstrap did not report the bound prepared content'
    return None


def holders(pods, node, resource, run):
    out = []
    for p in pods:
        if p.get('spec', {}).get('nodeName') != node or p.get('status', {}).get('phase') in ('Succeeded', 'Failed'):
            continue
        if any(resource in c.get('resources', {}).get(k, {}) for c in p['spec'].get('containers', []) + p['spec'].get('initContainers', [])
               for k in ('requests', 'limits')):
            out.append(dict(namespace=p['metadata'].get('namespace'), name=p['metadata']['name'], uid=p['metadata'].get('uid'),
                            own=p['metadata'].get('labels', {}).get('run-id') == run))
    return out


def preflight_problems(nodes, pods, run, arms=None):
    """Every selected arm's node Ready and untainted; no foreign holder of the server NPU or of any dev0009 GPU share."""
    problems, by_name = [], {n['metadata']['name']: n for n in nodes}
    for arm in selection(arms)[0]:
        a = P.ARMS[arm]
        n = by_name.get(a['node'])
        if n is None:
            problems.append(f'{a["node"]}: node not found')
            continue
        ready = next((c['status'] for c in n['status'].get('conditions', []) if c['type'] == 'Ready'), None)
        blocking = [t for t in n['spec'].get('taints') or [] if t.get('effect') in ('NoSchedule', 'NoExecute')]
        if ready != 'True' or blocking:
            problems.append(f'{a["node"]}: Ready={ready} taints={blocking}')
            continue
        resource = next(iter(a['device_resource']))
        if int(n['status']['allocatable'].get(resource, '0')) < 1:
            problems.append(f'{a["node"]}: no allocatable {resource}')
        foreign = [h for h in holders(pods, a['node'], resource, run) if not h['own']]
        if foreign:
            problems.append(f'{a["node"]}: {resource} held by {foreign}')
    return problems


class Session(collect.Session):
    """collect.Session for this experiment: own labels; streams only to the owned server-side cache (exec) and owned
    server Pods (logs); edge-node Pods are never streamed."""

    def __init__(self, out_root, run_id):
        if not run_id.startswith('gmv-') or len(run_id) > 40 or run_id.lower() != run_id or not run_id.replace('-', '').isalnum():
            raise ValueError('run-id must be gmv-[a-z0-9-], at most 40 characters')
        self.run, self.out = run_id, Path(out_root)/run_id
        self.out.mkdir(parents=True, exist_ok=False)
        self.counter, self.resources, self.unresolved = 0, [], []

    def call(self, stem, args, stdin=None, timeout=60):
        if args and args[0] in STREAM_VERBS:
            name = next((a for a in args[1:] if a.startswith(self.run+'-')), None)
            key = name.removeprefix(self.run+'-') if name else None
            reason = None
            if name is None or self.record(name) is None:
                reason = f'stream target {name!r} is not an owned Pod of this run'
            elif P.ARMS.get(key, {}).get('edge'):
                reason = 'kubelet stream to an edge-node Pod is outside the control boundary'
            elif not (args[0] == 'logs' or (args[0] == 'exec' and key == 'cache')):
                reason = f'{args[0]} into {key} is not part of this collector'
            else:
                reason = self.fresh_stream_problem(name, key)
            if reason:
                self.counter += 1
                self.save(f'{self.counter:04d}-{stem}.{"skipped" if args[0] == "logs" else "refused"}.json',
                          dict(at=datetime.now(timezone.utc).isoformat(), verb=args[0], args=args[:8], reason=reason+f'; kubectl {args[0]} not invoked'))
                if args[0] == 'logs':
                    return ''
                raise RuntimeError(f'refused: {args[0]} ({reason})')
        return collect.Session.call(self, stem, args, stdin, timeout)

    def fresh_stream_problem(self, name, key):
        """A fresh GET immediately before the stream: the creation-record UID (pod_state refuses a replacement), the server
        node, and this run's name/labels. Any mismatch or failed GET refuses the stream."""
        try:
            obj = self.pod_state(name)
        except Exception as error:
            return f'fresh GET refused the target: {error!r}'[:500]
        labels, want = obj['metadata'].get('labels') or {}, meta(self.run, name, key)['labels']
        if obj['metadata'].get('name') != name or {k: labels.get(k) for k in want} != want or not self.record(name).get('owned_labels'):
            return f'fresh GET: name/labels {obj["metadata"].get("name")!r} {labels} are not this run\'s {key}'
        if obj['spec'].get('nodeName') != P.SERVER:
            return f'fresh GET: actual node {obj["spec"].get("nodeName")!r} is not the server {P.SERVER}'
        return None

    def create(self, stem, items):
        """collect.Session.create with this experiment's owned-label check (same records/unresolved semantics)."""
        value = dict(apiVersion='v1', kind='List', items=items)
        self.save(stem+'.manifest.json', value)
        self.call(stem+'-dry-run', ['create', '--dry-run=server', '-f', '-', '-o', 'json'], json.dumps(value))
        error = None
        try:
            raw = self.call(stem, ['create', '-f', '-', '-o', 'json'], json.dumps(value))
        except Exception as exc:
            error = exc
            raw = (self.out/f'{self.counter:04d}-{stem}.stdout').read_text(errors='replace')
        objects, complete = collect.created_objects(raw)
        records = []
        for obj in objects:
            m, labels = obj['metadata'], obj['metadata'].get('labels', {})
            record = dict(kind=obj['kind'], name=m['name'], uid=m['uid'], created_at=m.get('creationTimestamp'),
                          owned_labels=labels.get('experiment') == P.EXPERIMENT and labels.get('run-id') == self.run)
            records.append(record)
            self.resources.append(record)
        confirmed = {(r['kind'], r['name']) for r in records}
        unresolved = [dict(kind=i['kind'], name=i['metadata']['name'], stem=stem) for i in items if (i['kind'], i['metadata']['name']) not in confirmed]
        if error is not None or not complete:
            self.unresolved += unresolved
        self.save(stem+'.created.json', dict(confirmed=records, unresolved=unresolved if (error is not None or not complete) else [],
                                             stdout_complete=complete))
        if error is not None:
            raise RuntimeError(f'{stem}: create failed after {len(records)}/{len(items)} confirmed') from error
        if not complete or len(records) != len(items) or not all(r['owned_labels'] for r in records):
            raise RuntimeError(f'{stem}: create confirmed {len(records)}/{len(items)} owned objects')

    def ip(self, key):
        return self.pod_state(f'{self.run}-{key}')['status']['podIP']


def dependency_hashes():
    """sha256 of every repository module a fresh collector process loads plus every Pod-shipped file."""
    probe = ("import json,sys;sys.path.insert(0,sys.argv[1]);import live;"
             "print(json.dumps(sorted({m.__file__ for m in list(sys.modules.values()) if getattr(m,'__file__',None)})))")
    loaded = json.loads(subprocess.run([sys.executable, '-c', probe, str(HERE)], capture_output=True, text=True, check=True, timeout=120,
                                       env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}).stdout)
    files = {Path(f).resolve() for f in loaded if f.endswith('.py') and Path(f).resolve().is_relative_to(ROOT/'experiments')}
    files |= {p.resolve() for p in [*SHIPPED.values(), *SHIPPED_PREPARED.values(), *BUILD_CODE]}
    return {str(f.relative_to(ROOT)): P.file_sha(f) for f in sorted(files)}


def release_problems(release, frozen, code, arms=None, prepared=None):
    problems = []
    want = dict(prepared=prepared and prepared['binding'], cuda_cache_pvc=prepared and prepared['pvc'],
                prepared_deadlines=prepared and prepared['deadlines'], io_profile=prepared and prepared['io_profile'])
    for key, value in want.items():
        if release.get(key) != value:
            problems.append(f'{key} differs from the release ({"prepared mode" if prepared else "old mode"})')
    if list(release.get('arms', P.ARMS)) != list(selection(arms)[0]):
        problems.append(f"release arms {list(release.get('arms', P.ARMS))} differ from the selected arms {list(selection(arms)[0])}")
    for name in ('contract.json', 'assets-manifest.json'):
        if release.get('frozen', {}).get(name) != P.file_sha(Path(frozen)/name):
            problems.append(f'{name} hash differs from the release')
    if release.get('code') != code:
        problems.append('collector/shipped code hashes differ from the release')
    if not release.get('approved_by') or not release.get('stages'):
        problems.append('release needs approved_by and stages')
    return problems


def check_occupancy(session, label, arms=None):
    pods = json.loads(session.call(label, ['get', 'pods', '-A', '-o', 'json']))['items']
    seen = {}
    for arm in selection(arms)[0]:
        a = P.ARMS[arm]
        resource = next(iter(a['device_resource']))
        own = session.record(f'{session.run}-{arm}')
        found = holders(pods, a['node'], resource, session.run)
        seen[arm] = dict(holders=found, owned_uid=own and own['uid'], only_owned=[h['uid'] for h in found] == ([own['uid']] if own else []))
    session.save(label+'.json', seen)
    if not all(v['only_owned'] for v in seen.values()):
        raise RuntimeError(f'{label}: accelerator holders are not exactly the owned worker Pods')


def identity(session, arm, health, contract_sha):
    """The worker must report this run's creation UID/node, the frozen contract and weights, the expected device."""
    obj = session.pod_state(f'{session.run}-{arm}')
    stage = (health.get('stages') or {}).get(P.STAGE) or {}
    contract = json.loads(Path(session.frozen/'contract.json').read_text())
    want = dict(status='ready', pod_uid=obj['metadata']['uid'], node=obj['spec']['nodeName'], device=EXPECTED_DEVICE[arm],
                contract_sha256=contract_sha, model_sha256=contract['models'][P.ARMS[arm]['model']]['weights'])
    seen = dict(status=health.get('status'), pod_uid=health.get('pod_uid'), node=health.get('node'), device=stage.get('device'),
                contract_sha256=stage.get('contract_sha256'), model_sha256=stage.get('model_sha256'))
    problems = [k for k in want if want[k] != seen[k]]
    session.save(f'identity-{arm}.json', dict(want=want, seen=seen, problems=problems))
    if problems:
        raise RuntimeError(f'{arm}: identity mismatch {problems}')
    return want


def call_arm(session, arm, url, row, data, want, timeout=None):
    """One /run call with an absolute transfer deadline and a reply byte cap (bounded_http; binary framing unchanged)."""
    request_id = f"{row['input']}-{arm}"
    body = dict(run_id=session.run, request_id=request_id, stage=P.STAGE, data=data)
    started = time.monotonic()
    try:
        reply = bounded_http.post(url+'/run', body, timeout=timeout or P.DEADLINES['call_s']+30, max_body=RUN_REPLY_MAX)
    except urllib.error.HTTPError as error:
        raw = error.read()[:ERROR_KEEP]
        try:
            reply = json.loads(raw)
        except ValueError:
            reply = dict(status='error', error=raw.decode(errors='replace'))
        reply['http_status'] = error.code
    except Exception as error:
        reply = dict(status='error', error=repr(error)[:2000])
    reply.update(arm=arm, input=row['input'], request_id=request_id, collector_wall_ms=(time.monotonic()-started)*1000)
    if reply.get('status') == 'ok':
        try:
            output = __import__('base64').b64decode(reply['data'], validate=True)
            reply['result'] = json.loads(output)
            problems = receipt_problems(session, arm, row, data, output, reply['receipt'], reply['result'], want)
            reply['output_evidence'] = output_evidence(arm, reply['result'])
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            problems = [f'malformed reply {error!r}'[:500]]
        if problems:
            reply.update(status='error', error='receipt not bound to the verified arm: '+'; '.join(problems))
    return reply


def output_evidence(arm, result):
    """CUDA: the generated output tensor's device. Mobilint: the host output tensor, labeled apart from NPU computation."""
    device = result['device']
    if arm == 'cuda':
        return dict(output_device=device.get('output_device'))
    return dict(output_tensor_device=device.get('output_tensor_device'), output_tensor_note=device.get('output_tensor_note'))


def receipt_problems(session, arm, row, data, output, receipt, result, want):
    """Every successful receipt against the identity verified at readiness (UID/node/device/model/contract) and this request."""
    expect = dict(pod_uid=want['pod_uid'], node=want['node'], device=want['device'], backend=arm, model_sha256=want['model_sha256'],
                  run_id=session.run, request_id=f"{row['input']}-{arm}", stage=P.STAGE, input_sha256=P.sha256(data),
                  output_sha256=P.sha256(output))
    problems = [f'{k}={receipt.get(k)!r}' for k, v in expect.items() if receipt.get(k) != v]
    if (receipt.get('static') or {}).get('contract_sha256') != want['contract_sha256']:
        problems.append('static.contract_sha256 differs')
    if result.get('input_id') != row['input']:
        problems.append(f"result input_id={result.get('input_id')!r}")
    device = result.get('device') or {}
    if arm == 'cuda':
        if not str(device.get('output_device')).startswith('cuda') or (receipt.get('timing') or {}).get('output_device') != device.get('output_device'):
            problems.append(f"output_device={device.get('output_device')!r} is not the CUDA generated-tensor device")
    elif device.get('output_tensor_device') != 'cpu' or not device.get('output_tensor_note'):
        problems.append(f"output_tensor_device={device.get('output_tensor_device')!r} is not the labeled host output tensor")
    return problems


def collect_stage(session, name, rows, urls, inputs, identities):
    """Sequential calls; each reply is appended durably (fsync) before the next. The first failed call stops the stage:
    that arm's /health (dead child record with its output tail) is saved and the remaining schedule is not executed."""
    ledger = []
    for row in rows:
        reply = call_arm(session, row['arm'], urls[row['arm']], row, inputs[row['input']], identities[row['arm']])
        ledger.append(reply)
        with (session.out/'ledger.jsonl').open('a') as handle:
            handle.write(json.dumps(reply)+'\n')
            handle.flush()
            os.fsync(handle.fileno())
        if reply.get('status') != 'ok':
            try:
                health = bounded_http.control(urls[row['arm']]+'/health', timeout=30)
            except Exception as error:
                health = dict(status='unavailable', error=repr(error)[:ERROR_KEEP])
            session.save(f"health-after-failure-{row['arm']}.json", health)
            break
    session.save(f'ledger-{name}.json', ledger)
    if not all(r.get('status') == 'ok' for r in ledger):
        raise RuntimeError(f'{name}: call {len(ledger)}/{len(rows)} failed; remaining calls not executed; see ledger-{name}.json')


def execute(session, stage, arms=None, prepared=None):
    phase = collect.phase
    arms, _ = selection(arms)
    groups = bundle_groups(arms, prepared)
    contract_sha = P.file_sha(session.frozen/'contract.json')
    manifest_sha = P.file_sha(session.frozen/'assets-manifest.json')
    manifest = json.loads((session.frozen/'assets-manifest.json').read_text())
    contract = json.loads((session.frozen/'contract.json').read_text())
    with phase(session, 'preflight'):
        problems = prepare.verify_manifest(manifest)
        collect.protected_snapshot(session, 'protected-before')
        nodes = json.loads(session.call('nodes', ['get', 'nodes', '-o', 'json']))['items']
        pods = json.loads(session.call('pods', ['get', 'pods', '-A', '-o', 'json']))['items']
        problems += preflight_problems(nodes, pods, session.run, arms)
        if prepared:
            want = prepared['pvc']
            pvc = json.loads(session.call('pvc', ['get', 'pvc', '-n', P.NS, want['name'], '-o', 'json']))
            pv = json.loads(session.call('pv', ['get', 'pv', want['pv'], '-o', 'json']))
            need = prepared['binding']['total_bytes'] + 4096*prepared['binding']['file_count'] + 256*1024**2
            node = next((n for n in nodes if n['metadata']['name'] == P.JETSON), dict(metadata={}))
            problems += pvc_problems(pvc, pv, node, pods, want, need)
            session.save('preflight-pvc.json', dict(want=want, pvc_uid=pvc['metadata'].get('uid'), pv=want['pv'], need_bytes=need))
        footprint = selected_footprint(manifest['footprint_bytes'], arms, prepared)
        session.save('preflight.json', dict(problems=problems, footprint_bytes=manifest['footprint_bytes'], cache_size=cache_size(footprint)))
        if problems:
            raise RuntimeError('preflight: '+'; '.join(problems))
    with phase(session, 'cache'):
        session.create('cache', [config_map(session.run, prepared),
                                 cache_pod(session.run, footprint, prepared and prepared['deadlines']['cache_lifetime_s'])])
        status = session.wait_ready('cache', [session.run+'-cache'], P.DEADLINES['cache_ready_s'])
        if status[session.run+'-cache']:
            raise RuntimeError('cache not Ready: '+json.dumps(status))
        cache_url = f"http://{session.ip('cache')}:{P.PORT}"
        tar_path = session.out/'bundle.tar'
        with tarfile.open(tar_path, 'x') as archive:
            archive.add(session.frozen/'assets-manifest.json', 'assets-manifest.json')
            for item in manifest['files']:
                if item['group'] in groups:
                    archive.add(item['source'], item['path'])
            if prepared:
                archive.add(prepared['manifest_path'], 'prepared/prepared-manifest.json')
                archive.add(prepared['tar'], 'prepared/runtime.tar')
        bundle_bytes = tar_path.stat().st_size
        with tar_path.open('rb') as source:
            session.call('cache-upload', ['exec', '-i', '-n', P.NS, session.run+'-cache', '-c', 'worker', '--', 'python3', '-c',
                                          "import tarfile,sys;tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall('/work',filter='data')"],
                         stdin=source, timeout=P.DEADLINES['upload_s'])
        tar_path.unlink()  # reproducible from the frozen manifest; size recorded below
        code, body, _ = bounded_http.exchange(cache_url+'/assets-manifest.json', None, None, 30, 8*1024*1024)
        if code != 200 or P.sha256(body) != manifest_sha:
            raise RuntimeError('cache does not serve the frozen asset manifest')
        if prepared:
            code, body, _ = bounded_http.exchange(cache_url+'/prepared/prepared-manifest.json', None, None, 30, C.MANIFEST_MAX)
            if code != 200 or P.sha256(body) != prepared['binding']['manifest_sha256']:
                raise RuntimeError('cache does not serve the bound prepared manifest')
        session.save('cache.json', dict(url=cache_url, bundle_bytes=bundle_bytes, manifest_sha256=manifest_sha))
    urls = {}
    with phase(session, 'workers'):
        session.create('workers', [worker_pod(session.run, arm, cache_url, manifest_sha, prepared) for arm in arms])
        ready_s = prepared['deadlines']['worker_ready_s'] if prepared else P.DEADLINES['worker_ready_s']
        status = session.wait_ready('workers', [f'{session.run}-{arm}' for arm in arms], ready_s)
        session.save('workers-ready.json', status)
        health = {}
        for arm in arms:
            if status[f'{session.run}-{arm}'] is None:
                urls[arm] = f"http://{session.ip(arm)}:{P.PORT}"
                health[arm] = bounded_http.control(urls[arm]+'/health', timeout=30)
            else:
                health[arm] = dict(status='not_ready', error=status[f'{session.run}-{arm}'])
            session.save(f'health-{arm}.json', health[arm])
        identities = {arm: identity(session, arm, health[arm], contract_sha) for arm in arms}
        if prepared and 'cuda' in arms and prepared_event_problem(health['cuda'], prepared):
            raise RuntimeError(prepared_event_problem(health['cuda'], prepared))
        session.save('identities.json', identities)
        check_occupancy(session, 'occupancy-ready', arms)
    inputs = {row['id']: Path(row['source']).read_bytes() for row in manifest['inputs']}
    stages = ['probe'] + (['qualification'] if stage == 'qualification' else [])
    for name in stages:
        with phase(session, name):
            collect_stage(session, name, [r for r in contract['schedule'][name] if r['arm'] in arms], urls, inputs, identities)
    with phase(session, 'final'):
        check_occupancy(session, 'occupancy-final', arms)
        for arm, url in urls.items():
            session.save(f'health-final-{arm}.json', bounded_http.control(url+'/health', timeout=30))


def verify_cleanup(session):
    problems = [f"{r['kind']} {r['name']}: delete={r.get('delete')}" for r in session.resources if r.get('delete') not in ('deleted', 'already-absent')]
    for record in session.resources:
        try:
            if session.owned_present(record):
                problems.append(f"{record['kind']} {record['name']} uid {record['uid']} still present")
        except Exception as error:
            problems.append(f"{record['name']}: presence check failed {error!r}"[:300])
    if session.unresolved:
        problems.append(f'unresolved create outcome (never adopted or deleted): {session.unresolved}')
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--frozen', required=True)
    ap.add_argument('--runs')
    ap.add_argument('--release')
    ap.add_argument('--stage', choices=('probe', 'qualification'))
    ap.add_argument('--run-id', default='gmv-'+datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%S'))
    ap.add_argument('--print-release-draft', action='store_true')
    ap.add_argument('--plan', action='store_true')
    ap.add_argument('--arm', choices=tuple(P.ARMS), help='one arm only (arm-only qualification); default: the pair')
    ap.add_argument('--prepared', help='prepared CUDA runtime dir (prepare_runtime.py output); needs --cuda-cache-pvc')
    ap.add_argument('--cuda-cache-pvc', help='json: {name, uid, pv, path, backing_device, verified_by} of the verified PVC')
    ap.add_argument('--io-profile', choices=(C.SD_PROFILE['name'],), help='explicit SD I/O profile for an mmcblk-backed cache PVC')
    a = ap.parse_args(argv)
    arms = (a.arm,) if a.arm else None
    frozen = Path(a.frozen)
    prepared = None
    if bool(a.prepared) != bool(a.cuda_cache_pvc) or (a.prepared and 'cuda' not in selection(arms)[0]) or (a.io_profile and not a.prepared):
        raise SystemExit('--prepared and --cuda-cache-pvc go together and need the cuda arm; --io-profile needs --prepared')
    if a.prepared:
        prepared = load_prepared(a.prepared, frozen, json.loads(Path(a.cuda_cache_pvc).read_text()), hash_tar=not a.plan,
                                 io_profile_name=a.io_profile)
    if a.print_release_draft:
        draft = dict(frozen={n: P.file_sha(frozen/n) for n in ('contract.json', 'assets-manifest.json')}, code=dependency_hashes(),
                     approved_by=None, stages=[])
        if prepared:
            draft.update(prepared=prepared['binding'], cuda_cache_pvc=prepared['pvc'], prepared_deadlines=prepared['deadlines'])
            if prepared['io_profile']:
                draft['io_profile'] = prepared['io_profile']
        print(json.dumps(draft, indent=1))
        return
    manifest = json.loads((frozen/'assets-manifest.json').read_text())
    if a.plan:
        print(json.dumps(plan(a.run_id, 'http://<cache-pod-ip>:8080', P.file_sha(frozen/'assets-manifest.json'), manifest['footprint_bytes'],
                              arms, prepared), indent=1))
        return
    release = json.loads(Path(a.release).read_text())
    problems = release_problems(release, frozen, dependency_hashes(), arms, prepared)
    if a.stage not in release.get('stages', []):
        problems.append(f'stage {a.stage} not released')
    if problems:
        raise SystemExit('release check failed: '+'; '.join(problems))
    session = Session(a.runs, a.run_id)
    session.frozen = frozen
    session.save('release.json', release)
    selected, groups = selection(arms)
    session.save('selection.json', dict(arms=list(selected), groups=sorted(groups), scope='pair' if selected == tuple(P.ARMS) else
                                        'arm-only qualification of ONE accelerator arm; not a pair result'))
    error = None
    try:
        execute(session, a.stage, arms, prepared)
    except BaseException as exc:
        error = repr(exc)
        raise
    finally:
        cleanup_problems = ['cleanup did not run']
        try:
            with collect.phase(session, 'cleanup'):
                session.delete_owned('cleanup', [r['name'] for r in session.resources])
                cleanup_problems = verify_cleanup(session)
                collect.protected_snapshot(session, 'protected-after')
        except BaseException as exc:
            cleanup_problems.append(f'cleanup error {exc!r}'[:500])
        finally:
            session.save('cleanup.json', dict(records=session.resources, unresolved=session.unresolved, error=error,
                                              problems=cleanup_problems, verified=not cleanup_problems))
        if error is None and cleanup_problems:
            raise SystemExit('run not complete: '+'; '.join(cleanup_problems))


if __name__ == '__main__':
    main()
