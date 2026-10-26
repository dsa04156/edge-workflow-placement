"""Round-1 supplement regressions: the two partials developer-fix-r1.md left open (fix-r1-supplement-task.md).

S5 collector control plane: Live's default control-HTTP seam must end a call that a peer holds open by trickling bytes
(an inactivity timeout never fires) at an absolute deadline, cap the reply body, and give each blocking control call of a
bounded phase (job polling, drain) only that phase's remaining budget. Exercised against a real loopback HTTP peer.

S7 plan-side model provenance: the per-stage model / derived-artifact hashes a receipt must carry are bound to the frozen
assets manifest at plan creation; the UID-checked /health report is validated against them, never the source of them.
"""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_executor as fx  # noqa: E402,F401  (sets sys.path for qnh/real-topology-live)
import analyze  # noqa: E402
import baselines  # noqa: E402
import design  # noqa: E402
import live  # noqa: E402
import test_review_r1 as r1  # noqa: E402
from test_review_r1 import SavingSession, plan_models  # noqa: E402

FROZEN_PLAN = design.ROOT/'artifacts/quality-network-baselines/plan-20261026T031122Z'  # preserved 03:11 plan (read only)


class ControlPeer:
    """Loopback control peer. Paths in `trickle` answer 200 with a large declared length and then one byte every 20 ms
    until the test ends (never idle long enough for a socket timeout); '/big' answers `big` bytes of JSON at once; any
    other path answers {"status": "started"}."""

    def __init__(self, trickle=(), big=0):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        stop = self.stop = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def reply(self):
                path = self.path.split('?')[0]
                if path in trickle:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(1 << 20))
                    self.end_headers()
                    try:
                        while not stop.is_set():
                            self.wfile.write(b' ')
                            self.wfile.flush()
                            time.sleep(0.02)
                    except OSError:
                        pass
                    return
                body = (b'{"pad":"' + b'x' * big + b'"}') if path == '/big' else b'{"status":"started"}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass  # the client refused an over-cap reply after its headers

            def do_GET(self):
                self.reply()

            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length') or 0))
                self.reply()

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()


def bounded(fn, limit_s):
    """Runs fn in a daemon thread -> (finished within limit_s, result or exception, elapsed_s)."""
    out = {}

    def target():
        try:
            out['value'] = fn()
        except BaseException as error:  # noqa: BLE001 - the outcome is asserted by the caller
            out['value'] = error
    started = time.monotonic()
    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(limit_s)
    return not t.is_alive(), out.get('value'), time.monotonic() - started


class CollectorControlBounds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def live(self, peer):
        l = live.Live(SavingSession(self.tmp.name), {}, self.tmp.name, 'pilot', sleep=lambda s: None)  # default control HTTP
        l.urls = {'generator': peer.url, 'gateway': peer.url, 'w': peer.url}
        l.workers = [dict(key='w')]
        return l

    def test_job_poll_held_in_a_trickle_ends_at_the_phase_budget(self):
        peer = ControlPeer(trickle=('/job',))
        try:
            l = self.live(peer)
            done, value, elapsed = bounded(lambda: l.job('/profile', {}, 'profile', 0.5), 4)
            self.assertTrue(done, 'a poll stuck inside a trickling reply must end at the job budget')
            self.assertEqual(value['state'], 'timeout', value)
            self.assertLess(elapsed, 2)
        finally:
            peer.close()

    def test_drain_with_trickling_control_replies_ends_at_the_drain_budget(self):
        peer = ControlPeer(trickle=('/run', '/health'))
        try:
            l = self.live(peer)
            l.b = dict(l.b, drain_timeout_s=0.5)
            done, value, elapsed = bounded(lambda: l.drain('next'), 4)
            self.assertTrue(done, 'drain must not exceed its budget inside a trickling worker/gateway reply')
            self.assertFalse(value['idle'])
            self.assertLess(elapsed, 2)
        finally:
            peer.close()

    def test_control_reply_above_the_body_cap_is_refused_and_a_ledger_sized_reply_is_accepted(self):
        peer = ControlPeer(big=4 << 20)
        try:
            l = self.live(peer)
            self.assertEqual(len(l.control(peer.url + '/big')['pad']), 4 << 20)
            l.b = dict(l.b, control_max_body_bytes=1 << 20)
            with self.assertRaises(ValueError):
                l.control(peer.url + '/big')
        finally:
            peer.close()


class PlanModelProvenance(unittest.TestCase):
    """design.model_expectations / baselines.plan|binding: expected stage hashes come from the frozen assets."""

    def test_expectations_are_the_frozen_manifest_hashes_along_the_deterministic_load_path(self):
        manifest = json.loads((FROZEN_PLAN/'assets-manifest.json').read_text())
        export = json.loads((Path(manifest['export_dir']) if Path(manifest['export_dir']).is_absolute()
                             else design.ROOT/manifest['export_dir']).joinpath('manifest.json').read_text())
        workers = json.loads((FROZEN_PLAN/'plan.json').read_text())['workers']
        files = {e['path']: e['sha256'] for e in manifest['files']}
        got = design.model_expectations(workers, manifest, export)
        hashes = {k: {t: (e['model_sha256'], e['derived_artifact_sha256']) for t, e in v.items()} for k, v in got.items()}
        self.assertEqual(hashes['npu-c'], {'vision': (files['models/resnet18-f37072fd.pth'], files['onnx/vision-static-448.onnx'])})
        self.assertEqual(hashes['cpu-c'], dict(image_preprocess=(None, None), vision=(files['models/resnet18-f37072fd.pth'], None),
                                               audio_preprocess=(files['models/asr/preprocessor_config.json'], None),
                                               speech=(files['models/asr/model.safetensors'], None),
                                               fusion=(files['models/mutan-resnet18.pth.tar'], None)))
        self.assertEqual(hashes['jgpu-dev0009-cu130'], {t: hashes['cpu-c'][t] for t in ('vision', 'speech', 'fusion')})
        self.assertTrue(all(e['chain'] for v in got.values() for e in v.values()))

    def test_a_derived_onnx_not_traced_to_the_shipped_weights_is_refused(self):
        manifest = json.loads((FROZEN_PLAN/'assets-manifest.json').read_text())
        export = json.loads((design.ROOT/manifest['export_dir']/'manifest.json').read_text())
        export['stages']['vision']['original_model_sha256'] = '0' * 64
        workers = json.loads((FROZEN_PLAN/'plan.json').read_text())['workers']
        with self.assertRaises(ValueError):
            design.model_expectations(workers, manifest, export)

    def test_plan_records_and_binding_rechecks_model_provenance(self):
        import test_design
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)/'plan'
            value, _ = baselines.plan(d, test_design.INVENTORY, test_design.EXPORT, test_design.DEPS, hash_files=False)
            self.assertEqual(set(value['model_provenance']['expectations']), {w['key'] for w in value['workers']})
            self.assertEqual(baselines.binding(d, 'pilot', 'qnb-fix', verify_files=False)[1], [])
            plan = json.loads((d/'plan.json').read_text())
            plan['model_provenance']['expectations']['cpu-c']['fusion']['model_sha256'] = 'f' * 64
            (d/'plan.json').write_text(json.dumps(plan))
            self.assertIn('model provenance differs from the frozen assets', baselines.binding(d, 'pilot', 'qnb-fix', verify_files=False)[1])
            del plan['model_provenance']
            (d/'plan.json').write_text(json.dumps(plan))
            self.assertIn('plan predates model provenance binding', baselines.binding(d, 'pilot', 'qnb-fix', verify_files=False)[1])


def provenance_plan(stage_hash):
    return dict(workers=r1.PLAN_WORKERS, model_provenance=dict(expectations={
        w['key']: {t: dict(model_sha256=stage_hash(w, t), derived_artifact_sha256=None) for t in w['stages']} for w in r1.PLAN_WORKERS}))


class HealthAgainstPlan(unittest.TestCase):
    def test_health_reporting_a_model_other_than_the_plan_removes_the_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = provenance_plan(lambda w, t: 'plan-' + t)
            plan['model_provenance']['expectations']['jgpu-dev9']['speech']['model_sha256'] = 'frozen-asr'

            def health(url, body=None, timeout=None):
                w = next(w for w in r1.PLAN_WORKERS if w['key'] == url.split('/')[0])
                return dict(pod_uid='uid-' + w['key'], node=w['node'], errors={},
                            stages={t: dict(model_sha256='plan-' + t, derived_artifact_sha256=None) for t in w['stages']})
            l = live.Live(r1.HealthSession(tmp), plan, tmp, 'pilot', http=health)
            l.urls = {w['key']: w['key'] for w in r1.PLAN_WORKERS}
            with self.assertRaises(RuntimeError):
                l.verify_workers([])
            removed = json.loads((Path(tmp)/'worker-health.json').read_text())['removed']
            self.assertEqual(list(removed), ['jgpu-dev9'])
            self.assertEqual(removed['jgpu-dev9'], dict(model_provenance_mismatch=dict(speech=dict(model_sha256='plan-speech', derived_artifact_sha256=None))))


class CalibrationAgainstPlan(unittest.TestCase):
    """A health self-report that agrees with the receipts but not with the frozen plan cannot qualify a candidate."""

    @classmethod
    def setUpClass(cls):
        r1.CalibrationQualification.setUpClass.__func__(cls)

    def test_receipts_matching_a_forged_health_report_do_not_qualify(self):
        import copy
        result = copy.deepcopy(self.result)
        for r in result['rows'] + result['original_rows']:  # every npu-c receipt agrees with the forged health report
            if r.get('receipt') and r['worker'] == 'npu-c':
                r['receipt']['model_sha256'] = 'forged'
        with tempfile.TemporaryDirectory() as tmp:
            l = r1.CalibrationQualification.live(self, tmp, result)
            l.plan['model_provenance'] = plan_models(self.workers)
            l.health['npu-c']['stages']['vision']['model_sha256'] = 'forged'
            with self.assertRaises(RuntimeError):
                l.profile()
            problems = json.loads((Path(tmp)/'profile-qualification.json').read_text())['problems']
            self.assertTrue(any('vision@npu-c' in p and 'model_mismatch' in p for p in problems), problems[:3])


class AnalysisAgainstPlan(unittest.TestCase):
    """analyze takes expected stage hashes from the plan; a run whose health and receipts agree on another model is invalid."""

    setUpClass = classmethod(r1.FrozenRunAnalysis.setUpClass.__func__)
    setUp, tearDown, write = r1.FrozenRunAnalysis.setUp, r1.FrozenRunAnalysis.tearDown, r1.FrozenRunAnalysis.write

    def rebind(self, plan):
        (self.plan_dir/'plan.json').write_text(json.dumps(plan))
        self.write('release.json', dict(binding=dict(plan_sha256=baselines.sha(self.plan_dir/'plan.json'), run_id=self.run_id)))

    def test_rows_whose_model_is_not_the_plan_model_are_invalid(self):
        plan = json.loads((self.plan_dir/'plan.json').read_text())
        plan['model_provenance'] = plan_models(self.workers)
        plan['model_provenance']['expectations']['cpu-c']['fusion']['model_sha256'] = 'frozen-fusion'
        self.rebind(plan)
        trace = analyze.analyze(self.run, self.plan_dir)['traces'][0]
        self.assertEqual((trace['counts'], trace['invalid_reasons']), ({'invalid': len(self.arrivals)}, {'model_mismatch': len(self.arrivals)}))

    def test_plan_without_model_provenance_is_refused(self):
        plan = json.loads((self.plan_dir/'plan.json').read_text())
        plan.pop('model_provenance', None)
        self.rebind(plan)
        with self.assertRaises(ValueError):
            analyze.analyze(self.run, self.plan_dir)


if __name__ == '__main__':
    unittest.main()
