"""Supervised live collection of the Mobilint DAG pilot (only with a release file bound to the frozen protocol and code).

  python3 live.py --prepared <prepare --out dir> --frozen <frozen.json> --runs <dir> --release <release.json> --stage probe|full

Control boundary (reuses multi-edge-sensors collect.Session: create-only objects with recorded creation UIDs, UID-
precondition deletes of exactly those objects, every kubectl call saved with event/stdout/stderr):
- no exec/attach/logs/cp/port-forward to the edge Pod (refused or skipped before kubectl is invoked);
- server-Pod streams only to this run's own Pods after a fresh GET confirms the creation UID and node (cache upload);
- preflight refuses when any non-terminal Pod already holds mobilint.com/npu on the server (never preempts) or the
  chosen node lacks free requests; protected Pods are snapshotted before and after;
- data never flows from the collector: requests start at the edge Pod; the collector sends control HTTP only.
Stages: probe = deploy, MXQ load + one Mobilint request (+ one CPU request), cleanup. full = probe gate, then the frozen
schedule (warmup, 200 labeled pairs, COCO parity pairs), cleanup. Cleanup always runs (finally).
"""
import argparse
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import protocol as P  # noqa: E402
import prepare  # noqa: E402
sys.path.insert(0, str(HERE.parent/'multi-edge-sensors'))
import collect  # noqa: E402
sys.path.insert(0, str(HERE.parent/'quality-network-baselines'))
import bounded_http  # noqa: E402

STREAM_VERBS = {'exec', 'attach', 'logs', 'cp', 'port-forward'}
KEYS = ('cache', 'mobilint', 'cpu-fp32', 'edge')
CODE_FILES = ('worker.py', 'protocol.py')


def millicpu(raw):
    return collect.millicpu(raw)


def mebibytes(raw):
    return collect.mebibytes(raw)


def pod(run, key, node, frozen_sha, cache_url=None):
    """Pure manifest builder. Device resource only on the Mobilint Pod; no privileged/host namespaces/host paths."""
    r = P.RESOURCES[key]
    labels = dict(experiment=P.EXPERIMENT, **{'run-id': run}, component=key)
    limits = dict(cpu=r['cpu'], memory=r['memory_limit'], **{'ephemeral-storage': '2Gi'})
    requests = dict(cpu=r['cpu'], memory=r['memory'], **{'ephemeral-storage': r['disk']})
    if r.get('npu'):
        limits['mobilint.com/npu'] = requests['mobilint.com/npu'] = str(r['npu'])
    env = [dict(name='NODE_NAME', valueFrom=dict(fieldRef=dict(fieldPath='spec.nodeName'))),
           dict(name='POD_UID', valueFrom=dict(fieldRef=dict(fieldPath='metadata.uid'))),
           dict(name='PYTHONDONTWRITEBYTECODE', value='1'), dict(name='OMP_NUM_THREADS', value='1'),
           dict(name='OPENBLAS_NUM_THREADS', value='1'), dict(name='TMPDIR', value='/work')]
    security = dict(allowPrivilegeEscalation=False, capabilities=dict(drop=['ALL']))
    if key == 'cache':
        command = ['python3', '-u', '-m', 'http.server', str(P.PORT), '--directory', '/work']
    else:
        command = ['python3', '-u', '/config/worker.py']
        env += [dict(name='ROLE', value=key), dict(name='CACHE_URL', value=cache_url), dict(name='FROZEN_SHA256', value=frozen_sha)]
    container = dict(name='worker', image=P.MOBILINT_IMAGE if key == 'mobilint' else P.PY_IMAGE, imagePullPolicy='IfNotPresent',
                     command=command, env=env, resources=dict(requests=requests, limits=limits), securityContext=security,
                     volumeMounts=[dict(name='work', mountPath='/work')],
                     readinessProbe=dict(httpGet=dict(path='/health' if key != 'cache' else '/', port=P.PORT),
                                         initialDelaySeconds=1, periodSeconds=3, timeoutSeconds=3))
    spec = dict(automountServiceAccountToken=False, restartPolicy='Never', enableServiceLinks=False,
                activeDeadlineSeconds=P.DEADLINES['active_deadline_s'], terminationGracePeriodSeconds=3,
                securityContext=dict(seccompProfile=dict(type='RuntimeDefault')),
                affinity=dict(nodeAffinity=dict(requiredDuringSchedulingIgnoredDuringExecution=dict(nodeSelectorTerms=[
                    dict(matchFields=[dict(key='metadata.name', operator='In', values=[node])])]))),
                containers=[container], volumes=[dict(name='work', emptyDir=dict(sizeLimit='2Gi'))])
    if key != 'mobilint':
        # The Mobilint Pod keeps the image's default user, as in the earlier successful qbruntime Jobs (device node access).
        spec['securityContext'].update(runAsUser=65532, runAsGroup=65532, runAsNonRoot=True, fsGroup=65532)
        security['readOnlyRootFilesystem'] = True
    if key != 'cache':
        container['volumeMounts'].append(dict(name='config', mountPath='/config', readOnly=True))
        spec['volumes'].append(dict(name='config', configMap=dict(name=run+'-code')))
    return dict(apiVersion='v1', kind='Pod', metadata=dict(name=f'{run}-{key}', namespace=P.NS, labels=labels), spec=spec)


def config_map(run):
    data = {n: (HERE/n).read_text() for n in CODE_FILES}
    data.update({n: Path(p).read_text() for n, p in prepare.REUSED.items()})
    return dict(apiVersion='v1', kind='ConfigMap', data=data,
                metadata=dict(name=run+'-code', namespace=P.NS, labels=dict(experiment=P.EXPERIMENT, **{'run-id': run}, component='code')))


def dependency_hashes():
    """sha256 of every repository module a fresh collector process loads (the executed dependency closure, e.g.
    collect.py, run_baseline_comparison.py, run_smoke.py) plus the Pod-shipped files. A clean interpreter is used so the
    closure does not depend on what the caller imported."""
    root = HERE.parents[1]
    probe = ("import json,sys;sys.path.insert(0,sys.argv[1]);import live;"
             "print(json.dumps(sorted({m.__file__ for m in list(sys.modules.values()) if getattr(m,'__file__',None)})))")
    loaded = json.loads(subprocess.run([sys.executable, '-c', probe, str(HERE)], capture_output=True, text=True, check=True,
                                       timeout=120, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}).stdout)
    files = {Path(f).resolve() for f in loaded if f.endswith('.py') and Path(f).resolve().is_relative_to(root/'experiments')}
    files |= {(HERE/name).resolve() for name in CODE_FILES} | {Path(path).resolve() for path in prepare.REUSED.values()}
    return {str(f.relative_to(root)): prepare.file_sha(f) for f in sorted(files)}


def check_release(release, frozen_sha, code):
    problems = []
    if release.get('frozen_sha256') != frozen_sha:
        problems.append('frozen protocol hash differs from the release')
    if release.get('code') != code:
        problems.append('shipped/reused code hashes differ from the release')
    if not release.get('approved_by') or not release.get('stages'):
        problems.append('release needs approved_by and stages')
    return problems


def npu_holders(pods, node=P.SERVER):
    out = []
    for p in pods:
        if p.get('spec', {}).get('nodeName') != node or p.get('status', {}).get('phase') in ('Succeeded', 'Failed'):
            continue
        for c in p['spec'].get('containers', []) + p['spec'].get('initContainers', []):
            res = c.get('resources', {})
            if any('mobilint.com/npu' in res.get(k, {}) for k in ('requests', 'limits')):
                out.append(dict(namespace=p['metadata'].get('namespace'), name=p['metadata']['name'], uid=p['metadata'].get('uid')))
    return out


def free_capacity(node_obj, pods):
    name = node_obj['metadata']['name']
    alloc = node_obj['status']['allocatable']
    cpu, mem = millicpu(alloc['cpu']), mebibytes(alloc['memory'])
    for p in pods:
        if p.get('spec', {}).get('nodeName') != name or p.get('status', {}).get('phase') in ('Succeeded', 'Failed'):
            continue
        for c in p['spec'].get('containers', []):
            req = c.get('resources', {}).get('requests', {})
            cpu -= millicpu(req.get('cpu', '0'))
            mem -= mebibytes(req.get('memory', '0'))
    return dict(cpu_free_m=cpu, memory_free_mib=mem)


def fits(free, keys):
    return (free['cpu_free_m'] >= sum(millicpu(P.RESOURCES[k]['cpu']) for k in keys)
            and free['memory_free_mib'] >= sum(mebibytes(P.RESOURCES[k]['memory']) for k in keys))


def ready(node_obj):
    return any(c['type'] == 'Ready' and c['status'] == 'True' for c in node_obj['status'].get('conditions', []))


def choose(nodes, pods):
    """-> (edge node, report). Raises with evidence when the server or every edge candidate is unusable."""
    by_name = {n['metadata']['name']: n for n in nodes}
    report = dict(npu_holders=npu_holders(pods), candidates={})
    server = by_name.get(P.SERVER)
    if server is None or not ready(server):
        raise RuntimeError(f'{P.SERVER} missing or not Ready')
    report['server_free'] = free_capacity(server, pods)
    report['server_npu_allocatable'] = server['status']['allocatable'].get('mobilint.com/npu')
    if report['npu_holders']:
        raise RuntimeError('Mobilint NPU already held (no preemption): '+json.dumps(report['npu_holders']))
    if report['server_npu_allocatable'] != '1':
        raise RuntimeError(f"mobilint.com/npu allocatable {report['server_npu_allocatable']!r} != '1'")
    if not fits(report['server_free'], ('cache', 'mobilint', 'cpu-fp32')):
        raise RuntimeError('server lacks free requests: '+json.dumps(report['server_free']))
    for name in P.EDGE_CANDIDATES:
        n = by_name.get(name)
        info = dict(present=n is not None, ready=bool(n) and ready(n), taints=(n or {}).get('spec', {}).get('taints'))
        if n is not None:
            info['free'] = free_capacity(n, pods)
        report['candidates'][name] = info
        if info['ready'] and not info['taints'] and fits(info['free'], ('edge',)):
            report['edge'] = name
            return name, report
    raise RuntimeError('no edge candidate is Ready, untainted and fits: '+json.dumps(report['candidates']))


class Session(collect.Session):
    """collect.Session with this experiment's labels and a fail-closed stream guard."""

    def __init__(self, out_root, run_id):
        if not run_id.startswith('mdp-') or len(run_id) > 40 or not run_id.replace('-', '').isalnum() or run_id.lower() != run_id:
            raise ValueError('run-id must be mdp-[a-z0-9-], at most 40 characters')
        self.run, self.out = run_id, Path(out_root)/run_id
        self.out.mkdir(parents=True, exist_ok=False)
        self.counter, self.resources, self.unresolved = 0, [], []
        self.expected = {}

    def owned_state(self, key, stem):
        name = f'{self.run}-{key}'
        obj = json.loads(collect.Session.call(self, stem, ['get', 'pod', '-n', P.NS, name, '-o', 'json']))
        record = self.record(name)
        if record is None or obj['metadata']['uid'] != record['uid']:
            raise RuntimeError(f'{key}: Pod UID is not the UID this run created')
        if obj.get('spec', {}).get('nodeName') != self.expected[key][0]:
            raise RuntimeError(f"{key} runs on {obj.get('spec', {}).get('nodeName')}, expected {self.expected[key][0]}")
        return obj

    def call(self, stem, args, stdin=None, timeout=60):
        if args and args[0] in STREAM_VERBS:
            name = next((a for a in args[1:] if a.startswith(self.run+'-')), None)
            key = name.removeprefix(self.run+'-') if name else None
            reason = None
            if key not in self.expected:
                reason = f'stream target {name!r} is not an owned Pod of this run'
            elif self.expected[key][1]:
                reason = 'kubelet stream to an edge-node Pod is outside the control boundary'
            else:
                try:
                    self.owned_state(key, 'stream-owner-'+stem)
                except Exception as error:
                    reason = f'fresh ownership check failed: {error!r}'
            if reason:
                self.counter += 1
                skipped = args[0] == 'logs'
                self.save(f'{self.counter:04d}-{stem}.{"skipped" if skipped else "refused"}.json',
                          dict(at=datetime.now(timezone.utc).isoformat(), verb=args[0], args=args[:8], reason=reason+'; kubectl not invoked'))
                if skipped:
                    return ''
                raise RuntimeError(f'refused: {args[0]} ({reason})')
        return collect.Session.call(self, stem, args, stdin, timeout)

    def create(self, stem, items):
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
            m = obj['metadata']
            labels = m.get('labels', {})
            record = dict(kind=obj['kind'], name=m['name'], uid=m['uid'], created_at=m.get('creationTimestamp'),
                          owned_labels=labels.get('experiment') == P.EXPERIMENT and labels.get('run-id') == self.run)
            records.append(record)
            self.resources.append(record)
        confirmed = {(r['kind'], r['name']) for r in records}
        if error is not None or not complete:
            self.unresolved += [dict(kind=i['kind'], name=i['metadata']['name'], stem=stem) for i in items
                                if (i['kind'], i['metadata']['name']) not in confirmed]
        self.save(stem+'.created.json', dict(confirmed=records, stdout_complete=complete))
        if error is not None:
            raise RuntimeError(f'{stem}: create failed after {len(records)}/{len(items)} confirmed') from error
        if not complete or len(records) != len(items) or not all(r['owned_labels'] for r in records):
            raise RuntimeError(f'{stem}: create confirmed {len(records)}/{len(items)} owned objects')

    def control(self, url, body=None, timeout=30):
        return bounded_http.control(url, body, timeout=timeout)


def protected(session, label):
    listing = json.loads(session.call(label, ['get', 'pods', '-A', '-o', 'json']))
    rows = [dict(namespace=p['metadata'].get('namespace'), name=p['metadata']['name'], uid=p['metadata'].get('uid'),
                 node=p.get('spec', {}).get('nodeName'), phase=p.get('status', {}).get('phase'))
            for p in listing.get('items', []) if p['metadata'].get('labels', {}).get('run-id') != session.run]
    session.save(label+'.projected.json', rows)
    return listing


def check_occupancy(session, label):
    """The NPU on the server must be held by this run's Mobilint Pod (creation UID) and nobody else."""
    holders = npu_holders(json.loads(session.call(label, ['get', 'pods', '-A', '-o', 'json']))['items'])
    own = session.record(f'{session.run}-mobilint')
    ok = [h['uid'] for h in holders] == ([own['uid']] if own else [])
    session.save(label+'.json', dict(holders=holders, owned_uid=own and own['uid'], only_owned=ok))
    if not ok:
        raise RuntimeError(f'{label}: NPU holders {holders} are not exactly the owned Mobilint Pod')


def poll_health(session, key, url, deadline_s):
    end, last = time.monotonic()+deadline_s, None
    while time.monotonic() < end:
        try:
            last = session.control(url+'/health', timeout=10)
            if last['state'] in ('ready', 'failed'):
                break
        except Exception as error:
            last = dict(state='unreachable', error=repr(error)[:300])
        time.sleep(2)
    session.save(f'health-{key}.json', last)
    return last


def identity(session, key, health, frozen, frozen_sha):
    """Expected execution identity of one arm: creation UID/node from the API, model/runtime from health, both
    checked against the frozen protocol; any disagreement refuses the arm."""
    obj = session.owned_state(key, 'identity-'+key)
    model = P.ASSETS_BY_ARM[key]
    ev = health.get('evidence') or {}
    want = dict(pod_uid=obj['metadata']['uid'], node=obj['spec']['nodeName'], model_sha256=frozen['assets'][model]['sha256'],
                runtime_version=ev.get('runtime_version'), frozen_sha256=frozen_sha)
    seen = dict(pod_uid=health.get('pod_uid'), node=health.get('node'), model_sha256=ev.get('model_sha256'),
                frozen_sha256=health.get('frozen_sha256'))
    bad = [k for k, v in seen.items() if v != want[k]]
    if key == 'mobilint' and ev.get('runtime_version') != P.QBRUNTIME_VERSION:
        bad.append('runtime_version')
    if bad:
        raise RuntimeError(f'{key}: health identity differs from created/frozen identity in {bad}')
    return want


def trace(session, edge_url, label='main', sleep=time.sleep, clock=time.monotonic):
    """Poll a running edge trace; every poll appends new ledger rows to ledger-<label>.jsonl (checkpoint).
    A failed control call is recorded and polling continues until the trace ends or the deadline passes."""
    checkpoint = session.out/f'ledger-{label}.jsonl'
    end, have, state, errors = clock() + P.DEADLINES['trace_s'] + 60, 0, None, []
    while clock() < end:
        try:
            page = session.control(f'{edge_url}/ledger?label={label}&since={have}', timeout=60)
            with checkpoint.open('a') as f:
                for row in page['rows']:
                    f.write(json.dumps(row)+'\n')
            have += len(page['rows'])
            state = session.control(edge_url+'/status')['traces'][label]
            if state['state'] != 'running' and have >= state['done']:
                break
        except Exception as error:
            errors.append(dict(at=datetime.now(timezone.utc).isoformat(), error=repr(error)[:300]))
        sleep(5)
    session.save(f'trace-{label}-status.json', dict(state=state, checkpoint_rows=have, poll_errors=errors))
    if state is None or state['state'] != 'done':
        raise RuntimeError(f'trace {label} did not finish: {state}; {len(errors)} poll errors')


def salvage(session, budget_s=180, clock=time.monotonic):
    """Best-effort bounded recovery before cleanup on every exit: the edge ledger first, then each worker's health,
    each attempt independent of the others. Errors are recorded per item; the caller still guards against a raise."""
    end, out = clock() + budget_s, {}
    urls = dict(getattr(session, 'urls', {}) or {})
    attempts = ([('ledger-main', urls['edge']+'/ledger?label=main', 120)] if 'edge' in urls else [])
    attempts += [(f'health-{key}', url+'/health', 20) for key, url in urls.items()]
    for name, url, cap in attempts:
        left = end - clock()
        if left <= 1:
            out[name] = 'budget exhausted'
            continue
        try:
            session.save(f'salvage-{name}.json', session.control(url, timeout=min(cap, left)))
            out[name] = 'saved'
        except Exception as error:
            out[name] = f'error {error!r}'[:300]
    session.save('salvage.json', out)
    return out


def verify_cleanup(session):
    """-> list of problems; empty only when every created UID is verified absent and no creation is unresolved
    (an unresolved same-name object is reported, never deleted)."""
    problems = []
    for record in session.resources:
        try:
            present = session.owned_present(record)
        except Exception as error:
            present = None
            record['verify_error'] = repr(error)[:300]
        record['verified_absent'] = present is False
        if present is not False:
            problems.append(f"{record['kind']} {record['name']} uid {record['uid']}: delete={record.get('delete')} present={present}")
    for item in session.unresolved:
        problems.append(f"unresolved creation {item['kind']} {item['name']} (no UID; not deleted)")
    return problems


def execute(session, prepared, frozen_path, stage):
    phase = collect.phase
    frozen_sha = prepare.file_sha(frozen_path)
    frozen = json.loads(Path(frozen_path).read_text())
    with phase(session, 'preflight'):
        before = protected(session, 'protected-before')
        nodes = json.loads(session.call('nodes', ['get', 'nodes', '-o', 'json']))['items']
        edge_node, report = choose(nodes, before['items'])
        session.save('preflight.json', report)
    session.expected = {'cache': (P.SERVER, False), 'mobilint': (P.SERVER, False), 'cpu-fp32': (P.SERVER, False), 'edge': (edge_node, True)}
    names = [f'{session.run}-{k}' for k in KEYS]
    with phase(session, 'cache'):
        session.create('cache', [config_map(session.run), pod(session.run, 'cache', P.SERVER, frozen_sha)])
        status = session.wait_ready('cache', [names[0]], P.DEADLINES['pod_ready_s'])
        if status[names[0]]:
            raise RuntimeError('cache not Ready: '+json.dumps(status))
        cache_url = f"http://{session.owned_state('cache', 'cache-ip')['status']['podIP']}:{P.PORT}"
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            for path in sorted((Path(prepared)/'bundle').rglob('*')):
                if path.is_file():
                    archive.add(path, arcname=str(path.relative_to(Path(prepared)/'bundle')))
            archive.add(frozen_path, arcname='frozen.json')
        session.call('cache-upload', ['exec', '-i', '-n', P.NS, names[0], '--', 'python3', '-c',
                     "import tarfile,sys;tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall('/work',filter='data')"],
                     stdin=stream.getvalue(), timeout=600)
        status_code, body, _ = bounded_http.exchange(cache_url+'/frozen.json', None, None, 30, 8*1024*1024)
        if status_code != 200 or P.sha256(body) != frozen_sha:
            raise RuntimeError('cache does not serve the frozen protocol')
        session.save('cache.json', dict(url=cache_url, bundle_bytes=len(stream.getvalue()), frozen_sha256=frozen_sha))
    session.urls = {}
    with phase(session, 'workers'):
        session.create('workers', [pod(session.run, k, session.expected[k][0], frozen_sha, cache_url) for k in KEYS[1:]])
        status = session.wait_ready('workers', names[1:], P.DEADLINES['pod_ready_s'])
        session.save('workers-ready.json', status)
        for key in KEYS[1:]:
            if not status[f'{session.run}-{key}']:
                session.urls[key] = f"http://{session.owned_state(key, 'ip-'+key)['status']['podIP']}:{P.PORT}"
        # install + load are bounded inside the Pods (fetch deadlines, terminable load); this is the outer wait.
        health = {k: poll_health(session, k, u, P.DEADLINES['install_s']+P.DEADLINES['load_s']+30) for k, u in session.urls.items()}
    with phase(session, 'probe'):
        result = dict(health={k: dict(state=v.get('state'), error=v.get('error')) for k, v in health.items()}, workers_ready=status)
        session.save('probe.json', result)
        if not all(health.get(k, {}).get('state') == 'ready' for k in ('edge', 'mobilint', 'cpu-fp32')):
            raise RuntimeError('workers not ready; see health-*.json (no probe sent)')
        identities = {arm: identity(session, arm, health[arm], frozen, frozen_sha) for arm in P.ARMS}
        session.save('identities.json', identities)
        session.save('edge-configure.json', session.control(session.urls['edge']+'/configure', dict(
            run_id=session.run, urls={a: session.urls[a] for a in P.ARMS}, identities=identities)))
        check_occupancy(session, 'npu-occupancy-probe')
        sample = frozen['warmup'][0]['id']
        for arm in P.ARMS:
            probe = session.control(session.urls['edge']+'/probe', dict(arm=arm, sample=sample), timeout=P.DEADLINES['probe_s']+10)
            probe.pop('logits_b64', None)
            result['probe-'+arm] = probe
        result['mobilint_gate'] = all(result['probe-'+a].get('status') == 'ok' for a in P.ARMS)
        session.save('probe-result.json', result)
        if not result['mobilint_gate']:
            raise RuntimeError('probe gate failed; see probe-result.json and health-*.json')
    if stage != 'full':
        return
    with phase(session, 'trace'):
        session.save('trace-start.json', session.control(session.urls['edge']+'/trace', dict(label='main')))
        trace(session, session.urls['edge'])
        session.save('ledger-main.json', session.control(session.urls['edge']+'/ledger?label=main', timeout=120))
        check_occupancy(session, 'npu-occupancy-after-trace')
        for key, url in session.urls.items():
            session.save(f'health-final-{key}.json', session.control(url+'/health'))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--prepared', required=True)
    ap.add_argument('--frozen', required=True)
    ap.add_argument('--runs')
    ap.add_argument('--release')
    ap.add_argument('--stage', choices=('probe', 'full'))
    ap.add_argument('--run-id', default='mdp-'+datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%S'))
    ap.add_argument('--print-release-draft', action='store_true')
    a = ap.parse_args(argv)
    if a.print_release_draft:
        print(json.dumps(dict(frozen_sha256=prepare.file_sha(a.frozen), code=dependency_hashes(), approved_by=None, stages=[]), indent=1))
        return
    release = json.loads(Path(a.release).read_text())
    problems = check_release(release, prepare.file_sha(a.frozen), dependency_hashes())
    if a.stage not in release.get('stages', []):
        problems.append(f'stage {a.stage} not released')
    if problems:
        raise SystemExit('release check failed: '+'; '.join(problems))
    session = Session(a.runs, a.run_id)
    session.save('release.json', release)
    error = None
    try:
        execute(session, a.prepared, Path(a.frozen), a.stage)
    except BaseException as exc:
        error = repr(exc)
        raise
    finally:
        recovery = []
        try:
            salvage(session)
        except BaseException as exc:  # includes KeyboardInterrupt: cleanup below must still run
            recovery.append(f'salvage error {exc!r}'[:500])
        finally:
            cleanup_problems = ['cleanup did not run']
            try:
                with collect.phase(session, 'cleanup'):
                    session.delete_owned('cleanup', [r['name'] for r in session.resources])
                    cleanup_problems = verify_cleanup(session)
                    protected(session, 'protected-after')
            except BaseException as exc:
                cleanup_problems.append(f'cleanup error {exc!r}'[:500])
            finally:
                try:
                    session.save('cleanup.json', dict(records=session.resources, unresolved=session.unresolved, error=error,
                                                      recovery_errors=recovery, problems=cleanup_problems, verified=not cleanup_problems))
                except BaseException as exc:
                    cleanup_problems.append(f'cleanup record not saved {exc!r}'[:300])
            if error is None and (cleanup_problems or recovery):
                raise SystemExit('run not complete: '+'; '.join(cleanup_problems + recovery))


if __name__ == '__main__':
    main()
