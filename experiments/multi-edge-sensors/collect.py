"""Multi-edge RTSP/text -> YOLO11n (V) || MiniLM (T) -> fusion (F) placement collector.

--dry-run prints the frozen design (source mapping, 9 candidates, Pod resources,
workload bounds) without any kubectl call. --execute runs, in order:
preflight -> cache -> edge sources (RTSP publish + freeze) -> per-task qualification
on every candidate (compute, input fetch, RTSP protocol probe) -> pairwise payload
transfer probes -> per-workflow profile/plans (task-fastest, network greedy, exact,
source-local, server-fixed) -> fresh default-scheduler observation -> frozen
schedule -> evaluation (warmup1 + eval3 per unique placement) -> cleanup.
Every kubectl call, phase start/end, partial stdout/stderr and failure is saved.
kubectl exec targets only the owned cache (one bundle upload) and controller Pods on the
helper server; edge Pods are driven over HTTP from the controller (no exec into EdgeCore edges).
Only resources created by this run (name list + run-id label) are deleted.
"""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LIVE = ROOT/'experiments/real-topology-live'
sys.path.insert(0, str(LIVE))
import topology  # noqa: E402
from placement_policies import prepare as planner  # noqa: E402
import run_baseline_comparison as baseline  # noqa: E402
from run_placement import Pilot  # noqa: E402
from run_smoke import IMAGE, NS  # noqa: E402
sys.path.insert(0, str(HERE))
import models  # noqa: E402
import plan as plan_module  # noqa: E402
import scenario as scenario_module  # noqa: E402

EXPERIMENT = 'multi-edge-sensors'
DEFAULT_TOPOLOGY = LIVE/'topologies/ready-nodes-20261022.json'
DEFAULT_SCENARIO = HERE/'scenario-20261022.json'
TASKS = ('V', 'T', 'F')
SEED = 20261022
WARMUP, EVALUATION = 1, 3
MAX_EVAL_WORKFLOWS = 72
POLICIES = ('kube-default', 'task-fastest', 'greedy-network', 'exact')
DIAGNOSTICS = ('source-local', 'server-fixed')
# Controller exec carries one JSON argument; Linux caps a single argument at 128 KiB.
REMOTE_ARG_LIMIT = 96*1024
PROBE_REPEATS = 3
# Pod HTTP port; overridable only for offline fake-kubectl tests on a host whose 8080 is taken.
PORT = int(os.environ.get('MES_POD_PORT', '8080'))
RESOURCES = {
    'cache': dict(cpu='1', memory='1Gi'),
    'video-source': dict(cpu='1', memory='768Mi'),
    'text-source': dict(cpu='100m', memory='128Mi'),
    'controller': dict(cpu='200m', memory='256Mi'),
    'V': dict(cpu='2', memory='1536Mi', threads=2),
    'T': dict(cpu='1', memory='2Gi', threads=1),
    'F': dict(cpu='250m', memory='384Mi', threads=1)}
CODE = ('runtime.py', 'models.py', 'rtsp.py', 'scenario.py', 'bootstrap.py')
TIMEOUTS = dict(cache_ready=120, upload=600, source_ready=420, worker_ready=480, batch=600, drive=600, delete=120, terminal_grace=45)
TERMINAL_REASONS = {'ImagePullBackOff', 'ErrImagePull', 'CrashLoopBackOff', 'CreateContainerConfigError', 'InvalidImageName'}


def millicpu(raw):
    raw = str(raw)
    return float(raw[:-1]) if raw.endswith('m') else float(raw)*1000


def mebibytes(raw):
    raw = str(raw)
    for unit, factor in (('Ki', 1/1024), ('Mi', 1), ('Gi', 1024)):
        if raw.endswith(unit):
            return float(raw[:-len(unit)])*factor
    return float(raw)/1024**2


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_design(prepared_dir, topology_path, scenario_path):
    prepared = json.loads((Path(prepared_dir)/'prepared.json').read_text())
    frozen = topology.load(topology_path)
    names = topology.aliases(frozen)
    roles = topology.roles(frozen)
    sc = scenario_module.validate(json.loads(Path(scenario_path).read_text()),
                                  edge_nodes=[a for a, r in roles.items() if r == 'edge'])
    if sha(Path(scenario_path).read_bytes()) != prepared['scenario']['sha256']:
        raise ValueError('Scenario differs from the prepared scenario; prepare again')
    for name in CODE:
        if sha((HERE/name).read_bytes()) != prepared['code'][name]:
            raise ValueError(f'{name} differs from the prepared code hash; prepare again')
    candidates = topology.candidates(frozen)
    sources = {sid: dict(kind='video', alias=s['node_alias'], node=names[s['node_alias']], image=s['image'], path=s['path'])
               for sid, s in sc['video_sources'].items()}
    sources.update({sid: dict(kind='text', alias=s['node_alias'], node=names[s['node_alias']])
                    for sid, s in sc['text_sources'].items()})
    workflows = [dict(id=f['id'], video=f['video'], text=f['text'], video_node=sources[f['video']]['node'],
                      text_node=sources[f['text']]['node'], destination_node=sources[f['video']]['node']) for f in sc['workflows']]
    policies = POLICIES + DIAGNOSTICS
    max_jobs = len(workflows)*len(policies)
    design = dict(experiment=EXPERIMENT, seed=SEED, namespace=NS, image=IMAGE, pod_port=PORT,
        topology=topology.describe(frozen), candidates=candidates, server=names['c'],
        scenario=dict(name=sc['name'], sha256=prepared['scenario']['sha256'], stream=sc['stream'],
                      frame_representation=sc['frame_representation'], inputs=sc['inputs']),
        sources=sources, workflows=workflows, prepared=dict(name=prepared['name'], models=prepared['models'],
                                                            bundles={k: v['sha256'] for k, v in prepared['bundles'].items()}),
        dag=dict(tasks=[f"V: {prepared['models']['V'].get('label', 'yolo11n-onnx')} CPU detection", f"T: {prepared['models']['T'].get('label', 'all-MiniLM-L6-v2-onnx')} CPU embedding + class-prompt cosine",
                        'F: deterministic fusion (not a learned model)'], edges=['source(video)->V', 'source(text)->T', 'V->F', 'T->F',
                        'F->destination(video source edge)'], parallel='V || T, joined by F'),
        resources=RESOURCES, policies=list(POLICIES), diagnostics=list(DIAGNOSTICS),
        policy_sources={'kube-default': 'fresh unpinned default-scheduler observation per workflow, then re-pinned evaluation',
                        'task-fastest': 'plan.taskwise_fastest: V,T,F in order, each min measured execution cost among remaining-capacity quality-valid nodes; no network, no critical path',
                        'greedy-network': 'real-topology-live/placement_policies.sequential (network-aware greedy, topological order)',
                        'exact': 'real-topology-profile/prepare.plan topology_profile (exhaustive over the frozen cost model; not a new algorithm)',
                        'source-local': 'diagnostic: V on video edge, T on text edge, F on destination edge',
                        'server-fixed': 'diagnostic: all tasks on the helper server'},
        cost_model='no-queue critical path; V||T in parallel (max), not summed; ignores link/node contention',
        calibration='qualification inputs = calibration seqs only; evaluation seqs never used for planning',
        warmup=WARMUP, evaluation_count=EVALUATION,
        workload=dict(source_pods=len(sources), qualification_pods=len(TASKS)*len(candidates),
                      qualification_requests=dict(V=len(candidates)*len(sc['video_sources'])*(1+len(sc['inputs']['calibration'])),
                                                  T=len(candidates)*len(sc['text_sources'])*(1+len(sc['inputs']['calibration'])),
                                                  F=len(candidates)*(1+len(sc['inputs']['calibration']))),
                      rtsp_protocol_probes=len(candidates)*len(sc['video_sources']),
                      transfer_probe_gets=PROBE_REPEATS*(2*len(candidates)**2 + len(candidates)*len(workflows)),
                      scheduler_observation_pods=len(TASKS)*len(workflows),
                      max_unique_jobs=max_jobs, max_eval_workflows=max_jobs*EVALUATION,
                      max_workflow_requests=max_jobs*(WARMUP+EVALUATION), eval_workflow_cap=MAX_EVAL_WORKFLOWS),
        network='native LAN, no qdisc/shaping (first bounded pilot)',
        separation='RTSP protocol probes (live stream) are reported separately from the frozen decoded-frame placement comparison',
        observation_sharing='policies with the same placement in the same workflow share one job/observation',
        control_path='kubectl exec only into the owned cache (bundle upload) and controller Pods on the server; edge Pods via HTTP')
    if design['workload']['max_eval_workflows'] > MAX_EVAL_WORKFLOWS:
        raise ValueError('Design exceeds the evaluation workflow cap')
    return design, prepared


def created_objects(raw):
    """(objects, complete) printed by `kubectl create -o json` (one JSON document per object, or a List).

    Every complete document is kept as soon as it parses; a truncated or garbled tail stops parsing
    with complete=False instead of discarding the identities already read."""
    decoder, index, out = json.JSONDecoder(), 0, []
    while True:
        while index < len(raw) and raw[index].isspace():
            index += 1
        if index >= len(raw):
            return out, True
        try:
            value, index = decoder.raw_decode(raw, index)
        except ValueError:
            return out, False
        out += value.get('items', []) if isinstance(value, dict) and value.get('kind') == 'List' else [value]


# ---------------- session ----------------

class Session:
    """kubectl seam: every call goes through `rtk proxy kubectl`; events/stdout/stderr saved per call."""

    def __init__(self, out, design, prepared_dir):
        self.out, self.design, self.prepared_dir = Path(out), design, Path(prepared_dir)
        self.out.mkdir(parents=True, exist_ok=False)
        self.run = self.out.name
        self.counter, self.resources, self.unresolved = 0, [], []
        self.nodes = dict(design['topology']['aliases'])  # alias -> node name, topology order

    def save(self, name, value):
        with (self.out/name).open('x') as handle:
            json.dump(value, handle, indent=1)
            handle.write('\n')

    def call(self, stem, args, stdin=None, timeout=60):
        if args == baseline.CAPACITY_PODS_ARGS and stdin is None:
            return json.dumps(baseline.projected_call(self, stem, args, baseline.capacity_pods, timeout))
        self.counter += 1
        tag = f'{self.counter:04d}-{stem}'
        command = ['rtk', 'proxy', 'kubectl', '--request-timeout=30s', *args]
        event = dict(started_at=datetime.now(timezone.utc).isoformat(), command=command[:3]+args[:8], timeout_s=timeout)
        start = time.monotonic()
        try:
            with (self.out/(tag+'.stdout')).open('wb') as so, (self.out/(tag+'.stderr')).open('wb') as se:
                if hasattr(stdin, 'read'):
                    result = subprocess.run(command, stdin=stdin, stdout=so, stderr=se, timeout=timeout)
                else:
                    result = subprocess.run(command, input=stdin.encode() if isinstance(stdin, str) else stdin,
                                            stdout=so, stderr=se, timeout=timeout)
            event.update(status='completed', returncode=result.returncode)
            if result.returncode:
                raise RuntimeError(f'{tag} failed rc={result.returncode}; see saved stderr')
            return (self.out/(tag+'.stdout')).read_text()
        except BaseException as error:
            event.update(status='timeout' if isinstance(error, subprocess.TimeoutExpired) else 'error', error=repr(error))
            raise
        finally:
            event.update(finished_at=datetime.now(timezone.utc).isoformat(), duration_ms=(time.monotonic()-start)*1000, tag=tag)
            self.save(tag+'.event.json', event)

    # ---- manifests ----
    def meta(self, name, component, extra=None):
        return dict(name=name, namespace=NS, labels=dict(experiment=EXPERIMENT, **{'run-id': self.run}, component=component, **(extra or {})))

    def pod(self, name, component, nodes, role=None, task=None, source=None, labels=None):
        kind = task or role or 'cache'
        res = RESOURCES[kind]
        env = [dict(name='NODE_NAME', valueFrom=dict(fieldRef=dict(fieldPath='spec.nodeName'))),
               dict(name='POD_UID', valueFrom=dict(fieldRef=dict(fieldPath='metadata.uid'))),
               dict(name='POD_IP', valueFrom=dict(fieldRef=dict(fieldPath='status.podIP'))),
               dict(name='TMPDIR', value='/work/tmp'), dict(name='PORT', value=str(PORT)), dict(name='PYTHONDONTWRITEBYTECODE', value='1'),
               dict(name='OMP_NUM_THREADS', value=str(res.get('threads', 1))), dict(name='OPENBLAS_NUM_THREADS', value='1')]
        if role:
            env += [dict(name='ROLE', value=role), dict(name='ASSET_URL', value=self.cache_url)]
        if task:
            env += [dict(name='TASK_ID', value=task), dict(name='THREADS', value=str(res['threads']))]
        if source:
            env.append(dict(name='SOURCE_ID', value=source))
        container = dict(name='worker', image=IMAGE, imagePullPolicy='IfNotPresent',
            resources=dict(requests=dict(cpu=res['cpu'], memory=res['memory'], **{'ephemeral-storage': '512Mi'}),
                           limits=dict(cpu=res['cpu'], memory=res['memory'], **{'ephemeral-storage': '3Gi'})),
            env=env, volumeMounts=[dict(name='work', mountPath='/work')],
            securityContext=dict(allowPrivilegeEscalation=False, readOnlyRootFilesystem=True, capabilities=dict(drop=['ALL'])))
        if role:
            container['command'] = ['python3', '-u', '-c', "import os,runpy;os.makedirs('/work/tmp',exist_ok=True);"
                                    "runpy.run_path('/config/bootstrap.py',run_name='__main__')"]
            container['volumeMounts'].append(dict(name='config', mountPath='/config', readOnly=True))
            container['readinessProbe'] = dict(httpGet=dict(path='/health', port=PORT), initialDelaySeconds=2, periodSeconds=3, timeoutSeconds=3)
        else:
            container['command'] = ['python3', '-u', '-m', 'http.server', str(PORT), '--directory', '/work']
            container['readinessProbe'] = dict(httpGet=dict(path='/', port=PORT), initialDelaySeconds=1, periodSeconds=3, timeoutSeconds=3)
        spec = dict(schedulerName='default-scheduler', automountServiceAccountToken=False, restartPolicy='Never',
                    activeDeadlineSeconds=5400, terminationGracePeriodSeconds=3, enableServiceLinks=False,
                    securityContext=dict(runAsUser=65532, runAsGroup=65532, runAsNonRoot=True, fsGroup=65532),
                    affinity=dict(nodeAffinity=dict(requiredDuringSchedulingIgnoredDuringExecution=dict(nodeSelectorTerms=[
                        dict(matchFields=[dict(key='metadata.name', operator='In', values=[n])]) for n in nodes]))),
                    containers=[container], volumes=[dict(name='work', emptyDir=dict(sizeLimit='3Gi'))])
        if role:
            spec['volumes'].append(dict(name='config', configMap=dict(name=self.run+'-code')))
        return dict(apiVersion='v1', kind='Pod', metadata=self.meta(name, component, labels), spec=spec)

    def create(self, stem, items):
        """Create-only (never apply/update): server dry-run, then create; record the API-returned UID of every
        object actually created, also when the create partially fails, and only then raise."""
        value = dict(apiVersion='v1', kind='List', items=items)
        self.save(stem+'.manifest.json', value)
        self.call(stem+'-dry-run', ['create', '--dry-run=server', '-f', '-', '-o', 'json'], json.dumps(value))
        error = None
        try:
            raw = self.call(stem, ['create', '-f', '-', '-o', 'json'], json.dumps(value))
        except Exception as exc:
            error = exc
            raw = (self.out/f'{self.counter:04d}-{stem}.stdout').read_text(errors='replace')
        records = []
        objects, complete = created_objects(raw)
        for obj in objects:
            meta = obj['metadata']
            labels = meta.get('labels', {})
            owned = labels.get('experiment') == EXPERIMENT and labels.get('run-id') == self.run
            record = dict(kind=obj['kind'], name=meta['name'], uid=meta['uid'], created_at=meta.get('creationTimestamp'), owned_labels=owned)
            records.append(record)
            self.resources.append(record)  # kept immediately; a later parse failure cannot erase it
        confirmed = {(r['kind'], r['name']) for r in records}
        unresolved = [dict(kind=i['kind'], name=i['metadata']['name'], stem=stem) for i in items
                      if (i['kind'], i['metadata']['name']) not in confirmed]
        if error is not None or not complete:
            # Objects without a returned UID may or may not exist: never adopted, never counted as cleaned.
            self.unresolved += unresolved
        self.save(stem+'.created.json', dict(confirmed=records, unresolved=unresolved if (error is not None or not complete) else [],
                                             stdout_complete=complete))
        if error is not None:
            stderr = (self.out/f'{self.counter:04d}-{stem}.stderr').read_text(errors='replace')
            raise RuntimeError(f'{stem}: create failed after {len(records)}/{len(items)} confirmed objects: {stderr.strip()[:2000]}') from error
        if not complete or len(records) != len(items) or not all(r['owned_labels'] for r in records):
            raise RuntimeError(f'{stem}: create output confirmed {len(records)}/{len(items)} owned objects (stdout complete={complete})')

    def record(self, name, kind='Pod'):
        return next((r for r in reversed(self.resources) if r['name'] == name and r['kind'] == kind), None)

    def delete_record(self, stem, record):
        """API-enforced UID precondition delete: a replaced or foreign object with the same name is never deleted."""
        plural = 'pods' if record['kind'] == 'Pod' else 'configmaps'
        body = dict(kind='DeleteOptions', apiVersion='v1', preconditions=dict(uid=record['uid']), propagationPolicy='Background',
                    **(dict(gracePeriodSeconds=3) if record['kind'] == 'Pod' else {}))
        try:
            self.call(stem+'-delete', ['delete', '--raw', f'/api/v1/namespaces/{NS}/{plural}/{record["name"]}', '-f', '-'], json.dumps(body))
            record['delete'] = 'deleted'
        except Exception as error:
            stderr = (self.out/f'{self.counter:04d}-{stem}-delete.stderr').read_text(errors='replace')
            if 'NotFound' in stderr:
                record['delete'] = 'already-absent'
            elif 'Precondition failed' in stderr or 'Conflict' in stderr:
                record['delete'] = 'refused-uid-mismatch'
            else:
                record['delete'] = 'error'
                record['delete_error'] = repr(error)

    def owned_present(self, record):
        """True while the object with the recorded UID still exists (a same-name object with another UID is not ours)."""
        try:
            obj = json.loads(self.call('state-'+record['name'].removeprefix(self.run+'-'),
                                       ['get', 'pod' if record['kind'] == 'Pod' else 'configmap', '-n', NS, record['name'], '-o', 'json']))
        except Exception:
            stderr = (self.out/f"{self.counter:04d}-state-{record['name'].removeprefix(self.run+'-')}.stderr").read_text(errors='replace')
            if 'NotFound' in stderr:
                return False
            raise
        return obj['metadata']['uid'] == record['uid']

    def delete_owned(self, stem, names):
        records = [r for r in self.resources if r['name'] in names and r.get('delete') not in ('deleted', 'already-absent')]
        for record in records:
            self.delete_record(stem, record)
        deadline = time.monotonic() + TIMEOUTS['delete']
        for record in records:
            while record['delete'] == 'deleted' and self.owned_present(record):
                if time.monotonic() > deadline:
                    record['delete'] = 'deletion-timeout'
                    break
                time.sleep(1)

    def pod_state(self, name):
        state = json.loads(self.call('pod-'+name.removeprefix(self.run+'-'), ['get', 'pod', '-n', NS, name, '-o', 'json']))
        record = self.record(name)
        if record is not None and state['metadata']['uid'] != record['uid']:
            raise RuntimeError(f'{name} was replaced (UID {state["metadata"]["uid"]} != created {record["uid"]}); not ours')
        return state

    def wait_ready(self, stem, names, timeout):
        """Per-Pod readiness; returns {name: None (ready) | reason}. Never raises for not-ready Pods.

        Waits in chunks; a terminal waiting reason (e.g. ImagePullBackOff) persisting for
        TIMEOUTS['terminal_grace'] seconds ends that Pod's wait early and is recorded as the reason.
        """
        status = {}
        deadline = time.monotonic() + timeout
        for name in names:
            first_terminal = None
            while True:
                left = int(deadline - time.monotonic())
                if left <= 0:
                    status[name] = 'readiness timeout'
                    break
                chunk = max(5, min(20, left))
                try:
                    self.call(stem+'-ready', ['wait', 'pod', '-n', NS, name, '--for=condition=Ready', f'--timeout={chunk}s'],
                              timeout=chunk+15)
                    status[name] = None
                    break
                except Exception as error:
                    reasons = set()
                    try:
                        pod = self.pod_state(name)
                        reasons = {s.get('state', {}).get('waiting', {}).get('reason') for s in pod.get('status', {}).get('containerStatuses', [])}
                        if pod.get('status', {}).get('phase') in ('Failed', 'Succeeded'):
                            status[name] = 'terminated: '+pod['status']['phase']
                            break
                    except Exception:
                        pass  # Inspection failure: keep waiting until the deadline.
                    if reasons & TERMINAL_REASONS:
                        first_terminal = first_terminal or time.monotonic()
                        if time.monotonic() - first_terminal >= TIMEOUTS['terminal_grace']:
                            status[name] = 'terminal waiting reason: '+','.join(sorted(r for r in reasons if r))
                            break
                    else:
                        first_terminal = None
                    if time.monotonic() >= deadline:
                        status[name] = repr(error)
                        break
        for name, problem in status.items():
            if problem is not None:
                try:
                    pod = self.pod_state(name)
                    waiting = [s.get('state', {}) for s in pod.get('status', {}).get('containerStatuses', [])]
                    status[name] = dict(error=problem, phase=pod.get('status', {}).get('phase'), container_states=waiting,
                                        node=pod.get('spec', {}).get('nodeName'))
                except Exception as error:
                    status[name] = dict(error=problem, inspect_error=repr(error))
                try:
                    self.call('logs-'+name.removeprefix(self.run+'-'), ['logs', '-n', NS, name], timeout=30)
                except Exception:
                    pass  # Missing logs are expected for Pods that never started.
        return status

    def exec_lines(self, stem, pod, argv, timeout):
        """Run a runtime.py command in a Pod (no stdin); parse JSON lines, keeping partial output on failure."""
        try:
            raw = self.call(stem, ['exec', '-n', NS, pod, '--', 'python3', '/config/runtime.py', *argv], timeout=timeout)
            error = None
        except Exception as exc:
            raw, error = (self.out/f'{self.counter:04d}-{stem}.stdout').read_text(errors='replace'), repr(exc)
        rows = []
        for line in raw.splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        return rows, error

    def remote(self, stem, url, body, timeout):
        """HTTP call to another Pod issued from the controller Pod on the server; no exec into edge Pods.

        A list body (batch/drive) is split into sequential chunks under REMOTE_ARG_LIMIT bytes; one item
        over the limit is an explicit error, never a silently dropped call."""
        if not isinstance(body, list):
            return self.exec_lines(stem, self.controller, ['remote', url] + ([json.dumps(body)] if body is not None else []), timeout)
        chunks, current = [], []
        for item in body:
            if len(json.dumps(item)) > REMOTE_ARG_LIMIT:
                raise RuntimeError(f'{stem}: one call body exceeds {REMOTE_ARG_LIMIT} bytes')
            if current and len(json.dumps(current+[item])) > REMOTE_ARG_LIMIT:
                chunks.append(current)
                current = []
            current.append(item)
        chunks.append(current)
        rows, errors = [], []
        for index, chunk in enumerate(chunks):
            got, error = self.exec_lines(stem if len(chunks) == 1 else f'{stem}-part{index}', self.controller,
                                         ['remote', url, json.dumps(chunk)], timeout)
            rows += got
            if error:
                errors.append(error)
        return rows, '; '.join(errors) or None


@contextmanager
def phase(session, name, **meta):
    with baseline.phase(session, name, **meta):
        yield


# ---------------- phases ----------------

def protected_snapshot(session, label):
    def project(snapshot):
        return {p['metadata']['uid']: dict(name=p['metadata']['name'], namespace=p['metadata'].get('namespace'),
                node=p.get('spec', {}).get('nodeName'), phase=p.get('status', {}).get('phase'),
                ready=next((c['status'] for c in p.get('status', {}).get('conditions', []) if c['type'] == 'Ready'), None))
                for p in snapshot.get('items', []) if p['metadata'].get('labels', {}).get('run-id') != session.run}
    return baseline.projected_call(session, label, ['get', 'pods', '-A', '-o', 'json'], project)


def preflight(session):
    observed = topology.project_nodes(json.loads(session.call('nodes', ['get', 'nodes', '-o', 'json'])))
    frozen = topology.load(session.topology_path)
    report = topology.preflight(frozen, observed)
    session.save('topology-preflight.json', dict(report, observed=observed))
    if report['errors']:
        raise RuntimeError('Topology preflight failed: '+'; '.join(report['errors']))


def capacity(session):
    return Pilot.capacity(session)


def fits(free, kind):
    return free['cpu_free_m'] >= millicpu(RESOURCES[kind]['cpu']) and free['memory_free_mib'] >= mebibytes(RESOURCES[kind]['memory'])


def deploy_cache(session):
    code = {name: (HERE/name).read_text() for name in CODE}
    code['scenario.json'] = json.dumps(session.scenario)
    code['bundles.json'] = json.dumps({k: dict(sha256=v['sha256'], bytes=v['bytes']) for k, v in session.prepared['bundles'].items()})
    configmap = dict(apiVersion='v1', kind='ConfigMap', metadata=session.meta(session.run+'-code', 'code'), data=code)
    session.cache_url = None
    cache = session.pod(session.run+'-cache', 'cache', [session.design['server']])
    session.create('cache', [configmap, cache])
    status = session.wait_ready('cache', [session.run+'-cache'], TIMEOUTS['cache_ready'])
    if status[session.run+'-cache']:
        raise RuntimeError('Cache Pod not Ready: '+json.dumps(status))
    ip = session.pod_state(session.run+'-cache')['status']['podIP']
    session.cache_url = f'http://{ip}:{PORT}'
    # One stdin upload to the cache on the server node (not an edge); in-Pod init fetches bundles over HTTP.
    import tarfile
    import io
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name in sorted(session.prepared['bundles']):
            archive.add(session.prepared_dir/'bundles'/name, arcname=name)
    stream.seek(0)
    session.call('cache-upload', ['exec', '-i', '-n', NS, session.run+'-cache', '--', 'python3', '-c',
                 "import tarfile,sys;tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall('/work',filter='data')"],
                 stdin=stream.getvalue(), timeout=TIMEOUTS['upload'])
    controller = session.run+'-ctl'
    session.create('controller', [session.pod(controller, 'controller', [session.design['server']], role='controller')])
    status = session.wait_ready('controller', [controller], TIMEOUTS['cache_ready'])
    if status[controller]:
        raise RuntimeError('Controller Pod not Ready: '+json.dumps(status))
    session.controller = controller
    session.save('cache.json', dict(url=session.cache_url, controller=controller, bundles=session.design['prepared']['bundles'],
                                    scope='owned cache Pod on the helper server; deployment cost, not inference'))


def deploy_sources(session):
    pods, names = [], {}
    for sid, source in session.design['sources'].items():
        name = f"{session.run}-src-{sid}"
        role = 'video-source' if source['kind'] == 'video' else 'text-source'
        pods.append(session.pod(name, 'source', [source['node']], role=role, source=sid, labels={'source-id': sid}))
        names[sid] = name
    session.create('sources', pods)
    status = session.wait_ready('sources', list(names.values()), TIMEOUTS['source_ready'])
    evidence = {}
    for sid, name in names.items():
        state = session.pod_state(name)
        entry = dict(pod=name, uid=state['metadata']['uid'], node=state['spec'].get('nodeName'), ip=state['status'].get('podIP'),
                     expected_node=session.design['sources'][sid]['node'], ready_problem=status[name])
        if status[name] is None:
            rows, error = session.remote('evidence-'+sid, f"http://{entry['ip']}:{PORT}/evidence", None, 60)
            entry.update(evidence=rows[0] if rows else None, evidence_error=error)
        evidence[sid] = entry
    session.save('sources-evidence.json', evidence)
    bad = [sid for sid, e in evidence.items() if e['ready_problem'] or e['node'] != e['expected_node'] or not e.get('evidence')]
    if bad:
        raise RuntimeError('Source Pods failed: '+', '.join(bad))
    frozen_dir = session.out/'frozen-inputs'
    frozen_dir.mkdir()
    seqs = sorted(session.scenario['inputs']['calibration'] + session.scenario['inputs']['evaluation'])
    frozen = {}
    for sid, entry in evidence.items():
        if session.design['sources'][sid]['kind'] == 'video':
            rows, error = session.exec_lines('frames-'+sid, session.controller, ['frames', f"http://{entry['ip']}:{PORT}", json.dumps(seqs)], 120)
            if error or not rows:
                raise RuntimeError(f'Frozen frame download failed for {sid}: {error}')
            for seq, b64 in rows[0].items():
                png = base64.b64decode(b64)
                (frozen_dir/f'{sid}-{seq}.png').write_bytes(png)
                frozen.setdefault(sid, {})[seq] = dict(png_sha256=sha(png), png_bytes=len(png))
                if sha(png) != entry['evidence']['freeze']['frozen'][seq]['png_sha256']:
                    raise RuntimeError(f'Frozen frame hash mismatch {sid}/{seq}')
        else:
            texts = entry['evidence']['text_sha256']
            expected = session.prepared['inputs']['text'][sid]
            if any(texts[k] != expected[k]['sha256'] for k in expected):
                raise RuntimeError(f'Text source {sid} differs from the prepared sentences')
            frozen[sid] = {k: dict(sha256=v) for k, v in texts.items()}
    session.save('frozen-inputs.json', frozen)
    session.sources = evidence
    return evidence


def calibration_reference(session):
    # Loaded by path: real-topology-live (also on sys.path) has its own `reference` module.
    import importlib.util
    spec = importlib.util.spec_from_file_location('multi_edge_reference', HERE/'reference.py')
    reference_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference_module)
    result = reference_module.compute(session.prepared_dir, session.out, session.scenario['inputs']['calibration'],
                                       session.design['workflows'])
    session.save('calibration-reference.json', result)
    return result


def source_url(session, sid):
    return f"http://{session.sources[sid]['ip']}:{PORT}"


def qualify(session, free, reference):
    """Per task, one Pod per candidate (waves V, T, F); failures/not-fitting nodes recorded, never filled.

    Calibration seqs only. Every infer call is followed by an untimed GET of that request's full stage
    output; F is fed the CPU-reference V/T outputs so its own output is checkable against the reference.
    """
    sc = session.scenario
    cal = sc['inputs']['calibration']
    results = dict(rows=[], nodes={}, rtsp=[], outputs={})
    for task in TASKS:
        names, skipped = {}, {}
        for alias, node in session.nodes.items():
            if not fits(free[node], task):
                skipped[node] = 'insufficient free requests at capacity snapshot'
                continue
            names[node] = f'{session.run}-q-{task.lower()}-{alias}'
        pods = [session.pod(n, 'qualify', [node], role='worker', task=task, labels={'task': task}) for node, n in names.items()]
        with phase(session, 'qualification-'+task, nodes=len(names)):
            if pods:
                session.create('qualify-'+task, pods)
            status = session.wait_ready('qualify-'+task, list(names.values()), TIMEOUTS['worker_ready'])
            for node, name in names.items():
                entry = dict(pod=name, ready_problem=status[name])
                if status[name] is None:
                    state = session.pod_state(name)
                    entry.update(uid=state['metadata']['uid'], ip=state['status']['podIP'], node=state['spec']['nodeName'])
                results['nodes'].setdefault(task, {})[node] = entry
            for node, reason in skipped.items():
                results['nodes'].setdefault(task, {})[node] = dict(ready_problem=reason, skipped=True)
            for node, name in names.items():
                entry = results['nodes'][task][node]
                if entry['ready_problem']:
                    continue
                calls = []
                if task == 'V':
                    for sid in [s for s, v in session.design['sources'].items() if v['kind'] == 'video']:
                        for i, seq in enumerate([cal[0]] + cal):
                            calls += infer_calls(session, node, sid, seq, i)
                        calls.append(dict(tag=f'rtsp/{sid}', path='/rtsp-probe', timeout_s=60,
                                          body=dict(url=session.sources[sid]['evidence']['rtsp_url'], wanted=list(range(sc['stream']['loop_frames'])),
                                                    max_frames=3*sc['stream']['loop_frames'], timeout_s=30)))
                elif task == 'T':
                    for sid in [s for s, v in session.design['sources'].items() if v['kind'] == 'text']:
                        for i, seq in enumerate([cal[0]] + cal):
                            calls += infer_calls(session, node, sid, seq, i)
                else:
                    for flow in session.design['workflows']:
                        for i, seq in enumerate([cal[0]] + cal):
                            calls.append(dict(tag=f'{flow["id"]}/{seq}/{"warmup" if i == 0 else "cal"}', path='/fuse',
                                              body=dict(request_id=f'q-{node}-{flow["id"]}-{seq}-{i}',
                                                        detections=reference['V'][f"{flow['video']}/{seq}"]['output'],
                                                        text={k: reference['T'][f"{flow['text']}/{seq}"]['output'][k]
                                                              for k in ('similarities', 'text_top_class')})))
                rows, error = session.remote(f'qualify-{task}-{node}', f"http://{entry['ip']}:{PORT}/batch", calls, TIMEOUTS['batch'])
                by_tag = {r.get('tag'): r for r in rows}
                for call in calls:
                    row = by_tag.get(call['tag'], dict(tag=call['tag'], status='missing', error=error))
                    row.update(task=task, node=node, pod=name, pod_uid=entry['uid'])
                    if call['tag'].startswith('rtsp/'):
                        results['rtsp'].append(row)
                    elif call['tag'].endswith('/output'):
                        results['outputs'][call['tag'].removesuffix('/output')] = row
                    else:
                        results['rows'].append(row)
            session.delete_owned('qualify-'+task, list(names.values()))
    session.save('qualification.json', results)
    return results


def infer_calls(session, node, sid, seq, index):
    """One timed /infer call and one untimed GET of the full output of that request (same tag + '/output')."""
    tag, request_id = f'{sid}/{seq}/{"warmup" if index == 0 else "cal"}', f'q-{node}-{sid}-{seq}-{index}'
    return [dict(tag=tag, path='/infer', body=dict(seq=seq, source_url=source_url(session, sid), request_id=request_id)),
            dict(tag=f'{node}|{tag}/output', path=f'/outputs?request_id={request_id}')]


def candidate_quality(session, qual, reference):
    """Before any profile/plan freeze: provenance and numerical quality of every calibration row against the
    CPU reference on the same frozen inputs. Per workflow and task, a node qualifies only if all its rows
    (warmup + calibration) are ok; every failure keeps its row and reasons. All policies share these candidates."""
    prepared, sources = session.prepared, session.sources
    frozen = json.loads((session.out/'frozen-inputs.json').read_text())
    model_sha = dict(V=prepared['models']['V']['sha256'], T=prepared['models']['T']['sha256'], F=prepared['code']['models.py'])
    checked = []
    for row in qual['rows']:
        key, seq, _ = row['tag'].split('/')
        task, problems = row['task'], []
        reply = row.get('reply') or {}
        receipt = reply.get('receipt') or {}
        if row.get('status') != 'ok' or not receipt:
            problems.append(f"call {row.get('status')}: {row.get('error')}")
        else:
            if receipt.get('model_sha256') != model_sha[task]:
                problems.append(f'model hash {receipt.get("model_sha256")} != prepared {model_sha[task]}')
            if receipt.get('node') != row['node'] or receipt.get('pod_uid') != row['pod_uid']:
                problems.append('receipt node/Pod UID differs from the qualification Pod')
            if task in ('V', 'T'):
                src = receipt.get('source') or {}
                if (src.get('x-source-id'), src.get('x-source-node'), src.get('x-source-pod-uid')) != (key, sources[key]['node'], sources[key]['uid']):
                    problems.append(f'input source is not the frozen edge source {key}: {src}')
                if task == 'V' and receipt.get('input_payload_sha256') != frozen[key][seq]['png_sha256']:
                    problems.append('V input frame hash differs from the frozen frame')
                if task == 'T' and receipt.get('input_sha256') != prepared['inputs']['text'][key][seq]['sha256']:
                    problems.append('T input text hash differs from the prepared sentence')
                fetched = qual['outputs'].get(f"{row['node']}|{row['tag']}") or {}
                output = (fetched.get('reply') or {}).get(f"{receipt.get('request_id')}/{task}")
                expected = reference[task][f'{key}/{seq}']['output']
            else:
                output, expected = reply.get('output'), reference['F'][f'{key}/{seq}']
            problems += ['missing full output'] if output is None else models.compare(task, output, expected, prepared['class_names'])
        checked.append(dict(task=task, node=row['node'], tag=row['tag'], ok=not problems, problems=problems))
    workflows = {}
    for flow in session.design['workflows']:
        workflows[flow['id']] = {}
        for task, key in (('V', flow['video']), ('T', flow['text']), ('F', flow['id'])):
            valid, excluded = [], {}
            for node in session.nodes.values():
                rows = [c for c in checked if c['task'] == task and c['node'] == node and c['tag'].startswith(key+'/')]
                entry = qual['nodes'].get(task, {}).get(node, {})
                if entry.get('ready_problem'):
                    excluded[node] = dict(stage='deploy', reason=entry['ready_problem'])
                elif len(rows) != 1+len(session.scenario['inputs']['calibration']):
                    excluded[node] = dict(stage='calibration', reason=f'{len(rows)} calibration rows recorded')
                elif not all(c['ok'] for c in rows):
                    excluded[node] = dict(stage='quality/provenance', rows=[c for c in rows if not c['ok']])
                else:
                    valid.append(node)
            workflows[flow['id']][task] = dict(valid=valid, excluded=excluded)
    result = dict(scope='calibration seqs only, CPU reference on the same frozen inputs, before profile/plan freeze; '
                        'evaluation outputs are never used here', tolerance=models.TOLERANCE, rows=checked, workflows=workflows)
    session.save('candidate-quality.json', result)
    return result


def probe_transfers(session, qualified_f, quality):
    """Pull-timed payload GETs between F worker Pods (all ordered pairs incl. same node) and to destinations.

    Payload sizes are actual response bodies: V/T /infer (calibration rows) and F /run (measured here first)."""
    names = {node: f'{session.run}-p-{alias}' for alias, node in session.nodes.items() if node in qualified_f}
    pods = [session.pod(n, 'probe', [node], role='worker', task='F') for node, n in names.items()]
    rows = []
    with phase(session, 'transfer-probes', nodes=len(names)):
        session.create('probes', pods)
        status = session.wait_ready('probes', list(names.values()), TIMEOUTS['worker_ready'])
        ips = {node: session.pod_state(n)['status']['podIP'] for node, n in names.items() if status[n] is None}
        if not ips:
            raise RuntimeError('No F probe Pod Ready')
        run_response_sizes(session, quality, ips)
        sizes = dict(V=max(s['V'] for s in session.sizes.values()), T=max(s['T'] for s in session.sizes.values()),
                     F=max(s['F'] for s in session.sizes.values()))
        for node, name in names.items():
            if status[name] is not None:
                continue
            calls = [dict(tag=f'{kind}|{origin}|{node}', path='/probe', body=dict(url=f'http://{ip}:{PORT}/payload?bytes={sizes[kind]}',
                          repeats=PROBE_REPEATS)) for origin, ip in ips.items() for kind in ('V', 'T')]
            got, error = session.remote('probe-'+node, f'http://{ips[node]}:{PORT}/batch', calls, TIMEOUTS['batch'])
            rows += [dict(r, receiver=node, exec_error=error) for r in got]
        for flow in session.design['workflows']:
            calls = [dict(tag=f'F|{origin}|{flow["destination_node"]}', path='/probe', body=dict(url=f'http://{ip}:{PORT}/payload?bytes={sizes["F"]}',
                          repeats=PROBE_REPEATS)) for origin, ip in ips.items()]
            got, error = session.remote('probe-dest-'+flow['id'], source_url(session, flow['video'])+'/batch', calls, TIMEOUTS['batch'])
            rows += [dict(r, receiver=flow['destination_node'], exec_error=error) for r in got]
        session.delete_owned('probes', list(names.values()))
    session.save('transfer-probes.json', dict(sizes=sizes, rows=rows, scope='pull-timed HTTP GET of payload bytes at the receiver; '
                                              'monotonic, one host; same-node pairs measured pod-to-pod'))
    table = {}
    for row in rows:
        if row.get('status') != 'ok':
            continue
        oks = [r['ms'] for r in row['reply']['rows'] if r['status'] == 'ok']
        if len(oks) == PROBE_REPEATS:
            table[row['tag']] = statistics.median(oks)
    return table


def profiles(session, qual, transfers, free, quality):
    """Per workflow, a prepare.validate profile from calibration rows only; candidates = quality-qualified nodes."""
    out, report = {}, {}
    for flow in session.design['workflows']:
        compute, fetch, candidates, components = {}, {}, {}, {}
        for task, key in (('V', flow['video']), ('T', flow['text']), ('F', flow['id'])):
            ok_nodes = []
            for node in quality['workflows'][flow['id']][task]['valid']:
                rows = [r for r in qual['rows'] if r['task'] == task and r['node'] == node and r['tag'].endswith('/cal')
                        and r['tag'].startswith(key+'/')]
                receipts = [r['reply']['receipt'] for r in rows]
                ok_nodes.append(node)
                # V execution = PNG decode + inference per row (median of sums); fetch_ms is transfer, kept separate.
                compute.setdefault(task, {})[node] = statistics.median(r['compute_ms']+(r['decode_ms'] if task == 'V' else 0) for r in receipts)
                components.setdefault(task, {})[node] = dict(decode_ms=statistics.median(r.get('decode_ms', 0) for r in receipts),
                                                             compute_ms=statistics.median(r['compute_ms'] for r in receipts))
                if task in ('V', 'T'):
                    fetch.setdefault(task, {})[node] = statistics.median(r['fetch_ms'] for r in receipts)
            candidates[task] = ok_nodes
        missing = [f'{kind}|{a}|{b}' for kind in ('V', 'T') for a in candidates[kind] for b in candidates['F']
                   if f'{kind}|{a}|{b}' not in transfers]
        missing += [f'F|{a}|{flow["destination_node"]}' for a in candidates['F'] if f'F|{a}|{flow["destination_node"]}' not in transfers]
        if missing:
            # Pairs without a full measurement are removed, never imputed.
            for key in missing:
                kind, a, b = key.split('|')
                if kind in ('V', 'T') and a in candidates[kind]:
                    candidates[kind].remove(a)
                elif kind == 'F' and a in candidates['F']:
                    candidates['F'].remove(a)
        sizes = session.sizes[flow['id']]
        tasks = [dict(id='V', candidates=candidates['V'], compute_ms=compute['V'], image=IMAGE,
                      cpu_m=millicpu(RESOURCES['V']['cpu']), memory_mib=mebibytes(RESOURCES['V']['memory']),
                      inputs=[dict(source_node=flow['video_node'], bytes=sizes['frame'],
                                   transfer_ms={flow['video_node']+'|'+n: fetch['V'][n] for n in candidates['V']})]),
                 dict(id='T', candidates=candidates['T'], compute_ms=compute['T'], image=IMAGE,
                      cpu_m=millicpu(RESOURCES['T']['cpu']), memory_mib=mebibytes(RESOURCES['T']['memory']),
                      inputs=[dict(source_node=flow['text_node'], bytes=sizes['text'],
                                   transfer_ms={flow['text_node']+'|'+n: fetch['T'][n] for n in candidates['T']})]),
                 dict(id='F', candidates=candidates['F'], compute_ms=compute['F'], image=IMAGE,
                      cpu_m=millicpu(RESOURCES['F']['cpu']), memory_mib=mebibytes(RESOURCES['F']['memory']),
                      inputs=[dict(task='V', bytes=sizes['V'], transfer_ms={f'{a}|{b}': transfers[f'V|{a}|{b}'] for a in candidates['V'] for b in candidates['F']}),
                              dict(task='T', bytes=sizes['T'], transfer_ms={f'{a}|{b}': transfers[f'T|{a}|{b}'] for a in candidates['T'] for b in candidates['F']})])]
        profile = dict(provenance='measured', evidence_id=f'{session.run}/qualification+transfer-probes', namespace=NS,
                       nodes={n: free[n] for n in session.nodes.values()}, tasks=tasks,
                       outputs=[dict(task='F', destination_node=flow['destination_node'],
                                     transfer_ms={f'{a}|{flow["destination_node"]}': transfers[f'F|{a}|{flow["destination_node"]}'] for a in candidates['F']})],
                       workflow=flow['id'], representation='frozen decoded PNG frame / UTF-8 sentence')
        planner.validate(profile)
        out[flow['id']] = profile
        report[flow['id']] = dict(candidates=candidates, removed_for_missing_transfer=missing, cost_components=components,
                                  cost_rule='V execution = median(decode_ms + compute_ms) per row; T/F = median(compute_ms); fetch_ms is transfer',
                                  excluded_by_quality={t: sorted(quality['workflows'][flow['id']][t]['excluded']) for t in TASKS})
    return out, report


def plan(session, profile, flow):
    return plan_module.plan_all(profile, flow['video_node'], flow['text_node'], flow['destination_node'], session.design['server'])


def observe_scheduler(session, flow, profile):
    cands = {t['id']: t['candidates'] for t in profile['tasks']}
    names = {t: f"{session.run}-s-{flow['id']}-{t.lower()}" for t in TASKS}
    pods = [session.pod(names[t], 'scheduler', cands[t], role='worker', task=t, labels={'task': t}) for t in TASKS]
    session.create('scheduler-'+flow['id'], pods)
    status = session.wait_ready('scheduler-'+flow['id'], list(names.values()), TIMEOUTS['worker_ready'])
    record = {}
    for t, name in names.items():
        state = session.pod_state(name)
        record[t] = dict(pod=name, uid=state['metadata']['uid'], node=state['spec'].get('nodeName'), ready_problem=status[name],
                         scheduler=state['spec'].get('schedulerName'),
                         scheduled=next((c for c in state['status'].get('conditions', []) if c['type'] == 'PodScheduled'), None))
    session.delete_owned('scheduler-'+flow['id'], list(names.values()))
    placement = {t: r['node'] for t, r in record.items()}
    ok = all(r['node'] for r in record.values()) and all(r['ready_problem'] is None for r in record.values())
    session.save(f"scheduler-{flow['id']}.json", dict(workflow=flow['id'], candidates=cands, pods=record, placement=placement,
                 scope='actual default-scheduler selection among qualified candidates; re-pinned for evaluation'))
    if not ok:
        return dict(status='failed', error='scheduler observation Pods not all scheduled/Ready', placement=placement)
    # The observed placement actually ran, so it is evaluated even if the frozen request-sum snapshot disagrees.
    return dict(status='ok', placement=placement, profile_feasible=planner.feasible(profile, placement),
                predicted_ms=planner.cost(profile, placement, 'topology_profile'), source=f"scheduler-{flow['id']}.json")


def schedule(plans, seed=SEED):
    """Unique placements per workflow in seeded order; policies sharing a placement share a job."""
    rng = random.Random(seed)
    jobs = []
    for flow_id in sorted(plans):
        groups = {}
        for policy, p in plans[flow_id].items():
            if p['status'] == 'ok':
                groups.setdefault(tuple(p['placement'][t] for t in TASKS), []).append(policy)
        items = sorted(groups.items())
        rng.shuffle(items)
        jobs += [dict(workflow=flow_id, placement=dict(zip(TASKS, key)), policies=sorted(policies)) for key, policies in items]
    for index, job in enumerate(jobs):
        job['label'] = f"j{index:02d}-{job['workflow']}"
    if len(jobs)*EVALUATION > MAX_EVAL_WORKFLOWS:
        raise RuntimeError('Evaluation cap exceeded')
    return jobs


def evaluate(session, job, flow, attempts):
    names = {t: f"{session.run}-{job['label']}-{t.lower()}" for t in TASKS}
    pods = [session.pod(names[t], 'evaluation', [job['placement'][t]], role='worker', task=t, labels={'task': t, 'job': job['label']}) for t in TASKS]
    sc = session.scenario
    session.create(job['label'], pods)
    status = session.wait_ready(job['label'], list(names.values()), TIMEOUTS['worker_ready'])
    trajectory = [(sc['inputs']['calibration'][0], True)]*WARMUP + [(s, False) for s in sc['inputs']['evaluation']][:EVALUATION]
    rows = []
    if any(status.values()):
        rows = [dict(status='deploy-failed', error=json.dumps(status), seq=s, warmup=w) for s, w in trajectory]
        pods_state = {}
    else:
        pods_state = {t: session.pod_state(n) for t, n in names.items()}
        ips = {t: f"http://{s['status']['podIP']}:{PORT}" for t, s in pods_state.items()}
        controls = [dict(request_id=f"{job['label']}-{i}", seq=s, warmup=w, F=ips['F'], V=ips['V'], T=ips['T'],
                         video_source=source_url(session, flow['video']), text_source=source_url(session, flow['text']))
                    for i, (s, w) in enumerate(trajectory)]
        # Driven from the destination (video source) Pod via its /drive endpoint; E2E measured there.
        got, error = session.remote('drive-'+job['label'], source_url(session, flow['video'])+'/drive', controls, TIMEOUTS['drive'])
        by_id = {r.get('request_id'): r for r in got}
        for control in controls:
            rows.append(by_id.get(control['request_id'], dict(status='timeout' if error and 'Timeout' in error else 'missing',
                                                              error=error, request_id=control['request_id'], seq=control['seq'])))
            rows[-1]['warmup'] = control['warmup']
    for row in rows:
        row.update(job=job['label'], workflow=flow['id'], policies=job['policies'], placement=job['placement'],
                   pods={t: dict(name=s['metadata']['name'], uid=s['metadata']['uid'], node=s['spec']['nodeName']) for t, s in pods_state.items()})
        attempts.write(json.dumps(row)+'\n')
        attempts.flush()
    session.delete_owned(job['label'], list(names.values()))
    return rows


def payload_sizes(session, qual, quality):
    """Median measured bytes per edge kind over quality-valid calibration rows (planning bytes only)."""
    sizes = {}
    ok = {(c['task'], c['node'], c['tag']) for c in quality['rows'] if c['ok']}
    for flow in session.design['workflows']:
        def rows(task, key):
            return [r for r in qual['rows'] if r['task'] == task and r['tag'].startswith(key+'/') and (task, r['node'], r['tag']) in ok]
        v, t, f = rows('V', flow['video']), rows('T', flow['text']), rows('F', flow['id'])
        if not (v and t and f):
            raise RuntimeError(f"No quality-valid calibration rows for every task of {flow['id']}")
        # V/T: the /infer response body F actually receives (output + receipt envelope); F is set from real /run bodies later.
        sizes[flow['id']] = dict(frame=int(statistics.median(r['reply']['receipt']['input_bytes'] for r in v)),
                                 text=int(statistics.median(r['reply']['receipt']['input_bytes'] for r in t)),
                                 V=int(statistics.median(r['reply_bytes'] for r in v)), T=int(statistics.median(r['reply_bytes'] for r in t)),
                                 categories=dict(V_task_bytes=int(statistics.median(len(json.dumps(r['reply']['output'])) for r in v)),
                                                 T_task_bytes=int(statistics.median(len(json.dumps(r['reply']['output'])) for r in t))))
    return sizes


def run_response_sizes(session, quality, f_ips):
    """Actual /run response bodies (fusion result + V/T/F receipts, as returned to the destination) per workflow.

    One V and one T worker on quality-valid nodes (server preferred) serve /infer for F probe Pods;
    calibration seqs only. Body bytes exclude HTTP headers; task-data and receipt-envelope bytes are recorded separately."""
    server, cal = session.design['server'], session.scenario['inputs']['calibration']
    pick = {}
    for task in ('V', 'T'):
        valid = set.intersection(*(set(w[task]['valid']) for w in quality['workflows'].values()))
        if not valid:
            raise RuntimeError(f'No node is quality-valid for {task} in every workflow; cannot measure /run bodies')
        pick[task] = server if server in valid else sorted(valid)[0]
    names = {t: f'{session.run}-p-{t.lower()}' for t in pick}
    session.create('run-size', [session.pod(names[t], 'probe', [pick[t]], role='worker', task=t, labels={'task': t}) for t in pick])
    status = session.wait_ready('run-size', list(names.values()), TIMEOUTS['worker_ready'])
    if any(status.values()):
        session.delete_owned('run-size', list(names.values()))
        raise RuntimeError('Run-size V/T workers not Ready: '+json.dumps(status))
    ips = {t: f"http://{session.pod_state(n)['status']['podIP']}:{PORT}" for t, n in names.items()}
    f_node = server if server in f_ips else sorted(f_ips)[0]
    rows = []
    for flow in session.design['workflows']:
        calls = [dict(tag=f"{flow['id']}/{seq}/{'warmup' if i == 0 else 'cal'}", path='/run',
                      body=dict(request_id=f"rs-{flow['id']}-{seq}-{i}", seq=seq,
                                video=dict(endpoint=ips['V'], source_url=source_url(session, flow['video'])),
                                text=dict(endpoint=ips['T'], source_url=source_url(session, flow['text']))))
                 for i, seq in enumerate([cal[0]] + cal)]
        got, error = session.remote('run-size-'+flow['id'], f'http://{f_ips[f_node]}:{PORT}/batch', calls, TIMEOUTS['batch'])
        rows += [dict(r, workflow=flow['id'], exec_error=error) for r in got]
    session.delete_owned('run-size', list(names.values()))
    for flow in session.design['workflows']:
        ok = [r for r in rows if r['workflow'] == flow['id'] and r.get('status') == 'ok' and 'result' in (r.get('reply') or {})
              and r['tag'].endswith('/cal')]
        if len(ok) != len(cal):
            raise RuntimeError(f"/run size calibration incomplete for {flow['id']}: {len(ok)}/{len(cal)}")
        session.sizes[flow['id']]['F'] = int(statistics.median(r['reply_bytes'] for r in ok))
        session.sizes[flow['id']]['categories'].update(
            F_task_bytes=int(statistics.median(len(json.dumps(r['reply']['result'])) for r in ok)),
            F_receipt_envelope_bytes=int(statistics.median(r['reply_bytes'] - len(json.dumps(r['reply']['result'])) for r in ok)))
    session.save('run-response-sizes.json', dict(nodes=dict(pick, F=f_node), rows=rows, sizes=session.sizes,
        scope='HTTP response body bytes only (headers excluded). V/T: /infer body to F (output + receipt); F: /run body to the '
              'destination (result + V/T/F receipts). *_task_bytes = JSON of the semantic output alone; the rest is instrumentation.'))


def cleanup(session):
    """Delete only objects this run created, by UID precondition; same-name foreign/replaced objects are preserved."""
    errors = []
    try:
        session.call('events', ['get', 'events', '-n', NS, '-o', 'json'], timeout=60)
    except Exception as error:
        errors.append('events: '+repr(error))
    for record in session.resources:
        if record['kind'] == 'Pod' and record.get('delete') not in ('deleted', 'already-absent'):
            try:
                if session.owned_present(record):
                    session.call('logs-final-'+record['name'].removeprefix(session.run+'-'), ['logs', '-n', NS, record['name']], timeout=30)
            except Exception:
                pass  # Logs are diagnostics only; deletion below still runs.
    try:
        session.delete_owned('cleanup', [r['name'] for r in session.resources])
    except Exception as error:
        errors.append('delete: '+repr(error))
    remaining, foreign = None, None
    try:
        left = json.loads(session.call('remaining', ['get', 'pods,configmaps', '-n', NS, '-l', 'run-id='+session.run, '-o', 'json']))
        ours = {r['uid'] for r in session.resources}
        labelled = {item['metadata']['uid'] for item in left['items']}
        foreign = [dict(kind=item.get('kind'), name=item['metadata']['name'], uid=item['metadata']['uid'])
                   for item in left['items'] if item['metadata']['uid'] not in ours]
        # Ours = created UIDs still listed under the run label, plus created UIDs still present by name (labels edited).
        still = {r['uid'] for r in session.resources if r.get('delete') not in ('deleted', 'already-absent') and session.owned_present(r)}
        remaining = len((labelled & ours) | still)
    except Exception as error:
        errors.append('remaining: '+repr(error))
    preserved = [dict(name=r['name'], kind=r['kind'], created_uid=r['uid'], reason=r['delete'])
                 for r in session.resources if r.get('delete') == 'refused-uid-mismatch']
    unresolved = []
    for item in session.unresolved:
        # Creation outcome unknown (no UID returned): report what exists now, never delete or adopt it.
        try:
            obj = json.loads(session.call('unresolved-'+item['name'].removeprefix(session.run+'-'),
                                          ['get', 'pod' if item['kind'] == 'Pod' else 'configmap', '-n', NS, item['name'], '-o', 'json']))
            unresolved.append(dict(item, present=True, observed_uid=obj['metadata']['uid'],
                                   observed_labels=obj['metadata'].get('labels', {})))
        except Exception as error:
            stderr = (session.out/f"{session.counter:04d}-unresolved-{item['name'].removeprefix(session.run+'-')}.stderr").read_text(errors='replace')
            unresolved.append(dict(item, present=False if 'NotFound' in stderr else None, error=None if 'NotFound' in stderr else repr(error)))
    open_items = [u for u in unresolved if u['present'] is not False]
    try:
        after = protected_snapshot(session, 'protected-after')
        before = json.loads((session.out/'protected-before.json').read_text())
        changes = [dict(uid=uid, before=b, after=after.get(uid)) for uid, b in before.items()
                   if uid not in after or (b['phase'], b['ready']) != (after[uid]['phase'], after[uid]['ready'])]
        session.save('protected-diff.json', dict(protected=len(before), changes=changes,
                     note='observations only; no causal claim about this experiment'))
    except Exception as error:
        errors.append('protected-after: '+repr(error))
    # Diagnostic-only failures (events/logs/protected snapshot) do not decide ownership cleanup; delete/listing failures do.
    verified = remaining == 0 and not open_items and not any(e.startswith(('delete', 'remaining')) for e in errors)
    session.save('cleanup.json', dict(remaining_owned_pods_and_configmaps=remaining, cleanup_verified=verified, created=session.resources,
                                      unresolved_creation=unresolved, preserved_not_ours=preserved, foreign_with_run_label=foreign,
                                      errors=errors, rule='delete only by API UID precondition of objects this run created; '
                                      'objects whose creation outcome is unknown are reported, never deleted or counted as cleaned'))
    return (remaining if verified else None), errors


def refuse_reused_run(session):
    """No object may already carry this run label; the run never adopts or deletes them."""
    left = json.loads(session.call('preexisting', ['get', 'pods,configmaps', '-n', NS, '-l', 'run-id='+session.run, '-o', 'json']))
    if left['items']:
        raise RuntimeError('Objects already carry run-id=%s: %s' % (session.run, [i['metadata']['name'] for i in left['items']]))


def collect(session):
    design = session.design
    session.save('design.json', design)
    session.save('prepared-ref.json', dict(path=str(session.prepared_dir), sha256=sha((session.prepared_dir/'prepared.json').read_bytes()),
                                           code=session.prepared['code']))
    session.save('protected-before.json', protected_snapshot(session, 'protected-before'))
    jobs, attempts_path = [], session.out/'attempts.jsonl'
    try:
        with phase(session, 'preflight'):
            refuse_reused_run(session)
            preflight(session)
        with phase(session, 'scheduler-provenance'):
            session.save('scheduler-provenance.json', baseline.projected_call(session, 'scheduler-snapshot',
                ['get', 'pods', '-n', 'kube-system', '-l', 'component=kube-scheduler', '-o', 'json'], baseline.scheduler_provenance))
        with phase(session, 'cache'):
            deploy_cache(session)
        with phase(session, 'sources'):
            deploy_sources(session)
        with phase(session, 'calibration-reference'):
            # Calibration seqs only; the final evaluation audit (reference.py) is separate.
            reference = calibration_reference(session)
        with phase(session, 'capacity-qualification'):
            free = capacity(session)
            session.save('capacity-qualification.json', free)
        qual = qualify(session, free, reference)
        with phase(session, 'candidate-quality'):
            quality = candidate_quality(session, qual, reference)
        session.sizes = payload_sizes(session, qual, quality)
        qualified_f = sorted({n for flow in quality['workflows'].values() for n in flow['F']['valid']})
        transfers = probe_transfers(session, qualified_f, quality)
        with phase(session, 'capacity-planning'):
            free = capacity(session)
            session.save('capacity-planning.json', free)
        with phase(session, 'planning'):
            frozen_profiles, report = profiles(session, qual, transfers, free, quality)
            session.save('profiles-frozen.json', dict(profiles=frozen_profiles, candidates=report, payload_sizes=session.sizes))
            plans = {flow['id']: plan(session, frozen_profiles[flow['id']], flow) for flow in design['workflows']}
        for flow in design['workflows']:
            with phase(session, 'scheduler-'+flow['id']):
                plans[flow['id']]['kube-default'] = observe_scheduler(session, flow, frozen_profiles[flow['id']])
        session.save('plans-frozen.json', plans)
        jobs = schedule(plans)
        session.save('schedule-frozen.json', dict(seed=SEED, jobs=jobs))
        flows = {f['id']: f for f in design['workflows']}
        with attempts_path.open('x') as attempts:
            for job in jobs:
                with phase(session, 'evaluation-'+job['label'], policies=job['policies']):
                    evaluate(session, job, flows[job['workflow']], attempts)
        mapping = {f: {p: j['label'] for j in jobs if j['workflow'] == f for p in j['policies']} for f in flows}
        session.save('policy-observation-map.json', mapping)
        session.save('completed.json', dict(status='collection finished; audit with verify.py before any claim', jobs=len(jobs)))
    except BaseException as error:
        session.save('failure.json', dict(error=repr(error), traceback=traceback.format_exc(), scheduled_jobs=len(jobs)))
        raise
    finally:
        with phase(session, 'cleanup'):
            remaining, errors = cleanup(session)
        print('evidence', session.out, 'remaining', remaining, flush=True)
    if remaining != 0:
        raise RuntimeError('Owned resource cleanup not verified')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true', help='print the frozen design; no kubectl call')
    mode.add_argument('--execute', action='store_true', help='run the live collection (cluster mutation)')
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--topology', type=Path, default=DEFAULT_TOPOLOGY)
    parser.add_argument('--scenario', type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument('--inventory', type=Path, help='dry-run only: offline preflight against a saved node inventory')
    parser.add_argument('--output-root', type=Path, default=ROOT/'artifacts'/EXPERIMENT)
    parser.add_argument('--run-id', help='default mes-<utc timestamp>; the run directory must not exist')
    parser.add_argument('--environment', default='cluster', help="recorded label, e.g. 'local-fake-kubectl' for offline tests")
    args = parser.parse_args(argv)
    if args.inventory and not args.dry_run:
        parser.error('--inventory is dry-run only')
    try:
        design, prepared = load_design(args.prepared, args.topology, args.scenario)
    except ValueError as error:
        parser.exit(2, f'design rejected: {error}\n')
    design['topology']['aliases'] = topology.aliases(topology.load(args.topology))
    design['environment'] = args.environment
    if args.dry_run:
        output = dict(design, dry_run=True, cluster_calls=0)
        if args.inventory:
            raw = json.loads(args.inventory.read_text())
            observed = raw['nodes'] if raw['nodes'] and 'ready' in raw['nodes'][0] else topology.project_nodes(dict(items=[
                dict(metadata=dict(name=n['name'], labels={'kubernetes.io/arch': n.get('nodeInfo', {}).get('architecture')}),
                     status=dict(conditions=n.get('conditions', []), nodeInfo=n.get('nodeInfo', {})),
                     spec=dict(taints=n.get('taints') or [])) for n in raw['nodes']]))
            output['preflight'] = topology.preflight(topology.load(args.topology), observed)
        print(json.dumps(output, indent=1))
        return 1 if output.get('preflight', {}).get('errors') else 0
    run = args.run_id or datetime.now(timezone.utc).strftime('mes-%Y%m%dt%H%M%Sz')
    session = Session(args.output_root/run, design, args.prepared)
    session.prepared, session.scenario, session.topology_path = prepared, prepared['scenario']['content'], args.topology
    collect(session)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
