"""Read-only cluster preflight: bounded API GETs only (nodes, all Pods), projected immediately to the node/device
inventory format of .scratch/quality-network-heterogeneous/node-inventory.json (catalog.load_audit input).

No create/patch/delete/exec/logs. Raw Pod objects are never written: the projection keeps names, nodes, phase,
readiness, images and accelerator requests only (env/args/volumes dropped so no credential is retained).
Output: <out>/node-inventory.json, <out>/projected-pods.json, <out>/capture.json (commands, rc, timing). Refused if present.
capture.json is written on every outcome (ok | timeout | error) with start/finish times and per-call stderr tails; raw
stdout (unprojected Pod objects) is never written, only its byte count. kubectl runs through `rtk proxy` (Session.call).
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ACCEL = ('nvidia.com', 'npu.intel.com', 'hailo.ai', 'rockchip.com', 'mobilint.com', 'gpu.intel.com', 'amd.com', 'xilinx.com')
READ_VERBS = ('get', 'version', 'config')
KUBECTL = ['rtk', 'proxy', 'kubectl']
COMMANDS = {'nodes': KUBECTL + ['get', 'nodes', '-o', 'json', '--request-timeout=30s'],
            'pods': KUBECTL + ['get', 'pods', '-A', '-o', 'json', '--request-timeout=30s']}


def accel(resources):
    return {k: v for k, v in (resources or {}).items() if k.startswith(ACCEL)}


def cpu_milli(value):
    return int(float(value[:-1])) if value.endswith('m') else int(float(value) * 1000)


def project_pod(pod):
    status = pod.get('status', {})
    containers = [dict(name=c['name'], image=c.get('image'), requests=(c.get('resources') or {}).get('requests', {}),
                       limits=(c.get('resources') or {}).get('limits', {}))
                  for c in pod['spec'].get('containers', []) + pod['spec'].get('initContainers', [])]
    return dict(namespace=pod['metadata']['namespace'], name=pod['metadata']['name'], uid=pod['metadata'].get('uid'),
                node=pod['spec'].get('nodeName'), phase=status.get('phase'), runtime_class=pod['spec'].get('runtimeClassName'),
                labels=pod['metadata'].get('labels', {}),
                ready=next((x['status'] for x in status.get('conditions', []) if x['type'] == 'Ready'), None), containers=containers)


def project(nodes, pods):
    projected = [project_pod(p) for p in pods['items']]
    inventory = []
    for n in nodes['items']:
        name, labels, st = n['metadata']['name'], n['metadata'].get('labels', {}), n['status']
        on = [p for p in projected if p['node'] == name and p['phase'] in ('Running', 'Pending')]
        requested = {}
        for p in on:
            for c in p['containers']:
                for k, v in accel(c['requests'] or c['limits']).items():
                    requested[k] = requested.get(k, 0) + int(v)
        inventory.append(dict(
            node=name, ready=next((c['status'] for c in st['conditions'] if c['type'] == 'Ready'), None),
            ready_since=next((c['lastTransitionTime'] for c in st['conditions'] if c['type'] == 'Ready'), None),
            role='edge' if 'node-role.kubernetes.io/edge' in labels else 'server', arch=st['nodeInfo']['architecture'],
            os_image=st['nodeInfo']['osImage'], kernel=st['nodeInfo']['kernelVersion'], kubelet=st['nodeInfo']['kubeletVersion'],
            runtime=st['nodeInfo']['containerRuntimeVersion'], capacity_cpu=st['capacity'].get('cpu'),
            allocatable_cpu=st['allocatable'].get('cpu'), allocatable_memory=st['allocatable'].get('memory'),
            allocatable_ephemeral_storage=st['allocatable'].get('ephemeral-storage'),
            pressure={c['type']: c['status'] for c in st['conditions'] if c['type'].endswith('Pressure')},
            accelerators_capacity=accel(st['capacity']), accelerators_allocatable=accel(st['allocatable']),
            accelerators_requested_by_running_or_pending_pods=requested,
            cpu_requested_m=sum(cpu_milli(c['requests']['cpu']) for p in on for c in p['containers'] if c['requests'].get('cpu')),
            unschedulable=n['spec'].get('unschedulable', False), taints=n['spec'].get('taints', []),
            accelerator_pods=[dict(namespace=p['namespace'], name=p['name'], phase=p['phase'],
                                   requests={k: v for c in p['containers'] for k, v in accel(c['requests'] or c['limits']).items()})
                              for p in on if any(accel(c['requests'] or c['limits']) for c in p['containers'])]))
    return inventory, projected


def tail(value, n=1000):
    if isinstance(value, bytes):
        value = value.decode(errors='replace')
    return (value or '')[-n:]


def run(cmd, runner=subprocess.run, calls=None):
    """One read-only kubectl call; its record is appended to `calls` before any outcome propagates."""
    if cmd[:len(KUBECTL)] != KUBECTL or cmd[len(KUBECTL)] not in READ_VERBS:
        raise ValueError(f'capture is read-only, refused {cmd[:5]}')
    started = time.monotonic()
    record = dict(cmd=cmd, outcome='error')
    calls = calls if calls is not None else []
    calls.append(record)
    try:
        done = runner(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as error:
        record.update(outcome='timeout', seconds=time.monotonic()-started, stderr=tail(error.stderr),
                      partial_stdout_bytes=len(error.output or ''))
        raise
    except Exception as error:
        record.update(seconds=time.monotonic()-started, error=repr(error)[:300])
        raise
    record.update(outcome='ok' if done.returncode == 0 else 'error', rc=done.returncode, seconds=time.monotonic()-started,
                  stderr=tail(done.stderr), stdout_bytes=len(done.stdout or ''))
    return done, record


def capture(out_dir, runner=subprocess.run):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=False)
    header = dict(started_utc=datetime.now(timezone.utc).isoformat(), purpose='read-only preflight (bounded GET)', calls=[],
                  outcome='error')
    try:
        raw = {}
        for key, cmd in COMMANDS.items():
            try:
                done, _ = run(cmd, runner, header['calls'])
            except subprocess.TimeoutExpired:
                header['outcome'] = 'timeout'
                raise
            if done.returncode:
                raise RuntimeError(f'{key}: kubectl rc {done.returncode}: {done.stderr[-300:]}')
            raw[key] = json.loads(done.stdout)
        inventory, projected = project(raw['nodes'], raw['pods'])
        header['outcome'] = 'ok'
    except Exception as error:
        header['error'] = repr(error)[:500]
        raise
    finally:
        header['finished_utc'] = datetime.now(timezone.utc).isoformat()
        with (out/'capture.json').open('x') as handle:
            json.dump(header, handle, indent=1)
    for name, value in (('node-inventory.json', dict(source_header=header, nodes=inventory)),
                        ('projected-pods.json', dict(source_header=header, count=len(projected), items=projected))):
        with (out/name).open('x') as handle:
            json.dump(value, handle, indent=1)
    return inventory


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--out', default=None, help='default artifacts/quality-network-baselines/preflight-<UTC>')
    args = ap.parse_args(argv)
    out = args.out or ROOT/'artifacts/quality-network-baselines'/('preflight-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    inventory = capture(out)
    for n in inventory:
        print(json.dumps(dict(node=n['node'], ready=n['ready'], arch=n['arch'], accel=n['accelerators_allocatable'],
                              used=n['accelerators_requested_by_running_or_pending_pods'], pressure={k: v for k, v in n['pressure'].items() if v != 'False'},
                              storage=n['allocatable_ephemeral_storage'])))
    print('wrote', out)


if __name__ == '__main__':
    main()
