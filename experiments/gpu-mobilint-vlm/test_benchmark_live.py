"""benchmark_live seams: plan validation + release binding, sequential receipt freshness, first-failure stop, durable partial
summary and UID cleanup on failure/interrupt. Fakes: loopback HTTP workers on the owned Pods' podIPs (the heterogeneous
Worker's /health + /run reply shape), the kubectl seam (collect.Session.call) and live.execute (the qualification, already
covered by test_vlm). live.call_arm / identity / check_occupancy / verify_cleanup and Session cleanup run for real.

Run (repo root, stdlib only):
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=experiments/gpu-mobilint-vlm:experiments/quality-network-heterogeneous:experiments/real-topology-live:experiments/multi-edge-sensors:experiments/quality-network-baselines \
    python3 -m unittest -v test_benchmark_live
"""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock


def modules():
    """Import without leaking live.py's collector sys.path into other tests (see test_vlm.live_module)."""
    saved = list(sys.path)
    try:
        import benchmark_live
        import live
        import collect
        import protocol
    finally:
        sys.path[:] = saved
    return benchmark_live, live, collect, protocol


B, live, collect, P = modules()
RUN = 'gmv-bench-test'
ARMS = tuple(P.ARMS)
IDS = [i['id'] for i in P.INPUTS]
IP = dict(mobilint='127.0.0.1', cuda='127.0.0.2')
QUALIFICATION_CALLS = 4


def plan_rows():
    """The coordinator's shape: 10 contiguous rounds, 4 pairs each (global ids), both arms per pair, order alternating."""
    rows, pair = [], 0
    for rnd in range(1, 11):
        for k, item in enumerate(IDS):
            pair += 1
            for arm in (ARMS if (rnd + k) % 2 else ARMS[::-1]):
                rows.append(dict(measurement_id=f'm{len(rows)+1:03d}', round=rnd, pair=pair, arm=arm, input=item))
    return rows


def frozen_dir(tmp):
    frozen = Path(tmp)/'frozen'
    frozen.mkdir()
    models = {a['model']: dict(weights=P.MODELS[a['model']]['weights']) for a in P.ARMS.values()}
    (frozen/'contract.json').write_text(json.dumps(dict(models=models)))
    inputs = []
    for item in IDS:
        (frozen/f'{item}.npy').write_bytes(f'img-{item}'.encode())
        inputs.append(dict(id=item, source=str(frozen/f'{item}.npy')))
    (frozen/'assets-manifest.json').write_text(json.dumps(dict(inputs=inputs, files=[], footprint_bytes={})))
    return frozen


class FakeWorker:
    """The shared Worker's observable contract: /health queue.completed == receipt.sequence of the last /run."""

    def __init__(self, arm, host, port, contract_sha, fail_at=None, reported=None):
        self.arm, self.uid, self.completed, self.fail_at = arm, f'uid-{arm}', 0, fail_at
        self.reported = reported or (lambda sequence: sequence)
        self.contract_sha, self.runs = contract_sha, []
        worker = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.reply(200, worker.health())

            def do_POST(self):
                from http_runtime import decode_body
                raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
                self.reply(*worker.run(decode_body(raw, self.headers.get('Content-Type'))))

            def reply(self, status, value):
                raw = json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def health(self):
        stage = dict(device=live.EXPECTED_DEVICE[self.arm], contract_sha256=self.contract_sha,
                     model_sha256=P.MODELS[P.ARMS[self.arm]['model']]['weights'])
        return dict(status='ready', pod_uid=self.uid, node=P.ARMS[self.arm]['node'], stages={P.STAGE: stage},
                    queue=dict(waiting=0, active=0, completed=self.completed))

    def run(self, body):
        self.completed += 1
        self.runs.append(body['request_id'])
        if self.completed == self.fail_at:
            return 500, dict(status='error', error='call_failed: fake runtime', sequence=self.completed)
        item = body['request_id'][:-len(self.arm)-1]
        device = dict(output_device='cuda:0') if self.arm == 'cuda' else dict(output_tensor_device='cpu', output_tensor_note='host tensor')
        output = json.dumps(dict(input_id=item, text='skis', token_ids=[1, 2], device=device)).encode()
        h = self.health()['stages'][P.STAGE]
        receipt = dict(stage=P.STAGE, backend=self.arm, device=h['device'], node=P.ARMS[self.arm]['node'], pod_uid=self.uid,
                       run_id=body['run_id'], request_id=body['request_id'], sequence=self.reported(self.completed),
                       input_sha256=P.sha256(body['data']), output_sha256=P.sha256(output), model_sha256=h['model_sha256'],
                       static=dict(contract_sha256=self.contract_sha), worker_wall_ms=12.5,
                       timing=dict(output_device='cuda:0', generate_ms=10.0) if self.arm == 'cuda' else dict(generate_ms=11.0))
        return 200, dict(status='ok', data=base64.b64encode(output).decode(), receipt=receipt)


class FakeCluster:
    """The kubectl seam (collect.Session.call): owned Pods by name, `get pods -A` for occupancy, UID delete via --raw."""

    def __init__(self):
        self.objects, self.calls = {}, []

    def pod(self, run, arm, uid):
        resource = next(iter(P.ARMS[arm]['device_resource']))
        return dict(kind='Pod', metadata=dict(name=f'{run}-{arm}', namespace=P.NS, uid=uid, labels={'run-id': run}),
                    spec=dict(nodeName=P.ARMS[arm]['node'], containers=[dict(resources=dict(requests={resource: '1'}))]),
                    status=dict(phase='Running', podIP=IP[arm], conditions=[dict(type='Ready', status='True')]))

    def call(self, session, stem, args, stdin=None, timeout=60):
        self.calls.append(list(args))
        session.counter += 1
        tag = f'{session.counter:04d}-{stem}'
        out, err = '', ''
        if args[:2] == ['get', 'pods']:
            out = json.dumps(dict(items=[o for o in self.objects.values() if o['kind'] == 'Pod']))
        elif args[0] == 'get' and args[4] in self.objects:
            out = json.dumps(self.objects[args[4]])
        elif args[0] == 'delete' and args[2].rsplit('/', 1)[1] in self.objects:
            del self.objects[args[2].rsplit('/', 1)[1]]
        else:
            err = 'Error from server (NotFound)'
        (session.out/(tag+'.stdout')).write_text(out)
        (session.out/(tag+'.stderr')).write_text(err)
        if err:
            raise RuntimeError(f'{tag} failed rc=1')
        return out


class Harness:
    """Real Session/main around fake workers + cluster; execute() is the qualification stand-in."""

    def __init__(self, tmp, fail_at=None, reported=None, interrupt=False):
        self.tmp, self.frozen = Path(tmp), frozen_dir(tmp)
        self.contract_sha = P.file_sha(self.frozen/'contract.json')
        self.plan = self.tmp/'plan.json'
        self.plan.write_text(json.dumps(dict(schema=1, seed=20261028, rows=plan_rows())))
        self.cluster, self.interrupt = FakeCluster(), interrupt
        first = FakeWorker('mobilint', IP['mobilint'], 0, self.contract_sha, fail_at.get('mobilint') if fail_at else None,
                           (reported or {}).get('mobilint'))
        self.port = first.server.server_port
        self.workers = dict(mobilint=first, cuda=FakeWorker('cuda', IP['cuda'], self.port, self.contract_sha,
                                                            fail_at.get('cuda') if fail_at else None, (reported or {}).get('cuda')))
        self.snapshots, self.executed = [], []

    def close(self):
        for w in self.workers.values():
            w.close()

    def execute(self, session, stage, arms=None, prepared=None):
        self.executed.append((stage, arms, prepared))
        if self.interrupt:
            raise KeyboardInterrupt()
        identities = {}
        for arm in live.selection(arms)[0]:
            obj = self.cluster.pod(session.run, arm, f'uid-{arm}')
            self.cluster.objects[obj['metadata']['name']] = obj
            session.resources.append(dict(kind='Pod', name=obj['metadata']['name'], uid=obj['metadata']['uid'], owned_labels=True))
            identities[arm] = live.identity(session, arm, self.workers[arm].health(), self.contract_sha)
            for item in IDS:  # the qualification calls this run made (ledger.jsonl), mirrored in the worker's count
                self.workers[arm].completed += 1
                B.append(session.out/'ledger.jsonl', dict(arm=arm, input=item, status='ok'))
        session.save('identities.json', identities)

    def argv(self, *extra):
        return ['--frozen', str(self.frozen), '--benchmark-plan', str(self.plan), *extra]

    def draft(self, *extra):
        with mock.patch.object(live, 'dependency_hashes', return_value={'stub.py': 'a'*64}), \
                mock.patch('sys.stdout', new_callable=lambda: __import__('io').StringIO()) as out:
            B.main(self.argv('--print-release-draft', *extra))
        return json.loads(out.getvalue())

    def release(self, value):
        path = self.tmp/'release.json'
        path.write_text(json.dumps(value))
        return path

    def run(self, release, *extra):
        with mock.patch.object(live, 'dependency_hashes', return_value={'stub.py': 'a'*64}), \
                mock.patch.object(live, 'execute', self.execute), mock.patch.object(P, 'PORT', self.port), \
                mock.patch.object(collect.Session, 'call', lambda s, *a, **k: self.cluster.call(s, *a, **k)), \
                mock.patch.object(collect, 'protected_snapshot', lambda s, label: self.snapshots.append(label)):
            B.main(self.argv('--runs', str(self.tmp/'runs'), '--release', str(release), '--run-id', RUN, *extra))

    def out(self):
        return self.tmp/'runs'/RUN

    def load(self, name):
        return json.loads((self.out()/name).read_text())

    def ledger(self):
        return [json.loads(line) for line in (self.out()/B.LEDGER).read_text().splitlines()]


def approved(draft, **changes):
    return dict(draft, approved_by='coordinator', stages=['benchmark'], **changes)


class PlanValidationTest(unittest.TestCase):
    def test_the_full_paired_plan_passes_and_each_defect_is_named(self):
        rows = plan_rows()
        self.assertEqual(B.plan_problems(rows, ARMS, IDS), [])
        swapped = [dict(r) for r in rows]
        swapped[0]['input'], swapped[2]['input'] = swapped[2]['input'], swapped[0]['input']
        defects = dict(
            duplicate=[dict(rows[0], measurement_id=rows[1]['measurement_id'])] + rows[1:],
            missing_row=rows[:-1],
            unknown_input=[dict(rows[0], input='cal-9999999')] + rows[1:],
            same_arm_twice=[dict(rows[0], arm=rows[1]['arm'])] + rows[1:],
            split_pair=[rows[0], rows[2], rows[1]] + rows[3:],
            mixed_pair_input=swapped,
            round_split=rows[2:16] + rows[:2] + rows[16:],
            unbalanced_round=[dict(r, round=2) if r['round'] == 1 and r['input'] == IDS[0] else r for r in rows])
        for name, bad in defects.items():
            with self.subTest(name=name):
                self.assertTrue(B.plan_problems(bad, ARMS, IDS), name)
        self.assertTrue(B.plan_problems([], ARMS, IDS))
        self.assertTrue(B.plan_problems([dict(arm='cuda')], ARMS, IDS))

    def test_arm_only_runs_the_effective_rows_of_the_full_plan_bound_by_their_own_hash(self):
        raw = json.dumps(dict(rows=plan_rows())).encode()
        pair, rows = B.binding(raw, plan_rows(), ARMS)
        one, cuda_rows = B.binding(raw, plan_rows(), ('cuda',))
        self.assertEqual((pair['plan_rows'], pair['effective_rows'], one['effective_rows']), (80, 80, 40))
        self.assertEqual(pair['plan_sha256'], one['plan_sha256'])
        self.assertNotEqual(pair['effective_sha256'], one['effective_sha256'])
        self.assertEqual({r['arm'] for r in cuda_rows}, {'cuda'})
        self.assertEqual(pair['collector_sha256'], P.file_sha(Path(B.__file__)))


class ReleaseBindingTest(unittest.TestCase):
    def test_the_draft_is_live_draft_plus_the_collector_and_plan_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            try:
                draft = h.draft()
                raw = h.plan.read_bytes()
            finally:
                h.close()
        self.assertEqual(set(draft) - {'benchmark'}, {'frozen', 'code', 'approved_by', 'stages'})
        self.assertEqual(draft['code'], {'stub.py': 'a'*64})
        self.assertEqual(draft['benchmark']['plan_sha256'], P.sha256(raw))
        self.assertEqual(draft['benchmark']['collector_sha256'], P.file_sha(Path(B.__file__)))

    def test_missing_or_changed_approvals_are_refused_before_any_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            try:
                draft = h.draft()
                bench = draft['benchmark']
                cases = dict(
                    missing_binding=approved({k: v for k, v in draft.items() if k != 'benchmark'}),
                    changed_collector=approved(draft, benchmark=dict(bench, collector_sha256='0'*64)),
                    changed_plan=approved(draft, benchmark=dict(bench, plan_sha256='0'*64)),
                    other_arm_selection=approved(draft, benchmark=dict(bench, selected_arms=['cuda'])),
                    stage_not_released=dict(approved(draft), stages=['qualification']),
                    unapproved=dict(approved(draft), approved_by=None),
                    changed_code=approved(draft, code={'stub.py': 'b'*64}))
                for name, release in cases.items():
                    with self.subTest(name=name), self.assertRaises(SystemExit) as raised:
                        h.run(h.release(release))
                    self.assertIn('release check failed', str(raised.exception))
                # a plan edited after approval: same release, new bytes
                good = h.release(approved(draft))
                h.plan.write_text(json.dumps(dict(schema=1, seed=1, rows=plan_rows())))
                with self.assertRaises(SystemExit) as raised:
                    h.run(good)
                self.assertIn('benchmark', str(raised.exception))
                self.assertFalse((h.tmp/'runs').exists())
                self.assertEqual((h.executed, h.cluster.calls), ([], []))
                h.plan.write_text(json.dumps(plan_rows()))
                with self.assertRaises(SystemExit):
                    h.run(good)
            finally:
                h.close()


class BenchmarkRunTest(unittest.TestCase):
    def run_case(self, **harness):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        h = Harness(tmp.name, **harness)
        self.addCleanup(h.close)
        release = h.release(approved(h.draft()))
        return h, release

    def test_all_planned_calls_are_sequential_fresh_and_durably_ledgered(self):
        h, release = self.run_case()
        h.run(release)
        ledger, summary = h.ledger(), h.load('benchmark-summary.json')
        rows = plan_rows()
        self.assertEqual([r['measurement_id'] for r in ledger], [r['measurement_id'] for r in rows])
        self.assertEqual([(r['round'], r['pair'], r['arm'], r['input']) for r in ledger],
                         [(r['round'], r['pair'], r['arm'], r['input']) for r in rows])
        self.assertTrue(all(r['status'] == 'ok' for r in ledger))
        for arm in ARMS:
            seqs = [r['receipt']['sequence'] for r in ledger if r['arm'] == arm]
            self.assertEqual(seqs, list(range(QUALIFICATION_CALLS + 1, QUALIFICATION_CALLS + 41)))
            self.assertEqual(h.load(f'benchmark-health-final-{arm}.json')['queue']['completed'], QUALIFICATION_CALLS + 40)
        self.assertEqual(ledger[0]['receipt']['timing']['generate_ms'], 11.0 if ledger[0]['arm'] == 'mobilint' else 10.0)
        self.assertIn('collector_wall_ms', ledger[0])
        self.assertEqual((summary['planned'], summary['completed'], summary['failed'], summary['unattempted']), (80, 80, 0, 0))
        self.assertEqual({p: summary['phases'][p]['status'] for p in summary['phases']},
                         {'live-qualification': 'ok', 'benchmark-ready': 'ok', 'benchmark': 'ok', 'benchmark-final': 'ok'})
        self.assertEqual(h.executed, [('qualification', None, None)])
        self.assertEqual((h.out()/'benchmark-plan.json').read_bytes(), h.plan.read_bytes())
        self.assertEqual(h.load('benchmark-targets.json')['urls'], {a: f'http://{IP[a]}:{h.port}' for a in ARMS})
        self.assertTrue(h.load('cleanup.json')['verified'])
        self.assertEqual(h.cluster.objects, {})
        self.assertEqual(h.snapshots, ['protected-after'])
        self.assertEqual(len((h.out()/'ledger.jsonl').read_text().splitlines()), 2 * QUALIFICATION_CALLS)

    def test_a_stale_or_skipped_sequence_stops_the_run_with_a_durable_partial_summary(self):
        cases = dict(stale=lambda s: s - 1 if s == QUALIFICATION_CALLS + 3 else s,     # a repeated (cached-looking) sequence
                     skipped=lambda s: s + 1 if s == QUALIFICATION_CALLS + 3 else s)   # a foreign call in between
        for name, reported in cases.items():
            with self.subTest(name=name):
                h, release = self.run_case(reported=dict(cuda=reported))
                with self.assertRaises(RuntimeError):
                    h.run(release)
                ledger, summary = h.ledger(), h.load('benchmark-summary.json')
                self.assertEqual([r['status'] for r in ledger], ['ok'] * (len(ledger) - 1) + ['error'])
                self.assertIn('sequence', ledger[-1]['error'])
                self.assertEqual((ledger[-1]['arm'], sum(r['arm'] == 'cuda' for r in ledger)), ('cuda', 3))
                self.assertEqual((summary['completed'], summary['failed'], summary['unattempted']),
                                 (len(ledger) - 1, 1, 80 - len(ledger)))
                self.assertEqual(summary['stopped_at'][0]['measurement_id'], ledger[-1]['measurement_id'])
                self.assertEqual(summary['phases']['benchmark']['status'], 'error')
                self.assertNotIn('benchmark-final', summary['phases'])
                self.assertTrue((h.out()/'benchmark-health-after-failure-cuda.json').exists())
                self.assertEqual(len(h.workers['cuda'].runs), 3)
                self.assertTrue(h.load('cleanup.json')['verified'])

    def test_the_first_failed_call_stops_and_the_rest_is_unattempted_and_cleaned_up(self):
        h, release = self.run_case(fail_at=dict(mobilint=QUALIFICATION_CALLS + 2))
        with self.assertRaises(RuntimeError):
            h.run(release)
        ledger, summary = h.ledger(), h.load('benchmark-summary.json')
        self.assertEqual((ledger[-1]['arm'], ledger[-1]['status'], ledger[-1]['http_status']), ('mobilint', 'error', 500))
        self.assertEqual(summary['per_arm']['mobilint'], dict(planned=40, completed=1, failed=1, interrupted=0))
        self.assertEqual(summary['unattempted'], 80 - len(ledger))
        self.assertEqual(len(h.workers['mobilint'].runs), 2)
        self.assertEqual(h.load('benchmark-health-after-failure-mobilint.json')['queue']['completed'], QUALIFICATION_CALLS + 2)
        cleanup = h.load('cleanup.json')
        self.assertTrue(cleanup['verified'])
        self.assertIn('RuntimeError', cleanup['error'])
        self.assertEqual(h.cluster.objects, {})

    def test_an_interrupt_during_qualification_still_saves_the_summary_and_cleans_up(self):
        h, release = self.run_case(interrupt=True)
        with self.assertRaises(KeyboardInterrupt):
            h.run(release)
        summary = h.load('benchmark-summary.json')
        self.assertEqual((summary['planned'], summary['completed'], summary['unattempted']), (80, 0, 80))
        self.assertEqual(summary['phases']['live-qualification']['status'], 'error')
        self.assertIn('KeyboardInterrupt', summary['error'])
        self.assertTrue(h.load('cleanup.json')['verified'])
        self.assertEqual(h.snapshots, ['protected-after'])
        self.assertFalse((h.out()/B.LEDGER).exists())
        self.assertEqual(sum(len(w.runs) for w in h.workers.values()), 0)

    def interrupted_run(self, target, name, fake):
        """Run with `fake` injecting KeyboardInterrupt at the third measured call; the interrupt must reach the caller."""
        h, release = self.run_case()
        with mock.patch.object(target, name, fake), self.assertRaises(KeyboardInterrupt):
            h.run(release)
        return h

    def assert_stopped_and_cleaned(self, h, ledger, summary):
        rows = plan_rows()
        self.assertEqual([r['measurement_id'] for r in ledger], [r['measurement_id'] for r in rows[:3]])  # no duplicate, no next
        self.assertEqual(sum(len(w.runs) for w in h.workers.values()), 3)                                 # no further call
        self.assertEqual(summary['unattempted'], 77)
        self.assertEqual(summary['completed'] + summary['failed'] + summary['interrupted'] + summary['unattempted'], 80)
        self.assertEqual(summary['phases']['benchmark']['status'], 'error')
        self.assertNotIn('benchmark-final', summary['phases'])
        self.assertIn('KeyboardInterrupt', summary['error'])
        cleanup = h.load('cleanup.json')
        self.assertTrue(cleanup['verified'])
        self.assertIn('KeyboardInterrupt', cleanup['error'])
        self.assertEqual(h.cluster.objects, {})
        self.assertEqual(h.snapshots, ['protected-after'])

    def test_an_interrupt_inside_a_measured_call_is_ledgered_as_attempted_with_unknown_outcome(self):
        real, posts = live.bounded_http.post, []

        def post(url, *args, **kwargs):  # the worker has run the request; the interrupt lands before call_arm returns
            reply = real(url, *args, **kwargs)
            posts.append(url)
            if len(posts) == 3:
                raise KeyboardInterrupt()
            return reply
        h = self.interrupted_run(live.bounded_http, 'post', post)
        ledger, summary = h.ledger(), h.load('benchmark-summary.json')
        last = ledger[-1]
        self.assertEqual([r['status'] for r in ledger], ['ok', 'ok', 'interrupted'])
        self.assertEqual((last['outcome'], last['reply_status'], last['plan_index']), ('unknown', None, 2))
        self.assertEqual((last['arm'], last['input']), (plan_rows()[2]['arm'], plan_rows()[2]['input']))
        self.assertGreaterEqual(last['collector_partial_ms'], 0)
        self.assertNotIn('receipt', last)
        self.assertEqual((summary['completed'], summary['failed'], summary['interrupted']), (2, 0, 1))
        self.assertEqual(summary['per_arm'][last['arm']]['interrupted'], 1)
        self.assertEqual((summary['stopped_at'][0]['measurement_id'], summary['stopped_at'][0]['status']), (last['measurement_id'], 'interrupted'))
        self.assert_stopped_and_cleaned(h, ledger, summary)

    def test_an_interrupt_after_a_reply_keeps_the_reply_once(self):
        real = B.append
        for name, written in dict(before_write=False, after_write=True).items():
            with self.subTest(name=name):
                seen = []

                def append(path, value, written=written, seen=seen):
                    if path.name != B.LEDGER or seen:
                        return real(path, value)
                    if value.get('plan_index') == 2:
                        seen.append(value)
                        if written:  # durable row, interrupt before append returns
                            real(path, value)
                        raise KeyboardInterrupt()
                    return real(path, value)
                h = self.interrupted_run(B, 'append', append)
                ledger, summary = h.ledger(), h.load('benchmark-summary.json')
                last = ledger[-1]
                self.assertEqual(last['receipt']['sequence'], QUALIFICATION_CALLS + 2)  # the reply itself is kept
                if written:
                    self.assertEqual(last['status'], 'ok')
                    self.assertEqual((summary['completed'], summary['interrupted']), (3, 0))
                else:
                    self.assertEqual((last['status'], last['reply_status']), ('interrupted', 'ok'))
                    self.assertIn('collector_wall_ms', last)
                    self.assertEqual((summary['completed'], summary['interrupted']), (2, 1))
                self.assert_stopped_and_cleaned(h, ledger, summary)

    def test_a_worker_with_foreign_calls_after_qualification_is_refused_before_any_benchmark_call(self):
        h, release = self.run_case()
        original = h.execute

        def foreign(session, stage, arms=None, prepared=None):
            original(session, stage, arms, prepared)
            h.workers['cuda'].completed += 1  # a /run this collector did not make
        h.execute = foreign
        with self.assertRaises(RuntimeError):
            h.run(release)
        self.assertEqual(h.load('benchmark-summary.json')['phases']['benchmark-ready']['status'], 'error')
        self.assertFalse((h.out()/B.LEDGER).exists())
        self.assertTrue(h.load('cleanup.json')['verified'])

    def test_arm_only_executes_only_its_effective_rows(self):
        h, release = self.run_case()
        release = h.release(approved(h.draft('--arm', 'cuda'), arms=['cuda']))  # live.py: an arm-only release names its arms
        h.run(release, '--arm', 'cuda')
        ledger = h.ledger()
        self.assertEqual((len(ledger), {r['arm'] for r in ledger}), (40, {'cuda'}))
        self.assertEqual(h.workers['mobilint'].runs, [])
        self.assertEqual(h.load('benchmark-summary.json')['planned'], 40)
        self.assertIn('not a paired comparison', h.load('selection.json')['scope'])


if __name__ == '__main__':
    unittest.main()
