"""Contract tests: full inventory retained, manifests match observed resources, review gate, stream boundary,
no CPU timing relabelled as accelerator, request errors preserved, Qmin failure disqualifies the main plan.
Mock receipts/stages here are plumbing evidence only (no accelerator was used)."""
import base64
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import catalog  # noqa: E402
import qualify  # noqa: E402
import stages  # noqa: E402
import worker as worker_module  # noqa: E402

AUDIT = catalog.load_audit()
FAKE_RTK = ROOT/'experiments/quality-network-vqa/fake_rtk.py'
REFS = {r['question_id']: r for r in map(json.loads, (qualify.REFERENCE/'results.jsonl').read_text().splitlines())}
PAIRS = json.loads(qualify.LOAD_PAIRS.read_text())['pairs']


class Catalog(unittest.TestCase):
    def test_every_observed_node_and_accelerator_family_is_kept(self):
        cat = catalog.build(AUDIT)
        self.assertEqual(cat['nodes'], 12)
        self.assertEqual({r['node'] for r in cat['rows']}, {n['node'] for n in AUDIT['nodes']})
        families = {}
        for r in cat['rows']:
            if r['stage'] == 'vision' and r['resource']:
                families.setdefault(r['family'], set()).add(r['node'])
        self.assertEqual({k: len(v) for k, v in families.items()},
                         {'nvidia-jetson-gpu': 3, 'nvidia-server-gpu': 3, 'intel-npu': 1, 'hailo-8': 2, 'mobilint': 1, 'rockchip-rk3399pro': 1})

    def test_statuses_reflect_observed_blockers_without_claiming_unsupported(self):
        rows = {(r['node'], r['family'], r['stage']): r for r in catalog.build(AUDIT)['rows']}
        self.assertEqual(rows[('etri-dev0004-tedger', 'rockchip-rk3399pro', 'vision')]['status'], 'node_unavailable')
        for node in ('etri-ser0001-cg0msb', 'etri-ser0002-cgnmsb', 'etri-ser0003-cg0ms0'):
            self.assertEqual(rows[(node, 'nvidia-server-gpu', 'vision')]['status'], 'occupied')
        self.assertEqual(rows[('etri-dev0006-raspi5', 'hailo-8', 'vision')]['status'], 'occupied')
        self.assertEqual(rows[('etri-dev0002-raspi5', 'hailo-8', 'vision')]['status'], 'dependency_unavailable')
        self.assertEqual(rows[('etri-ser0004-cgnms0', 'mobilint', 'vision')]['status'], 'dependency_unavailable')  # run 1: no qbcompiler
        self.assertIn('qnh-20261023t090355', rows[('etri-ser0004-cgnms0', 'mobilint', 'vision')]['dependency_evidence'])
        self.assertEqual(rows[('etri-ser0002-cgnmsb', 'intel-npu', 'fusion')]['status'], 'export_failed')
        self.assertEqual(rows[('etri-ser0002-cgnmsb', 'intel-npu', 'vision')]['status'], 'not_attempted')
        self.assertEqual(rows[('etri-dev0007-jetagx', 'nvidia-jetson-gpu', 'fusion')]['status'], 'deployment_blocked')  # run-1 eviction
        self.assertFalse(any(r['status'] == 'unsupported' for r in rows.values()))

    def test_unknown_resource_is_refused_not_dropped(self):
        audit = copy.deepcopy(AUDIT)
        audit['nodes'][0]['accelerators_allocatable']['vendor.example/npu'] = '1'
        with self.assertRaisesRegex(ValueError, 'refusing to drop'):
            catalog.build(audit)

    def test_executed_requires_real_receipts_and_quality_failure_is_reported(self):
        key = ('etri-ser0002-cgnmsb', 'openvino-npu', 'vision')
        with self.assertRaisesRegex(ValueError, 'without real receipts'):
            catalog.build(AUDIT, {key: dict(execution='executed', parity='passed', receipts=[])})
        row = next(r for r in catalog.build(AUDIT, {key: dict(execution='executed', parity='passed', quality='failed', receipts=[{}])})['rows']
                   if (r['node'], r['backend'], r['stage']) == key)
        self.assertEqual(row['status'], 'quality_failed')


class Manifests(unittest.TestCase):
    def setUp(self):
        self.workers = qualify.workers_for(AUDIT)
        self.pods = {w['key']: qualify.pod_manifest('qnh-t', w, 'http://cache:8080', 'x') for w in self.workers}

    def test_requests_match_observed_free_resources_one_unit_per_worker(self):
        nodes = {n['node']: n for n in AUDIT['nodes']}
        used = {}
        for w in self.workers:
            limits = self.pods[w['key']]['spec']['containers'][0]['resources']['limits']
            devices = {k: v for k, v in limits.items() if '/' in k}
            self.assertEqual(devices, {k: str(v) for k, v in w['resources'].items()})
            for key, value in devices.items():
                self.assertIn(key, nodes[w['node']]['accelerators_allocatable'])
                self.assertEqual(value, '1')
                used[(w['node'], key)] = used.get((w['node'], key), 0) + 1
        for (node, key), count in used.items():
            n = nodes[node]
            self.assertLessEqual(count, int(n['accelerators_allocatable'][key]) - n['accelerators_requested_by_running_or_pending_pods'].get(key, 0))
        self.assertFalse(any(w['node'] in ('etri-ser0001-cg0msb', 'etri-ser0003-cg0ms0') for w in self.workers))
        self.assertFalse(any('nvidia.com/gpu' in w['resources'] for w in self.workers))

    def test_mobilint_compile_probe_is_cpu_only_with_the_pinned_public_image(self):
        # the probe is no longer in the live wave (run 1: image lacks qbcompiler); its manifest shape is still checked
        from test_fix_r1 import r2_workers
        compile_worker = next(w for w in r2_workers() if w['key'] == 'mblt-compile')
        pod = qualify.pod_manifest('qnh-t', compile_worker, 'http://cache:8080', 'x')
        limits = pod['spec']['containers'][0]['resources']['limits']
        self.assertFalse(any('/' in k for k in limits))
        self.assertTrue(compile_worker['image'].startswith('docker.io/mobilint/qbcompiler@sha256:'))
        self.assertEqual(pod['spec']['containers'][0]['command'], ['python3', '-u', '/config/mobilint_compile.py'])
        self.assertNotIn('mblt-compile', [w['key'] for w in self.workers])

    def test_cuda_workers_host_all_stages_in_one_process(self):
        for w in self.workers:
            if w['backend'] == 'cuda-torch':
                self.assertEqual(w['stages'], ['vision', 'speech', 'fusion'])

    def test_no_privileged_host_namespace_or_host_path_and_known_runtime_classes(self):
        for pod in [*self.pods.values(), qualify.cache_manifest('qnh-t')]:
            spec = pod['spec']
            for key in ('hostNetwork', 'hostPID', 'hostIPC'):
                self.assertNotIn(key, spec)
            self.assertFalse(any('hostPath' in v for v in spec['volumes']))
            self.assertIn(spec.get('runtimeClassName'), (None, *qualify.OBSERVED_RUNTIME_CLASSES))
            for c in spec['containers']:
                sc = c['securityContext']
                self.assertFalse(sc.get('privileged', False))
                self.assertFalse(sc['allowPrivilegeEscalation'])
                self.assertEqual(sc['capabilities'], {'drop': ['ALL']})
            self.assertEqual(spec['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms'][0]
                             ['matchFields'][0]['key'], 'metadata.name')

    def test_edge_workers_are_http_only(self):
        for w in self.workers:
            container = self.pods[w['key']]['spec']['containers'][0]
            self.assertEqual(container['readinessProbe']['httpGet']['path'], '/ready')
            self.assertIn(container['command'], (['python3', '-u', '/config/bootstrap.py'], ['python3', '-u', '/config/mobilint_compile.py']))


def fake_env(tmp):
    bin_dir = tmp/'bin'
    bin_dir.mkdir()
    shutil.copy(FAKE_RTK, bin_dir/'rtk')
    (bin_dir/'rtk').chmod(0o755)
    return dict(PATH=f'{bin_dir}:{os.environ["PATH"]}', FAKE_STATE=str(tmp/'state.json'), FAKE_LOG=str(tmp/'calls.jsonl'))


def calls(tmp):
    path = tmp/'calls.jsonl'
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


class LiveBoundary(unittest.TestCase):
    def wave(self):
        return dict(assets_manifest_sha256='abc123', workers=qualify.workers_for(AUDIT))

    def test_execute_without_review_release_never_calls_kubectl(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp/'plan').mkdir()
            (tmp/'plan/wave.json').write_text(json.dumps(self.wave()))
            (tmp/'release.txt').write_text('RELEASE for some other wave')
            env = dict(os.environ, **fake_env(tmp))
            result = subprocess.run([sys.executable, str(HERE/'qualify.py'), 'execute', '--plan', str(tmp/'plan'), '--run-id', 'qnh-t',
                                     '--review-release', str(tmp/'release.txt'), '--output-root', str(tmp/'runs')],
                                    capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('refused', result.stderr)
            self.assertEqual(calls(tmp), [])
            self.assertFalse((tmp/'runs').exists())

    def test_edge_streams_are_refused_and_server_streams_need_fresh_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with unittest.mock.patch.dict(os.environ, fake_env(tmp)):
                session = qualify.HetSession(tmp/'runs', 'qnh-t', self.wave())
                for verb in ('exec', 'attach', 'port-forward', 'cp'):
                    with self.assertRaisesRegex(RuntimeError, 'refused'):
                        session.call('probe', [verb, '-n', qualify.NS, 'qnh-t-jgpu-dev0007', '--', 'true'])
                self.assertEqual(session.call('logs', ['logs', '-n', qualify.NS, 'qnh-t-cpu-e0']), '')
                self.assertEqual(calls(tmp), [])
                # Server Pod never created by this run: the fresh GET finds no creation UID -> refused, no exec issued.
                with self.assertRaisesRegex(RuntimeError, 'refused'):
                    session.call('upload', ['exec', '-i', '-n', qualify.NS, 'qnh-t-cache', '--', 'true'])
                self.assertFalse(any('exec' in c for c in calls(tmp)))
                with self.assertRaisesRegex(RuntimeError, 'refused'):
                    session.call('other', ['exec', '-n', qualify.NS, 'someone-else', '--', 'true'])


def rows_for(worker, stage, uid, device, data, **timing):
    """Bound receipts (stage/node/UID/request/input/output hashes) as the worker produces them; plumbing only."""
    rows = []
    for i, p in enumerate(PAIRS):
        payload = base64.b64decode(data(p))
        receipt = dict(stage=stage, backend=worker['backend'], device=device, node=worker['node'], pod_uid=uid, worker_wall_ms=10.0,
                       request_id=f"{worker['key']}-{stage}-{i}", input_sha256=qualify.expected_input_sha(stage, worker['backend'], p, REFS),
                       output_sha256=__import__('hashlib').sha256(payload).hexdigest(), timing=dict(device_compute_ms=5.0, **timing))
        rows.append(dict(index=i, question_id=p['question_id'], split=p['split'], status='ok', data=data(p), receipt=receipt))
    return rows


class Analysis(unittest.TestCase):
    gpu = dict(key='jgpu-dev0007-cu130', node='etri-dev0007-jetagx', backend='cuda-torch')
    vision = staticmethod(lambda p: base64.b64encode((qualify.REFERENCE/f"{p['question_id']}.npy").read_bytes()).decode())

    def test_cpu_receipts_never_count_as_accelerator_execution(self):
        rows = rows_for(self.gpu, 'vision', 'u1', 'cpu', self.vision, output_device='cpu')
        result = qualify.stage_result(self.gpu, 'vision', rows, 'u1', REFS)
        self.assertEqual(result['execution'], 'runtime_failed')
        self.assertEqual(result['invalid_reasons']['identity_mismatch'], 60)
        self.assertEqual(qualify.cost_table({('etri-dev0007-jetagx', 'cuda-torch', 'vision'): result}), {})

    def test_wrong_pod_uid_or_node_is_not_execution(self):
        rows = rows_for(self.gpu, 'vision', 'someone-else', 'cuda:0', self.vision, output_device='cuda:0')
        self.assertEqual(qualify.stage_result(self.gpu, 'vision', rows, 'u1', REFS)['execution'], 'runtime_failed')

    def test_request_errors_are_preserved_not_filtered(self):
        rows = rows_for(self.gpu, 'vision', 'u1', 'cuda:0', self.vision, output_device='cuda:0')
        rows[7] = dict(rows[7], status='http_error', code=500)
        result = qualify.stage_result(self.gpu, 'vision', rows, 'u1', REFS)
        self.assertEqual((result['execution'], result['request_errors'], result['coverage']['rows']), ('partial', 1, 60))
        self.assertEqual(result['invalid_rows'][0]['reason'], 'request_error')

    def test_real_device_receipts_execute_and_parity_is_checked_against_the_frozen_reference(self):
        rows = rows_for(self.gpu, 'vision', 'u1', 'cuda:0', self.vision, output_device='cuda:0')
        result = qualify.stage_result(self.gpu, 'vision', rows, 'u1', REFS)
        self.assertEqual((result['execution'], result['parity'], result['parity_matches']), ('executed', 'passed', 60))
        table = qualify.cost_table({('etri-dev0007-jetagx', 'cuda-torch', 'vision'): result})
        self.assertEqual(table['etri-dev0007-jetagx|cuda-torch|vision']['n'], 60)
        speech = rows_for(self.gpu, 'speech', 'u1', 'cuda:0', lambda p: base64.b64encode(b'WRONG').decode(), output_device='cuda:0')
        self.assertEqual(qualify.stage_result(self.gpu, 'speech', speech, 'u1', REFS)['parity'], 'failed')

    def test_npu_receipt_needs_exact_npu_execution_devices(self):
        npu = dict(key='npu-c', node='etri-ser0002-cgnmsb', backend='openvino-npu')
        good = rows_for(npu, 'vision', 'u2', 'NPU', self.vision, execution_devices=['NPU'])
        self.assertEqual(qualify.stage_result(npu, 'vision', good, 'u2', REFS)['execution'], 'executed')
        mixed = rows_for(npu, 'vision', 'u2', 'NPU', self.vision, execution_devices=['NPU', 'CPU'])
        self.assertEqual(qualify.stage_result(npu, 'vision', mixed, 'u2', REFS)['execution'], 'runtime_failed')

    def test_cost_table_rejects_receipts_under_another_key(self):
        rows = rows_for(self.gpu, 'vision', 'u1', 'cuda:0', self.vision, output_device='cuda:0')
        result = qualify.stage_result(self.gpu, 'vision', rows, 'u1', REFS)
        with self.assertRaisesRegex(ValueError, 'identity'):
            qualify.cost_table({('etri-dev0009-jetorn', 'cuda-torch', 'vision'): result})

    def test_reference_answers_fail_qmin_and_keep_every_composition_out_of_the_main_plan(self):
        quality = qualify.composition_quality({q: r['answers'][0] for q, r in REFS.items()})
        self.assertAlmostEqual(quality['values']['calibration'], .515)
        self.assertAlmostEqual(quality['values']['evaluation'], .355)
        self.assertFalse(quality['passed'])
        key = ('etri-dev0007-jetagx', 'cuda-torch', 'vision')
        results = {key: dict(execution='executed', parity='passed')}
        executed = lambda q: dict(status='executed', complete=True, stages=[key], quality=q)
        self.assertEqual(qualify.main_plan_candidates({'c': executed(quality)}, results), [])
        self.assertEqual(qualify.main_plan_candidates({'c': executed(dict(quality, passed=True))}, results), ['c'])

    def test_missing_answers_are_zero_imputed(self):
        answers = {q: r['answers'][0] for q, r in REFS.items()}
        answers[next(iter(answers))] = None
        self.assertLess(qualify.composition_quality(answers)['values']['calibration'] + 1e-12, .515 + 1e-12)


class FakeStage:
    def __init__(self, backend, device):
        self.backend, self.device = backend, device

    def run(self, data):
        return b'out', dict(output_device=self.device)

    def describe(self):
        return dict(stage='vision', backend=self.backend, device=self.device, model_sha256='m')


class Worker(unittest.TestCase):
    def test_unavailable_backend_stays_failed_and_never_serves(self):
        def loader(name, backend, **kw):
            raise stages.BackendUnavailable('no CUDA device')
        w = worker_module.Worker('cuda-torch', ['vision'], loader=loader)
        self.assertEqual(w.health()['errors']['vision']['status'], 'dependency_unavailable')
        self.assertEqual(w.run(dict(stage='vision', data=b'x'))[0], 503)

    def test_stage_loaded_on_another_backend_is_a_load_failure(self):
        w = worker_module.Worker('cuda-torch', ['vision'], loader=lambda name, backend, **kw: FakeStage('cpu-torch', 'cpu'))
        self.assertEqual(w.health()['errors']['vision']['type'], 'DeviceMismatch')

    def test_output_on_another_device_is_refused(self):
        stage = FakeStage('cuda-torch', 'cuda:0')
        stage.run = lambda data: (b'out', dict(output_device='cpu'))
        w = worker_module.Worker('cuda-torch', ['vision'], loader=lambda name, backend, **kw: stage)
        with self.assertRaises(stages.DeviceMismatch):
            w.run(dict(stage='vision', data=b'x'))

    def test_http_round_trip_carries_the_receipt(self):
        w = worker_module.Worker('cuda-torch', ['vision'], loader=lambda name, backend, **kw: FakeStage('cuda-torch', 'cuda:0'))
        server = worker_module.serve(w, host='127.0.0.1', port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_port}'
        reply = qualify.http(url+'/run', dict(run_id='r', request_id='1', stage='vision', data=b'payload'))
        self.assertEqual(base64.b64decode(reply['data']), b'out')
        self.assertEqual(reply['receipt']['device'], 'cuda:0')
        self.assertEqual(reply['receipt']['input_sha256'], __import__('hashlib').sha256(b'payload').hexdigest())
        self.assertEqual(qualify.http(url+'/health')['status'], 'ready')


if __name__ == '__main__':
    unittest.main()
