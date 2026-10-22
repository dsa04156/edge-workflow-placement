"""Owned real-AI G1/G3 smoke; no policy superiority or network-cap claims.

Frozen first calibration pair for warmup; first five evaluation pairs x2.
All phase events and partial stdout/stderr survive timeouts. No old artifacts
are overwritten. Resources are removed in finally, namespace retained.
"""
import base64
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ASSETS = ROOT / 'artifacts/topology-profile-live'
IMAGE = '10.254.192.47:5000/node-inventory-python@sha256:370a8339507905053693d178e1cda8fbf77937f045ead58a3c32d8cead2ca67f'
NODES = {'e0': 'etri-dev0003-raspi5', 'e1': 'etri-dev0002-raspi5', 'c': 'etri-ser0002-cgnmsb'}
NS = 'topology-profile-lab'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--placement', choices=['edge', 'default_scheduler'], default='edge')
    mode = parser.parse_args().placement
    run = datetime.now(timezone.utc).strftime('tp-%Y%m%dt%H%M%S')
    out = ASSETS / run
    out.mkdir(exist_ok=False)
    (out / 'collector-source').mkdir()
    for source in HERE.glob('*.py'):
        (out / 'collector-source' / source.name).write_bytes(source.read_bytes())
    counter = 0
    resources = []

    def save(name, value):
        path = out / name
        with path.open('x') as handle:
            json.dump(value, handle, indent=2)
            handle.write('\n')

    def call(stem, args, stdin=None, timeout=30):
        nonlocal counter
        counter += 1
        tag = f'{counter:03d}-' + stem
        cmd = ['rtk', 'proxy', 'kubectl', '--request-timeout=20s', *args]
        event = {'started_at': datetime.now(timezone.utc).isoformat(), 'command': cmd, 'timeout_s': timeout}
        try:
            with (out / (tag + '.stdout')).open('wb') as so, (out / (tag + '.stderr')).open('wb') as se:
                result = subprocess.run(cmd, input=stdin.encode() if isinstance(stdin, str) else stdin,
                                        stdout=so, stderr=se, timeout=timeout)
            event.update(status='completed', returncode=result.returncode)
            if result.returncode:
                raise RuntimeError(tag + ' failed; inspect saved stderr')
            return (out / (tag + '.stdout')).read_text()
        except BaseException as error:
            event.update(status='timeout' if isinstance(error, subprocess.TimeoutExpired) else 'error', error=repr(error))
            raise
        finally:
            event['finished_at'] = datetime.now(timezone.utc).isoformat()
            save(tag + '.event.json', event)

    def apply(stem, items):
        value = {'apiVersion': 'v1', 'kind': 'List', 'items': items}
        save(stem + '.manifest.json', value)
        call(stem + '-dry-run', ['apply', '--dry-run=server', '-f', '-'], json.dumps(value))
        resources.extend(items)
        call(stem, ['apply', '-f', '-'], json.dumps(value))

    def meta(name):
        return {'name': name, 'namespace': NS, 'labels': {'experiment': 'topology-profile', 'run-id': run}}

    def pod(name, node, role, endpoint=None):
        source = role == 'source'
        cache = role == 'cache'
        command = ['python3', '-u', '/config/bootstrap.py']
        if cache:
            command = ['python3', '-u', '-m', 'http.server', '8080', '--directory', '/work']
        container = {'name': 'worker', 'image': IMAGE, 'imagePullPolicy': 'IfNotPresent', 'command': command,
                     'resources': {'requests': {'cpu': '100m' if source or cache else '1',
                                               'memory': '256Mi' if source or cache else '2Gi', 'ephemeral-storage': '128Mi'},
                                   'limits': {'cpu': '1', 'memory': '512Mi' if source or cache else '2Gi', 'ephemeral-storage': '8Gi'}},
                     'env': [{'name': 'NODE_NAME', 'valueFrom': {'fieldRef': {'fieldPath': 'spec.nodeName'}}},
                             {'name': 'POD_UID', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.uid'}}},
                             {'name': 'TASK_ID', 'value': role}, {'name': 'PYTHONDONTWRITEBYTECODE', 'value': '1'},
                             {'name': 'HF_HUB_OFFLINE', 'value': '1'}, {'name': 'TRANSFORMERS_OFFLINE', 'value': '1'}],
                     'volumeMounts': [{'name': 'work', 'mountPath': '/work'}],
                     'readinessProbe': {'httpGet': {'path': '/' if cache else '/health', 'port': 8080},
                                        'initialDelaySeconds': 2, 'periodSeconds': 10, 'timeoutSeconds': 3},
                     'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                         'capabilities': {'drop': ['ALL']}}}
        if not cache:
            container['volumeMounts'].append({'name': 'config', 'mountPath': '/config', 'readOnly': True})
            container['env'].append({'name': 'ASSET_URL', 'value': endpoint})
        container['env'].append({'name': 'TMPDIR', 'value': '/work/tmp'})
        # TMPDIR parent exists before pip starts; model code is in immutable ConfigMap.
        if not cache:
            container['command'] = ['python3', '-u', '-c',
                "import os,runpy;os.makedirs('/work/tmp',exist_ok=True);runpy.run_path('/config/bootstrap.py',run_name='__main__')"]
        candidates = [node]
        if mode == 'default_scheduler' and role in ['vision', 'speech']:
            candidates = [NODES['e0' if role == 'vision' else 'e1'], NODES['c']]
        spec = {'schedulerName': 'default-scheduler', 'automountServiceAccountToken': False, 'restartPolicy': 'Never',
                'activeDeadlineSeconds': 1800, 'terminationGracePeriodSeconds': 3,
                'securityContext': {'runAsUser': 65532, 'runAsGroup': 65532, 'runAsNonRoot': True, 'fsGroup': 65532},
                'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [
                    {'matchFields': [{'key': 'metadata.name', 'operator': 'In', 'values': [candidate]}]} for candidate in candidates]}}},
                'containers': [container], 'volumes': [{'name': 'work', 'emptyDir': {'sizeLimit': '8Gi'}}]}
        if not cache:
            spec['volumes'].append({'name': 'config', 'configMap': {'name': run}})
        return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': meta(name), 'spec': spec}

    def poddata(name):
        return json.loads(call('pod-state', ['get', 'pod', '-n', NS, name, '-o', 'json']))

    def edge(code):
        raw = call('edge-operation', ['exec', '-n', NS, run + '-source-e0', '--', 'python3', '-c', code], timeout=120)
        return json.loads(raw)

    # Capture only selected status fields, never broad Pod env/secret data.
    before = json.loads(subprocess.check_output(['rtk', 'proxy', 'kubectl', '--request-timeout=15s',
                                                'get', 'pods', '-A', '-o', 'json'], text=True, timeout=25))
    protected = {p['metadata']['uid']: {'name': p['metadata']['name'], 'namespace': p['metadata']['namespace'],
                  'phase': p['status'].get('phase'), 'ready': next((c['status'] for c in p['status'].get('conditions', []) if c['type'] == 'Ready'), None)}
                 for p in before['items'] if p['status'].get('phase') == 'Running'}
    save('protected-before.json', protected)
    save('design.json', {'run_id': run, 'placement_mode': mode, 'scope': 'real-AI native deployment smoke; sequential placements, not controlled policy benchmark',
                        'nodes': NODES, 'warmups': 2, 'evaluation_inputs': 5, 'repeats': 2,
                        'input_selection': 'first calibration pair; first5evaluation pairs, fixed before deployment',
                        'quality': {'feature_atol': 1e-5, 'feature_rtol': 1e-4, 'transcript': 'exact',
                                    'answer_id': 'exact', 'score_atol': 1e-4},
                        'source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.glob('*.py')}})
    save('bundle-manifest.json', json.loads((ASSETS / 'bundles.json').read_text()))
    actual = {}
    try:
        cache_name = run + '-cache'
        apply('cache', [pod(cache_name, NODES['c'], 'cache')])
        call('cache-ready', ['wait', 'pod', '-n', NS, cache_name, '--for=condition=Ready', '--timeout=90s'], timeout=100)
        cache = poddata(cache_name)
        cache_url = 'http://' + cache['status']['podIP'] + ':8080'
        event = {'started_at': datetime.now(timezone.utc).isoformat(), 'scope': 'cache upload; deployment cost, not inference'}
        try:
            with (ASSETS / 'cache-upload.tar').open('rb') as source, (out / 'upload.stdout').open('wb') as so, (out / 'upload.stderr').open('wb') as se:
                r = subprocess.run(['rtk', 'proxy', 'kubectl', '--request-timeout=20s', 'exec', '-i', '-n', NS, cache_name,
                                    '--', 'python3', '-c', "import tarfile,sys;tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall('/work',filter='data')"],
                                   stdin=source, stdout=so, stderr=se, timeout=240)
                event['returncode'] = r.returncode
                if r.returncode:
                    raise RuntimeError('cache upload failed')
        finally:
            event['finished_at'] = datetime.now(timezone.utc).isoformat()
            save('upload.event.json', event)
        cm = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': meta(run),
              'data': {name: (HERE / name).read_text() for name in ['http_runtime.py', 'model_adapter.py', 'edge_client.py', 'bootstrap.py']}}
        cm['data']['bundles.json'] = (ASSETS / 'bundles.json').read_text()
        apply('workers', [cm, pod(run + '-source-e0', NODES['e0'], 'source', cache_url),
                          pod(run + '-source-e1', NODES['e1'], 'source', cache_url),
                          pod(run + '-vision', NODES['e0'], 'vision', cache_url),
                          pod(run + '-speech', NODES['e1'], 'speech', cache_url),
                          pod(run + '-fusion', NODES['c'], 'fusion', cache_url)])
        call('all-ready', ['wait', 'pods', '-n', NS, '-l', 'run-id=' + run, '--for=condition=Ready', '--timeout=360s'], timeout=370)
        for role in ['vision', 'speech', 'fusion', 'source-e0', 'source-e1']:
            actual[role] = poddata(run + '-' + role)
        endpoints = {role: 'http://' + actual[role]['status']['podIP'] + ':8080' for role in ['vision', 'speech', 'fusion']}
        save('actual-pods.json', actual)
        for role, endpoint in endpoints.items():
            print(role, edge('import sys,json;sys.path.insert(0,"/config");from http_runtime import request;print(json.dumps(request(' + repr(endpoint + '/health') + ',{})))'), flush=True)
        corpus = json.loads((ASSETS / 'pod-pairs.json').read_text())['pairs']
        cal = next(p for p in corpus if p['split'] == 'calibration')
        selected = [p for p in corpus if p['split'] == 'evaluation'][:5]
        refs = {r['question_id']: r for r in map(json.loads, (ASSETS / 'reference-v1/results.jsonl').read_text().splitlines())}
        reference_models = json.loads((ASSETS / 'reference-v1/metadata.json').read_text())['stages']
        qualifications = []
        for pair in [cal, *selected]:
            q = pair['question_id']
            code = 'import sys,json,base64;sys.path.insert(0,"/config");from http_runtime import request;from pathlib import Path;'
            code += 'print(json.dumps({' + ','.join(repr(t) + ':request(' + repr(endpoints[t] + '/infer') + ',{"run_id":' + repr(run) + ',"request_id":' + repr('q-' + str(q) + '-' + t) + ',"data":base64.b64encode(Path(' + repr(pair[k + '_path']) + ').read_bytes()).decode()})' for t, k in [('vision', 'image'), ('speech', 'audio')]) + '}))'
            reply = edge(code)
            feature = base64.b64decode(reply['vision']['data'])
            expected = (ASSETS / 'reference-v1' / (str(q) + '.npy')).read_bytes()
            # Both adapters assert 512xfloat32 NPY; compare the tensor payload.
            v, e = struct.unpack('<512f', feature[-2048:]), struct.unpack('<512f', expected[-2048:])
            feature_ok = all(abs(a-b) <= 1e-5 + 1e-4 * abs(b) for a, b in zip(v, e))
            speech = base64.b64decode(reply['speech']['data']).decode()
            record = {'question_id': q, 'feature_ok': feature_ok, 'feature_max_abs_error': max(abs(a-b) for a,b in zip(v,e)),
                      'transcript': speech, 'transcript_ok': speech == refs[q]['transcript'],
                      'vision_output_sha256': reply['vision']['receipt']['output_sha256'],
                      'speech_output_sha256': reply['speech']['receipt']['output_sha256'], 'receipts': reply}
            qualifications.append(record)
        save('qualification.json', qualifications)
        if not all(q['feature_ok'] and q['transcript_ok'] for q in qualifications):
            raise RuntimeError('Quality gate failed; retain all input results')
        qualifies = {q['question_id']: q for q in qualifications}
        attempts = []
        trajectory = [(cal, True)] * 2 + [(p, False) for _ in range(2) for p in selected]
        with (out / 'attempts.jsonl').open('x') as handle:
            for i, (pair, warmup) in enumerate(trajectory):
                control = {'run_id': run, 'request_id': f'r{i:03d}', 'question_id': pair['question_id'],
                           'endpoints': endpoints, 'remote_source': 'http://' + actual['source-e1']['status']['podIP'] + ':8080'}
                row = dict(control, warmup=warmup)
                try:
                    result = edge('import sys,json;sys.path.insert(0,"/config");from http_runtime import request;print(json.dumps(request("http://127.0.0.1:8080/run",' + repr(control) + ',timeout=90)))')
                    receipts = {r['task']: r for r in result['receipts']}
                    assert set(receipts) == {'vision', 'speech', 'fusion'}
                    for role, receipt in receipts.items():
                        assert receipt['pod_uid'] == actual[role]['metadata']['uid']
                        assert receipt['node'] == actual[role]['spec']['nodeName']
                        assert receipt['scope'] == 'real_ai_file_replay' and receipt['device'] == 'cpu'
                        assert receipt['model_sha256'] == reference_models[role]['model_sha256']
                    assert receipts['vision']['input_sha256']['source'] == pair['image_sha256']
                    assert receipts['speech']['input_sha256']['source'] == pair['audio_sha256']
                    assert result['source_node'] == NODES['e0'] and result['speech_source_node'] == NODES['e1']
                    assert result['speech_source_payload_sha256'] == pair['audio_sha256']
                    for role in ['vision', 'speech']:
                        assert receipts['fusion']['input_sha256'][role] == receipts[role]['output_sha256']
                        assert receipts[role]['output_sha256'] == qualifies[pair['question_id']][role + '_output_sha256']
                    row.update(result, protocol_ok=True,
                               quality_ok=result['answer_id_matches'] and result['max_score_abs_error'] <= 1e-4)
                except Exception as error:
                    row.update(status='error', error=repr(error), quality_ok=False)
                attempts.append(row)
                handle.write(json.dumps(row) + '\n')
                handle.flush()
                print('attempt', i, row['status'], row.get('e2e_ms'), row.get('quality_ok'), flush=True)
        measured = [r for r in attempts if not r['warmup']]
        valid = [r for r in measured if r['status'] == 'ok' and r['quality_ok']]
        summary = {'run_id': run, 'placement_mode': mode,
                   'actual_placement': {r: actual[r]['spec']['nodeName'] for r in endpoints},
                   'scope': 'real-AI native deployment smoke; sequential placements, not controlled policy benchmark',
                   'attempts': len(attempts), 'measured': len(measured), 'valid_measured': len(valid),
                   'failures': sum(r['status'] != 'ok' for r in attempts),
                   'quality_failures': sum(not r.get('quality_ok', False) for r in attempts),
                   'top5_order_matches': sum(r.get('top5_order_matches', False) for r in attempts),
                   'mean_success_e2e_ms': sum(r['e2e_ms'] for r in valid) / len(valid) if valid else None,
                   'receipts': sum(len(r.get('receipts', [])) for r in attempts)}
        save('summary.json', summary)
        print(json.dumps(summary), flush=True)
    finally:
        cleanup_errors = []
        try:
            call('events', ['get', 'events', '-n', NS, '-o', 'json'])
        except Exception as error:
            cleanup_errors.append('events: ' + repr(error))
        for item in resources:
            if item['kind'] == 'Pod':
                try:
                    call('logs-' + item['metadata']['name'], ['logs', '-n', NS, item['metadata']['name']])
                except Exception:
                    pass  # Failure captured in its phase files; cleanup continues.
        if resources:
            try:
                call('cleanup', ['delete', '-f', '-', '--ignore-not-found=true', '--wait=false'],
                     json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': resources}))
                call('deleted', ['wait', '--for=delete', 'pod', '-n', NS, '-l', 'run-id=' + run, '--timeout=60s'], timeout=70)
            except Exception as error:
                cleanup_errors.append('delete: ' + repr(error))
        remaining = None
        try:
            left = json.loads(call('remaining', ['get', 'pods,configmaps', '-n', NS, '-l', 'run-id=' + run, '-o', 'json']))
            remaining = len(left['items'])
        except Exception as error:
            cleanup_errors.append('verify: ' + repr(error))
        try:
            raw = subprocess.check_output(['rtk', 'proxy', 'kubectl', '--request-timeout=15s', 'get', 'pods', '-A', '-o', 'json'], text=True, timeout=25)
            after = {p['metadata']['uid']: {'phase': p['status'].get('phase'), 'ready': next((c['status'] for c in p['status'].get('conditions', []) if c['type'] == 'Ready'), None)} for p in json.loads(raw)['items']}
            changes = [{'uid': uid, 'before': before, 'after': after.get(uid)} for uid, before in protected.items()
                       if uid not in after or before['phase'] != after[uid]['phase'] or before['ready'] != after[uid]['ready']]
            save('protected-after.json', {'protected_count': len(protected), 'changes': changes,
                                         'note': 'Changes are observations; do not infer this experiment caused them'})
        except Exception as error:
            cleanup_errors.append('protected-after: ' + repr(error))
        save('cleanup.json', {'remaining_owned_pods_and_configmaps': remaining, 'diagnostic_or_cleanup_errors': cleanup_errors})
        print('evidence', out, flush=True)
        if remaining != 0:
            raise RuntimeError('Owned resource cleanup not verified; inspect cleanup evidence')


if __name__ == '__main__':
    main()
