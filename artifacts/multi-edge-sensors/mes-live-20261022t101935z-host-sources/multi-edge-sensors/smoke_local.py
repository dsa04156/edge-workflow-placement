"""Local real-protocol/model smoke: the in-Pod bootstrap and runtime as local processes, no cluster.

Starts a cache HTTP server over the prepared bundles, then one process per role
(1 video source, 1 text source, V, T, F) through bootstrap.py with a plain
CPython 3.11 (hash-pinned offline pip install into each work dir), on separate
loopback IPs. Collects MediaMTX/publisher evidence, an RTSP probe from the V
worker, WARMUP+EVALUATION driven workflows, and compares every stage output
with an in-process CPU reference on the same frozen inputs. Writes a NEW
directory; x86_64 only, so it says nothing about arm64 edges or cluster timing.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import models  # noqa: E402

CODE = ('runtime.py', 'models.py', 'rtsp.py', 'scenario.py', 'bootstrap.py')
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(url, body=None, timeout=120):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'} if data else {})
    with OPENER.open(request, timeout=timeout) as response:
        return response.read()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--python', default=shutil.which('python3.11'), help='plain CPython 3.11 with pip (stands in for the image)')
    parser.add_argument('--output', type=Path, help='new directory (default artifacts/multi-edge-sensors/local-smoke-<utc>)')
    parser.add_argument('--port', type=int, default=18080)
    parser.add_argument('--workflow', default='w1')
    args = parser.parse_args(argv)
    out = (args.output or ROOT/'artifacts/multi-edge-sensors'/datetime.now(timezone.utc).strftime('local-smoke-%Y%m%dt%H%M%Sz')).resolve()
    out.mkdir(parents=True, exist_ok=False)
    prepared = json.loads((args.prepared/'prepared.json').read_text())
    sc = prepared['scenario']['content']
    flow = next(f for f in sc['workflows'] if f['id'] == args.workflow)
    events = []

    def event(name, **kw):
        events.append(dict(event=name, at=datetime.now(timezone.utc).isoformat(), monotonic=time.monotonic(), **kw))
        print(json.dumps(events[-1]), flush=True)

    def save(name, value):
        with (out/name).open('x') as handle:
            json.dump(value, handle, indent=1)

    config = out/'config'
    config.mkdir()
    for name in CODE:
        shutil.copyfile(HERE/name, config/name)
    (config/'scenario.json').write_text(json.dumps(sc))
    (config/'bundles.json').write_text(json.dumps({k: dict(sha256=v['sha256'], bytes=v['bytes']) for k, v in prepared['bundles'].items()}))
    processes = {}
    roles = {'video': dict(ROLE='video-source', SOURCE_ID=flow['video'], ip='127.0.3.11'),
             'text': dict(ROLE='text-source', SOURCE_ID=flow['text'], ip='127.0.3.12'),
             'V': dict(ROLE='worker', TASK_ID='V', THREADS='2', ip='127.0.3.13'),
             'T': dict(ROLE='worker', TASK_ID='T', THREADS='1', ip='127.0.3.14'),
             'F': dict(ROLE='worker', TASK_ID='F', THREADS='1', ip='127.0.3.15')}
    cache_ip = '127.0.3.10'
    summary = dict(scope='local x86_64 smoke of the Pod bootstrap/runtime path; not cluster evidence, not a placement comparison',
                   prepared=prepared['name'], workflow=flow, python=args.python)
    try:
        event('cache-start')
        processes['cache'] = subprocess.Popen([sys.executable, '-m', 'http.server', str(args.port), '--bind', cache_ip, '--directory',
                                               str(args.prepared/'bundles')], stdout=(out/'cache.log').open('wb'), stderr=subprocess.STDOUT,
                                              start_new_session=True)
        version = subprocess.run([args.python, '-c', 'import sys,platform;print(sys.version.split()[0], platform.machine())'],
                                 capture_output=True, text=True, check=True).stdout.strip()
        summary['pod_python'] = version
        for name, role in roles.items():
            work = out/'pods'/name/'work'
            (work/'tmp').mkdir(parents=True)
            env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'LANG')}
            env.update({k: v for k, v in role.items() if k != 'ip'}, POD_IP=role['ip'], BIND_IP=role['ip'], PORT=str(args.port),
                       NODE_NAME='local-smoke-host', POD_UID=str(uuid.uuid4()), ASSET_ROOT=str(work), TMPDIR=str(work/'tmp'),
                       ASSET_URL=f'http://{cache_ip}:{args.port}', PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS=role.get('THREADS', '1'))
            role['uid'] = env['POD_UID']
            event('bootstrap-start', role=name)
            processes[name] = subprocess.Popen([args.python, '-u', str(config/'bootstrap.py')], cwd=str(work), env=env,
                                               stdout=(out/f'{name}.log').open('wb'), stderr=subprocess.STDOUT, start_new_session=True)
        ready = {}
        deadline = time.monotonic() + 600
        while len(ready) < len(roles) and time.monotonic() < deadline:
            for name, role in roles.items():
                if name in ready:
                    continue
                if processes[name].poll() is not None:
                    raise RuntimeError(f'{name} exited rc={processes[name].returncode}; see {name}.log')
                try:
                    ready[name] = json.loads(http(f"http://{role['ip']}:{args.port}/health", timeout=3))
                    event('ready', role=name)
                except Exception:
                    pass  # Not serving or 503 until bootstrap/model load/freeze finish.
            time.sleep(1)
        if len(ready) < len(roles):
            raise RuntimeError('roles not ready: '+repr(sorted(set(roles)-set(ready))))
        save('health.json', ready)
        url = {name: f"http://{role['ip']}:{args.port}" for name, role in roles.items()}
        evidence = json.loads(http(url['video']+'/evidence'))
        save('video-source-evidence.json', evidence)
        event('rtsp-probe-start')
        probe = json.loads(http(url['V']+'/rtsp-probe', dict(url=evidence['rtsp_url'], wanted=list(range(sc['stream']['loop_frames'])),
                                                              max_frames=3*sc['stream']['loop_frames'], timeout_s=30)))
        save('rtsp-probe.json', probe)
        event('rtsp-probe-end', status=probe['status'])
        seqs = [sc['inputs']['calibration'][0]] + sc['inputs']['evaluation']
        rows = []
        for i, seq in enumerate(seqs):
            control = dict(request_id=f'smoke-{i}', seq=seq, F=url['F'], V=url['V'], T=url['T'], video_source=url['video'], text_source=url['text'])
            result = subprocess.run([args.python, str(config/'runtime.py'), 'drive', json.dumps(control)], capture_output=True, text=True,
                                    env=dict(POD_IP=roles['video']['ip'], PORT=str(args.port), NODE_NAME='local-smoke-host',
                                             POD_UID=roles['video']['uid'], ROLE='video-source', PATH=os.environ['PATH']), timeout=300)
            row = json.loads(result.stdout.strip().splitlines()[-1])
            row['warmup'] = i == 0
            rows.append(row)
            event('workflow', seq=seq, status=row['status'], e2e_ms=row.get('e2e_ms'))
        with (out/'attempts.jsonl').open('x') as handle:
            handle.write(''.join(json.dumps(r)+'\n' for r in rows))
        # In-process CPU reference on the same frozen frame bytes and prepared sentences.
        import tarfile
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(args.prepared/'bundles/models.tar') as archive:
                archive.extractall(tmp, filter='data')
            names = json.loads((Path(tmp)/'models/class-names.json').read_text())
            detector = models.Detector(Path(tmp)/'models/yolo11n.onnx', 1)
            embedder = models.Embedder(str(Path(tmp)/'models/minilm'), 1, names)
            checks = []
            for row in rows:
                seq = row['seq']
                png = http(f"{url['video']}/frame?seq={seq}")
                rgb = models.decode_png(png)
                text = prepared['inputs']['text'][flow['text']][str(seq)]['text']
                ref = dict(V=detector(rgb), T=embedder(text))
                ref['F'] = models.fuse(ref['V'], ref['T'], names)
                outputs = row.get('outputs', {})
                problems = {t: models.compare(t, outputs.get(f"{row['request_id']}/{t}"), ref[t]) if outputs.get(f"{row['request_id']}/{t}") is not None
                            else ['missing'] for t in 'VTF'}
                receipts = {r['task']: r for r in row.get('receipts', [])}
                checks.append(dict(seq=seq, frame_png_sha256=hashlib.sha256(png).hexdigest(),
                                   frame_matches_freeze=hashlib.sha256(png).hexdigest() == evidence['freeze']['frozen'][str(seq)]['png_sha256'],
                                   v_receipt_input_matches=receipts.get('V', {}).get('input_payload_sha256') == hashlib.sha256(png).hexdigest(),
                                   t_receipt_input_matches=receipts.get('T', {}).get('input_sha256') == hashlib.sha256(text.encode()).hexdigest(),
                                   detections=[d['name'] for d in ref['V']], text=text, text_top_class=ref['T']['text_top_class'],
                                   fused=ref['F'], problems=problems))
        save('reference-check.json', checks)
        frozen_equal = [probe['frame_sha256'].get(s, [None])[0] == f['rgb_sha256'] for s, f in evidence['freeze']['frozen'].items()]
        summary.update(status='passed' if all(r['status'] == 'ok' for r in rows) and probe['status'] == 'ok'
                       and not any(p for c in checks for p in c['problems'].values()) and all(c['frame_matches_freeze'] for c in checks)
                       else 'failed',
                       rtsp=dict(codec=probe['codec'], setup_ms=probe['setup_ms'], decoded_frames=probe['decoded_frames'],
                                 skipped_frames=probe['skipped_frames'], loop_consistent=all(probe['loop_consistent'].values()),
                                 probe_equals_freeze=f'{sum(frozen_equal)}/{len(frozen_equal)}',
                                 mediamtx_paths=evidence.get('mediamtx_paths'),
                                 processes=dict(mediamtx=evidence['mediamtx'], publisher=evidence['publisher'])),
                       workflows=[dict(seq=r['seq'], warmup=r['warmup'], status=r['status'], e2e_ms=r.get('e2e_ms'),
                                       stage_ms={x['task']: x.get('compute_ms') for x in r.get('receipts', [])},
                                       fetch_ms={x['task']: x.get('fetch_ms') for x in r.get('receipts', []) if 'fetch_ms' in x},
                                       result=r.get('result', {}).get('top_class'), alert=r.get('result', {}).get('alert')) for r in rows],
                       quality_rows_without_violation=sum(not any(c['problems'].values()) for c in checks), quality_rows=len(checks))
    except BaseException as error:
        summary.update(status='error', error=repr(error))
        raise
    finally:
        for name, process in processes.items():
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for process in processes.values():
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        summary['events'] = events
        save('summary.json', summary)
        for name in roles:
            shutil.rmtree(out/'pods'/name/'work', ignore_errors=True)  # Installed wheels/models; logs stay.
        print(json.dumps({k: v for k, v in summary.items() if k != 'events'}, indent=1))
    return 0 if summary['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
