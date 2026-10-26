"""Round-1 review regressions (review-spec-r1.md, review-standards-r1.md, coordinator-review-notes-r1.md).

Each test is a behavioral regression at an agreed public seam (spec.md / acceptance.md): DAG HTTP request/reply,
frozen-trace analysis, scheduling design, release binding and release-bound live orchestration. Model stages are the
deterministic loopback fakes of test_executor (plumbing evidence only, never device or performance evidence).
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_executor as fx  # noqa: E402  (loopback fixture; also sets sys.path for qnh/real-topology-live)
import analyze  # noqa: E402
import baselines  # noqa: E402
import design  # noqa: E402
import live  # noqa: E402


class ValidatedRow(unittest.TestCase):
    """A row counts only when every scored field is the validated bound response."""

    @classmethod
    def setUpClass(cls):
        cls.c = fx.Cluster(fx.LAYOUT)
        cls.row = cls.c.trace('fix-valid', fx.MIXED, [0.0])['rows'][0]
        cls.args = cls.c.validation()

    @classmethod
    def tearDownClass(cls):
        cls.c.close()

    def check(self, row):
        return analyze.validate_row(row, *self.args)

    def test_untouched_row_is_valid(self):
        self.assertEqual(self.row['status'], 'ok')
        self.assertIsNone(self.check(self.row))

    def test_scored_answer_must_be_the_fusion_payload_answer(self):
        row = copy.deepcopy(self.row)
        row.update(answer='not in the hashed response', answer_ids=[-100], output_sha256='0' * 64)
        self.assertEqual(self.check(row), 'answer_mismatch')

    def receipts(self, row, stage=None):
        return [r for b in row['branches'].values() for r in b['reply']['receipts'] if stage is None or r['stage'] == stage]

    def test_foreign_fusion_identity_is_rejected(self):
        for field, value in (('request_id', 'other-request'), ('run_id', 'other-run'), ('backend', 'cuda-torch'),
                             ('model_sha256', 'unreviewed-model')):
            row = copy.deepcopy(self.row)
            for r in self.receipts(row, 'fusion'):
                r[field] = value
            self.assertIsNotNone(self.check(row), field)

    def test_two_fusion_receipts_must_agree(self):
        row = copy.deepcopy(self.row)
        self.receipts(row, 'fusion')[0]['sequence'] = 10 ** 6
        self.assertEqual(self.check(row), 'fusion_receipt_disagreement')

    def test_stage_receipts_must_carry_this_run_and_the_expected_model(self):
        row = copy.deepcopy(self.row)
        for r in self.receipts(row):
            r['run_id'] = 'other-run'
        self.assertEqual(self.check(row), 'run_id_mismatch')
        row = copy.deepcopy(self.row)
        next(r for r in self.receipts(row) if r['stage'] == 'speech')['model_sha256'] = 'unreviewed-model'
        self.assertEqual(self.check(row), 'model_mismatch')

    def test_missing_creation_uid_never_validates(self):
        workers, created, corpus, run_id = self.args
        row = copy.deepcopy(self.row)
        for r in self.receipts(row):
            r['pod_uid'] = None
        self.assertEqual(analyze.validate_row(row, workers, {k: None for k in created}, corpus, run_id), 'identity_mismatch')

    def test_malformed_evidence_is_a_reason_not_a_crash(self):
        workers, created, corpus, run_id = self.args
        row = copy.deepcopy(self.row)
        for b in row['branches'].values():
            b['reply']['data'] = '%%% not base64 %%%'
        self.assertEqual(self.check(row), 'malformed_output')
        row = copy.deepcopy(self.row)
        self.assertEqual(analyze.validate_row(row, workers, created, {}, run_id), 'input_mismatch')
        row = copy.deepcopy(self.row)
        row['branches']['image']['reply']['receipts'][0] = 'not-a-receipt'
        self.assertIsNotNone(self.check(row))
        row = copy.deepcopy(self.row)
        row['branches']['audio']['reply']['hops'] = None
        self.assertIsNotNone(self.check(row))


GT_YES = {'human_answers': [dict(answer='yes', answer_id=i) for i in range(1, 11)]}  # fake fusion answers[0] == 'yes'


class FrozenCohort(unittest.TestCase):
    """Analysis reconciles exactly one result per frozen scheduled arrival and recomputes timeliness on the source clock."""

    @classmethod
    def setUpClass(cls):
        c = fx.Cluster(fx.LAYOUT)
        try:
            cls.ledger = c.trace('fix-cohort', fx.MIXED, [0.0, 0.05, 0.1])
            cls.args = c.validation()
            cls.gt = {q: dict(GT_YES, question_id=q) for q in c.questions}
        finally:
            c.close()
        cls.cohort = dict(arrivals=[dict(index=r['index'], offset_s=r['scheduled_offset_s'], question_id=r['question_id'])
                                    for r in cls.ledger['rows']], deadline_s=2.0)
        assert [r['status'] for r in cls.ledger['rows']] == ['ok'] * 3

    def summary(self, ledger, cohort=None):
        workers, created, corpus, run_id = self.args
        return analyze.trace_summary(ledger, cohort or self.cohort, 1.0, workers, created, corpus, self.gt, run_id)

    def test_baseline_counts(self):
        s = self.summary(self.ledger)
        self.assertEqual((s['requests'], s['counts'], s['goodput_rps']), (3, {'ok': 3}, 3.0))

    def test_missing_and_duplicate_rows_never_add_goodput(self):
        ledger = copy.deepcopy(self.ledger)
        ledger['rows'] = [ledger['rows'][0], copy.deepcopy(ledger['rows'][0]), ledger['rows'][1]]  # index 2 missing, 0 twice
        s = self.summary(ledger)
        self.assertEqual(s['requests'], 3)
        self.assertEqual(s['counts'], {'duplicate_result': 1, 'ok': 1, 'missing_result': 1})
        self.assertEqual(s['goodput_rps'], 1.0)

    def test_foreign_rows_are_not_part_of_the_cohort(self):
        ledger = copy.deepcopy(self.ledger)
        extra = copy.deepcopy(ledger['rows'][0])
        extra.update(index=99, request_id='fix-cohort-99')
        ledger['rows'].append(extra)
        s = self.summary(ledger)
        self.assertEqual((s['requests'], s['counts'], s['foreign_rows']), (3, {'ok': 3}, 1))

    def test_deadline_status_is_recomputed_from_source_clock_completion(self):
        ledger = copy.deepcopy(self.ledger)
        row = ledger['rows'][0]
        row['completed_offset_s'] = row['scheduled_offset_s'] + 2.5  # string still says 'ok'
        s = self.summary(ledger)
        self.assertEqual(s['counts'], {'late_ok': 1, 'ok': 2})
        self.assertEqual(s['goodput_rps'], 2.0)

    def test_row_must_match_its_frozen_arrival(self):
        cohort = copy.deepcopy(self.cohort)
        cohort['arrivals'][1]['question_id'] = 'other-question'
        s = self.summary(self.ledger, cohort)
        self.assertEqual((s['counts'], s['invalid_reasons']), ({'ok': 2, 'invalid': 1}, {'cohort_mismatch': 1}))

    def test_late_correct_answers_keep_eventual_quality_separate_from_timely_quality(self):
        ledger = copy.deepcopy(self.ledger)
        ledger['rows'][0]['completed_offset_s'] = ledger['rows'][0]['scheduled_offset_s'] + 3.0
        ledger['rows'] = ledger['rows'][:1]
        cohort = dict(self.cohort, arrivals=self.cohort['arrivals'][:1])
        q = self.summary(ledger, cohort)['quality']
        self.assertEqual((q['returned'], q['in_deadline'], q['coverage_returned'], q['coverage_in_deadline']), (1.0, None, 1.0, 0.0))
        self.assertEqual((q['all_offered_eventual_zero_imputed'], q['all_offered_timely_zero_imputed']), (1.0, 0.0))


class ComparisonDesign(unittest.TestCase):
    def test_every_policy_takes_every_order_position_equally_often(self):
        cells = design.compare_cells(dict(loads_rps=dict(low=0.3, near_saturation=0.8, high=1.3)))
        groups = {}
        for c in cells:
            groups.setdefault((c['block'], c['condition'], c['load']), []).append(c['policy'])
        self.assertEqual(len(groups), 12)
        positions = {}
        for order in groups.values():
            for i, name in enumerate(order):
                positions.setdefault(name, [0] * len(order))[i] += 1
        self.assertEqual(positions, {s['name']: [2] * 6 for s in design.POLICY_SPECS})

    def rows(self, *cells):
        return [dict(condition='good', policy=p, rate_rps=r, requests=n, ok=ok, admission_drops=0) for p, r, n, ok in cells]

    def test_sustained_fraction_uses_the_realized_scheduled_cohort(self):
        # 19 timely answers is 0.95 of the nominal 20 but only 0.76 of the 25 actually scheduled arrivals.
        frozen = design.freeze_loads(self.rows(('fixed', 0.5, 9, 9), ('fixed', 1.0, 25, 19)))
        self.assertEqual(frozen['saturation_rps'], 0.5)
        self.assertEqual((frozen['capacity'], frozen['upper_failure_rps']), ('bracketed', 1.0))

    def test_largest_tested_rate_without_a_failing_larger_rate_is_censored(self):
        frozen = design.freeze_loads(self.rows(('fixed', 0.5, 9, 9), ('fixed', 1.0, 21, 21)))
        self.assertEqual((frozen['saturation_rps'], frozen['capacity'], frozen['upper_failure_rps']), (1.0, 'censored_at_largest_tested_rate', None))


class ReleaseBinding(unittest.TestCase):
    """baselines.binding: every input the live run consumes is bound before any kubectl call."""

    @classmethod
    def setUpClass(cls):
        import test_design
        cls.tmp = tempfile.TemporaryDirectory()
        cls.source = Path(cls.tmp.name)/'plan'
        baselines.plan(cls.source, test_design.INVENTORY, test_design.EXPORT, test_design.DEPS, hash_files=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def copy(self):
        target = Path(tempfile.mkdtemp(dir=self.tmp.name))
        for name in ('plan.json', 'corpus.json', 'assets-manifest.json'):
            (target/name).write_bytes((self.source/name).read_bytes())
        return target

    def drift(self, plan_dir, verify_files=False):
        return baselines.binding(plan_dir, 'pilot', 'qnb-fix', verify_files=verify_files)[1]

    def edit(self, path, change):
        value = json.loads(path.read_text())
        change(value)
        path.write_text(json.dumps(value))

    def test_fresh_plan_has_no_drift(self):
        self.assertEqual(self.drift(self.copy()), [])

    def test_reassigning_a_question_image_is_drift(self):
        d = self.copy()
        def swap(corpus):
            images = [f for f in corpus['files'] if f['kind'] == 'image']
            images[0]['path'], images[0]['sha256'] = images[1]['path'], images[1]['sha256']
        self.edit(d/'corpus.json', swap)
        self.assertTrue(self.drift(d))

    def test_changed_assets_manifest_is_drift(self):
        d = self.copy()
        self.edit(d/'assets-manifest.json', lambda m: m.update(files=[]))
        self.assertTrue(self.drift(d))

    def test_changed_reused_source_or_input_is_drift(self):
        for change in (lambda p: p['reused_frozen_sources'].update({next(iter(p['reused_frozen_sources'])): '0' * 64}),
                       lambda p: p['inputs'].update(gt_sha256='0' * 64), lambda p: p['inputs'].update(pairs_sha256='0' * 64)):
            d = self.copy()
            self.edit(d/'plan.json', change)
            self.assertTrue(self.drift(d))

    def test_plan_without_corpus_binding_is_drift(self):
        d = self.copy()
        self.edit(d/'plan.json', lambda p: p.pop('corpus_sha256'))
        self.assertTrue(self.drift(d))

    def test_asset_sources_are_verified_before_release(self):
        d = self.copy()
        self.assertTrue(self.drift(d, verify_files=True))  # unhashed (test-only) plan is never releasable
        asset = Path(self.tmp.name)/'asset.bin'
        asset.write_bytes(b'frozen asset')
        self.edit(d/'assets-manifest.json', lambda m: m.update(files=[dict(group='common', path='a.bin', source=str(asset),
                                                                           bytes=12, sha256=baselines.sha(asset))]))
        self.edit(d/'plan.json', lambda p: p.update(assets_manifest_sha256=baselines.sha(d/'assets-manifest.json')))
        # the one-file manifest also drops the corpus and model files: those are separate (correct) drifts, not asset-source ones
        self.assertEqual([x for x in self.drift(d, verify_files=True) if 'corpus' not in x and 'model provenance' not in x], [])
        asset.write_bytes(b'changed asset')
        self.assertTrue([x for x in self.drift(d, verify_files=True) if 'asset source' in x])


class SavingSession:
    """kubectl-free session double at the collector's session seam: the real exclusive-create writer, fake tc."""

    def __init__(self, out, run='qnb-fix'):
        self.out, self.run, self.nets = Path(out), run, []

    def save(self, name, value):
        live.ownership.Session.save(self, name, value)

    def net(self, spec, stem):
        self.nets.append((stem, spec))
        return dict(status='applied', action=spec['action'])


class LiveNetwork(unittest.TestCase):
    def test_repeated_condition_shapes_keep_every_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = SavingSession(tmp)
            idle = dict(active=0, waiting=0, joins_open=0, inflight=0)
            l = live.Live(s, {}, tmp, 'pilot', http=lambda url, body=None, timeout=None: idle, sleep=lambda s: None)
            l.workers, l.urls = [], {'gateway': 'gateway'}
            l.cloud_ips, l.edge_ips = ['10.1.0.1'], ['10.1.0.2']
            for name in ('relay-unshaped', 'relay-unshaped', 'good', 'relay-unshaped'):
                l.shape(name)
            saved = sorted(p.name for p in Path(tmp).glob('net-*.json'))
            self.assertEqual(len(saved), 4, saved)
            self.assertEqual(len({stem for stem, _ in s.nets}), 4)


PLAN_WORKERS = [dict(key='cpu-c', node='ser', backend='cpu-torch', stages=list(fx.policies.TASKS), zone='cloud', edge=False),
                dict(key='npu-c', node='ser', backend='openvino-npu', stages=['vision'], zone='cloud', edge=False),
                dict(key='cpu-e0', node='dev3', backend='cpu-torch', stages=list(fx.policies.TASKS), zone='edge', edge=True),
                dict(key='jgpu-dev9', node='dev9', backend='cuda-torch', stages=['vision', 'speech', 'fusion'], zone='edge', edge=True)]


def plan_models(workers):
    """Plan-side model provenance for fixture workers ({key: {models}}): what receipts and /health must carry."""
    return dict(expectations={k: {t: dict(m) for t, m in w['models'].items()} for k, w in workers.items()})


class HealthSession(SavingSession):
    def record_for(self, name):
        return dict(uid='uid-' + name.removeprefix(self.run + '-'))


def healthy(url, body=None, timeout=None):
    key = url.split('/')[0]
    w = next(w for w in PLAN_WORKERS if w['key'] == key)
    return dict(pod_uid='uid-' + key, node=w['node'], stages={t: {} for t in w['stages']}, errors={})


class LiveCoverage(unittest.TestCase):
    def live(self, tmp):
        models = {w['key']: dict(models={t: dict(model_sha256=None, derived_artifact_sha256=None) for t in w['stages']}) for w in PLAN_WORKERS}
        l = live.Live(HealthSession(tmp), dict(workers=PLAN_WORKERS, model_provenance=plan_models(models)), tmp, 'pilot', http=healthy)
        l.urls = {w['key']: w['key'] for w in PLAN_WORKERS}
        return l

    def test_full_heterogeneous_fleet_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            l = self.live(tmp)
            l.verify_workers([])
            self.assertEqual(len(l.workers), 4)

    def test_lost_accelerators_refuse_collection_and_keep_the_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            l = self.live(tmp)
            with self.assertRaises(RuntimeError):
                l.verify_workers(['jgpu-dev9', 'npu-c'])
            saved = json.loads((Path(tmp)/'live-candidates.json').read_text())
            self.assertEqual((saved['coverage']['gpu'], saved['coverage']['npu'], sorted(saved['removed'])), (False, False, ['jgpu-dev9', 'npu-c']))

    def test_missing_fixed_heterogeneous_plan_refuses_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                self.live(tmp).verify_workers(['cpu-e0'])  # CPU+GPU+NPU remain, but the fixed baseline cannot run


class Forwarding:
    """A DagWorker whose downstream POST (the network boundary) is held at an explicit gate after compute finished."""

    def __init__(self):
        import threading
        self.entered, self.release, self.sent = threading.Event(), threading.Event(), []
        stage = type('Stage', (), dict(describe=lambda self: dict(backend='cpu-torch', device='cpu', model_sha256='m'),
                                        run=lambda self, data: (b'tensor', dict(output_device='cpu'))))()
        self.worker = fx.dag_worker.DagWorker('cpu-torch', ['image_preprocess'], loader=lambda *a, **kw: stage, post=self.post)
        self.relay = fx.gateway.Relay()
        self.thread = threading.Thread(target=self.worker.run, args=(dict(
            mode='dag', run_id='old-run', request_id='old-request', stage='image_preprocess', data=b'img', budget_s=10,
            forward=[dict(url='http://next/run', stage='vision', input='tensor', src='cpu-e', dst='gpu-e', route='direct')]),))

    def post(self, url, body, timeout):
        self.entered.set()
        assert self.release.wait(10)
        self.sent.append(body['request_id'])
        return dict(status='ok', data='YQ==', receipts=[], hops=[])

    def http(self, url, body=None, timeout=None):
        if url == 'gateway/health':
            return self.relay.health()
        if url == 'gateway/reset':
            return self.relay.reset(body['label'])[1]
        return self.worker.run(dict(mode='queue'))[1]

    def live(self, tmp):
        l = live.Live(SavingSession(tmp), {}, tmp, 'pilot', http=self.http, sleep=lambda s: None)
        l.b = dict(l.b, drain_timeout_s=0.3)
        l.workers, l.urls = [dict(key='cpu-e')], {'cpu-e': 'worker', 'gateway': 'gateway'}
        l.cloud_ips, l.edge_ips = ['10.1.0.1'], ['10.1.0.2']
        return l


class Drain(unittest.TestCase):
    def setUp(self):
        self.f = Forwarding()
        self.f.thread.start()
        self.assertTrue(self.f.entered.wait(2))
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.f.release.set()
        self.f.thread.join(2)
        self.tmp.cleanup()

    def test_outstanding_forward_is_not_idle(self):
        self.assertFalse(self.f.live(self.tmp.name).drain('next-policy')['idle'])
        self.f.release.set()
        self.f.thread.join(2)
        self.assertEqual(self.f.sent, ['old-request'])
        self.assertTrue(self.f.live(self.tmp.name).drain('next-policy')['idle'])

    def test_condition_change_waits_for_quiescence_but_restore_always_applies(self):
        l = self.f.live(self.tmp.name)
        with self.assertRaises(RuntimeError):
            l.shape('good')
        self.assertEqual(l.s.nets, [])
        l.shape('relay-unshaped', restore=True)
        self.assertEqual([spec['mbps'] for _, spec in l.s.nets], [None])


class CountingStage:
    def __init__(self, name):
        self.name, self.calls = name, 0

    def describe(self):
        return dict(backend='cpu-torch', device='cpu', model_sha256='m')

    def run(self, data):
        self.calls += 1
        return (b'{"answers":["yes"],"answer_ids":[1]}' if self.name == 'fusion' else b'out'), dict(output_device='cpu')


class QueueDeadline(unittest.TestCase):
    """A request's budget bounds its wait for the one-unit device lock; expired work never reaches the model."""

    def worker(self):
        stages = {}
        def load(name, *a, **kw):
            return stages.setdefault(name, CountingStage(name))
        return fx.dag_worker.DagWorker('cpu-torch', ['image_preprocess', 'fusion'], loader=load), stages

    def run_async(self, w, body):
        import threading
        out = []
        t = threading.Thread(target=lambda: out.append(w.run(body)))
        t.start()
        return t, out

    def test_expired_stage_request_is_rejected_without_a_model_call(self):
        w, stages = self.worker()
        w.lock.acquire()
        try:
            t, out = self.run_async(w, dict(mode='dag', run_id='r', request_id='q', stage='image_preprocess', data=b'x', budget_s=0.05))
            t.join(1.0)
            self.assertFalse(t.is_alive(), 'expired queued request must answer while the device is still busy')
        finally:
            w.lock.release()
        status, reply = out[0]
        self.assertEqual((status, reply['error'], stages['image_preprocess'].calls, w.waiting), (504, 'queue_deadline_expired', 0, 0))

    def test_expired_fusion_answers_both_join_parts_without_fusion(self):
        w, stages = self.worker()
        w.lock.acquire()
        try:
            body = dict(mode='dag', run_id='r', request_id='q', stage='fusion_join', budget_s=0.3)
            t1, out1 = self.run_async(w, dict(body, part='vision', data=b'v'))
            t2, out2 = self.run_async(w, dict(body, part='speech', data=b's'))
            t1.join(1.5)
            t2.join(1.5)
            self.assertFalse(t1.is_alive() or t2.is_alive())
        finally:
            w.lock.release()
        self.assertEqual(sorted(o[0][0] for o in (out1, out2)), [504, 504])
        self.assertEqual((stages['fusion'].calls, w.run(dict(mode='queue'))[1]['joins_open']), (0, 0))

    def test_qualification_requests_without_a_deadline_keep_waiting(self):
        w, stages = self.worker()
        w.lock.acquire()
        t, out = self.run_async(w, dict(run_id='r', request_id='q', stage='image_preprocess', data=b'x'))
        t.join(0.2)
        self.assertTrue(t.is_alive())
        w.lock.release()
        t.join(2)
        self.assertEqual((out[0][0], stages['image_preprocess'].calls), (200, 1))


class TricklePeer:
    """Loopback HTTP peer that answers 200 one byte every 20 ms (a slow but never idle upstream)."""

    def __init__(self, payload=b'{"status":"ok","data":"YQ==","receipts":[],"hops":[]}'):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                try:
                    for b in payload:
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


class AbsoluteBounds(unittest.TestCase):
    def setUp(self):
        self.peer = TricklePeer()

    def tearDown(self):
        self.peer.close()

    def test_gateway_relay_ends_at_the_request_budget(self):
        from http_runtime import encode_body
        relay = fx.gateway.Relay(allowed_prefixes=('http://127.0.0.1',))
        relay.freeze(dict(p=dict(url=self.peer.url)))
        raw, ctype = encode_body(dict(budget_s=0.1))
        started = time.monotonic()
        status, _, _ = relay.relay('p', 'run', raw, ctype)
        self.assertEqual(status, 504)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(relay.health()['counters']['p']['timeouts'], 1)

    def test_dag_forward_ends_at_the_request_budget(self):
        w = fx.dag_worker.DagWorker('cpu-torch', ['image_preprocess'], loader=lambda name, *a, **kw: CountingStage(name))
        started = time.monotonic()
        status, reply = w.run(dict(mode='dag', run_id='r', request_id='q', stage='image_preprocess', data=b'x', budget_s=0.3,
                                   forward=[dict(url=self.peer.url + '/run', stage='vision', src='a', dst='b', route='direct')]))
        self.assertEqual((status, reply['error']), (504, 'forward_timeout'))
        self.assertLess(time.monotonic() - started, 0.8)

    def test_gateway_refuses_an_oversized_upstream_body(self):
        from http_runtime import encode_body
        big = TricklePeer(payload=b'x' * 64)
        old = fx.gateway.MAX_BODY
        fx.gateway.MAX_BODY = 16
        try:
            relay = fx.gateway.Relay(allowed_prefixes=('http://127.0.0.1',))
            relay.freeze(dict(p=dict(url=big.url)))
            raw, ctype = encode_body(dict(budget_s=5))
            self.assertEqual(relay.relay('p', 'run', raw, ctype)[0], 502)
        finally:
            fx.gateway.MAX_BODY = old
            big.close()

    def test_generator_drops_a_trickling_incoming_request_at_its_read_deadline(self):
        import socket
        old = fx.generator.READ_DEADLINE_S
        fx.generator.READ_DEADLINE_S = 0.3
        srv = fx.generator.serve(fx.generator.Generator(), host='127.0.0.1', port=0)
        import threading
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            conn = socket.create_connection(('127.0.0.1', srv.server_address[1]), timeout=5)
            conn.sendall(b'POST /configure HTTP/1.1\r\nContent-Length: 1000\r\n\r\n')
            started = time.monotonic()
            closed = None
            while time.monotonic() - started < 3:
                try:
                    conn.sendall(b'x')
                    if conn.recv(1, socket.MSG_DONTWAIT) == b'':
                        closed = time.monotonic() - started
                        break
                except BlockingIOError:
                    pass
                except OSError:
                    closed = time.monotonic() - started
                    break
                time.sleep(0.02)
            conn.close()
            self.assertIsNotNone(closed, 'trickling client must be cut at the absolute read deadline')
            self.assertLess(closed, 1.5)
        finally:
            fx.generator.READ_DEADLINE_S = old
            srv.shutdown()
            srv.server_close()


class CalibrationQualification(unittest.TestCase):
    """Live.profile freezes candidate costs only from complete, identity/device/model-verified calibration receipts whose
    split path gives the same final answer as the unchanged original path."""

    @classmethod
    def setUpClass(cls):
        c = fx.Cluster(fx.LAYOUT)
        try:
            producers = {t: 'cpu-c' for t in fx.policies.TASKS}
            cls.result = c.gen.profile(dict(question_ids=['11', '12'], warmup=1, producers=producers))
            cls.workers, cls.created, _, cls.run_id = c.validation()
            cls.candidates = c.candidates
        finally:
            c.close()

    def live(self, tmp, result):
        state = dict(state='done', result=result)
        l = live.Live(SavingSession(tmp, run=self.run_id), {}, tmp, 'pilot', sleep=lambda s: None,
                      http=lambda url, body=None, timeout=None: state if '/job' in url else dict(status='started'))
        l.urls, l.candidates, l.uids = {'generator': 'gen'}, self.candidates, dict(self.created)
        l.workers = [dict(key=k, node=w['node'], backend=w['backend']) for k, w in self.workers.items()]
        l.health = {k: dict(stages={t: dict(m) for t, m in w['models'].items()}) for k, w in self.workers.items()}
        l.plan = dict(pairs=[dict(question_id=q, split='calibration') for q in ('11', '12')], model_provenance=plan_models(self.workers))
        l.b = dict(l.b, profile_pairs=2, profile_warmup=1)
        return l

    def test_generator_checks_every_model_candidate_against_the_original_path(self):
        eq = self.result['equivalence']
        covered = {(r['task'], r['worker']) for r in eq}
        self.assertEqual(covered, {(t, w) for t in ('vision', 'speech') for w in self.candidates[t]})
        self.assertTrue(all(r['answer_equal'] for r in eq), eq[:2])

    def test_verified_calibration_freezes_costs(self):
        with tempfile.TemporaryDirectory() as tmp:
            l = self.live(tmp, self.result)
            l.profile()
            self.assertIn('vision@npu-c', l.compute)

    def test_foreign_or_cpu_receipts_cannot_qualify_a_candidate(self):
        result = copy.deepcopy(self.result)
        for r in result['rows']:
            if r.get('receipt') and r['worker'] == 'npu-c':
                r['receipt'].update(pod_uid='not-the-created-worker', device='cpu', backend='cpu-torch')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                self.live(tmp, result).profile()
            verdict = json.loads((Path(tmp)/'profile-qualification.json').read_text())
            self.assertTrue(any('npu-c' in p for p in verdict['problems']))

    def test_incomplete_calibration_cannot_qualify_a_candidate(self):
        result = copy.deepcopy(self.result)
        result['rows'] = [r for r in result['rows'] if not (r['worker'] == 'gpu-e' and r['task'] == 'speech' and not r['warmup'])][:]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                self.live(tmp, result).profile()

    def test_split_path_answer_differing_from_the_original_path_is_refused(self):
        result = copy.deepcopy(self.result)
        result['equivalence'][0]['answer_equal'] = False
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                self.live(tmp, result).profile()


def load_capture():
    """This directory's capture.py by path (another experiment's `capture` module may precede it on sys.path)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('qnb_capture', HERE/'capture.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PreflightEvidence(unittest.TestCase):
    """capture.capture at its subprocess seam: every outcome leaves a terminal record; raw stdout is never retained."""

    def capture(self, runner):
        capture = load_capture()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name)/'preflight'
        with self.assertRaises(Exception):
            capture.capture(out, runner=runner)
        return json.loads((out/'capture.json').read_text())

    def test_timeout_keeps_a_terminal_record_with_safe_diagnostics(self):
        import subprocess
        def times_out(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 60, output='{"items": [{"env": "SECRET"}', stderr='partial stderr')
        record = self.capture(times_out)
        self.assertEqual((record['outcome'], record['calls'][0]['outcome']), ('timeout', 'timeout'))
        self.assertIn('partial stderr', record['calls'][0]['stderr'])
        self.assertNotIn('SECRET', json.dumps(record))
        self.assertIn('finished_utc', record)

    def test_nonzero_exit_keeps_the_header(self):
        import subprocess
        def fails(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout='', stderr='forbidden')
        record = self.capture(fails)
        self.assertEqual((record['outcome'], record['calls'][0]['rc']), ('error', 1))

    def test_kubectl_runs_through_rtk_proxy_read_only(self):
        capture = load_capture()
        self.assertTrue(all(cmd[:3] == ['rtk', 'proxy', 'kubectl'] for cmd in capture.COMMANDS.values()))
        with self.assertRaises(ValueError):
            capture.run(['rtk', 'proxy', 'kubectl', 'delete', 'pod', 'x'])


VQA_EVAL = 'experiments/quality-network-vqa/vqa_eval.py'


class FrozenRunAnalysis(unittest.TestCase):
    """analyze.analyze on a run directory: the frozen plan, GT/corpus hashes and the seeded cohort govern the result."""

    @classmethod
    def setUpClass(cls):
        c = fx.Cluster(fx.LAYOUT)
        cls.pilot = dict(design.PILOT, conditions=('good',), rates_rps=(1.0,), policies=('fixed',), window_s=4.0)
        cls.cell = design.pilot_cells(cls.pilot)[0]
        cls.questions = c.questions
        cls.arrivals = design.arrivals(cls.cell['rate_rps'], 4.0, cls.cell['seed'], cls.questions)
        try:
            cls.ledger = c.trace(cls.cell['label'], fx.MIXED, [a['offset_s'] for a in cls.arrivals],
                                 question_ids=[a['question_id'] for a in cls.arrivals])
            cls.workers, cls.created, cls.corpus, cls.run_id = c.validation()
        finally:
            c.close()
        assert all(r['status'] == 'ok' for r in cls.ledger['rows'])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.plan_dir, self.run = root/'plan', root/self.run_id
        self.plan_dir.mkdir()
        self.run.mkdir()
        gt = root/'gt.json'
        gt.write_text(json.dumps(dict(pairs=[dict(GT_YES, question_id=q) for q in self.questions])))
        corpus = dict(files=[dict(question_id=q, kind=k, path=f'corpus/{q}-{k}.npy', sha256=self.corpus[q][k])
                             for q in self.questions for k in ('image', 'audio')])
        (self.plan_dir/'corpus.json').write_text(json.dumps(corpus))
        plan = dict(workers=[dict(key=k, node=w['node'], backend=w['backend']) for k, w in self.workers.items()],
                    pilot=self.pilot, compare=design.COMPARE, quality=dict(mode='diagnostic', qmin=0.4),
                    inputs=dict(gt=str(gt), gt_sha256=baselines.sha(gt)), corpus_sha256=baselines.sha(self.plan_dir/'corpus.json'),
                    pairs=[dict(question_id=q, split='evaluation') for q in self.questions], model_provenance=plan_models(self.workers),
                    # the execution contract the loopback trace ran under (fx.Cluster.trace defaults) and the scoring dependency
                    budgets=dict(design.BUDGETS, deadline_s=2.0, cancel_s=5.0, max_inflight=8), fixed_plan=fx.MIXED,
                    policies=list(design.POLICY_SPECS), reused_frozen_sources={VQA_EVAL: baselines.sha(design.ROOT/VQA_EVAL)})
        (self.plan_dir/'plan.json').write_text(json.dumps(plan))
        self.write('release.json', dict(binding=dict(plan_sha256=baselines.sha(self.plan_dir/'plan.json'), run_id=self.run_id)))
        self.write('collection.json', dict(stage='pilot'))
        self.write('cells.json', [self.cell])
        self.write('pods.created.json', dict(confirmed=[dict(kind='Pod', name=f'{self.run_id}-{k}', uid=u) for k, u in self.created.items()]))
        self.write('worker-health.json', dict(health={k: dict(stages={t: dict(m) for t, m in w['models'].items()}) for k, w in self.workers.items()}))
        self.request = dict(label=self.cell['label'], policy=dict(name='fixed', plan=fx.MIXED), arrivals=self.arrivals, deadline_s=2.0,
                            cancel_s=5.0, max_inflight=8, quality=plan['quality'])
        self.write(f"trace-{self.cell['label']}.request.json", self.request)
        self.write(f"ledger-{self.cell['label']}.json", self.ledger)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, value):
        (self.run/name).write_text(json.dumps(value))

    def test_frozen_run_is_summarized_on_its_cohort(self):
        trace = analyze.analyze(self.run, self.plan_dir)['traces'][0]
        self.assertEqual((trace['requests'], trace['counts']), (len(self.arrivals), {'ok': len(self.arrivals)}))

    def test_changed_ground_truth_or_corpus_is_refused(self):
        gt = json.loads((self.plan_dir/'plan.json').read_text())['inputs']['gt']
        Path(gt).write_text(Path(gt).read_text().replace('"yes"', '"no"'))
        with self.assertRaises(ValueError):
            analyze.analyze(self.run, self.plan_dir)
        self.setUp()
        corpus = json.loads((self.plan_dir/'corpus.json').read_text())
        corpus['files'][0]['sha256'], corpus['files'][2]['sha256'] = corpus['files'][2]['sha256'], corpus['files'][0]['sha256']
        (self.plan_dir/'corpus.json').write_text(json.dumps(corpus))
        with self.assertRaises(ValueError):
            analyze.analyze(self.run, self.plan_dir)

    def test_run_released_for_another_plan_is_refused(self):
        self.write('release.json', dict(binding=dict(plan_sha256='0' * 64, run_id=self.run_id)))
        with self.assertRaises(ValueError):
            analyze.analyze(self.run, self.plan_dir)

    def test_trace_request_that_is_not_the_seeded_cohort_is_refused(self):
        self.write(f"trace-{self.cell['label']}.request.json", dict(self.request, arrivals=self.arrivals[1:]))
        with self.assertRaises(ValueError):
            analyze.analyze(self.run, self.plan_dir)

    def test_trace_without_ledger_counts_its_whole_cohort_as_missing(self):
        (self.run/f"ledger-{self.cell['label']}.json").unlink()
        trace = analyze.analyze(self.run, self.plan_dir)['traces'][0]
        self.assertEqual((trace['requests'], trace['counts'], trace['goodput_rps']), (len(self.arrivals), {'missing_result': len(self.arrivals)}, 0.0))


if __name__ == '__main__':
    unittest.main()
