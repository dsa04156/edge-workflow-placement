"""R1 regression tests (fix-r1-spec.md findings 1-7 + readiness corrections). Fake receipts/cores/children are plumbing
evidence only; no accelerator, driver or device memory behaviour is demonstrated here."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bootstrap  # noqa: E402
import catalog  # noqa: E402
import proc  # noqa: E402
import qualify  # noqa: E402
import stages  # noqa: E402
import worker as worker_module  # noqa: E402

AUDIT = catalog.load_audit()
PAIRS = json.loads(qualify.LOAD_PAIRS.read_text())['pairs']
REFS = {r['question_id']: r for r in map(json.loads, (qualify.REFERENCE/'results.jsonl').read_text().splitlines())}
digest = lambda b: hashlib.sha256(b).hexdigest()


def r2_workers(audit=None):
    """Run-1 (fix-r2) wave layout: both Jetson recipes on every Jetson + the Mobilint compile probe. Kept as a fixture for
    lifecycle/analysis plumbing tests; the live policy is now qualify.workers_for (one driver-matched recipe, blocked
    Jetsons and the repeated compile probe excluded)."""
    base = [w for w in qualify.workers_for(audit or AUDIT) if w['backend'] != 'cuda-torch']
    for name in ('etri-dev0001-jetorn', 'etri-dev0007-jetagx', 'etri-dev0009-jetorn'):
        for recipe, image in (('cu130-aarch64-cp311', qualify.IMAGE), ('jp61-aarch64-cp310', qualify.JP61_IMAGE)):
            base.append(dict(key='jgpu-'+name.split('-')[1]+'-'+recipe.split('-')[0], node=name, edge=True, backend='cuda-torch',
                             stages=['vision', 'speech', 'fusion'], recipe=recipe, image=image, resources={'nvidia.com/gpu.shared': 1}, root=True))
    base.append(dict(key='mblt-compile', node='etri-ser0004-cgnms0', edge=False, backend='mobilint-compile', stages=[], recipe='mobilint-compile',
                     image=qualify.MOBILINT_COMPILER_IMAGE, resources={}, root=True, command=['python3', '-u', '/config/mobilint_compile.py']))
    return base


def valid_rows(worker, stage, uid, device='cuda:0', output=None, **timing):
    rows = []
    for index, pair in enumerate(PAIRS):
        data = output(pair) if output else (qualify.REFERENCE/f"{pair['question_id']}.npy").read_bytes()
        receipt = dict(stage=stage, backend=worker['backend'], device=device, node=worker['node'], pod_uid=uid,
                       request_id=f"{worker['key']}-{stage}-{index}", input_sha256=qualify.expected_input_sha(stage, worker['backend'], pair, REFS),
                       output_sha256=digest(data), worker_wall_ms=10.0, timing=dict(device_compute_ms=5.0, output_device=device, **timing))
        rows.append(dict(index=index, question_id=pair['question_id'], split=pair['split'], status='ok',
                         data=base64.b64encode(data).decode(), receipt=receipt))
    return rows


GPU = dict(key='jgpu-dev0007-cu130', node='etri-dev0007-jetagx', backend='cuda-torch')


class F3EvidenceBinding(unittest.TestCase):
    def result(self, rows):
        return qualify.stage_result(GPU, 'vision', rows, 'uid-1', REFS)

    def test_valid_full_coverage_executes_and_passes(self):
        r = self.result(valid_rows(GPU, 'vision', 'uid-1'))
        self.assertEqual((r['execution'], r['parity'], r['coverage']['valid_unique']), ('executed', 'passed', 60))

    def test_sixty_duplicates_never_qualify(self):
        rows = [copy.deepcopy(valid_rows(GPU, 'vision', 'uid-1')[0]) for _ in range(60)]
        r = self.result(rows)
        self.assertNotEqual(r['execution'], 'executed')
        self.assertEqual(r['invalid_reasons']['duplicate_question'], 59)
        self.assertEqual(r['coverage']['missing'], 59)

    def test_each_tamper_is_counted_and_blocks_qualification(self):
        cases = {
            'unexpected_question': lambda row: row.update(question_id=999999999),
            'wrong_split': lambda row: row.update(split='evaluation' if row['split'] == 'calibration' else 'calibration'),
            'input_mismatch': lambda row: row['receipt'].update(input_sha256='0'*64),
            'stage_mismatch': lambda row: row['receipt'].update(stage='speech'),
            'identity_mismatch': lambda row: row['receipt'].update(pod_uid='foreign'),
            'output_hash_mismatch': lambda row: row.update(data=base64.b64encode(b'tampered').decode()),
            'request_id_mismatch': lambda row: row['receipt'].update(request_id='other'),
        }
        for reason, tamper in cases.items():
            rows = valid_rows(GPU, 'vision', 'uid-1')
            tamper(rows[3])
            r = self.result(rows)
            self.assertEqual(r['execution'], 'partial', reason)
            self.assertEqual(r['invalid_reasons'][reason], 1, reason)
            self.assertEqual(len(r['invalid_rows']), 1, reason)
            self.assertEqual(qualify.cost_table({('etri-dev0007-jetagx', 'cuda-torch', 'vision'): r}), {}, reason)

    def test_partial_coverage_is_reported_as_partial(self):
        r = self.result(valid_rows(GPU, 'vision', 'uid-1')[:59])
        self.assertEqual((r['execution'], r['coverage']['missing']), ('partial', 1))


class F1F2AnalysisRobustness(unittest.TestCase):
    def run_dir(self, tmp, collector=None, status=None, health=None):
        run = Path(tmp)/'qnh-t'
        run.mkdir()
        workers = r2_workers()
        (run/'wave.json').write_text(json.dumps(dict(workers=workers, assets_manifest_sha256='x')))
        (run/'workers.created.json').write_text(json.dumps(dict(confirmed=[dict(kind='Pod', name='qnh-t-'+w['key'], uid='u-'+w['key']) for w in workers])))
        if collector is not None:
            (run/'collector-results.json').write_text(json.dumps(collector))
        if status:
            (run/'run-status.json').write_text(json.dumps(status))
        for key, value in (health or {}).items():
            (run/f'health-{key}.json').write_text(json.dumps(value))
        return run, workers

    def test_every_pod_failed_still_emits_complete_analysis_and_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            keys = [w['key'] for w in r2_workers()]
            run, workers = self.run_dir(tmp, collector={k: dict(deployment='deployment_failed', pod_status=dict(phase='Pending')) for k in keys},
                                        status=dict(status='error', error="RuntimeError('cache Pod not Ready')"))
            out = qualify.analyze(run)
            self.assertEqual(out['main_plan_candidates'], [])
            self.assertTrue((run/'catalog.json').exists())
            self.assertIn('cache Pod not Ready', out['run_status']['error'])
            rows = {(r['node'], r['backend'], r['stage']): r for r in json.loads((run/'catalog.json').read_text())['rows']}
            self.assertEqual(rows[('etri-dev0003-raspi5', 'cpu-torch', 'vision')]['execution'], 'deployment_failed')
            self.assertEqual(len(rows), len(catalog.build(AUDIT)['rows']))

    def test_run_stopped_before_collection_is_not_attempted_with_the_initiating_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, _ = self.run_dir(tmp, collector=None, status=dict(status='error', error="RuntimeError('preflight')"))
            out = qualify.analyze(run)
            r = out['stage_results']['etri-ser0002-cgnmsb|cpu-torch|vision']
            self.assertEqual(r['execution'], 'not_attempted')
            self.assertIn('preflight', r['execution_evidence'])

    def test_observed_failure_categories_are_kept(self):
        def h(phase, **kw):
            return dict(status=phase, bootstrap=dict(phase=phase, **kw))
        collector = {
            'jgpu-dev0007-cu130': dict(deployment='ready', health=h('dependency_unavailable', blocker='CUDA driver API version 12060')),
            'jgpu-dev0009-cu130': dict(deployment='ready', health=h('install_failed', events=[dict(kind='pip', returncode=1)])),
            'npu-c': dict(deployment='ready', health=dict(status='partial', errors=dict(vision=dict(status='load_failed', error='compile error')),
                                                           bootstrap=dict(phase='loaded'))),
            'mblt-compile': dict(deployment='ready', health=dict(status='compile_failed', probe=dict(phase='compile_failed', error='x'))),
        }
        with tempfile.TemporaryDirectory() as tmp:
            run, _ = self.run_dir(tmp, collector=collector)
            res = qualify.analyze(run)['stage_results']
            self.assertEqual(res['etri-dev0007-jetagx|cuda-torch|vision']['execution'], 'dependency_unavailable')
            self.assertEqual(res['etri-dev0009-jetorn|cuda-torch|speech']['execution'], 'deployment_failed')
            self.assertEqual(res['etri-dev0009-jetorn|cuda-torch|speech']['failure_phase'], 'install')
            self.assertEqual(res['etri-ser0002-cgnmsb|openvino-npu|vision']['execution'], 'compile_failed')
            self.assertEqual(res['etri-ser0004-cgnms0|mobilint-mxq|vision']['execution'], 'compile_failed')

    def test_compositions_without_stages_do_not_crash_main_plan(self):
        self.assertEqual(qualify.main_plan_candidates({'a': dict(status='not_attempted')}, {}), [])


class F4QualityCoverage(unittest.TestCase):
    def test_split_coverage_and_both_means_are_saved(self):
        answers = {q: r['answers'][0] for q, r in REFS.items()}
        for q in [p['question_id'] for p in PAIRS if p['split'] == 'evaluation'][:4]:
            answers[q] = None
        quality = qualify.composition_quality(answers)
        ev = quality['splits']['evaluation']
        self.assertEqual((ev['expected'], ev['answered'], ev['missing']), (40, 36, 4))
        self.assertAlmostEqual(ev['coverage'], 0.9)
        self.assertGreaterEqual(ev['answered_mean'], ev['zero_imputed_mean'])
        self.assertEqual(quality['splits']['calibration']['coverage'], 1.0)
        self.assertFalse(quality['passed'])
        self.assertIn('per_question', quality['evaluator_output'])


class F5FiniteSubprocesses(unittest.TestCase):
    HANG = ("import subprocess,sys,time;print('partial-out',flush=True);print('partial-err',file=sys.stderr,flush=True);"
            "subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);time.sleep(60)")

    def test_hanging_child_and_its_descendants_are_killed_with_partial_logs_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = dict(events=[])
            started = time.monotonic()
            event = proc.run([sys.executable, '-c', self.HANG], Path(tmp), 'hang', timeout_s=1.5, state=state)
            self.assertLess(time.monotonic()-started, 10)
            self.assertTrue(event['timed_out'])
            self.assertIn('partial-out', (Path(tmp)/'hang.stdout').read_text())
            self.assertIn('partial-err', (Path(tmp)/'hang.stderr').read_text())
            self.assertIn('partial-out', event['stdout_tail'])
            self.assertEqual(state['events'][-1]['name'], 'hang')
            self.assertFalse(proc.group_alive(event['pgid']))

    def test_running_child_progress_is_visible_before_it_ends(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = dict(events=[])
            code = "import time\nfor i in range(30):\n print('tick',i,flush=True);time.sleep(0.1)"
            thread = threading.Thread(target=proc.run, args=([sys.executable, '-c', code], Path(tmp), 'ticks'), kwargs=dict(timeout_s=20, state=state))
            thread.start()
            time.sleep(1.0)
            running = state.get('running', {}).get('ticks')
            self.assertIsNotNone(running)
            self.assertIn('tick', proc.tail(Path(tmp)/'ticks.stdout'))
            thread.join()
            self.assertEqual(state['events'][-1]['returncode'], 0)

    def test_mobilint_compile_child_has_a_finite_deadline(self):
        import mobilint_compile
        with tempfile.TemporaryDirectory() as tmp:
            state = dict(events=[])
            mobilint_compile.run_compile_child(state, Path(tmp), timeout_s=1.0, command=[sys.executable, '-c', self.HANG])
            self.assertEqual(state['phase'], 'compile_timeout')
            self.assertIn('partial-out', state['events'][-1]['stdout_tail'])


class F6BoundedAdmission(unittest.TestCase):
    class Slow:
        def run(self, data):
            time.sleep(0.2)
            return b'out', dict(output_device='cuda:0')

        def describe(self):
            return dict(stage='vision', backend='cuda-torch', device='cuda:0', model_sha256='m')

    def server(self):
        w = worker_module.Worker('cuda-torch', ['vision'], loader=lambda name, backend, **kw: self.Slow())
        s = worker_module.serve(w, host='127.0.0.1', port=0, max_active=2, body_timeout_s=0.5, max_body=1024)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        self.addCleanup(s.server_close)
        self.addCleanup(s.shutdown)
        return s, f'http://127.0.0.1:{s.server_port}'

    def partial(self, port):
        sock = socket.create_connection(('127.0.0.1', port))
        sock.sendall(b'POST /run HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 500\r\n\r\n{"stage"')
        self.addCleanup(sock.close)
        return sock

    def test_overload_is_rejected_with_structured_status_and_capacity_recovers(self):
        s, url = self.server()
        socks = [self.partial(s.server_port) for _ in range(2)]
        time.sleep(0.1)
        with self.assertRaises(qualify.urllib.error.HTTPError) as caught:
            qualify.http(url+'/run', dict(stage='vision', data=b'x'), timeout=5)
        self.assertEqual(caught.exception.code, 503)
        self.assertEqual(json.loads(caught.exception.read())['error'], 'worker at admission capacity')
        time.sleep(1.0)  # partial senders time out; their slots are released
        self.assertEqual(self.drained(s), 0)
        self.assertEqual(qualify.http(url+'/run', dict(stage='vision', data=b'x'), timeout=5)['status'], 'ok')
        del socks

    def test_oversized_body_is_rejected_before_reading_and_slot_released(self):
        s, url = self.server()
        with self.assertRaises(qualify.urllib.error.HTTPError) as caught:
            qualify.http(url+'/run', dict(stage='vision', data=b'x'*4096), timeout=5)
        self.assertEqual(caught.exception.code, 413)
        self.assertEqual(self.drained(s), 0)

    def test_concurrent_full_requests_are_bounded_and_all_answered(self):
        s, url = self.server()
        codes = []
        threads = [threading.Thread(target=lambda: codes.append(self.code(url))) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(set(codes) <= {200, 503})
        self.assertIn(200, codes)
        self.assertEqual(self.drained(s), 0)

    def drained(self, server, wait_s=2.0):
        # The handler thread releases its slot in `finally` right after the response is written; allow that to finish.
        deadline = time.monotonic() + wait_s
        while server.active_slots() and time.monotonic() < deadline:
            time.sleep(0.02)
        return server.active_slots()

    def code(self, url):
        try:
            qualify.http(url+'/run', dict(stage='vision', data=b'x'), timeout=10)
            return 200
        except qualify.urllib.error.HTTPError as error:
            return error.code


class F7SpeechProbeCache(unittest.TestCase):
    class Core:
        available_devices = ['NPU']

        def __init__(self):
            self.compiles = 0

        def read_model(self, path):
            outer = self

            class Model:
                inputs = ['in']

                def reshape(self, shape):
                    self.shape = shape

            return Model()

        def compile_model(self, model, device):
            self.compiles += 1
            import numpy as np

            class Compiled:
                def get_property(self, name):
                    return ['NPU']

                def create_infer_request(self):
                    class Request:
                        def infer(self, inputs):
                            x = inputs[0]
                            return {0: np.zeros((1, max(1, x.shape[1] // 320), 32), dtype=np.float32)}
                    return Request()
            return Compiled()

    def test_alternating_lengths_keep_one_compiled_graph_and_record_recompiles(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            onnx = Path(tmp)/'speech.onnx'
            onnx.write_bytes(b'fake')
            core = self.Core()
            probe = stages.OpenVinoSpeechProbe(onnx, core=core, ov_version='fake')
            for length in (16000, 24000, 16000, 24000):
                import io
                buf = io.BytesIO()
                np.save(buf, np.zeros(length, dtype=np.float32), allow_pickle=False)
                _, timing = probe.run(buf.getvalue())
                self.assertLessEqual(timing['cached_graphs'], 1)
            self.assertEqual(core.compiles, 4)
            self.assertEqual(timing['evictions_total'], 3)
            self.assertIsNotNone(timing['exact_length_compile_ms'])
            self.assertIn('process_rss_kib_before', timing)
            self.assertIn('not device memory', probe.describe()['memory_note'])


class Readiness(unittest.TestCase):
    def test_jetson_recipe_is_driver_matched_and_still_gated_in_pod(self):
        # superseded fix-r1 policy (both recipes per Jetson): now one recipe from observed driver evidence (npu-property-fix)
        workers = qualify.workers_for(AUDIT)
        jetsons = [(w['node'], w['recipe']) for w in workers if w['backend'] == 'cuda-torch']
        self.assertEqual(jetsons, [('etri-dev0009-jetorn', 'cu130-aarch64-cp311')])  # dev0001 jp61 held after run 2
        self.assertEqual(next(w for w in workers if w['backend'] == 'cuda-torch')['image'], qualify.IMAGE)
        self.assertTrue(qualify.JP61_IMAGE.startswith('nvcr.io/nvidia/l4t-jetpack@sha256:34ccf0f3'))
        info = lambda v: dict(cuda_driver=dict(driver_version=v))
        self.assertEqual(bootstrap.choose('jp61-aarch64-cp310', info(12060))[0], 'jp61-aarch64-cp310')
        self.assertIsNotNone(bootstrap.choose('jp61-aarch64-cp310', info(13000))[1])
        self.assertIsNotNone(bootstrap.choose('cu130-aarch64-cp311', info(12060))[1])
        self.assertEqual(bootstrap.choose('cu130-aarch64-cp311', info(13000))[0], 'cu130-aarch64-cp311')

    def test_pipless_wheel_install_extracts_every_wheel(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp/'wheels').mkdir()
            with zipfile.ZipFile(tmp/'wheels'/'demo-1.0-py3-none-any.whl', 'w') as z:
                z.writestr('demo/__init__.py', 'VALUE = 7\n')
            installed = bootstrap.install_without_pip(tmp/'wheels', tmp/'site')
            self.assertEqual(installed, ['demo-1.0-py3-none-any.whl'])
            self.assertEqual((tmp/'site/demo/__init__.py').read_text(), 'VALUE = 7\n')

    def test_shipped_sources_are_bound_to_the_plan(self):
        wave = dict(shipped_sources=qualify.shipped_hashes('qnh-x'))
        qualify.verify_shipped(wave, 'qnh-x')
        tampered = dict(shipped_sources=dict(wave['shipped_sources'], **{'worker.py': '0'*64}))
        with self.assertRaisesRegex(RuntimeError, 'shipped source'):
            qualify.verify_shipped(tampered, 'qnh-x')

    def test_mobilint_compile_uses_only_calibration_inputs(self):
        files = qualify.asset_files(qualify.OUT_ROOT/'export-20261023T075427Z',
                                    [qualify.OUT_ROOT/'deps-20261023T075919Z', qualify.OUT_ROOT/'deps-20261023T080028Z'])
        calib = {Path(p).name for g, p, _ in files if g == 'calib'}
        expected = {Path(p['raw_image_path']).name for p in PAIRS if p['split'] == 'calibration'}
        self.assertEqual(calib, expected)


class MixedComposition(unittest.TestCase):
    """npu-c vision || Jetson CUDA speech -> cpu-c fusion through three real local HTTP workers (fake stages)."""

    def setUp(self):
        self.by_image = {digest((qualify.CORPUS/Path(p['raw_image_path']).name).read_bytes()): p['question_id'] for p in PAIRS}
        self.by_audio = {digest((qualify.CORPUS/Path(p['raw_audio_path']).name).read_bytes()): p['question_id'] for p in PAIRS}
        self.by_feature = {digest((qualify.REFERENCE/f"{p['question_id']}.npy").read_bytes()): p['question_id'] for p in PAIRS}
        test = self
        self.seen = dict(vision=[], speech=[], fusion=[])

        class Stage:
            def __init__(self, name, backend, device):
                self.name, self.backend, self.device = name, backend, device

            def run(self, data):
                test.seen[self.name].append(time.monotonic())
                if self.name == 'vision':
                    out = (qualify.REFERENCE/f"{test.by_image[digest(data)]}.npy").read_bytes()
                    return out, dict(output_device='NPU', execution_devices=['NPU'])
                if self.name == 'speech':
                    return REFS[test.by_audio[digest(data)]]['transcript'].encode(), dict(output_device=self.device)
                q = test.by_feature[digest(data['vision'])]
                return json.dumps(dict(answers=REFS[q]['answers'], answer_ids=REFS[q]['answer_ids'], scores=REFS[q]['scores'])).encode(), \
                    dict(output_device=self.device)

            def describe(self):
                return dict(stage=self.name, backend=self.backend, device=self.device, model_sha256='m')
        self.workers, self.urls = {}, {}
        specs = dict(vision=('npu-c', 'etri-ser0002-cgnmsb', 'openvino-npu', 'NPU'),
                     speech=('jgpu-dev0007-cu130', 'etri-dev0007-jetagx', 'cuda-torch', 'cuda:0'),
                     fusion=('cpu-c', 'etri-ser0002-cgnmsb', 'cpu-torch', 'cpu'))
        for stage, (key, node, backend, device) in specs.items():
            os.environ['NODE_NAME'], os.environ['POD_UID'] = node, 'uid-'+key  # captured once by the Worker (per-process identity)
            w = worker_module.Worker(backend, [stage], loader=lambda name, b, _s=stage, _b=backend, _d=device, **kw: Stage(_s, _b, _d))
            server = worker_module.serve(w, host='127.0.0.1', port=0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            self.workers[stage], self.urls[stage] = dict(key=key, node=node, backend=backend), f'http://127.0.0.1:{server.server_port}'
        self.node_env = {stage: spec[1] for stage, spec in specs.items()}

    def test_fresh_parallel_calls_bind_all_three_devices_and_keep_quality_separate(self):
        import types
        with tempfile.TemporaryDirectory() as tmp:
            session = types.SimpleNamespace(out=Path(tmp), run='qnh-t')
            rows = qualify.qualify_composition(session, 'npu-c/jgpu-dev0007-cu130/cpu-c', self.workers, self.urls, PAIRS, REFS, None, None)
            self.assertEqual([len(self.seen[s]) for s in ('vision', 'speech', 'fusion')], [60, 60, 60])
            uids = {w['key']: 'uid-'+w['key'] for w in self.workers.values()}
            result = qualify.composition_result('npu-c/jgpu-dev0007-cu130/cpu-c', rows, self.workers, uids, REFS)
            self.assertEqual((result['valid_rows'], result['complete'], result['invalid_reasons']), (60, True, {}))
            self.assertEqual({st: d['backend'] for st, d in result['devices'].items()},
                             dict(vision='openvino-npu', speech='cuda-torch', fusion='cpu-torch'))
            self.assertFalse(result['quality']['passed'])  # same frozen model: reference quality, Qmin fail kept
            tampered = copy.deepcopy(rows)
            tampered[5]['branches']['speech']['receipt']['pod_uid'] = 'foreign'
            bad = qualify.composition_result('npu-c/jgpu-dev0007-cu130/cpu-c', tampered, self.workers, uids, REFS)
            self.assertEqual((bad['valid_rows'], bad['complete'], bad['invalid_reasons']), (59, False, {'identity_mismatch': 1}))
            self.assertEqual(bad['quality']['splits'][PAIRS[5]['split']]['missing'], 1)

    def test_failed_branch_is_an_incomplete_row_not_a_substitution(self):
        import types
        with tempfile.TemporaryDirectory() as tmp:
            session = types.SimpleNamespace(out=Path(tmp), run='qnh-t')
            urls = dict(self.urls, speech='http://127.0.0.1:9')  # nothing listens: speech branch fails
            rows = qualify.qualify_composition(session, 'npu-c/jgpu-dev0007-cu130/cpu-c', self.workers, urls, PAIRS[:3], REFS, None, None)
            self.assertEqual({r['status'] for r in rows}, {'upstream_missing'})
            self.assertTrue(all(r['branches']['speech']['status'] == 'transport_error' for r in rows))
            self.assertEqual(self.seen['fusion'], [])

    def test_mixed_compositions_exist_for_every_jetson_worker(self):
        wave = dict(workers=r2_workers())
        names = [c[0] for c in qualify.compositions_for(wave)]
        jetsons = sorted(w['key'] for w in wave['workers'] if w['key'].startswith('jgpu-'))
        self.assertEqual(len(jetsons), 6)
        for k in jetsons:
            self.assertIn(f'npu-c/{k}/cpu-c', names)
            self.assertIn(f'{k}/{k}/{k}', names)
        self.assertIn('cpu-c/cpu-c/cpu-c', names)
        self.assertIn('npu-c/cpu-c/cpu-c', names)


if __name__ == '__main__':
    unittest.main()
