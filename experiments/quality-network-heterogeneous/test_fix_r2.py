"""R2 regression tests (fix-r2-spec.md 1-6). Fake receipts, cores, sessions and clocks are plumbing evidence only;
no accelerator, kubelet or device behaviour is demonstrated here."""
import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import types
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import catalog  # noqa: E402
import proc  # noqa: E402
import qualify  # noqa: E402
import stages  # noqa: E402
import worker as worker_module  # noqa: E402
from test_fix_r1 import GPU, PAIRS, REFS, r2_workers, valid_rows  # noqa: E402

AUDIT = catalog.load_audit()
digest = lambda b: hashlib.sha256(b).hexdigest()


class F1ExactGate(unittest.TestCase):
    def test_full_valid_set_plus_one_duplicate_or_foreign_row_never_qualifies(self):
        for extra in ('duplicate', 'foreign'):
            rows = valid_rows(GPU, 'vision', 'uid-1')
            row = copy.deepcopy(rows[0])
            if extra == 'foreign':
                row['receipt']['pod_uid'] = 'someone-else'
            rows.append(row)
            r = qualify.stage_result(GPU, 'vision', rows, 'uid-1', REFS)
            self.assertEqual(r['coverage']['valid_unique'], 60, extra)
            self.assertNotEqual(r['execution'], 'executed', extra)
            self.assertEqual(len(r['invalid_rows']), 1, extra)
            self.assertEqual(qualify.cost_table({('etri-dev0007-jetagx', 'cuda-torch', 'vision'): r}), {}, extra)

    def test_missing_creation_uid_is_never_identity_proof(self):
        rows = valid_rows(GPU, 'vision', None)
        r = qualify.stage_result(GPU, 'vision', rows, None, REFS)
        self.assertEqual(r['execution'], 'runtime_failed')
        self.assertEqual(r['invalid_reasons'], {'missing_creation_uid': 60})

    def composition_rows(self, uids):
        workers = dict(vision=dict(key='npu-c', node='etri-ser0002-cgnmsb', backend='openvino-npu'),
                       speech=dict(key='jgpu-dev0007-cu130', node='etri-dev0007-jetagx', backend='cuda-torch'),
                       fusion=dict(key='cpu-c', node='etri-ser0002-cgnmsb', backend='cpu-torch'))
        name = 'npu-c/jgpu-dev0007-cu130/cpu-c'
        slug = name.replace('/', '_')
        rows = []
        for index, pair in enumerate(PAIRS):
            q = pair['question_id']
            feature = (qualify.REFERENCE/f'{q}.npy').read_bytes()
            transcript = REFS[q]['transcript']
            fusion = json.dumps(dict(answers=REFS[q]['answers'], answer_ids=REFS[q]['answer_ids'], scores=REFS[q]['scores'])).encode()
            branch = lambda stage, w, data, inp, **t: dict(status='ok', data=base64.b64encode(data).decode(), receipt=dict(
                stage=stage, backend=w['backend'], device={'openvino-npu': 'NPU', 'cuda-torch': 'cuda:0', 'cpu-torch': 'cpu'}[w['backend']],
                node=w['node'], pod_uid=uids.get(w['key']), request_id=f'e2e-{slug}-{stage}-{index}', input_sha256=inp, output_sha256=digest(data),
                timing=t))
            rows.append(dict(index=index, question_id=q, split=pair['split'], status='ok', transcript=transcript, branches=dict(
                vision=branch('vision', workers['vision'], feature, qualify.expected_input_sha('vision', 'openvino-npu', pair, REFS),
                              execution_devices=['NPU']),
                speech=branch('speech', workers['speech'], transcript.encode(), qualify.expected_input_sha('speech', 'cuda-torch', pair, REFS),
                              output_device='cuda:0'),
                fusion=branch('fusion', workers['fusion'], fusion, dict(vision=digest(feature), speech=digest(transcript.encode()))))))
        return name, workers, rows

    def test_composition_exact_gate_and_missing_uid(self):
        uids = {'npu-c': 'u1', 'jgpu-dev0007-cu130': 'u2', 'cpu-c': 'u3'}
        name, workers, rows = self.composition_rows(uids)
        self.assertTrue(qualify.composition_result(name, rows, workers, uids, REFS)['complete'])
        extra = qualify.composition_result(name, rows + [copy.deepcopy(rows[0])], workers, uids, REFS)
        self.assertEqual((extra['valid_rows'], extra['complete'], extra['invalid_reasons']), (60, False, {'duplicate_question': 1}))
        foreign = copy.deepcopy(rows[1])
        foreign['question_id'] = 999999
        self.assertFalse(qualify.composition_result(name, rows + [foreign], workers, uids, REFS)['complete'])
        name, workers, rows = self.composition_rows({})
        missing = qualify.composition_result(name, rows, workers, {}, REFS)
        self.assertEqual((missing['valid_rows'], missing['invalid_reasons']), (0, {'missing_creation_uid': 60}))


class F2InterruptedLogs(unittest.TestCase):
    def test_truncated_last_receipt_line_is_invalid_evidence_and_analysis_completes(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)/'qnh-t'
            run.mkdir()
            workers = r2_workers()
            (run/'wave.json').write_text(json.dumps(dict(workers=workers, assets_manifest_sha256='x')))
            (run/'workers.created.json').write_text(json.dumps(dict(confirmed=[dict(kind='Pod', name='qnh-t-'+w['key'], uid='uid-1') for w in workers])))
            gpu = next(w for w in workers if w['key'] == 'jgpu-dev0007-cu130')
            rows = valid_rows(gpu, 'vision', 'uid-1')
            text = ''.join(json.dumps(r)+'\n' for r in rows[:59]) + json.dumps(rows[59])[:120]  # interrupted final write
            path = run/'receipts-jgpu-dev0007-cu130-vision.jsonl'
            path.write_text(text)
            before = digest(path.read_bytes())
            out = qualify.analyze(run)
            r = out['stage_results']['etri-dev0007-jetagx|cuda-torch|vision']
            self.assertEqual(r['execution'], 'partial')
            self.assertEqual(r['invalid_reasons'], {'malformed_row': 1})
            bad = next(x for x in r['invalid_rows'] if x['reason'] == 'malformed_row')
            self.assertEqual(bad['line'], 60)
            self.assertTrue(bad['fragment'])
            self.assertTrue((run/'catalog.json').exists())
            self.assertEqual(digest(path.read_bytes()), before)

    def test_truncated_composition_line_blocks_completeness(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'c.jsonl'
            path.write_text('{"a": 1}\n{"question_id": 5, "spl')
            rows, malformed = qualify.read_jsonl(path)
            self.assertEqual((len(rows), malformed[0]['line']), (1, 2))


class FailingCore:
    available_devices = ['NPU']

    def read_model(self, path):
        class Model:
            inputs = ['in']

            def reshape(self, shape):
                pass
        return Model()

    def compile_model(self, model, device):
        raise RuntimeError('NPU compiler: unsupported operation GroupNorm')


class F3CompilePhasePlumbing(unittest.TestCase):
    def test_request_time_compile_failure_reaches_analysis_as_compile_failed(self):
        from transformers import Wav2Vec2Processor
        processor = Wav2Vec2Processor.from_pretrained(qualify.ASSETS/'model-assets/asr', local_files_only=True)
        with tempfile.TemporaryDirectory() as tmp:
            onnx = Path(tmp)/'speech.onnx'
            onnx.write_bytes(b'fake')
            os.environ['NODE_NAME'], os.environ['POD_UID'] = 'etri-ser0002-cgnmsb', 'uid-npu'
            w = worker_module.Worker('openvino-npu', ['speech'],
                                     loader=lambda name, backend, **kw: stages.OpenVinoSpeechProbe(onnx, core=FailingCore(), ov_version='fake'))
            server = worker_module.serve(w, host='127.0.0.1', port=0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            session = types.SimpleNamespace(out=Path(tmp), run='qnh-t')
            npu = dict(key='npu-c', node='etri-ser0002-cgnmsb', backend='openvino-npu')
            rows = qualify.qualify_stage(session, npu, f'http://127.0.0.1:{server.server_port}', 'speech', PAIRS, REFS, processor)
            self.assertEqual({r['error_phase'] for r in rows}, {'compile'})
            self.assertIn('GroupNorm', json.loads(rows[0]['body'])['error'])
            result = qualify.stage_result(npu, 'speech', rows, 'uid-npu', REFS, processor=processor)
            self.assertEqual((result['execution'], result['failure_phase'], result['error_phases']), ('compile_failed', 'compile', {'compile': 60}))


class FakeLive:
    """Fake session + clock for collect_live: scripted Pod states and in-process worker HTTP replies."""

    def __init__(self, tmp, script, health, now=None):
        self.out, self.run, self.t, self.script, self.health_of = Path(tmp), 'qnh-t', 0.0, script, health
        self.requests = []

    def save(self, name, value):
        with (self.out/name).open('x') as handle:
            json.dump(value, handle, default=str)

    def clock(self):
        return self.t

    def sleep(self, dt):
        self.t += dt

    def pod_states(self):
        out = {}
        for key, fn in self.script.items():
            obj = fn(self.t)
            if obj is not None:
                out[key] = obj
        return out

    def http(self, url, body=None, timeout=None):
        key = url.split('//')[1].split(':')[0]
        if url.endswith('/health'):
            return self.health_of(key, self.t)
        self.requests.append((key, body['stage'], body['request_id']))
        stage = body['stage']
        q = PAIRS[int(body['request_id'].rsplit('-', 1)[1])]['question_id']
        data = {'vision': (qualify.REFERENCE/f'{q}.npy').read_bytes(), 'speech': REFS[q]['transcript'].encode(),
                'fusion': json.dumps(dict(answers=REFS[q]['answers'], answer_ids=REFS[q]['answer_ids'], scores=REFS[q]['scores'])).encode()}[stage]
        return dict(status='ok', data=base64.b64encode(data).decode(), receipt=dict(stage=stage, request_id=body['request_id']))


def pod(ip, phase='Running', ready=False, waiting=None):
    cs = [dict(state=dict(waiting=dict(reason=waiting, message='x')) if waiting else dict(running={}))]
    return dict(metadata=dict(uid='uid-'+ip, name='qnh-t-'+ip), spec=dict(nodeName='n'),
                status=dict(phase=phase, podIP=ip, containerStatuses=cs, conditions=[dict(type='Ready', status='True' if ready else 'False')]))


class F4Lifecycle(unittest.TestCase):
    def test_never_ready_workers_share_one_window_and_ready_peers_reach_mixed_execution(self):
        workers = r2_workers()
        keys = [w['key'] for w in workers]
        ready = {'cpu-c', 'npu-c', 'cpu-e0', 'jgpu-dev0007-cu130', 'hailo-dev0002', 'mblt-ser0004', 'mblt-compile'}
        pull = {'jgpu-dev0001-jp61', 'jgpu-dev0007-jp61'}
        crash = {'jgpu-dev0001-cu130'}
        script = {}
        for k in keys:
            if k in ready:
                script[k] = lambda t, k=k: pod(k, ready=t >= 20)
            elif k in pull:
                script[k] = lambda t, k=k: pod(k, phase='Pending', waiting='ImagePullBackOff' if t >= 30 else 'ContainerCreating')
            elif k in crash:
                script[k] = lambda t, k=k: pod(k, waiting='CrashLoopBackOff')
            else:  # dev0009 workers: scheduled, never Ready, no definitive failure
                script[k] = lambda t, k=k: pod(k, phase='Pending')
        by_key = {w['key']: w for w in workers}

        def health(key, t):
            if key == 'mblt-compile':
                return dict(status='compiling', probe=dict(phase='compiling'))
            if key not in ready:
                return dict(status='installing', bootstrap=dict(phase='installing'))
            return dict(status='ready', bootstrap=dict(phase='loaded'), stages={s: {} for s in by_key[key]['stages']})
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeLive(tmp, script, health)
            budgets = dict(ready_window_s=1500, collection_s=3000, poll_s=10)
            from transformers import Wav2Vec2Processor
            processor = Wav2Vec2Processor.from_pretrained(qualify.ASSETS/'model-assets/asr', local_files_only=True)
            results = qualify.collect_live(fake, dict(workers=workers), PAIRS, REFS, processor, started=0.0, clock=fake.clock, sleep=fake.sleep,
                                           http_fn=fake.http, budgets=budgets)
            self.assertEqual(results['jgpu-dev0001-cu130']['deployment'], 'deployment_failed')
            self.assertLessEqual(results['jgpu-dev0001-cu130']['failed_at_s'], 1)
            self.assertEqual(results['jgpu-dev0007-jp61']['deployment'], 'deployment_failed')
            self.assertLessEqual(results['jgpu-dev0007-jp61']['failed_at_s'], 40)
            self.assertIn('ImagePullBackOff', results['jgpu-dev0007-jp61']['failure_reason'])
            for k in ('jgpu-dev0009-cu130', 'jgpu-dev0009-jp61'):
                self.assertEqual(results[k]['deployment'], 'not_ready_at_window')
                self.assertEqual(results[k]['last_phase'], 'installing')
            self.assertEqual(results['mblt-compile']['compile_state'], 'running_at_deadline')
            self.assertTrue(results['budget']['deadline_reached'])
            self.assertLessEqual(results['budget']['elapsed_s'], 3000 + 10)
            mixed = Path(tmp)/'composition-npu-c_jgpu-dev0007-cu130_cpu-c.jsonl'
            self.assertTrue(mixed.exists())
            self.assertEqual(sum(1 for _ in mixed.open()), 60)
            first_speech_probe = next(i for i, r in enumerate(fake.requests) if r[0] == 'npu-c' and r[1] == 'speech' and r[2].startswith('npu-c-'))
            first_mixed = next(i for i, r in enumerate(fake.requests) if r[2].startswith('e2e-npu-c_jgpu'))
            self.assertLess(first_mixed, first_speech_probe)  # serving + mixed DAG evidence before the long NPU speech probe
            self.assertTrue((Path(tmp)/'collector-results.json').exists())
            compile_entry = json.loads((Path(tmp)/'collector-results.json').read_text())['mblt-compile']
            self.assertEqual(qualify.worker_failure(by_key['mblt-compile'], compile_entry, None)['execution'], 'compile_incomplete')
            not_ready = json.loads((Path(tmp)/'collector-results.json').read_text())['jgpu-dev0009-jp61']
            self.assertTrue(qualify.worker_failure(by_key['jgpu-dev0009-jp61'], not_ready, None)['failure_phase'].startswith('not_ready_at_window'))
            self.assertTrue(list(Path(tmp).glob('health-mblt-compile-*.json')))

    def test_worker_ready_after_the_window_is_collected_once_by_the_late_check(self):
        workers = [w for w in qualify.workers_for(AUDIT) if w['key'] in ('cpu-c', 'cpu-e0')]
        script = {'cpu-c': lambda t: pod('cpu-c', ready=t >= 20), 'cpu-e0': lambda t: pod('cpu-e0', ready=t >= 1600)}
        by_key = {w['key']: w for w in workers}
        health = lambda key, t: dict(status='ready', bootstrap=dict(phase='loaded'), stages={s: {} for s in by_key[key]['stages']})
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeLive(tmp, script, health)
            original = fake.http

            def slow(url, body=None, timeout=None):
                if body is not None:
                    fake.t += 5  # model requests take time, so the clock passes the readiness window
                return original(url, body, timeout)
            results = qualify.collect_live(fake, dict(workers=workers), PAIRS, REFS, None, started=0.0, clock=fake.clock, sleep=fake.sleep,
                                           http_fn=slow, budgets=dict(ready_window_s=1500, collection_s=5000, poll_s=10))
            self.assertEqual(results['cpu-e0']['deployment'], 'ready')
            self.assertGreaterEqual(results['cpu-e0']['late_ready_at_s'], 1600)
            self.assertEqual(sorted(results['cpu-e0']['stages']), ['fusion', 'speech', 'vision'])

    def test_request_timeout_is_bounded_by_the_remaining_budget(self):
        self.assertEqual(qualify.request_timeout(100.0, lambda: 0.0), 100.0)
        self.assertEqual(qualify.request_timeout(10_000.0, lambda: 0.0), qualify.BUDGETS['request_cap_s'])
        self.assertIsNone(qualify.request_timeout(3.0, lambda: 0.0))

    def test_pod_lifetimes_exceed_collection_plus_cleanup(self):
        b = qualify.BUDGETS
        w = qualify.workers_for(AUDIT)[0]
        pod_spec = qualify.pod_manifest('qnh-t', w, 'http://c:8080', 'x')['spec']
        self.assertGreater(pod_spec['activeDeadlineSeconds'], b['collection_s'] + b['cleanup_reserve_s'])
        self.assertGreater(qualify.cache_manifest('qnh-t')['spec']['activeDeadlineSeconds'],
                           b['upload_timeout_s'] + b['cache_ready_s'] + pod_spec['activeDeadlineSeconds'])
        env = {e['name']: e.get('value') for e in pod_spec['containers'][0]['env']}
        self.assertEqual(env['COMPILE_TIMEOUT_S'], str(b['compile_timeout_s']))
        self.assertLess(b['compile_timeout_s'], b['collection_s'])


class F5AbsoluteReadDeadline(unittest.TestCase):
    def test_trickling_client_cannot_hold_a_slot(self):
        class Stage:
            def run(self, data):
                return b'out', dict(output_device='cuda:0')

            def describe(self):
                return dict(stage='vision', backend='cuda-torch', device='cuda:0', model_sha256='m')
        w = worker_module.Worker('cuda-torch', ['vision'], loader=lambda name, backend, **kw: Stage())
        server = worker_module.serve(w, host='127.0.0.1', port=0, max_active=1, body_timeout_s=0.15, read_deadline_s=0.5)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        sock = socket.create_connection(('127.0.0.1', server.server_port))
        self.addCleanup(sock.close)
        stop = threading.Event()

        def trickle():
            try:
                sock.sendall(b'POST /run HTTP/1.1\r\nHost: x\r\nContent-Length: 10000\r\n\r\n')
                while not stop.is_set():
                    sock.sendall(b'a')
                    time.sleep(0.08)  # faster than the 0.15 s inactivity timeout
            except OSError:
                pass
        thread = threading.Thread(target=trickle, daemon=True)
        thread.start()
        time.sleep(0.2)
        self.assertEqual(server.active_slots(), 1)
        deadline = time.monotonic() + 3
        while server.active_slots() and time.monotonic() < deadline:
            time.sleep(0.05)
        stop.set()
        self.assertEqual(server.active_slots(), 0)
        self.assertEqual(qualify.http(f'http://127.0.0.1:{server.server_port}/health')['status'], 'ready')
        live = [t for t in threading.enumerate() if t.name.startswith('Thread') and t.is_alive()]
        self.assertLess(len(live), 20)


class F6BoundedLogs(unittest.TestCase):
    NOISY = "import sys\nfor i in range(400):\n sys.stdout.write('x'*50000+'\\n')\nsys.stdout.flush();print('last-line',flush=True)"

    def test_noisy_child_is_drained_and_retained_output_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = dict(events=[])
            started = time.monotonic()
            event = proc.run([sys.executable, '-c', self.NOISY], Path(tmp), 'noisy', timeout_s=30, state=state,
                             head_bytes=64 << 10, tail_bytes=16 << 10)
            self.assertLess(time.monotonic()-started, 20)
            self.assertEqual((event['returncode'], event['timed_out']), (0, False))
            ret = event['retention']['stdout']
            self.assertGreater(ret['total_bytes'], 19_000_000)
            self.assertGreater(ret['truncated_bytes'], 19_000_000)
            size = (Path(tmp)/'noisy.stdout').stat().st_size
            self.assertLess(size, (64 << 10) + (16 << 10) + 200)
            self.assertIn('last-line', event['stdout_tail'])
            self.assertIn('bytes not retained', (Path(tmp)/'noisy.stdout').read_text(errors='replace'))

    def test_live_progress_is_visible_and_json_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = dict(events=[])
            code = "import time\nfor i in range(30):\n print('tick',i,flush=True);time.sleep(0.1)"
            thread = threading.Thread(target=proc.run, args=([sys.executable, '-c', code], Path(tmp), 'ticks'), kwargs=dict(timeout_s=20, state=state))
            thread.start()
            time.sleep(1.0)
            live = proc.live(state)
            json.dumps(live)
            self.assertIn('tick', live['ticks']['stdout_tail'])
            self.assertGreater(live['ticks']['stdout_bytes'], 0)
            thread.join()

    def test_hanging_noisy_child_times_out_with_bounded_partial_output(self):
        code = "import sys,time\nwhile True:\n sys.stdout.write('y'*10000+'\\n');sys.stdout.flush();time.sleep(0.001)"
        with tempfile.TemporaryDirectory() as tmp:
            event = proc.run([sys.executable, '-c', code], Path(tmp), 'hang', timeout_s=1.0, head_bytes=32 << 10, tail_bytes=8 << 10)
            self.assertTrue(event['timed_out'])
            self.assertFalse(proc.group_alive(event['pgid']))
            self.assertLess((Path(tmp)/'hang.stdout').stat().st_size, (32 << 10) + (8 << 10) + 200)
            self.assertGreater(event['retention']['stdout']['truncated_bytes'], 0)


if __name__ == '__main__':
    unittest.main()
