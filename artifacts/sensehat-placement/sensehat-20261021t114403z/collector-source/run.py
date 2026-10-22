"""Sense HAT forecast placement smoke CLI.

--prepare INPUT_ROOT --output NEW_DIR   freeze model/windows/targets/reference locally
--dry-run --prepared DIR                local only: hash check, manifests, reference replay
--execute --prepared DIR                cluster mode (coordinator only): owned resources in
                                        topology-profile-lab, fixed placements + one observed
                                        default-scheduler placement, cleanup of run manifest only.
Fixed-placement + scheduler-selected diagnostic; not an optimized-placement comparison.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

import forecast
import stage_runtime as sr
import verify

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ARTIFACTS = ROOT / 'artifacts/sensehat-placement'
IMAGE = '192.168.0.56:5000/node-inventory-python@sha256:370a8339507905053693d178e1cda8fbf77937f045ead58a3c32d8cead2ca67f'
NS = 'topology-profile-lab'
PORT = 8080
KUBECTL = ('rtk', 'proxy', 'kubectl', '--request-timeout=20s')
ROLES = ('t1', 't2', 't3')
CONFIG_KEYS = ('stage_runtime.py', 'model.json', 'windows.json')


def now():
    return datetime.now(timezone.utc).isoformat()


def meta(run, name, **labels):
    return {'name': name, 'namespace': NS,
            'labels': {'experiment': 'sensehat-placement', 'run-id': run, **labels}}


def stage_pod(run, name, role, candidates, group):
    """All stage Pods carry identical requests; only nodeAffinity candidates differ."""
    container = {'name': 'worker', 'image': IMAGE, 'imagePullPolicy': 'IfNotPresent',
                 'command': ['python3', '-u', '/config/stage_runtime.py', 'serve'],
                 'resources': {'requests': {'cpu': '100m', 'memory': '64Mi'},
                               'limits': {'cpu': '500m', 'memory': '128Mi'}},
                 'env': [{'name': 'NODE_NAME', 'valueFrom': {'fieldRef': {'fieldPath': 'spec.nodeName'}}},
                         {'name': 'POD_UID', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.uid'}}},
                         {'name': 'TASK_ID', 'value': role}, {'name': 'CONFIG_ROOT', 'value': '/config'},
                         {'name': 'PYTHONDONTWRITEBYTECODE', 'value': '1'}],
                 'ports': [{'containerPort': PORT}],
                 'volumeMounts': [{'name': 'config', 'mountPath': '/config', 'readOnly': True}],
                 'readinessProbe': {'httpGet': {'path': '/health', 'port': PORT}, 'initialDelaySeconds': 1,
                                    'periodSeconds': 2, 'timeoutSeconds': 2},
                 'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                     'capabilities': {'drop': ['ALL']}}}
    spec = {'schedulerName': 'default-scheduler', 'automountServiceAccountToken': False, 'enableServiceLinks': False,
            'restartPolicy': 'Never', 'activeDeadlineSeconds': 1800, 'terminationGracePeriodSeconds': 3,
            'securityContext': {'runAsUser': 65532, 'runAsGroup': 65532, 'runAsNonRoot': True},
            'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [
                {'matchFields': [{'key': 'metadata.name', 'operator': 'In', 'values': list(candidates)}]}]}}},
            'containers': [container], 'volumes': [{'name': 'config', 'configMap': {'name': run}}]}
    return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': meta(run, name, role=role, group=group), 'spec': spec}


def configmap(run, prepared):
    data = {'stage_runtime.py': (HERE / 'stage_runtime.py').read_text(),
            'model.json': (prepared / 'model.json').read_text(), 'windows.json': (prepared / 'windows.json').read_text()}
    return {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': meta(run, run), 'immutable': True, 'data': data}


def scheduler_pods(run):
    candidates = [forecast.NODES['edge'], forecast.NODES['server']]
    return [stage_pod(run, f'{run}-sched-{role}', role, candidates, 'scheduler-probe') for role in ROLES]


def pool_pods(run):
    pods = [stage_pod(run, f'{run}-src', 'source', [forecast.NODES['edge']], 'pool')]
    for role in ROLES:
        for site, node in forecast.NODES.items():
            pods.append(stage_pod(run, f'{run}-{role}-{site}', role, [node], 'pool'))
    return pods


def replay_reference(prepared):
    model_raw = (prepared / 'model.json').read_bytes()
    model_doc, model_sha = json.loads(model_raw), sr.digest(model_raw)
    windows = json.loads((prepared / 'windows.json').read_text())['windows']
    reference = json.loads((prepared / 'reference.json').read_text())['windows']
    return all(forecast.reference_chain(w['payload'], model_doc, model_sha) == reference[wid]
               for wid, w in windows.items())


def dry_run(prepared):
    manifest = forecast.check_prepared(prepared)
    run = 'sensehat-dryrun'
    cm = configmap(run, prepared)
    return {'mode': 'dry-run-local-only', 'run_id_placeholder': run, 'namespace': NS, 'image': IMAGE,
            'model_sha256': manifest['model_sha256'], 'plan': json.loads((prepared / 'plan.json').read_text()),
            'configmap_keys': sorted(cm['data']), 'configmap_bytes': sum(len(v.encode()) for v in cm['data'].values()),
            'pods': [*scheduler_pods(run), *pool_pods(run)], 'reference_reproduced': replay_reference(prepared)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', type=Path, metavar='INPUT_ROOT')
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--execute', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--prepared', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.prepare:
            if not args.output:
                parser.error('--prepare requires --output')
            out = forecast.prepare(args.prepare, args.output)
            evaluation = json.loads((out / 'evaluation.json').read_text())['channels']
            print(json.dumps({'prepared': str(out), 'model_sha256': forecast.check_prepared(out)['model_sha256'],
                              'held_out': {c: {'unit': e['unit'], 'model': e['model'], 'persistence': e['persistence']}
                                           for c, e in evaluation.items()}}, indent=2))
            return 0
        if not args.prepared:
            parser.error('--dry-run/--execute require --prepared')
        if args.dry_run:
            print(json.dumps(dry_run(args.prepared), indent=2))
            return 0
        return execute(args.prepared)
    except (forecast.SourceError, sr.InputError, FileExistsError) as error:
        print(f'{type(error).__name__}: {error}', file=sys.stderr)
        return 2


class Recorder:
    """Every kubectl call keeps argv, start/end, returncode and partial stdout/stderr files."""

    def __init__(self, out, kubectl):
        self.out, self.kubectl, self.counter = out, tuple(kubectl), 0

    def save(self, name, value):
        with (self.out / name).open('x') as handle:
            json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write('\n')

    @contextmanager
    def phase(self, name, **extra):
        begin, span = time.monotonic(), {}
        self.event({'event': 'start', 'phase': name, 'at': now(), **extra})
        try:
            yield span
        except BaseException as error:
            self.event({'event': 'end', 'phase': name, 'at': now(), 'status': 'error', 'error': repr(error),
                        'duration_s': time.monotonic() - begin, **span})
            raise
        span['duration_s'] = time.monotonic() - begin
        self.event({'event': 'end', 'phase': name, 'at': now(), 'status': span.pop('status', 'ok'), **span})

    def event(self, value):
        with (self.out / 'phases.jsonl').open('a') as handle:
            handle.write(json.dumps(value) + '\n')

    def call(self, stem, args, stdin=None, timeout=30, check=True, project=None):
        """project(raw_stdout) -> text, when given, is applied in memory; raw stdout never reaches disk.

        Captured stdout of nonzero/timed-out calls is projected the same way; if that is impossible only
        status, byte count and reason are recorded, and the original failure is never masked."""
        self.counter += 1
        tag = f'{self.counter:03d}-{stem}'
        cmd = [*self.kubectl, *args]
        event = {'started_at': now(), 'command': cmd, 'timeout_s': timeout}
        begin = time.monotonic()

        def keep(handle, raw):
            raw = raw or b''
            try:
                handle.write(project(raw).encode())
            except Exception as error:
                event['projection'] = {'status': 'unavailable', 'captured_bytes': len(raw),
                                       'reason': f'{type(error).__name__}; raw stdout omitted'}
                return error
            event['projection'] = {'status': 'projected', 'captured_bytes': len(raw)}

        try:
            with (self.out / (tag + '.stdout')).open('wb') as so, (self.out / (tag + '.stderr')).open('wb') as se:
                try:
                    result = subprocess.run(cmd, input=stdin.encode() if isinstance(stdin, str) else stdin,
                                            stdout=subprocess.PIPE if project else so, stderr=se, timeout=timeout)
                except subprocess.TimeoutExpired as error:
                    if project:
                        keep(so, error.output)
                    raise
                if project and (failure := keep(so, result.stdout)) and result.returncode == 0:
                    raise failure
            event.update(status='completed', returncode=result.returncode)
        except BaseException as error:
            event.update(status='timeout' if isinstance(error, subprocess.TimeoutExpired) else 'error', error=repr(error))
            raise
        finally:
            event.update(finished_at=now(), wall_ms=(time.monotonic() - begin) * 1000)
            self.save(tag + '.event.json', event)
        stdout = (self.out / (tag + '.stdout')).read_text()
        stderr = (self.out / (tag + '.stderr')).read_text()
        if check and result.returncode:
            raise RuntimeError(f'{tag} failed rc={result.returncode}: {stderr[-300:]}')
        return result.returncode, stdout, stderr, event['wall_ms']


def project_pod(pod):
    """Safe projection: identity/placement/scheduling fields only, never env or secrets."""
    spec, status, labels = pod['spec'], pod.get('status', {}), pod['metadata']['labels']
    role = labels.get('role')
    site = next((s for s, n in forecast.NODES.items() if n == spec.get('nodeName')), None)
    return {'name': pod['metadata']['name'], 'uid': pod['metadata']['uid'], 'role': role, 'group': labels.get('group'),
            'node': spec.get('nodeName'), 'site': site, 'podIP': status.get('podIP'),
            'schedulerName': spec.get('schedulerName'), 'affinity': spec.get('affinity'),
            'hostNetwork': spec.get('hostNetwork', False), 'creationTimestamp': pod['metadata'].get('creationTimestamp'),
            'conditions': [{k: c.get(k) for k in ('type', 'status', 'lastTransitionTime')}
                           for c in status.get('conditions', [])],
            'containers': [{k: c.get(k) for k in ('name', 'image', 'imageID', 'ready', 'restartCount', 'started')}
                           for c in status.get('containerStatuses', [])],
            # Admitted (not requested) safety fields; container env/args are never copied.
            'security': {'hostNetwork': spec.get('hostNetwork', False), 'hostPID': spec.get('hostPID', False),
                         'hostIPC': spec.get('hostIPC', False), 'securityContext': spec.get('securityContext'),
                         'containers': [{'kind': kind, 'name': c.get('name'), 'image': c.get('image'),
                                         'securityContext': c.get('securityContext')}
                                        for kind in ('initContainers', 'containers', 'ephemeralContainers')
                                        for c in spec.get(kind) or []],
                         'volumes': [{'name': v.get('name'), 'sources': sorted(k for k in v if k != 'name'),
                                      'configMap': (v.get('configMap') or {}).get('name')}
                                     for v in spec.get('volumes') or []]}}


def refuse_unsafe(pods, run):
    bad = {p['name']: problems for p in pods if (problems := verify.observed_pod_problems(p, run))}
    if bad:
        raise RuntimeError('admitted Pod violates safety contract: ' + json.dumps(bad))


# kubectl projects before printing, so unrelated Pod spec/env never enters captured stdout.
PROTECTED_JSONPATH = ('jsonpath={range .items[*]}{.metadata.uid}{"\\t"}{.metadata.namespace}{"\\t"}'
                      '{.metadata.name}{"\\t"}{.status.phase}{"\\t"}'
                      '{.status.conditions[?(@.type=="Ready")].status}{"\\n"}{end}')


def protected_pods(rec, stem):
    out = {}
    for line in rec.call(stem, ['get', 'pods', '-A', '-o', PROTECTED_JSONPATH])[1].splitlines():
        uid, namespace, name, phase, ready = line.split('\t')
        out[uid] = {'name': name, 'namespace': namespace, 'phase': phase or None, 'ready': ready or None}
    return out


def execute(prepared, kubectl=KUBECTL, artifacts=ARTIFACTS, port=PORT):
    prepared = Path(prepared)
    manifest = forecast.check_prepared(prepared)
    forecast.check_code(manifest)  # a source edit after --prepare needs a new preparation
    ctx = verify.load_context(prepared)
    run = datetime.now(timezone.utc).strftime('sensehat-%Y%m%dt%H%M%Sz')
    out = Path(artifacts) / run
    out.mkdir(exist_ok=False)
    (out / 'collector-source').mkdir()
    for name in (*forecast.CODE_FILES, 'fake_kubectl.py'):
        shutil.copyfile(HERE / name, out / 'collector-source' / name)
    shutil.copytree(prepared, out / 'prepared')
    rec = Recorder(out, kubectl)
    resources, rows, primary = [], [], None

    def apply(stem, items):
        rec.save(stem + '.manifest.json', {'apiVersion': 'v1', 'kind': 'List', 'items': items})
        body = json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': items})
        rec.call(stem + '-dry-run', ['apply', '--dry-run=server', '-f', '-'], body)
        resources.extend(items)  # tracked before apply so a partial apply is still cleaned up
        rec.call(stem, ['apply', '-f', '-'], body)

    def get_pod(name):
        projected = rec.call('pod-state', ['get', 'pod', '-n', NS, name, '-o', 'json'],
                             project=lambda raw: json.dumps(project_pod(json.loads(raw))))[1]
        return json.loads(projected)

    def client(stem, url, control, timeout=90):
        args = ['exec', '-i', '-n', NS, f'{run}-src', '--', 'python3', '/config/stage_runtime.py', 'client',
                '--url', url, '--timeout', '60']
        return rec.call(stem, args, json.dumps(control), timeout=timeout, check=False)

    protected = {}
    try:
        with rec.phase('protected-before'):
            protected = protected_pods(rec, 'protected-before')
            rec.save('protected-before.json', protected)
        rec.save('design.json', {
            'run_id': run, 'namespace': NS, 'image': IMAGE, 'nodes': forecast.NODES, 'port': port,
            'model_sha256': manifest['model_sha256'], 'prepared_files': manifest['files'],
            'code_sha256': {n: sr.digest((HERE / n).read_bytes()) for n in forecast.CODE_FILES},
            'scope': 'fixed diagnostic placements + one observed default-scheduler placement; frozen physical Sense HAT '
                     'history replayed from the edge source Pod; not live sensor E2E, not optimized placement comparison',
            'measured_span': 'source Pod monotonic from T1 request to final response; excludes target access, '
                             'artifact fetch, window formation and training',
            'limits': 'single native-network block; no injected delay/qdisc; no inference retry; '
                      'no p95/significance/throughput claim'})
        with rec.phase('configmap'):
            apply('configmap', [configmap(run, prepared)])
        with rec.phase('scheduler-probe'):
            begin = time.monotonic()
            apply('scheduler-probe', scheduler_pods(run))
            rec.call('scheduler-ready', ['wait', 'pods', '-n', NS, '-l', 'group=scheduler-probe,run-id=' + run,
                                         '--for=condition=Ready', '--timeout=300s'], timeout=310)
            ready_s = time.monotonic() - begin
            probes = {role: get_pod(f'{run}-sched-{role}') for role in ROLES}
            refuse_unsafe(probes.values(), run)
            names = {p['name'] for p in probes.values()}
            events = json.loads(rec.call('scheduler-events', ['get', 'events', '-n', NS, '-o', 'json'])[1])['items']
            rec.save('scheduler-observation.json', {
                'note': 'three unpinned Task Pods, equal requests, candidates dev0003/ser0002; observed before pool deploy',
                'apply_to_all_ready_s': ready_s, 'pods': probes,
                'events': [{'object': e['involvedObject']['name'], **{k: e.get(k) for k in (
                    'reason', 'message', 'type', 'reportingComponent', 'firstTimestamp', 'lastTimestamp', 'count')},
                    'source_component': e.get('source', {}).get('component')}
                    for e in events if e['involvedObject'].get('name') in names]})
        default_nodes = {role: probes[role]['node'] for role in ROLES}
        default_sites = {role: probes[role]['site'] for role in ROLES}
        if None in default_sites.values():
            raise RuntimeError('default-scheduler placed a Task outside the two candidates: ' + str(default_nodes))
        shares = next((label for label, sites in forecast.FIXED_PLACEMENTS.items() if sites == default_sites), None)
        rec.save('placements.json', {'fixed': forecast.FIXED_PLACEMENTS, 'default_scheduler': {
            'nodes': default_nodes, 'sites': default_sites, 'shares_with': shares,
            'execution': 're-pinned to the matching pool Pods; scheduler probe overhead reported separately'}})
        with rec.phase('pool-deploy'):
            apply('pool', pool_pods(run))
            rec.call('pool-ready', ['wait', 'pods', '-n', NS, '-l', 'group=pool,run-id=' + run,
                                    '--for=condition=Ready', '--timeout=300s'], timeout=310)
            pool = {item['metadata']['name']: get_pod(item['metadata']['name']) for item in pool_pods(run)}
            rec.save('pool-pods.json', pool)
            refuse_unsafe(pool.values(), run)
        for pod in pool.values():
            expected = forecast.NODES['edge'] if pod['role'] == 'source' else forecast.NODES[pod['name'].rsplit('-', 1)[1]]
            if pod['node'] != expected or pod['hostNetwork']:
                raise RuntimeError(f'pool Pod {pod["name"]} on {pod["node"]}, expected {expected}')
        endpoint = {(p['role'], p['site']): f'http://{p["podIP"]}:{port}' for p in pool.values()}
        source_url = endpoint[('source', 'edge')]
        with rec.phase('health'):
            health = {}
            for name, pod in pool.items():
                code, stdout, stderr, _ = client('health-' + pod['role'], f'http://{pod["podIP"]}:{port}/health', {})
                identity = json.loads(stdout) if code == 0 else {'error': stderr[-300:]}
                if (identity.get('node'), identity.get('pod_uid'), identity.get('model_sha256')) != (
                        pod['node'], pod['uid'], ctx['model_sha256']):
                    raise RuntimeError(f'health identity mismatch for {name}: {identity}')
                health[name] = identity
            rec.save('health.json', health)
        pool_ctx = dict(ctx, run_id=run, pods={(p['role'], p['site']): p for p in pool.values()},
                        source_pod=pool[f'{run}-src'])
        spans = {}
        with rec.phase('measure'), (out / 'attempts.jsonl').open('x') as handle:
            for block in ctx['plan']['blocks']:
                label = block['label']
                if label == 'default-scheduler' and shares:
                    continue
                sites = default_sites if label == 'default-scheduler' else forecast.FIXED_PLACEMENTS[label]
                index = 0
                for kind in ('warmup', 'evaluation'):
                    # Separate warmup/evaluation spans per placement (phase start/end in phases.jsonl).
                    with rec.phase(f'{kind}:{label}', label=label, kind=kind) as spans[(label, kind)]:
                        for wid in block[kind]:
                            request_id = f'{label}-{index:02d}'
                            chain = [endpoint[(role, sites[role])] for role in ROLES]
                            row = {'label': label, 'kind': kind, 'window_id': wid, 'request_id': request_id,
                                   'sites': sites, 'nodes': {r: forecast.NODES[s] for r, s in sites.items()},
                                   'chain': chain, 'started_at': now()}
                            code, stdout, stderr, wall = client('attempt', source_url + '/run', {
                                'run_id': run, 'request_id': request_id, 'window_id': wid, 'chain': chain})
                            row.update(exec_returncode=code, exec_wall_ms=wall, stderr=stderr[-2000:])
                            try:
                                row['reply'] = json.loads(stdout)
                            except ValueError:
                                row.update(reply=None, stdout=stdout[-2000:])
                            reply_ok = (code == 0 and isinstance(row['reply'], dict)
                                        and row['reply'].get('status') == 'ok')
                            row['status'] = 'ok' if reply_ok else 'error'
                            problems = verify.check_attempt(row, pool_ctx) if reply_ok else [
                                f'kubectl exec rc={code}: '
                                + str((row['reply'] or {}).get('error') or stderr[-300:])]
                            if problems:
                                row.update(status='error', problems=problems, error=problems[0])
                            else:
                                # Target read only now, after the workflow returned.
                                row.update(verify.scored(row['reply']['result'], ctx['targets'][wid]),
                                           e2e_ms=row['reply']['source']['e2e_ms'],
                                           t3_sha_equals_reference=row['reply']['receipts'][-1]['output_sha256']
                                           == ctx['reference'][wid]['t3']['output_sha256'])
                            rows.append(row)
                            handle.write(json.dumps(row, sort_keys=True) + '\n')
                            handle.flush()
                            print('attempt', label, index, row['status'], row.get('e2e_ms'), flush=True)
                            index += 1
        rec.save('summary.json', summarize(run, rows, shares, default_nodes, ready_s, spans))
    except BaseException as error:
        primary = error
    finally:
        cleanup_errors = teardown(rec, run, resources, protected)
    if primary is not None:
        raise primary
    if cleanup_errors:
        raise RuntimeError('cleanup not verified: ' + '; '.join(cleanup_errors))
    print('evidence', out, flush=True)
    return 0 if rows and all(r['status'] == 'ok' for r in rows) else 1


def summarize(run, rows, shares, default_nodes, ready_s, spans):
    blocks = {}
    for row in rows:
        block = blocks.setdefault(row['label'], {'nodes': row['nodes'], 'ok': 0, 'failed': 0, 'eval_e2e_ms': [],
                                                 'channels': {}})
        block['ok' if row['status'] == 'ok' else 'failed'] += 1
        if row['kind'] == 'evaluation' and row['status'] == 'ok':
            block['eval_e2e_ms'].append(row['e2e_ms'])
            channel = row['reply']['result']['channel']
            block['channels'].setdefault(channel, {'unit': row['reply']['result']['unit'], 'windows': []})[
                'windows'].append({k: row[k] for k in ('window_id', 'truth', 'forecast', 'persistence',
                                                       'model_error', 'persistence_error')})
    for label, block in blocks.items():
        block.update(warmup_phase_s=spans[(label, 'warmup')]['duration_s'],
                     evaluation_phase_s=spans[(label, 'evaluation')]['duration_s'])
        values = block['eval_e2e_ms']
        block['eval_e2e_descriptive'] = {'n': len(values), 'median_ms': statistics.median(values) if values else None,
                                         'min_ms': min(values, default=None), 'max_ms': max(values, default=None)}
    return {'run_id': run, 'blocks': blocks, 'default_scheduler': {'nodes': default_nodes, 'shares_with': shares,
                                                                   'probe_apply_to_ready_s': ready_s},
            'note': 'descriptive smoke values per placement; no p95/significance/throughput; per-channel units kept'}


def teardown(rec, run, resources, protected):
    """Always runs, also after failures; its own phase span records cleanup cost and errors."""
    with rec.phase('teardown') as span:
        errors = cleanup(rec, run, resources, protected)
        span.update(status='error' if errors else 'ok', errors=errors)
    return errors


def cleanup(rec, run, resources, protected):
    errors = []
    try:
        rec.call('events', ['get', 'events', '-n', NS, '-o', 'json'])
    except Exception as error:
        errors.append('events: ' + repr(error))
    for item in resources:
        if item['kind'] == 'Pod':
            try:
                rec.call('logs-' + item['metadata']['name'], ['logs', '-n', NS, item['metadata']['name']])
            except Exception:
                pass  # retained in its own phase files; cleanup continues
    deleted = []
    if resources:
        try:
            rec.call('cleanup', ['delete', '-f', '-', '--ignore-not-found=true', '--wait=false'],
                     json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': resources}))
            deleted = [[i['kind'], i['metadata']['name']] for i in resources]
            rec.call('deleted', ['wait', '--for=delete', 'pod', '-n', NS, '-l', 'run-id=' + run, '--timeout=90s'],
                     timeout=100)
        except Exception as error:
            errors.append('delete: ' + repr(error))
    remaining = None
    try:
        left = json.loads(rec.call('remaining', ['get', 'pods,configmaps', '-n', NS, '-l', 'run-id=' + run,
                                                 '-o', 'json'])[1])
        remaining = len(left['items'])
        if remaining:
            errors.append(f'{remaining} owned resources remain')
    except Exception as error:
        errors.append('verify: ' + repr(error))
    try:
        after = protected_pods(rec, 'protected-after')
        changes = [{'uid': uid, 'before': before, 'after': after.get(uid)} for uid, before in protected.items()
                   if (after.get(uid) or {}).get('phase') != before['phase']
                   or (after.get(uid) or {}).get('ready') != before['ready']]
        rec.save('protected-after.json', {'protected_count': len(protected), 'changes': changes,
                                          'note': 'observations; this run did not touch them by design'})
    except Exception as error:
        errors.append('protected-after: ' + repr(error))
    rec.save('cleanup.json', {'remaining_owned_pods_and_configmaps': remaining, 'deleted_resources': deleted,
                              'diagnostic_or_cleanup_errors': errors})
    return errors


if __name__ == '__main__':
    sys.exit(main())
