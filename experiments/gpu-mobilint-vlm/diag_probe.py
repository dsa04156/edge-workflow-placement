"""Collector for the dev0009 pip-startup diagnostic: one owned ConfigMap + one probe Pod, no model, no cache download.

  python3 diag_probe.py --runs <dir> [--run-id gmv-diag-...] [--plan]

The probe Pod is live.worker_pod('cuda', ...) with only name/component, command, the ConfigMap and the Pod lifetime
changed: same image, node, env (HOME=/work, TMPDIR, PYTHONDONTWRITEBYTECODE, ...), requests/limits (cpu 4, memory
5Gi/6656Mi, nvidia.com/gpu.shared 1, 20Gi /work), security context and readiness path. Lifecycle is live.Session
(create-only with server dry-run, UID records also on partial create, unresolved accounting, UID-precondition delete,
absence verification); edge streams are never invoked (live.Session skips/refuses them). Fresh preflight: dev0009 Ready,
untainted, allocatable gpu.shared, no foreign holder; protected workload snapshots before and after. Results come only
over HTTP /health (bounded_http, absolute deadline) and are saved as each step completes.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import live  # noqa: E402  (sets the shared sys.path entries)
import protocol as P  # noqa: E402
import collect  # noqa: E402
import bounded_http  # noqa: E402

SHIPPED = {'diag_pip_probe.py': HERE/'diag_pip_probe.py', 'proc.py': live.HET/'proc.py'}
PROBE_DEADLINE_S = 2700
POD_LIFETIME_S = PROBE_DEADLINE_S + 600
READY_S = 300
POLL_S = 15
HEALTH_MAX = 8*1024*1024


def config_map(run):
    return dict(apiVersion='v1', kind='ConfigMap', metadata=live.meta(run, run+'-code', 'code'),
                data={name: path.read_text() for name, path in SHIPPED.items()})


def probe_pod(run, probe_plan='r1'):
    pod = live.worker_pod(run, 'cuda', 'http://unused.invalid', 'unused')
    pod['metadata'] = live.meta(run, run+'-probe', 'probe')
    pod['spec']['activeDeadlineSeconds'] = POD_LIFETIME_S
    container = pod['spec']['containers'][0]
    container['command'] = ['python3', '-u', '/config/diag_pip_probe.py']
    container['env'] = [e for e in container['env'] if e['name'] not in ('CACHE_URL', 'ASSET_MANIFEST_SHA256')] + [
        dict(name='PROBE_DEADLINE_S', value=str(PROBE_DEADLINE_S))] + ([dict(name='PROBE_PLAN', value=probe_plan)] if probe_plan != 'r1' else [])
    return pod


def preflight_problems(nodes, pods, run):
    """live.preflight_problems restricted to the Jetson arm: Ready, untainted, allocatable share, no foreign holder."""
    a = P.ARMS['cuda']
    n = next((n for n in nodes if n['metadata']['name'] == a['node']), None)
    if n is None:
        return [f'{a["node"]}: node not found']
    ready = next((c['status'] for c in n['status'].get('conditions', []) if c['type'] == 'Ready'), None)
    blocking = [t for t in n['spec'].get('taints') or [] if t.get('effect') in ('NoSchedule', 'NoExecute')]
    if ready != 'True' or blocking:
        return [f'{a["node"]}: Ready={ready} taints={blocking}']
    resource, problems = next(iter(a['device_resource'])), []
    if int(n['status']['allocatable'].get(resource, '0')) < 1:
        problems.append(f'{a["node"]}: no allocatable {resource}')
    foreign = [h for h in live.holders(pods, a['node'], resource, run) if not h['own']]
    if foreign:
        problems.append(f'{a["node"]}: {resource} held by {foreign}')
    return problems


def execute(session, probe_plan='r1'):
    phase = collect.phase
    with phase(session, 'preflight'):
        collect.protected_snapshot(session, 'protected-before')
        nodes = json.loads(session.call('nodes', ['get', 'nodes', '-o', 'json']))['items']
        pods = json.loads(session.call('pods', ['get', 'pods', '-A', '-o', 'json']))['items']
        problems = preflight_problems(nodes, pods, session.run)
        session.save('preflight.json', dict(problems=problems, code={k: P.file_sha(v) for k, v in SHIPPED.items()},
                                            collector=P.file_sha(Path(__file__))))
        if problems:
            raise RuntimeError('preflight: '+'; '.join(problems))
    name = session.run+'-probe'
    with phase(session, 'probe'):
        session.create('probe', [config_map(session.run), probe_pod(session.run, probe_plan)])
        status = session.wait_ready('probe', [name], READY_S)
        session.save('probe-ready.json', status)
        if status[name]:
            raise RuntimeError('probe not Ready: '+json.dumps(status)[:2000])
        state = session.pod_state(name)
        if state['spec'].get('nodeName') != P.JETSON:
            raise RuntimeError(f'probe on {state["spec"].get("nodeName")!r}, not {P.JETSON}')
        url = f"http://{state['status']['podIP']}:{P.PORT}/health"
        deadline, saved_steps, health, polls = time.monotonic()+PROBE_DEADLINE_S+120, -1, None, 0
        while time.monotonic() < deadline:
            polls += 1
            try:
                health = bounded_http.control(url, timeout=30, max_body=HEALTH_MAX)
            except Exception as error:
                health = dict(status='unavailable', error=repr(error)[:2000])
            done = [s for s in health.get('steps', []) if 'end' in s]
            if len(done) != saved_steps:  # durable partial result at every completed step
                saved_steps = len(done)
                session.save(f'probe-health-{polls:04d}.json', health)
                print(json.dumps(dict(at=datetime.now(timezone.utc).strftime('%H:%M:%S'), phase=health.get('phase'),
                                      steps=[(s['name'], s.get('seconds'), s.get('timed_out'), s.get('MBps')) for s in done][-3:])),
                      flush=True)
            if health.get('phase') in ('done', 'probe_failed', 'probe_deadline'):
                break
            time.sleep(POLL_S)
        session.save('probe-health-final.json', health)
        if (health or {}).get('phase') != 'done':
            raise RuntimeError(f"probe ended in phase {(health or {}).get('phase')!r}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs')
    ap.add_argument('--run-id', default='gmv-diag-'+datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%S'))
    ap.add_argument('--plan', action='store_true')
    ap.add_argument('--probe-plan', choices=('r1', 'fsync'), default='r1')
    a = ap.parse_args(argv)
    if a.plan:
        print(json.dumps([config_map(a.run_id), probe_pod(a.run_id, a.probe_plan)], indent=1))
        return
    session = live.Session(a.runs, a.run_id)
    error = None
    try:
        execute(session, a.probe_plan)
    except BaseException as exc:
        error = repr(exc)
        raise
    finally:
        cleanup_problems = ['cleanup did not run']
        try:
            with collect.phase(session, 'cleanup'):
                session.delete_owned('cleanup', [r['name'] for r in session.resources])
                cleanup_problems = live.verify_cleanup(session)
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
