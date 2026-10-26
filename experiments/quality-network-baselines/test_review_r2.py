"""Final (round-2) review regressions: review-spec-r2.md (R5, unbound deadline) and review-standards-r2.md (S2, S3, S5,
S9), scope fix-r2-live-task.md.

1. Every offered image/audio preprocessing candidate is qualified before its cost is frozen: its calibration output is
   byte-identical (bytes, size, receipt shape/dtype) to the reference producer's, or the reference model + fusion on its
   output give the original path's final answer. Otherwise collection is refused with the evidence kept.
2. Offline analysis binds each trace's execution contract (label, policy spec, deadline, cancel, admission cap, quality
   contract) to the released plan/cell, the ledger to that request, compare cells to the release-bound frozen loads, and
   rejects non-finite or impossible completion timestamps.
3. The consumed execution/scoring dependencies (run_baseline_comparison.py, vqa_eval.py) are plan-bound; analysis refuses
   a scoring source that is not the plan's.
4. One absolute local monotonic deadline covers input preparation and lock acquisition: expired work never reaches the model.
5. Corpus asset fetches are bounded by an absolute deadline (bounded_http), per file and for the whole /corpus load.
Model stages are the deterministic loopback fakes of test_executor (plumbing evidence only, never device evidence).
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_executor as fx  # noqa: E402  (loopback fixture; also sets sys.path for qnh/real-topology-live)
import analyze  # noqa: E402
import baselines  # noqa: E402
import design  # noqa: E402
import live  # noqa: E402
import test_review_r1 as r1  # noqa: E402

PRODUCERS = {t: 'cpu-c' for t in fx.policies.TASKS}


def profile(change=None):
    """Generator.profile on the loopback cluster; change(cluster) may alter fake stages before calibration."""
    c = fx.Cluster(fx.LAYOUT)
    try:
        if change:
            change(c)
        result = c.gen.profile(dict(question_ids=['11', '12'], warmup=1, producers=PRODUCERS))
        workers, created, _, run_id = c.validation()
        return result, workers, created, run_id, c.candidates
    finally:
        c.close()


def gate(tmp, result, workers, created, run_id, candidates):
    helper = r1.CalibrationQualification()
    helper.workers, helper.created, helper.run_id, helper.candidates = workers, created, run_id, candidates
    return helper.live(tmp, result)


def invalid_image_preprocessing(c):
    """Reviewer R5 repro: cpu-e image preprocessing emits a tensor every vision consumer rejects (honest hashes/identity)."""
    bad = c.workers['cpu-e'].stages['image_preprocess']
    bad.run = lambda data: bad._work(b'INVALID_TENSOR')
    for w in c.workers.values():
        if 'vision' in w.stages:
            stage, original = w.stages['vision'], w.stages['vision'].run_tensor
            def guarded(data, original=original):
                if data == b'INVALID_TENSOR':
                    raise ValueError('invalid tensor from alternate preprocessing candidate')
                return original(data)
            stage.run_tensor = guarded


def different_valid_image_preprocessing(c):
    """cpu-e image preprocessing differs in bytes (e.g. another CPU arch's float rounding) but downstream still answers."""
    alt = c.workers['cpu-e'].stages['image_preprocess']
    alt.run = lambda data: alt._work(b'tensor-alt:' + hashlib.sha256(data).hexdigest().encode())


class PreprocessingQualification(unittest.TestCase):
    def test_alternate_preprocessor_with_invalid_output_is_refused_with_evidence(self):
        result, *rest = profile(invalid_image_preprocessing)
        with tempfile.TemporaryDirectory() as tmp:
            l = gate(tmp, result, *rest)
            with self.assertRaises(RuntimeError):
                l.profile()
            verdict = json.loads((Path(tmp)/'profile-qualification.json').read_text())
            self.assertFalse(verdict['qualified'])
            self.assertTrue(any('image_preprocess@cpu-e' in p for p in verdict['problems']), verdict['problems'])
            self.assertFalse(hasattr(l, 'compute'))
        rows = [r for r in result['preprocess_equivalence'] if r['task'] == 'image_preprocess' and r['worker'] == 'cpu-e']
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(not r['answer_equal'] and not r['bytes_identical'] and r.get('error') for r in rows), rows)

    def test_identical_alternate_preprocessor_is_qualified_by_byte_identity_without_model_calls(self):
        result, *rest = profile()
        rows = result['preprocess_equivalence']
        self.assertEqual({(r['task'], r['worker']) for r in rows}, {('image_preprocess', 'cpu-e'), ('audio_preprocess', 'cpu-e')})
        self.assertTrue(all(r['proof'] == 'bytes_identical' and r['answer_equal'] and r['reference'] == 'cpu-c' for r in rows), rows)
        self.assertTrue(all('model_request_id' not in r for r in rows))  # identity reuses the reference downstream evidence
        with tempfile.TemporaryDirectory() as tmp:
            l = gate(tmp, result, *rest)
            l.profile()
            self.assertIn('image_preprocess@cpu-e', l.compute)

    def test_different_preprocessor_output_qualifies_only_through_the_original_final_answer(self):
        result, *rest = profile(different_valid_image_preprocessing)
        rows = [r for r in result['preprocess_equivalence'] if r['task'] == 'image_preprocess']
        self.assertTrue(all(r['proof'] == 'downstream_answer' and r['answer_equal'] and not r['bytes_identical'] for r in rows), rows)
        with tempfile.TemporaryDirectory() as tmp:
            gate(tmp, result, *rest).profile()
        forged = copy.deepcopy(result)
        forged['preprocess_equivalence'][0]['answer_equal'] = False
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                gate(tmp, forged, *rest).profile()

    def test_missing_preprocessing_evidence_is_refused(self):
        result, *rest = profile()
        result = dict(result, preprocess_equivalence=[])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                gate(tmp, result, *rest).profile()
            problems = json.loads((Path(tmp)/'profile-qualification.json').read_text())['problems']
            self.assertTrue(any('audio_preprocess@cpu-e' in p for p in problems), problems)


class AnalysisContract(r1.FrozenRunAnalysis):
    """The trace execution contract is the released plan/cell's, never what a saved request or ledger claims."""

    def late_by(self, seconds):
        ledger = copy.deepcopy(self.ledger)
        for row in ledger['rows']:
            row['completed_offset_s'] = row['scheduled_offset_s'] + seconds
        self.write(f"ledger-{self.cell['label']}.json", ledger)
        return ledger

    def test_deadline_is_the_plan_slo(self):
        self.late_by(3.0)
        trace = analyze.analyze(self.run, self.plan_dir)['traces'][0]
        self.assertEqual((trace['goodput_rps'], trace['counts']), (0.0, {'late_ok': len(self.arrivals)}))

    def test_request_whose_contract_differs_from_the_plan_is_refused(self):
        self.late_by(3.0)
        for change in (dict(deadline_s=20.0), dict(cancel_s=50.0), dict(max_inflight=64), dict(policy=dict(name='task_fastest')),
                       dict(policy=dict(name='fixed', plan=dict(fx.MIXED, vision='cpu-c'))), dict(quality=dict(mode='strict', qmin=0.4)),
                       dict(label='another')):
            self.write(f"trace-{self.cell['label']}.request.json", dict(self.request, **change))
            with self.assertRaises(ValueError, msg=change):
                analyze.analyze(self.run, self.plan_dir)

    def test_ledger_that_is_not_the_released_request_is_refused(self):
        ledger = self.late_by(3.0)
        for change in (dict(policy=dict(name='task_fastest')), dict(deadline_s=20.0), dict(cancel_s=50.0), dict(max_inflight=64),
                       dict(label='another')):
            self.write(f"ledger-{self.cell['label']}.json", dict(ledger, **change))
            with self.assertRaises(ValueError, msg=change):
                analyze.analyze(self.run, self.plan_dir)

    def test_impossible_or_non_finite_completion_is_invalid(self):
        ledger = copy.deepcopy(self.ledger)
        ledger['rows'][0]['completed_offset_s'] = ledger['rows'][0]['scheduled_offset_s'] - 1.0
        ledger['rows'][1]['completed_offset_s'] = float('nan')
        ledger['rows'][2]['completed_offset_s'] = float('inf')
        self.write(f"ledger-{self.cell['label']}.json", ledger)
        trace = analyze.analyze(self.run, self.plan_dir)['traces'][0]
        self.assertEqual(trace['invalid_reasons'], {'impossible_timestamp': 3})
        self.assertEqual(trace['counts'], {'invalid': 3, 'ok': len(self.arrivals) - 3})
        self.assertGreaterEqual(trace['latency_ms']['p50'], 0)

    def compare_run(self):
        frozen = dict(loads_rps=dict(low=0.3, near_saturation=0.8, high=1.3))
        path = Path(self.tmp.name)/'frozen-loads.json'
        path.write_text(json.dumps(frozen))
        self.write('collection.json', dict(stage='compare'))
        self.write('release.json', dict(binding=dict(plan_sha256=baselines.sha(self.plan_dir/'plan.json'), run_id=self.run_id,
                                                     frozen_loads_sha256=baselines.sha(path))))
        plan = json.loads((self.plan_dir/'plan.json').read_text())
        cells = design.compare_cells(frozen, plan['compare'], plan['policies'])
        self.write('cells.json', cells)
        return path, cells

    def test_compare_cells_are_the_release_bound_frozen_loads(self):
        path, cells = self.compare_run()
        self.assertEqual(analyze.analyze(self.run, self.plan_dir, frozen=path)['traces'], [])  # no trace started
        with self.assertRaises(ValueError):
            analyze.analyze(self.run, self.plan_dir)  # compare analysis needs the release-bound frozen loads
        other = Path(self.tmp.name)/'other-loads.json'
        other.write_text(json.dumps(dict(loads_rps=dict(low=0.6, near_saturation=1.6, high=2.6))))
        with self.assertRaises(ValueError):
            analyze.analyze(self.run, self.plan_dir, frozen=other)
        self.write('cells.json', [dict(c, rate_rps=c['rate_rps'] * 2) for c in cells])
        with self.assertRaises(ValueError):
            analyze.analyze(self.run, self.plan_dir, frozen=path)


class DependencyBinding(unittest.TestCase):
    TARGETS = ('experiments/real-topology-live/run_baseline_comparison.py', 'experiments/quality-network-vqa/vqa_eval.py')

    def test_plan_binds_the_consumed_execution_and_scoring_sources(self):
        import test_design
        with tempfile.TemporaryDirectory() as tmp:
            value, _ = baselines.plan(Path(tmp)/'plan', test_design.INVENTORY, test_design.EXPORT, test_design.DEPS, hash_files=False)
            for target in self.TARGETS:
                self.assertEqual(value['reused_frozen_sources'].get(target), baselines.sha(design.ROOT/target))
            self.assertEqual(Path(live.baseline.__file__).resolve(), (design.ROOT/self.TARGETS[0]).resolve())
            actual = baselines.sha
            for target in self.TARGETS:
                resolved = (design.ROOT/target).resolve()
                with patch.object(baselines, 'sha', side_effect=lambda p: 'f' * 64 if Path(p).resolve() == resolved else actual(p)):
                    drift = baselines.binding(Path(tmp)/'plan', 'pilot', 'qnb-r2', verify_files=False)[1]
                self.assertEqual(drift, [f'reused frozen source changed: {target}'])


class ScoringDependency(r1.FrozenRunAnalysis):
    def test_analysis_refuses_a_scoring_source_that_is_not_the_plans(self):
        plan_path = self.plan_dir/'plan.json'
        for sources in ({r1.VQA_EVAL: '0' * 64}, {}):
            plan = json.loads(plan_path.read_text())
            plan['reused_frozen_sources'] = sources
            plan_path.write_text(json.dumps(plan))
            self.write('release.json', dict(binding=dict(plan_sha256=baselines.sha(plan_path), run_id=self.run_id)))
            with self.assertRaises(ValueError, msg=sources):
                analyze.analyze(self.run, self.plan_dir)


class PreparationDeadline(r1.QueueDeadline):
    def test_request_expiring_during_input_preparation_never_reaches_the_model(self):
        w, stages = self.worker()
        base = fx.dag_worker.base
        decode = base.payload

        def slow_decode(data):
            time.sleep(0.2)
            return decode(data)
        with patch.object(base, 'payload', side_effect=slow_decode):
            status, reply = w.run(dict(mode='dag', run_id='r', request_id='q', stage='image_preprocess', data=b'x', budget_s=0.15))
        self.assertEqual((status, reply['error'], stages['image_preprocess'].calls), (504, 'queue_deadline_expired', 0))
        self.assertEqual({k: w.run(dict(mode='queue'))[1][k] for k in ('waiting', 'active', 'inflight')}, dict(waiting=0, active=0, inflight=0))

    def test_fusion_expiring_during_preparation_answers_both_parts_without_fusion(self):
        w, stages = self.worker()
        base = fx.dag_worker.base
        decode = base.base64.b64decode

        def slow_b64(*a, **kw):
            time.sleep(0.3)
            return decode(*a, **kw)
        body = dict(mode='dag', run_id='r', request_id='q', stage='fusion_join', budget_s=0.35)
        with patch.object(base.base64, 'b64decode', side_effect=slow_b64):
            t1, out1 = self.run_async(w, dict(body, part='vision', data=b'v'))
            t2, out2 = self.run_async(w, dict(body, part='speech', data=b's'))
            t1.join(2)
            t2.join(2)
        self.assertEqual(sorted(o[0][0] for o in (out1, out2)), [504, 504])
        self.assertEqual(stages['fusion'].calls, 0)


class CorpusFetchBounds(unittest.TestCase):
    def setUp(self):
        self.data = b'x' * 30
        self.peer = TrickleGet(self.data)

    def tearDown(self):
        self.peer.close()

    def test_trickled_asset_ends_at_the_absolute_fetch_deadline(self):
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            fx.generator.fetch(self.peer.url + '/asset', hashlib.sha256(self.data).hexdigest(), timeout=0.05)
        self.assertLess(time.monotonic() - started, 0.3)

    def test_fetch_keeps_the_size_and_hash_checks(self):
        expected = hashlib.sha256(self.data).hexdigest()
        self.assertEqual(fx.generator.fetch(self.peer.url + '/asset', expected, timeout=5), self.data)
        with self.assertRaises(ValueError):
            fx.generator.fetch(self.peer.url + '/asset', '0' * 64, timeout=5)
        with self.assertRaises(ValueError):
            fx.generator.fetch(self.peer.url + '/asset', expected, timeout=5, limit=10)

    def test_corpus_load_ends_at_its_whole_budget(self):
        gen = fx.generator.Generator()
        files = [dict(question_id=str(q), kind='image', path=f'f{q}', sha256=hashlib.sha256(self.data).hexdigest()) for q in range(4)]
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            gen.load_corpus(dict(cache_url=self.peer.url, files=files, budget_s=0.9))  # each file trickles for ~0.6 s
        self.assertLess(time.monotonic() - started, 1.2)


class TrickleGet:
    """Loopback asset server: GET answers `data` one byte every 20 ms."""

    def __init__(self, data):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                try:
                    for b in data:
                        self.wfile.write(bytes([b]))
                        self.wfile.flush()
                        time.sleep(0.02)
                except OSError:
                    pass

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def load_tests(loader, tests, pattern):
    """Only the tests defined here: three classes reuse round-1 fixtures, whose own tests already ran in test_review_r1."""
    suite = unittest.TestSuite()
    for cls in (PreprocessingQualification, AnalysisContract, DependencyBinding, ScoringDependency, PreparationDeadline, CorpusFetchBounds):
        suite.addTests(cls(name) for name in cls.__dict__ if name.startswith('test_'))
    return suite


if __name__ == '__main__':
    unittest.main()
