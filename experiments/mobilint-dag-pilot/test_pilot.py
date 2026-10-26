"""Offline tests of the Mobilint DAG pilot (no cluster, no Mobilint device; the stub backend exists only here).

  uv run --python python3.11 --no-build --with numpy==1.26.4 --with Pillow==11.3.0 --with onnxruntime==1.23.2 \
      python -m unittest -v test_pilot
"""
import base64
import io
import json
import os
from pathlib import Path
import tarfile
import threading
import time
import unittest
from unittest import mock

import numpy as np
from PIL import Image

import analyze
import bounded_http
from http_runtime import encode_body
import live
import prepare
import protocol as P
import worker

ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT/'.scratch/mobilint-dag-pilot/frozen-protocol.json'
DOWNLOADS = ROOT/'.scratch/mobilint-dag-pilot/assets/downloads'


def jpeg(w, h, seed=0):
    arr = np.random.default_rng(seed).integers(0, 256, (h, w, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format='JPEG', quality=90)
    return buf.getvalue()


def fake_members():
    return [f"{P.IMAGENETV2['prefix']}{c}/{c:04d}{i}.jpeg" for c in range(1000) for i in range(10)]


class ProtocolTest(unittest.TestCase):
    def test_members_validated(self):
        members = P.imagenetv2_members(fake_members() + [P.IMAGENETV2['prefix'], P.IMAGENETV2['prefix']+'7/'])
        self.assertEqual(len(members), 10000)
        self.assertEqual(members, sorted(members))
        with self.assertRaises(ValueError):
            P.imagenetv2_members(fake_members()[:-1])
        with self.assertRaises(ValueError):
            P.imagenetv2_members(fake_members() + [P.IMAGENETV2['prefix']+'1000/x.jpeg'])

    def test_selection_deterministic_and_disjoint(self):
        members = P.imagenetv2_members(fake_members())
        ev, wu = P.select(members)
        self.assertEqual((ev, wu), P.select(members))
        self.assertEqual(len({s['member'] for s in ev}), 200)
        self.assertEqual(len(wu), 10)
        self.assertFalse({s['member'] for s in ev} & {s['member'] for s in wu})
        self.assertNotEqual(ev, P.select(members, seed=P.SEED+7)[0])

    def test_schedule_pairs_every_sample_once_per_arm(self):
        members = P.imagenetv2_members(fake_members())
        ev, wu = P.select(members)
        parity = [dict(id=f'coco-{i}') for i in range(3)]
        rows = P.schedule(ev, wu, parity)
        self.assertEqual([r['seq'] for r in rows], list(range(len(rows))))
        self.assertEqual([r['arm'] for r in rows[:10]], list(P.ARMS)*5)
        body = rows[10:]
        for i in range(0, len(body), 2):
            a, b = body[i], body[i+1]
            self.assertEqual((a['sample'], a['phase']), (b['sample'], b['phase']))
            self.assertEqual({a['arm'], b['arm']}, set(P.ARMS))
        self.assertEqual(len([r for r in body if r['phase'] == 'eval']), 400)
        self.assertTrue(all(r['phase'] == 'parity' for r in body[400:]))
        firsts = [body[i]['arm'] for i in range(0, 400, 2)]
        self.assertTrue(40 < firsts.count('mobilint') < 160)

    def test_torchvision_geometry(self):
        self.assertEqual(P.resize_shape(500, 375), (341, 256))
        self.assertEqual(P.resize_shape(375, 500), (256, 341))
        self.assertEqual(P.resize_shape(256, 256), (256, 256))
        self.assertEqual(P.center_crop_box(341, 256), (58, 16, 282, 240))  # round(117/2)=58 (banker's), round(32/2)=16

    @unittest.skipUnless(FROZEN.exists(), 'frozen protocol not prepared')
    def test_frozen_file_consistent(self):
        f = json.loads(FROZEN.read_text())
        self.assertEqual(f['schedule'], P.schedule(f['evaluation'], f['warmup'], f['parity']))
        self.assertEqual((len(f['evaluation']), len(f['warmup']), len(f['parity'])), (200, 10, 60))
        self.assertEqual(f['assets'], P.ASSETS)
        self.assertNotIn('code', f)
        self.assertTrue(all(s['label'] is None for s in f['parity']))


class StageTest(unittest.TestCase):
    def test_preprocess_shape_and_determinism(self):
        for w, h in ((500, 375), (300, 640), (224, 224)):
            data = jpeg(w, h)
            a, b = worker.preprocess(data), worker.preprocess(data)
            self.assertEqual(a.shape, (224, 224, 3))
            self.assertEqual(a.dtype, np.uint8)
            self.assertTrue(np.array_equal(a, b))

    def test_preprocess_matches_manual_pil_path(self):
        data = jpeg(640, 480, seed=3)
        img = Image.open(io.BytesIO(data)).convert('RGB').resize((341, 256), Image.BILINEAR).crop((58, 16, 282, 240))
        self.assertTrue(np.array_equal(worker.preprocess(data), np.asarray(img)))

    def test_postprocess(self):
        x = np.zeros(1000, dtype=np.float32)
        x[[5, 9, 1]] = [3, 2, 1]
        top, prob = worker.postprocess(x)
        self.assertEqual(top[:3], [5, 9, 1])
        self.assertAlmostEqual(sum(worker.postprocess(np.ones(1000))[1]), 5/1000)
        for bad in (np.zeros(999), np.full(1000, np.nan)):
            with self.assertRaises(ValueError):
                worker.postprocess(bad)

    def test_vendor_hwc_normalization(self):
        """Mobilint arm input = mblt-vision Normalize(torch) on HWC: float32, same values as the CPU arm's NCHW input."""
        arr = np.random.default_rng(1).integers(0, 256, (224, 224, 3), dtype=np.uint8)
        x = worker.normalize_hwc_vendor(arr)
        self.assertEqual((x.shape, x.dtype, x.flags.c_contiguous, x.flags.writeable), ((224, 224, 3), np.float32, True, True))
        ref = ((arr.astype(np.float32) / 255.0 - np.array([0.485, 0.456, 0.406])) / np.array([0.229, 0.224, 0.225])).astype(np.float32)
        self.assertTrue(np.array_equal(x, ref))
        self.assertTrue(np.allclose(x.transpose(2, 0, 1)[None], worker.normalize_nchw(arr), atol=1e-6))

    def test_mobilint_infer_feeds_vendor_float32(self):
        fake = mock.Mock()
        fake.infer.return_value = [np.zeros((1, 1, 1000), np.float32)]
        backend = worker.MobilintBackend.__new__(worker.MobilintBackend)
        backend.model = fake
        arr = np.full((224, 224, 3), 255, np.uint8)
        self.assertEqual(backend.infer(arr).shape, (1000,))
        sent = fake.infer.call_args[0][0][0]
        self.assertEqual((sent.dtype, sent.shape), (np.float32, (224, 224, 3)))
        self.assertAlmostEqual(float(sent[0, 0, 0]), (1-0.485)/0.229, places=5)

    def test_normalize(self):
        arr = np.full((224, 224, 3), 255, dtype=np.uint8)
        x = worker.normalize_nchw(arr)
        self.assertEqual(x.shape, (1, 3, 224, 224))
        self.assertAlmostEqual(float(x[0, 0, 0, 0]), (1-0.485)/0.229, places=5)
        self.assertAlmostEqual(float(x[0, 2, 0, 0]), (1-0.406)/0.225, places=5)


FROZEN_SHA = 'f'*64
MODEL_SHA = {'mobilint': 'a'*64, 'cpu-fp32': 'b'*64}


class StubBackend:
    """Test-only stand-in for a model; logits encode the tensor checksum so routing errors are visible."""

    def __init__(self, name, delay=0.0):
        self.name, self.delay, self.calls = name, delay, 0
        self.evidence = dict(runtime='stub', runtime_version='0.0-'+name)

    def infer(self, arr):
        self.calls += 1
        time.sleep(self.delay)
        x = np.zeros(1000, dtype=np.float32)
        x[int(arr.sum()) % 1000] = 10
        return x, 0.1


class ForeignService(worker.InferService):
    """Test-only: a worker answering with another execution's identity."""

    def __init__(self, *args, field, value, **kw):
        super().__init__(*args, **kw)
        self.field, self.value = field, value

    def handle(self, body):
        return dict(super().handle(body), **{self.field: self.value})


def serve(app):
    server = worker.ThreadingHTTPServer(('127.0.0.1', 0), worker.Handler)
    server.app = app
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_address[1]}'


def frozen_stub():
    images = {f's{i}': jpeg(320+i, 240, seed=i) for i in range(3)}
    samples = [dict(id=k, label=i, file=f'images/{k}.jpeg', sha256=P.sha256(v)) for i, (k, v) in enumerate(images.items())]
    ev, wu = samples[:2], samples[2:]
    return dict(evaluation=ev, warmup=wu, parity=[], schedule=P.schedule(ev, wu)), images


def expected(arm):
    return dict(pod_uid='uid-test', node='node-test', model_sha256=MODEL_SHA[arm], runtime_version='0.0-'+arm, frozen_sha256=FROZEN_SHA)


class LoopbackDagTest(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch.object(worker, 'FROZEN_SHA', FROZEN_SHA),
                   mock.patch.dict(os.environ, {'POD_UID': 'uid-test', 'NODE_NAME': 'node-test'})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.servers, self.urls, self.backends = [], {}, {}
        for arm in P.ARMS:
            self.urls[arm] = self.infer_server(arm, delay=0.05 if arm == 'cpu-fp32' else 0)
        frozen, images = frozen_stub()
        state = worker.State('edge')
        state.state = 'ready'
        self.edge = worker.Edge(state, frozen, images)
        server, self.edge_url = serve(dict(state=state, edge=self.edge))
        self.servers.append(server)
        self.frozen = frozen
        self.identities = {a: expected(a) for a in P.ARMS}

    def infer_server(self, arm, delay=0.0, cls=worker.InferService, **kw):
        state = worker.State(arm)
        state.state = 'ready'
        self.backends[arm] = StubBackend(arm, delay=delay)
        server, url = serve(dict(state=state, infer=cls(state, self.backends[arm], MODEL_SHA[arm], **kw)))
        self.servers.append(server)
        return url

    def tearDown(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()

    def post(self, path, body):
        return bounded_http.control(self.edge_url+path, body, timeout=10)

    def configure(self):
        return self.post('/configure', dict(run_id='mdp-test', urls=self.urls, identities=self.identities))

    def test_trace_runs_frozen_schedule_on_both_arms(self):
        self.configure()
        self.post('/trace', dict(label='main'))
        for _ in range(100):
            if bounded_http.control(self.edge_url+'/status')['traces']['main']['state'] != 'running':
                break
            time.sleep(0.05)
        rows = bounded_http.control(self.edge_url+'/ledger?label=main')['rows']
        self.assertEqual(bounded_http.control(self.edge_url+'/ledger?label=main&since=4')['rows'], rows[4:])
        self.assertEqual([r['seq'] for r in rows], [r['seq'] for r in self.frozen['schedule']])
        self.assertTrue(all(r['status'] == 'ok' for r in rows), rows)
        self.assertEqual({r['receipt']['arm'] for r in rows}, set(P.ARMS))
        for r in rows:
            self.assertEqual(r['request_body_bytes'], len(encode_body(dict(run_id='mdp-test', request_id=f"mdp-test-{r['seq']}-{r['arm']}",
                             arm=r['arm'], shape=[224, 224, 3], dtype='uint8', sha256=r['tensor_sha256'], data=b'\0'*150528))[0]))
            self.assertLessEqual(r['pre_ms'] + r['rpc_ms'] + r['post_ms'], r['e2e_ms'] + 1e-6)
            self.assertGreaterEqual(r['rpc_ms'], r['receipt']['server_ms'])
            self.assertEqual(r['receipt']['input_sha256'], r['tensor_sha256'])
        pairs = {}
        for r in rows:
            pairs.setdefault((r['phase'], r['sample']), set()).add(r['tensor_sha256'])
        self.assertTrue(all(len(v) == 1 for v in pairs.values()))
        self.assertEqual(self.backends['mobilint'].calls, 3)
        with self.assertRaises(Exception):
            self.configure()

    def test_configure_requires_identities(self):
        with self.assertRaises(Exception) as ctx:
            self.post('/configure', dict(run_id='mdp-test', urls=self.urls))
        self.assertEqual(ctx.exception.code, 400)

    def test_foreign_identity_is_never_ok(self):
        """R1 repro: foreign request id / node / Pod UID / input / model / runtime / frozen hash -> identity_mismatch."""
        for field, value in (('request_id', 'different-run-different-input'), ('node', 'wrong-node'), ('pod_uid', 'not-owned-uid'),
                             ('run_id', 'mdp-other'), ('input_sha256', '0'*64), ('model_sha256', 'c'*64),
                             ('runtime_version', '9.9'), ('frozen_sha256', 'e'*64)):
            with self.subTest(field=field):
                self.edge.urls = dict(self.urls, mobilint=self.infer_server('mobilint', cls=ForeignService, field=field, value=value))
                self.edge.identities, self.edge.run_id = self.identities, 'mdp-test'
                rec = self.edge.request(dict(seq=0, phase='eval', sample='s0', arm='mobilint'))
                self.assertEqual(rec['status'], 'identity_mismatch', rec)
                self.assertIn(field, rec['error'])
                self.assertNotIn('top5', rec)

    def test_timeout_and_errors_are_recorded_not_retried(self):
        self.backends['cpu-fp32'].delay = 1.0
        self.configure()
        rec = self.edge.request(dict(seq=0, phase='eval', sample='s0', arm='cpu-fp32'), deadline_s=0.3)
        self.assertEqual(rec['status'], 'rpc_timeout')
        self.assertGreaterEqual(rec['e2e_ms'], 300)
        self.assertGreaterEqual(rec['rpc_ms'], 300)
        self.assertEqual(self.backends['cpu-fp32'].calls, 1)
        time.sleep(1.0)
        self.edge.urls['mobilint'] = 'http://127.0.0.1:9'
        rec = self.edge.request(dict(seq=1, phase='eval', sample='s0', arm='mobilint'))
        self.assertEqual(rec['status'], 'error')
        self.assertIn('e2e_ms', rec)

    def test_infer_rejects_wrong_arm_and_tensor(self):
        data = np.zeros((224, 224, 3), np.uint8).tobytes()
        good = dict(run_id='r', request_id='q', arm='mobilint', shape=[224, 224, 3], dtype='uint8', sha256=P.sha256(data), data=data)
        url = self.urls['mobilint']+'/infer'
        receipt = bounded_http.post(url, good)
        self.assertEqual((receipt['arm'], receipt['run_id'], receipt['request_id'], receipt['input_sha256'], receipt['model_sha256']),
                         ('mobilint', 'r', 'q', P.sha256(data), MODEL_SHA['mobilint']))
        for bad in (dict(good, arm='cpu-fp32'), dict(good, sha256='0'*64), dict(good, data=data[:-1]), dict(good, dtype='float32')):
            with self.assertRaises(Exception) as ctx:
                bounded_http.post(url, bad)
            self.assertEqual(getattr(ctx.exception, 'code', None), 400)

    def test_not_ready_worker_refuses(self):
        state = worker.State('mobilint')
        state.state, state.error = 'failed', 'launch failed'
        server, url = serve(dict(state=state))
        self.servers.append(server)
        self.assertEqual(bounded_http.control(url+'/health')['state'], 'failed')
        with self.assertRaises(Exception) as ctx:
            bounded_http.post(url+'/infer', dict(arm='mobilint'))
        self.assertEqual(ctx.exception.code, 503)


class QuickBackend:
    """Test-only child-process backends for the terminable boundary."""
    name = 'quick'

    def __init__(self, path):
        self.evidence = dict(runtime='quick', runtime_version='0', pid=os.getpid())

    def infer(self, arr):
        if arr[0, 0, 0] == 7:
            time.sleep(30)
        return np.full(1000, float(arr.sum() % 13), dtype=np.float32)


class WriteableCheckBackend(QuickBackend):
    """Test-only: fails like qbruntime did when handed a read-only buffer."""

    def infer(self, arr):
        if not arr.flags.writeable or arr.dtype != np.uint8 or arr.shape != (224, 224, 3):
            raise ValueError('array is not writeable')
        return np.full(1000, float(arr[0, 0, 0]), dtype=np.float32)


class SlowLoadBackend(QuickBackend):
    def __init__(self, path):
        time.sleep(30)


class FailLoadBackend(QuickBackend):
    def __init__(self, path):
        raise RuntimeError('device open failed')


class BackendProcessTest(unittest.TestCase):
    """R1 repro: the load deadline is enforced by terminating the native child, with persistent evidence."""

    def make(self, factory, tmp, load_s=5.0, call_s=1.0):
        return worker.BackendProcess('quick', f'test_pilot:{factory}', '/none', [str(Path(__file__).resolve().parent)],
                                     load_s=load_s, call_s=call_s, evidence_path=Path(tmp)/'ev.json')

    def test_load_timeout_kills_child_and_persists(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            start = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, 'load_timeout'):
                self.make('SlowLoadBackend', tmp, load_s=1.0)
            self.assertLess(time.monotonic()-start, 10)
            ev = json.loads(Path(tmp, 'ev.json').read_text())
            self.assertEqual((ev['state'], ev['reason'], ev['child_alive_after_kill']), ('dead', 'load_timeout', False))

    def test_load_failure_reported(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, 'device open failed'):
                self.make('FailLoadBackend', tmp)
            self.assertEqual(json.loads(Path(tmp, 'ev.json').read_text())['reason'], 'load_failed')

    def test_child_passes_writeable_uint8_copy(self):
        """Probe mdp-20261026t091406 regression: the backend receives a writeable uint8 HWC array with unchanged values."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            b = self.make('WriteableCheckBackend', tmp)
            arr = np.full((224, 224, 3), 200, np.uint8)
            logits, _ = b.infer(arr)
            self.assertEqual(float(logits[0]), 200.0)
            b.kill('test-done')

    def test_call_timeout_kills_and_never_reuses(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            b = self.make('QuickBackend', tmp)
            self.assertNotEqual(b.evidence['pid'], os.getpid())
            logits, infer_ms = b.infer(np.ones((224, 224, 3), np.uint8))
            self.assertEqual((logits.shape, float(logits[0])), ((1000,), float((224*224*3) % 13)))
            hang = np.zeros((224, 224, 3), np.uint8)
            hang[0, 0, 0] = 7
            with self.assertRaises(TimeoutError):
                b.infer(hang)
            self.assertFalse(b.proc.is_alive())
            with self.assertRaisesRegex(RuntimeError, 'not reused'):
                b.infer(np.ones((224, 224, 3), np.uint8))
            self.assertEqual(json.loads(Path(tmp, 'ev.json').read_text())['reason'], 'call_timeout')


class BackendContractTest(unittest.TestCase):
    def test_mobilint_backend_has_no_cpu_path(self):
        source = Path(worker.__file__).read_text()
        start, end = source.index('class MobilintBackend'), source.index('class OrtCpuBackend')
        self.assertNotIn('onnxruntime', source[start:end])
        with mock.patch.dict('sys.modules', {'qbruntime': None}):
            with self.assertRaises(ImportError):
                worker.MobilintBackend('/nonexistent.mxq')

    @unittest.skipUnless((DOWNLOADS/'hf-resnet50/resnet50_IMAGENET1K_V1.onnx').exists() and FROZEN.exists(), 'assets not downloaded')
    def test_onnx_contract_on_non_selected_image(self):
        """Input contract check on one ImageNetV2 image that is NOT in the frozen evaluation/warmup selection."""
        frozen = json.loads(FROZEN.read_text())
        used = {s['member'] for s in frozen['evaluation'] + frozen['warmup']}
        with tarfile.open(DOWNLOADS/'imagenetv2'/P.IMAGENETV2['file'], 'r:') as archive:
            member = next(m for m in archive if m.name.endswith('.jpeg') and m.name not in used)
            data = archive.extractfile(member).read()
        label = int(member.name.split('/')[-2])
        backend = worker.OrtCpuBackend(DOWNLOADS/'hf-resnet50/resnet50_IMAGENET1K_V1.onnx')
        self.assertEqual(backend.evidence['providers'], ['CPUExecutionProvider'])
        top, _ = worker.postprocess(backend.infer(worker.preprocess(data)))
        print(f'\ncontract image {member.name} label {label} top5 {top}')
        self.assertIn(label, top)


class LiveManifestTest(unittest.TestCase):
    def test_pod_security_and_resources(self):
        for key in live.KEYS:
            p = live.pod('mdp-x', key, 'node-a', 'f'*64, 'http://c:8080')
            spec, c = p['spec'], p['spec']['containers'][0]
            self.assertFalse(any(spec.get(k) for k in ('hostNetwork', 'hostPID', 'hostIPC')))
            self.assertFalse(any('hostPath' in v for v in spec['volumes']))
            self.assertFalse(c['securityContext'].get('privileged'))
            self.assertFalse(c['securityContext']['allowPrivilegeEscalation'])
            self.assertEqual(c['securityContext']['capabilities'], dict(drop=['ALL']))
            self.assertEqual(spec['nodeAffinity'] if 'nodeAffinity' in spec else
                             spec['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms'][0]['matchFields'][0]['values'], ['node-a'])
            self.assertEqual(c['resources']['requests']['cpu'], c['resources']['limits']['cpu'])
            self.assertEqual('mobilint.com/npu' in c['resources']['limits'], key == 'mobilint')
            self.assertEqual(c['image'], P.MOBILINT_IMAGE if key == 'mobilint' else P.PY_IMAGE)
            self.assertEqual(p['metadata']['labels']['run-id'], 'mdp-x')
            self.assertFalse(spec['automountServiceAccountToken'])
        self.assertEqual(live.pod('mdp-x', 'mobilint', 'n', 'f'*64)['spec']['containers'][0]['resources']['requests']['mobilint.com/npu'], '1')

    def node(self, name, ready=True, taints=None, npu=None):
        alloc = dict(cpu='4', memory='8Gi', **({'mobilint.com/npu': npu} if npu else {}))
        return dict(metadata=dict(name=name), spec=dict(taints=taints) if taints else {},
                    status=dict(allocatable=alloc, conditions=[dict(type='Ready', status='True' if ready else 'False')]))

    def podobj(self, node, cpu='100m', npu=False, phase='Running'):
        req = dict(cpu=cpu, memory='64Mi', **({'mobilint.com/npu': '1'} if npu else {}))
        return dict(metadata=dict(name='p', namespace='ns', uid='u'), status=dict(phase=phase),
                    spec=dict(nodeName=node, containers=[dict(resources=dict(requests=req, limits=req))]))

    def test_choose(self):
        nodes = [self.node(P.SERVER, npu='1'), self.node(P.EDGE_CANDIDATES[0], ready=False),
                 self.node(P.EDGE_CANDIDATES[1], taints=[dict(key='x')]), self.node(P.EDGE_CANDIDATES[2])]
        edge, report = live.choose(nodes, [self.podobj(P.SERVER)])
        self.assertEqual(edge, P.EDGE_CANDIDATES[2])
        self.assertEqual(report['server_free']['cpu_free_m'], 3900)
        with self.assertRaisesRegex(RuntimeError, 'already held'):
            live.choose(nodes, [self.podobj(P.SERVER, npu=True)])
        edge, _ = live.choose(nodes, [self.podobj(P.SERVER, npu=True, phase='Succeeded')])
        with self.assertRaisesRegex(RuntimeError, 'lacks free'):
            live.choose(nodes, [self.podobj(P.SERVER, cpu='3')])
        with self.assertRaisesRegex(RuntimeError, 'allocatable'):
            live.choose([self.node(P.SERVER)] + nodes[1:], [])
        with self.assertRaisesRegex(RuntimeError, 'no edge candidate'):
            live.choose(nodes[:3], [])

    def test_release_binding(self):
        code = live.dependency_hashes()
        ok = dict(frozen_sha256='a', code=code, approved_by='Astra', stages=['probe'])
        self.assertEqual(live.check_release(ok, 'a', code), [])
        self.assertEqual(len(live.check_release(dict(ok, frozen_sha256='b'), 'a', code)), 1)
        self.assertEqual(len(live.check_release(dict(ok, code={}), 'a', code)), 1)
        self.assertEqual(len(live.check_release(dict(ok, approved_by=None), 'a', code)), 1)

    def test_dependency_closure_is_bound(self):
        """R1 repro: modules the collector executes (collect.py and its imports) are in the release hashes."""
        code = live.dependency_hashes()
        for path in ('experiments/multi-edge-sensors/collect.py', 'experiments/real-topology-live/run_baseline_comparison.py',
                     'experiments/real-topology-live/run_smoke.py', 'experiments/mobilint-dag-pilot/live.py',
                     'experiments/mobilint-dag-pilot/worker.py', 'experiments/mobilint-dag-pilot/protocol.py',
                     'experiments/quality-network-baselines/bounded_http.py', 'experiments/real-topology-live/http_runtime.py'):
            self.assertIn(path, code)
        self.assertNotIn('experiments/mobilint-dag-pilot/test_pilot.py', code)

    def test_stream_guard(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            s = live.Session(tmp, 'mdp-guard')
            s.expected = {'edge': ('edge-node', True), 'cache': (P.SERVER, False)}
            with mock.patch.object(live.collect.Session, 'call', return_value='') as called:
                with self.assertRaisesRegex(RuntimeError, 'refused'):
                    s.call('x', ['exec', '-n', P.NS, 'mdp-guard-edge', '--', 'true'])
                self.assertEqual(s.call('y', ['logs', '-n', P.NS, 'mdp-guard-edge']), '')
                with self.assertRaisesRegex(RuntimeError, 'refused'):
                    s.call('z', ['exec', '-n', P.NS, 'someone-else', '--', 'true'])
                self.assertFalse(called.called)
                called.return_value = json.dumps(dict(metadata=dict(uid='other'), spec=dict(nodeName=P.SERVER)))
                s.resources.append(dict(kind='Pod', name='mdp-guard-cache', uid='mine'))
                with self.assertRaisesRegex(RuntimeError, 'refused'):
                    s.call('w', ['exec', '-n', P.NS, 'mdp-guard-cache', '--', 'true'])
                self.assertEqual(called.call_count, 1)  # only the ownership GET, never the exec
            self.assertEqual(len(list(Path(tmp, 'mdp-guard').glob('*.refused.json'))), 3)
        with self.assertRaises(ValueError):
            live.Session('/tmp', 'qnh-bad')


class FakeSession(live.Session):
    """Test-only: live.Session whose control/kubectl seams are scripted."""

    def __init__(self, tmp, control=None):
        super().__init__(tmp, 'mdp-fake')
        self.script, self.calls = control, []

    def control(self, url, body=None, timeout=30):
        self.calls.append(url)
        return self.script(url, body)


class CollectorTest(unittest.TestCase):
    def test_transient_status_failure_keeps_polling_and_checkpoints(self):
        """R1 repro: one /status reset no longer loses the ledger."""
        import tempfile
        rows = [dict(seq=i, status='ok') for i in range(3)]
        state = dict(n=0)

        def script(url, body):
            if '/ledger' in url:
                since = int(url.rsplit('since=', 1)[1])
                return dict(rows=rows[since:min(len(rows), state['n'])])
            state['n'] += 1
            if state['n'] == 2:
                raise ConnectionResetError('reset')
            return dict(traces=dict(main=dict(state='running' if state['n'] < 4 else 'done', done=min(3, state['n']))))
        with tempfile.TemporaryDirectory() as tmp:
            s = FakeSession(tmp, script)
            live.trace(s, 'http://edge', sleep=lambda _: None)
            lines = (s.out/'ledger-main.jsonl').read_text().splitlines()
            self.assertEqual([json.loads(x)['seq'] for x in lines], [0, 1, 2])
            status = json.loads((s.out/'trace-main-status.json').read_text())
            self.assertEqual(len(status['poll_errors']), 1)

    def test_persistent_failure_raises_after_deadline_with_checkpoint(self):
        import tempfile
        clock = iter(range(0, 100000, 100)).__next__

        def script(url, body):
            raise ConnectionResetError('down')
        with tempfile.TemporaryDirectory() as tmp:
            s = FakeSession(tmp, script)
            with self.assertRaisesRegex(RuntimeError, 'did not finish'):
                live.trace(s, 'http://edge', sleep=lambda _: None, clock=clock)
            self.assertTrue((s.out/'trace-main-status.json').exists())

    def run_main(self, tmp, execute_error=None, delete_status='deleted', present=False):
        frozen = Path(tmp)/'frozen.json'
        frozen.write_text('{}')
        release = Path(tmp)/'release.json'
        release.write_text(json.dumps(dict(frozen_sha256=prepare.file_sha(frozen), code=live.dependency_hashes(),
                                           approved_by='Astra', stages=['full'])))
        order = []

        def execute(session, *args):
            session.resources.append(dict(kind='Pod', name='mdp-fake-edge', uid='u1'))
            session.urls = {'edge': 'http://edge'}
            order.append('execute')
            if execute_error:
                raise execute_error

        def delete_owned(self, stem, names):
            order.append('delete')
            for r in self.resources:
                r['delete'] = delete_status

        def control(self, url, body=None, timeout=30):
            order.append('salvage:'+url.split('/')[-1])
            return dict(rows=[dict(seq=0)]) if 'ledger' in url else dict(state='ready')
        with mock.patch.object(live, 'execute', execute), \
                mock.patch.object(live.Session, 'delete_owned', delete_owned), \
                mock.patch.object(live.Session, 'control', control), \
                mock.patch.object(live.Session, 'owned_present', lambda self, r: present), \
                mock.patch.object(live, 'protected', lambda s, label: order.append(label)):
            try:
                live.main(['--prepared', tmp, '--frozen', str(frozen), '--runs', tmp, '--release', str(release), '--stage', 'full',
                           '--run-id', 'mdp-fake'])
                outcome = None
            except BaseException as error:
                outcome = error
        return order, outcome, json.loads(Path(tmp, 'mdp-fake', 'cleanup.json').read_text())

    def test_salvage_before_cleanup_and_original_error_kept(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            order, outcome, cleanup = self.run_main(tmp, execute_error=RuntimeError('status reset'))
            self.assertIsInstance(outcome, RuntimeError)
            self.assertLess(order.index('salvage:ledger?label=main'), order.index('delete'))
            self.assertIn('status reset', cleanup['error'])
            self.assertTrue(cleanup['verified'])
            self.assertTrue(Path(tmp, 'mdp-fake', 'salvage-ledger-main.json').exists())

    def test_salvage_ledger_independent_of_health(self):
        """R2: a /health reset does not prevent the ledger GET, which is attempted first."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            def script(url, body):
                if url.endswith('/health'):
                    raise ConnectionResetError('reset')
                return dict(rows=[dict(seq=0)])
            s = FakeSession(tmp, script)
            s.urls = {'mobilint': 'http://m', 'edge': 'http://edge'}
            out = live.salvage(s)
            self.assertEqual(s.calls[0], 'http://edge/ledger?label=main')
            self.assertEqual(out['ledger-main'], 'saved')
            self.assertTrue(out['health-edge'].startswith('error'))
            self.assertTrue((s.out/'salvage-ledger-main.json').exists())

    def test_cleanup_runs_when_salvage_raises(self):
        """R2: salvage OSError or KeyboardInterrupt still reaches cleanup and verification; the run is not complete."""
        import tempfile
        for exc in (OSError('disk full'), KeyboardInterrupt()):
            for execute_error in (None, RuntimeError('trace failed')):
                with self.subTest(exc=type(exc).__name__, execute_error=execute_error), tempfile.TemporaryDirectory() as tmp:
                    with mock.patch.object(live, 'salvage', side_effect=exc):
                        order, outcome, cleanup = self.run_main(tmp, execute_error=execute_error)
                    self.assertIn('delete', order)
                    self.assertTrue(cleanup['verified'])
                    self.assertIn(type(exc).__name__, cleanup['recovery_errors'][0])
                    if execute_error:
                        self.assertIs(outcome, execute_error)
                        self.assertIn('trace failed', cleanup['error'])
                    else:
                        self.assertIsInstance(outcome, SystemExit)

    def test_unverified_cleanup_fails_completion(self):
        """R1 repro: a delete error or a still-present UID makes the run fail, with the record preserved."""
        import tempfile
        def api_down(self, record):
            raise RuntimeError('api down')
        for status, present in (('error', True), ('deletion-timeout', True), ('deleted', api_down)):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                order, outcome, cleanup = self.run_main(tmp, delete_status=status, present=present)
                self.assertIsInstance(outcome, SystemExit)
                self.assertFalse(cleanup['verified'])
                self.assertEqual(cleanup['records'][0]['uid'], 'u1')

    def test_verified_cleanup_succeeds_and_unresolved_fails(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            order, outcome, cleanup = self.run_main(tmp)
            self.assertIsNone(outcome)
            self.assertTrue(cleanup['verified'])
        with tempfile.TemporaryDirectory() as tmp:
            s = FakeSession(tmp)
            s.unresolved.append(dict(kind='Pod', name='mdp-fake-x', stem='workers'))
            self.assertEqual(len(live.verify_cleanup(s)), 1)

    def test_occupancy_must_be_exactly_owned(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            s = FakeSession(tmp)
            s.resources.append(dict(kind='Pod', name='mdp-fake-mobilint', uid='mine'))

            def pods(*holders):
                return json.dumps(dict(items=[dict(metadata=dict(name='p', namespace='n', uid=u), status=dict(phase='Running'),
                    spec=dict(nodeName=P.SERVER, containers=[dict(resources=dict(limits={'mobilint.com/npu': '1'}))])) for u in holders]))
            with mock.patch.object(live.collect.Session, 'call', return_value=pods('mine')):
                live.check_occupancy(s, 'occ-1')
            for holders in (('mine', 'other'), ('other',), ()):
                with mock.patch.object(live.collect.Session, 'call', return_value=pods(*holders)):
                    with self.assertRaises(RuntimeError):
                        live.check_occupancy(s, 'occ-'+'-'.join(holders))


class AnalyzeTest(unittest.TestCase):
    def frozen(self):
        ev = [dict(id=f'v{i}', label=i) for i in range(4)]
        wu = [dict(id=f'w{i}', label=0) for i in range(2)]
        par = [dict(id='c0', label=None)]
        return dict(evaluation=ev, warmup=wu, parity=par, schedule=P.schedule(ev, wu, par))

    RUN = 'mdp-a'
    IDS = {a: dict(pod_uid='u-'+a, node='n', model_sha256=MODEL_SHA[a], runtime_version='r-'+a, frozen_sha256=FROZEN_SHA) for a in P.ARMS}

    def ok(self, s, top1, logits=None, tensor='t'):
        x = np.linspace(-1, 1, 1000, dtype='<f4') if logits is None else logits
        raw = x.astype('<f4').tobytes()
        rid = f"{self.RUN}-{s['seq']}-{s['arm']}"
        return dict(s, status='ok', top5=[top1, 900, 901, 902, 903], tensor_sha256=tensor+s['sample'], request_id=rid,
                    logits_b64=base64.b64encode(raw).decode(), e2e_ms=10.0, pre_ms=2.0, rpc_ms=7.0, post_ms=0.5,
                    jpeg_bytes=1000, request_body_bytes=150700, response_body_bytes=5600,
                    receipt=dict(self.IDS[s['arm']], arm=s['arm'], run_id=self.RUN, request_id=rid, input_sha256=tensor+s['sample'],
                                 logits_sha256=P.sha256(raw), infer_ms=3.0, server_ms=4.0))

    def rundir(self, tmp, rows, identities=None, uids=None):
        d = Path(tmp)/self.RUN
        d.mkdir()
        if rows is not None:
            (d/'ledger-main.json').write_text(json.dumps(dict(rows=rows)))
        (d/'identities.json').write_text(json.dumps(self.IDS if identities is None else identities))
        (d/'cleanup.json').write_text(json.dumps(dict(records=[dict(kind='Pod', uid=u) for u in (uids or ['u-mobilint', 'u-cpu-fp32'])])))
        return d

    def test_foreign_or_malformed_identity_rejected(self):
        """R1 repro: analysis re-verifies every ok row against identities.json and creation UIDs."""
        import tempfile
        f = self.frozen()
        base = [self.ok(s, 0) for s in f['schedule']]
        target = next(i for i, r in enumerate(base) if r['phase'] == 'eval' and r['arm'] == 'mobilint')
        cases = {'pod': dict(receipt=dict(base[target]['receipt'], pod_uid='not-owned-uid')),
                 'node': dict(receipt=dict(base[target]['receipt'], node='wrong-node')),
                 'request': dict(receipt=dict(base[target]['receipt'], request_id='different-run-different-input')),
                 'input': dict(receipt=dict(base[target]['receipt'], input_sha256='x')),
                 'logits': dict(logits_b64=base64.b64encode(b'\0'*4000).decode()),
                 'malformed': dict(receipt={}),
                 'no-top5': dict(top5=None)}
        for name, change in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                rows = [dict(r) for r in base]
                rows[target] = dict(rows[target], **change)
                out = analyze.analyze(self.rundir(tmp, rows), f)
                self.assertEqual(out['identity_rejected'], 1)
                self.assertEqual(out['phases']['eval']['mobilint']['status'], {'ok': 3, 'identity_rejected': 1})
        with tempfile.TemporaryDirectory() as tmp:
            out = analyze.analyze(self.rundir(tmp, base, uids=['someone-else']), f)
            self.assertEqual(out['identity_rejected'], len(base))
        with tempfile.TemporaryDirectory() as tmp:
            out = analyze.analyze(self.rundir(tmp, base, identities={}), f)
            self.assertEqual(out['identity_rejected'], len(base))

    def test_ledger_fallbacks(self):
        import tempfile
        f = self.frozen()
        base = [self.ok(s, 0) for s in f['schedule']]
        with tempfile.TemporaryDirectory() as tmp:
            d = self.rundir(tmp, None)
            (d/'ledger-main.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in base[:5]))
            out = analyze.analyze(d, f)
            self.assertEqual((out['ledger_source'], out['ledger_rows']), ('ledger-main.jsonl', 5))
            (d/'salvage-ledger-main.json').write_text(json.dumps(dict(rows=base)))
            self.assertEqual(analyze.analyze(d, f)['ledger_source'], 'salvage-ledger-main.json')

    def test_failures_missing_duplicates_and_accuracy(self):
        import tempfile
        f = self.frozen()
        rows = []
        for s in f['schedule']:
            if s['phase'] == 'eval' and s['sample'] == 'v3' and s['arm'] == 'mobilint':
                continue  # missing
            if s['phase'] == 'eval' and s['sample'] == 'v2' and s['arm'] == 'mobilint':
                rows.append(dict(s, status='rpc_timeout'))
                continue
            label = int(s['sample'][1:]) if s['phase'] == 'eval' else 0
            rows.append(self.ok(s, label if s['arm'] == 'cpu-fp32' or s['sample'] != 'v1' else 7))
        rows.append(dict(rows[-1]))  # duplicate of the last parity row
        rows.append(dict(seq=999, phase='eval', sample='zz', arm='mobilint', status='ok'))
        with tempfile.TemporaryDirectory() as tmp:
            out = analyze.analyze(self.rundir(tmp, rows), f)
        m, c = out['phases']['eval']['mobilint'], out['phases']['eval']['cpu-fp32']
        self.assertEqual(out['foreign_rows'], 1)
        self.assertEqual(m['status'], {'ok': 2, 'rpc_timeout': 1, 'missing_result': 1})
        self.assertEqual(m['accuracy']['top1'], 1/4)
        self.assertEqual(m['accuracy']['ok_only']['top1'], 1/2)
        self.assertEqual(c['accuracy']['top1'], 1.0)
        self.assertEqual(m['latency']['residual_ms']['p50'], 3.0)
        self.assertEqual(out['phases']['parity'][f['schedule'][-1]['arm']]['status'], {'duplicate_result': 1})
        par = out['parity']['eval']
        self.assertEqual((par['both_ok'], par['identical_input'], par['top1_agree']), (2, 2, 1))
        self.assertEqual(par['discordant_top1'], dict(mobilint_only=0, cpu_only=1))
        self.assertEqual(out['arm_order']['pairs'], 4)

    def test_parity_requires_identical_input(self):
        f = self.frozen()
        results = {}
        for s in f['schedule']:
            results[s['seq']] = analyze.flatten(self.ok(s, 1, tensor='a' if s['arm'] == 'mobilint' else 'b'))
        self.assertEqual(analyze.parity(results, {s['id']: s['label'] for s in f['evaluation']+f['warmup']+f['parity']}, 'eval')['identical_input'], 0)

    def test_no_ledger(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = analyze.analyze(self.rundir(tmp, None), self.frozen())
        self.assertFalse(out['ledger_present'])
        self.assertEqual(out['phases']['eval']['mobilint']['status'], {'missing_result': 4})
        self.assertEqual(out['phases']['eval']['mobilint']['accuracy']['top1'], 0.0)

    def test_quantile(self):
        self.assertEqual(analyze.quantile([1, 2, 3, 4], .5), 2.5)
        self.assertIsNone(analyze.quantile([], .5))
        lo, hi = analyze.wilson(190, 200)
        self.assertTrue(lo < .95 < hi)


if __name__ == '__main__':
    os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')
    unittest.main()
