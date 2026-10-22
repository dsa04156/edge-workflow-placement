"""Sense HAT humidity NumPy MLP CPU smoke.

--prepare --output NEW_DIR    validate frozen history, train once locally, freeze model/inputs/reference
--execute --prepared DIR      two owned ephemeral Pods (dev0003 edge, ser0002 server) in topology-profile-lab:
                              upload numpy wheel + model via exec stdin, 1 warmup + 3 timed forwards each,
                              parity vs local reference, delete exactly the owned Pods.
CPU-only forward smoke: no HTTP/DAG/network measurement, no placement comparison.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / 'sensehat-placement'))
import run as placement_run  # noqa: E402  (Recorder/project_pod reused, not modified)

HISTORY = ROOT / 'artifacts/sensehat-placement/discovery-20261021/history'
ARTIFACTS = ROOT / 'artifacts/sensehat-neural-smoke'
WHEELHOUSE = ROOT / 'artifacts/topology-profile-live/wheelhouse'
WHEEL = 'numpy-1.26.4-cp311-cp311-manylinux_2_17_{arch}.manylinux2014_{arch}.whl'
IMAGE = placement_run.IMAGE
NS = 'topology-profile-lab'
NODES = {'edge': ('etri-dev0003-raspi5', 'aarch64'), 'server': ('etri-ser0002-cgnmsb', 'x86_64')}
KUBECTL = ('rtk', 'proxy', 'kubectl', '--request-timeout=20s')
LIVE_BUDGET_S = 300
INSTALL = ("import tarfile,sys,zipfile;tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall('/work',filter='data');"
           "zipfile.ZipFile('/work/numpy.whl').extractall('/work/site');print('installed',flush=True)")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pod(run, site):
    node, _ = NODES[site]
    env = [{'name': 'NODE_NAME', 'valueFrom': {'fieldRef': {'fieldPath': 'spec.nodeName'}}},
           {'name': 'POD_UID', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.uid'}}},
           {'name': 'PYTHONPATH', 'value': '/work/site'}, {'name': 'PYTHONDONTWRITEBYTECODE', 'value': '1'},
           {'name': 'OMP_NUM_THREADS', 'value': '1'}, {'name': 'OPENBLAS_NUM_THREADS', 'value': '1'}]
    container = {'name': 'worker', 'image': IMAGE, 'imagePullPolicy': 'IfNotPresent',
                 'command': ['python3', '-c', 'import time; time.sleep(%d)' % (LIVE_BUDGET_S + 60)],
                 'resources': {'requests': {'cpu': '1', 'memory': '1Gi'}, 'limits': {'cpu': '1', 'memory': '1Gi'}},
                 'env': env, 'volumeMounts': [{'name': 'work', 'mountPath': '/work'}],
                 'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                     'privileged': False, 'capabilities': {'drop': ['ALL']}}}
    spec = {'automountServiceAccountToken': False, 'enableServiceLinks': False,
            'restartPolicy': 'Never', 'activeDeadlineSeconds': LIVE_BUDGET_S + 60, 'terminationGracePeriodSeconds': 2,
            'securityContext': {'runAsUser': 65532, 'runAsGroup': 65532, 'fsGroup': 65532, 'runAsNonRoot': True},
            'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [
                {'matchFields': [{'key': 'metadata.name', 'operator': 'In', 'values': [node]}]}]}}},
            'containers': [container], 'volumes': [{'name': 'work', 'emptyDir': {'sizeLimit': '256Mi'}}]}
    return {'apiVersion': 'v1', 'kind': 'Pod',
            'metadata': {'name': f'{run}-{site}', 'namespace': NS,
                         'labels': {'experiment': 'sensehat-neural-smoke', 'run-id': run, 'site': site}},
            'spec': spec}


def bundle(prepared, arch):
    """tar stream: numpy wheel for arch + infer.py + model.bin + inputs.json (nothing else)."""
    members = {'numpy.whl': (WHEELHOUSE / arch / WHEEL.format(arch=arch)).read_bytes(),
               'infer.py': (HERE / 'infer.py').read_bytes(),
               'model.bin': (prepared / 'model.bin').read_bytes(), 'inputs.json': (prepared / 'inputs.json').read_bytes()}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue(), {name: {'sha256': sha(d), 'bytes': len(d)} for name, d in members.items()}


def evaluate(report, prepared_doc, model_sha):
    import neural
    reference = prepared_doc['reference_predictions']
    observed = {r['window_id']: r['prediction'] for r in report['requests']}
    timed = [r for r in report['requests'] if r['kind'] == 'evaluation']
    return {'model_sha256_matches': report['model_sha256'] == model_sha,
            'parity_vs_local_reference': neural.parity(reference, observed),
            'timed_forward_wall_ms': [r['forward_wall_ns'] / 1e6 for r in timed],
            'timed_forward_cpu_ms': [r['forward_cpu_ns'] / 1e6 for r in timed],
            'warmup_forward_wall_ms': [r['forward_wall_ns'] / 1e6 for r in report['requests'] if r['kind'] == 'warmup']}


def execute(prepared, kubectl=KUBECTL, artifacts=ARTIFACTS):
    import neural
    prepared = Path(prepared)
    prepared_doc = json.loads((prepared / 'prepared.json').read_text())
    model_sha = sha((prepared / 'model.bin').read_bytes())
    if model_sha != prepared_doc['model']['sha256']:
        raise RuntimeError('model.bin hash differs from prepared.json')
    run = datetime.now(timezone.utc).strftime('nsmoke-%Y%m%dt%H%M%Sz')
    out = Path(artifacts) / run
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(prepared, out / 'prepared')
    (out / 'source').mkdir()
    for name in ('infer.py', 'neural.py', 'smoke.py', 'test_neural.py'):
        shutil.copyfile(HERE / name, out / 'source' / name)
    rec = placement_run.Recorder(out, kubectl)
    start = time.monotonic()

    def left(cap):
        remaining = LIVE_BUDGET_S - (time.monotonic() - start)
        if remaining < 5:
            raise TimeoutError(f'live budget {LIVE_BUDGET_S}s exhausted')
        return min(cap, remaining)

    pods = [pod(run, site) for site in NODES]
    created, results, primary = False, {}, None
    rec.save('design.json', {
        'run_id': run, 'namespace': NS, 'image': IMAGE, 'nodes': NODES, 'live_budget_s': LIVE_BUDGET_S,
        'model_sha256': model_sha, 'scope': 'CPU-only NumPy MLP forward smoke on frozen 2026-10-21 humidity replay; '
        'not live sensor, not HTTP/DAG/network benchmark, not placement comparison; 3 overlapping test windows',
        'timing': 'forward_wall/cpu from perf_counter_ns/thread_time_ns around forward() only; '
                  'kubectl exec wall (includes upload/python start/import/JSON) recorded separately'})
    try:
        with rec.phase('apply'):
            body = json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': pods})
            rec.save('pods.manifest.json', json.loads(body))
            rec.call('apply-dry-run', ['apply', '--dry-run=server', '-f', '-'], body, timeout=left(30))
            created = True
            rec.call('apply', ['apply', '-f', '-'], body, timeout=left(30))
        with rec.phase('ready'):
            rec.call('ready', ['wait', 'pods', '-n', NS, '-l', 'run-id=' + run, '--for=condition=Ready',
                               f'--timeout={int(left(120))}s'], timeout=left(130))
        identity = {}
        for site in NODES:
            identity[site] = json.loads(rec.call(
                'pod-state', ['get', 'pod', '-n', NS, f'{run}-{site}', '-o', 'json'],
                project=lambda raw: json.dumps(placement_run.project_pod(json.loads(raw))), timeout=left(30))[1])
            if identity[site]['node'] != NODES[site][0] or identity[site]['hostNetwork']:
                raise RuntimeError(f'{site} Pod on {identity[site]["node"]}')
        rec.save('pods.json', identity)
        uploads = {}
        for site, (_, arch) in NODES.items():
            data, members = bundle(prepared, arch)
            uploads[site] = {'arch': arch, 'tar_bytes': len(data), 'members': members}
            with rec.phase('upload:' + site, bytes=len(data)):
                rec.call('upload-' + site, ['exec', '-i', '-n', NS, f'{run}-{site}', '--', 'python3', '-c', INSTALL],
                         data, timeout=left(120))
        rec.save('uploads.json', uploads)
        for site in NODES:
            with rec.phase('infer:' + site) as span:
                _, stdout, _, wall = rec.call('infer-' + site, ['exec', '-n', NS, f'{run}-{site}', '--', 'python3',
                                                                '/work/infer.py', '/work/model.bin', '/work/inputs.json'],
                                              timeout=left(90))
                report = json.loads(stdout)
                if (report['node'], report['pod_uid']) != (NODES[site][0], identity[site]['uid']):
                    raise RuntimeError(f'{site} identity mismatch: {report["node"]} {report["pod_uid"]}')
                result = evaluate(report, prepared_doc, model_sha)
                result.update(kubectl_exec_wall_ms=wall, report=report)
                span['status'] = 'ok' if result['model_sha256_matches'] and result['parity_vs_local_reference']['ok'] \
                    else 'mismatch'
                results[site] = result
        cross = neural.parity({r['window_id']: r['prediction'] for r in results['edge']['report']['requests']},
                              {r['window_id']: r['prediction'] for r in results['server']['report']['requests']})
        rec.save('results.json', {'sites': results, 'edge_vs_server_parity': cross,
                                  'live_elapsed_s': time.monotonic() - start})
    except BaseException as error:
        primary = error
        rec.event({'event': 'failure', 'error': repr(error), 'at': placement_run.now()})
    finally:
        errors = cleanup(rec, run, pods if created else [])
    summary = summarize(run, prepared_doc, results, primary, errors, time.monotonic() - start)
    rec.save('summary.json', summary)
    print(json.dumps(summary, indent=2))
    if primary is not None:
        raise primary
    return 0 if summary['status'] == 'ok' else 1


def cleanup(rec, run, pods):
    errors = []
    with rec.phase('cleanup') as span:
        for item in pods:
            try:
                rec.call('logs-' + item['metadata']['labels']['site'], ['logs', '-n', NS, item['metadata']['name']],
                         timeout=30, check=False)
            except Exception as error:
                errors.append('logs: ' + repr(error))
        if pods:
            names = [p['metadata']['name'] for p in pods]
            try:
                rec.call('delete', ['delete', 'pod', '-n', NS, *names, '--ignore-not-found=true', '--wait=false'],
                         timeout=40)
                rec.call('deleted', ['wait', '--for=delete', 'pod', '-n', NS, *names, '--timeout=90s'],
                         timeout=100, check=False)
            except Exception as error:
                errors.append('delete: ' + repr(error))
        try:
            left = rec.call('remaining', ['get', 'pods,configmaps', '-n', NS, '-l', 'run-id=' + run, '-o', 'name'],
                            timeout=30)[1].split()
            if left:
                errors.append(f'remaining owned resources: {left}')
            span['remaining'] = left
        except Exception as error:
            errors.append('verify: ' + repr(error))
        span.update(status='error' if errors else 'ok', errors=errors)
    return errors


def summarize(run, prepared_doc, results, primary, errors, elapsed):
    ok = primary is None and not errors and len(results) == 2 and all(
        r['model_sha256_matches'] and r['parity_vs_local_reference']['ok'] for r in results.values())
    return {'run_id': run, 'status': 'ok' if ok else 'failed', 'error': repr(primary) if primary else None,
            'cleanup_errors': errors, 'live_elapsed_including_cleanup_s': elapsed,
            'model': prepared_doc['model'], 'test_mae': prepared_doc['test'], 'training': prepared_doc['training'],
            'sites': {site: {k: r[k] for k in ('model_sha256_matches', 'parity_vs_local_reference',
                                                'warmup_forward_wall_ms', 'timed_forward_wall_ms',
                                                'timed_forward_cpu_ms', 'kubectl_exec_wall_ms')}
                      | {k: r['report'][k] for k in ('node', 'pod_uid', 'python', 'numpy', 'machine')}
                      for site, r in results.items()}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--execute', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--prepared', type=Path)
    args = parser.parse_args(argv)
    if args.prepare:
        import neural
        summary = neural.prepare(HISTORY, args.output)
        print(json.dumps({k: summary[k] for k in ('model', 'training', 'test', 'selected')}, indent=2))
        return 0
    return execute(args.prepared)


if __name__ == '__main__':
    sys.exit(main())
