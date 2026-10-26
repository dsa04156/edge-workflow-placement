"""Local integration of the real executor pieces over real HTTP on 127.0.0.1: qualification Worker lock + DagWorker
forwarding/join + hardened gateway + edge generator (open loop, ledger) + analyze validation.

Model stages are deterministic FAKES with configurable delays (plumbing evidence only, no model or device claim).
Covers: real branch overlap, self-forwarding without deadlock (device lock never held across forward/join waits),
one-unit device cap with visible queue, open-loop dispatch independent of completion, admission drops, deadline vs
cancel (late_ok / timeout), join timeout after a failed branch, no leaked joins/active work after drain, worker
admission rejection, hash/identity continuity checks, gateway allow-list/budget/reset, cross-zone refusal without gateway.
"""
import base64
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
import unittest
import urllib.error

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(ROOT/'experiments/quality-network-heterogeneous'))
sys.path.insert(2, str(ROOT/'experiments/real-topology-live'))
import analyze  # noqa: E402
import dag_worker  # noqa: E402
import gateway  # noqa: E402
import generator  # noqa: E402
import policies  # noqa: E402
from http_runtime import request  # noqa: E402

DELAYS = dict(image_preprocess=0.02, audio_preprocess=0.02, vision=0.05, speech=0.05, fusion=0.02)


def sha(b):
    return hashlib.sha256(b).hexdigest()


class FakeStage:
    def __init__(self, stage, backend, cluster, key):
        self.stage, self.backend, self.cluster, self.key = stage, backend, cluster, key
        self.device = {'cpu-torch': 'cpu', 'cuda-torch': 'cuda:0', 'openvino-npu': 'NPU'}[backend]

    def describe(self):
        return dict(stage=self.stage, backend=self.backend, device=self.device, model='fake-'+self.stage, model_sha256='fake-model-'+self.stage)

    def _work(self, out):
        started = time.monotonic()
        if self.stage in self.cluster.fail.get(self.key, ()):
            raise RuntimeError('injected stage failure')
        time.sleep(self.cluster.delays.get((self.key, self.stage), DELAYS[self.stage]))
        self.cluster.intervals.setdefault(self.key, []).append((started, time.monotonic(), self.stage))
        timing = dict(output_device=self.device)
        if self.backend == 'openvino-npu':
            timing['execution_devices'] = ['NPU']
        return out, timing

    def run(self, data):
        if self.stage == 'fusion':
            answer = dict(answers=['yes', 'no', '2', '1', 'red'], answer_ids=[1, 2, 3, 4, 5], scores=[.5, .2, .1, .1, .1],
                          inputs=sha(data['vision'] + data['speech']))
            return self._work(json.dumps(answer, separators=(',', ':')).encode())
        return self._work(b'tensor:' + sha(data).encode())

    def run_tensor(self, data):
        return self._work((b'feature:' if self.stage == 'vision' else b'what color is it ') + sha(data)[:8].encode())


class Cluster:
    """Workers (zone edge/cloud), a gateway and a generator, all on loopback."""

    def __init__(self, layout, max_active=None):
        self.delays, self.fail, self.intervals, self.servers = {}, {}, {}, []
        self.workers, self.urls, self.zones = {}, {}, {}
        for key, (zone, backend, stages) in layout.items():
            w = dag_worker.DagWorker(backend, stages, loader=lambda name, backend, key=key, **kw: FakeStage(name, backend, self, key))
            w.node, w.pod_uid = 'node-'+key, 'uid-'+key
            srv = dag_worker.serve(w, host='127.0.0.1', port=0, **({'max_active': max_active[key]} if max_active and key in max_active else {}))
            self.start(srv)
            self.workers[key], self.urls[key], self.zones[key] = w, f'http://127.0.0.1:{srv.server_address[1]}', zone
        self.relay = gateway.Relay(allowed_prefixes=('http://127.0.0.1',))
        gsrv = gateway.serve(self.relay, host='127.0.0.1', port=0)
        self.start(gsrv)
        self.gateway_url = f'http://127.0.0.1:{gsrv.server_address[1]}'
        self.gen = generator.Generator()
        osrv = generator.serve(self.gen, host='127.0.0.1', port=0)
        self.start(osrv)
        self.origin_url = f'http://127.0.0.1:{osrv.server_address[1]}'
        peers = {k: dict(url=u, zone=self.zones[k]) for k, u in self.urls.items()}
        peers['origin'] = dict(url=self.origin_url, zone='edge')
        self.relay.freeze(peers)
        self.questions = ['11', '12', '13']
        self.gen.corpus = {q: dict(image=b'IMG' + q.encode() * 50, audio=b'AUD' + q.encode() * 20) for q in self.questions}
        self.corpus_sha = {q: dict(image=sha(v['image']), audio=sha(v['audio'])) for q, v in self.gen.corpus.items()}
        cands = {t: [k for k, (z, b, st) in layout.items() if t in st] for t in policies.TASKS}
        self.gen.configure(dict(run_id='qnb-test', workers={k: dict(url=self.urls[k], zone=self.zones[k], node='node-'+k, pod_uid='uid-'+k)
                                                            for k in layout},
                                gateway_url=self.gateway_url, origin_url=self.origin_url, origin_zone='edge', candidates=cands,
                                poll_s=0.2, poll_timeout_s=0.5))
        self.candidates = cands
        self.layout = layout

    def start(self, srv):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers.append(srv)

    def close(self):
        self.gen.close()
        for s in self.servers:
            s.shutdown()
            s.server_close()

    def profile(self):
        compute = {f'{t}@{w}': 30.0 for t in policies.TASKS for w in self.candidates[t]}
        links = {f'{a}->{b}': dict(latency_ms=1.0, ms_per_byte=1e-6) for a in ['origin', *self.urls] for b in ['origin', *self.urls]}
        return dict(compute_ms=compute, links=links, payload_bytes=dict(image=200, image_tensor=80, feature=20, audio=60, input_values=60,
                                                                     text=30, answer=100), source='synthetic-test')

    def trace(self, label, plan, offsets, deadline_s=2.0, cancel_s=5.0, max_inflight=8, policy=None, wait_s=30, question_ids=None):
        qids = question_ids or [self.questions[i % len(self.questions)] for i in range(len(offsets))]
        arrivals = [dict(index=i, offset_s=o, question_id=qids[i]) for i, o in enumerate(offsets)]
        body = dict(label=label, policy=policy or dict(name='fixed', plan=plan), arrivals=arrivals, deadline_s=deadline_s, cancel_s=cancel_s,
                    max_inflight=max_inflight, profile=self.profile(), quality=dict(mode='diagnostic', qmin=0.4))
        request(self.origin_url + '/trace', body, timeout=10)
        stop = time.monotonic() + wait_s
        while time.monotonic() < stop:
            state = self.gen.status()['traces'][label]
            if state['state'] != 'running':
                break
            time.sleep(0.05)
        return self.gen.ledger(label)

    def idle(self, timeout=10):
        stop = time.monotonic() + timeout
        while time.monotonic() < stop:
            states = [w.run(dict(mode='queue'))[1] for w in self.workers.values()]
            if all(s['waiting'] == 0 and s['active'] == 0 and s['joins_open'] == 0 for s in states) and self.relay.active == 0:
                return True
            time.sleep(0.05)
        return False

    def validation(self):
        workers = {k: dict(node='node-'+k, backend=b, models={t: dict(model_sha256='fake-model-'+t, derived_artifact_sha256=None) for t in st})
                   for k, (z, b, st) in self.layout.items()}
        created = {k: 'uid-'+k for k in self.layout}
        return workers, created, self.corpus_sha, 'qnb-test'


ALL = list(policies.TASKS)
LAYOUT = {'cpu-e': ('edge', 'cpu-torch', ALL), 'gpu-e': ('edge', 'cuda-torch', ['vision', 'speech', 'fusion']),
          'cpu-c': ('cloud', 'cpu-torch', ALL), 'npu-c': ('cloud', 'openvino-npu', ['vision'])}
MIXED = dict(image_preprocess='cpu-e', vision='npu-c', audio_preprocess='cpu-e', speech='gpu-e', fusion='cpu-c')


def overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


class Executor(unittest.TestCase):
    def setUp(self):
        self.c = Cluster(LAYOUT)

    def tearDown(self):
        self.c.close()

    def test_cross_zone_chain_is_valid_and_routed_through_the_gateway(self):
        ledger = self.c.trace('t-mixed', MIXED, [0.0, 0.15, 0.3])
        self.assertEqual([r['status'] for r in ledger['rows']], ['ok'] * 3)
        workers, created, corpus, run_id = self.c.validation()
        for row in ledger['rows']:
            self.assertIsNone(analyze.validate_row(row, workers, created, corpus, run_id), row['request_id'])
            hops = {h['dst']: h['route'] for b in row['branches'].values() for h in b['reply']['hops']}
            self.assertEqual(hops['npu-c'], 'gateway')   # edge preprocessing -> server NPU
            self.assertEqual(hops['gpu-e'], 'direct')    # edge -> edge
            self.assertEqual(row['branches']['image']['url_route'], 'direct')
        counters = self.c.relay.health()['counters']
        self.assertGreaterEqual(counters['npu-c']['requests'], 3)
        self.assertGreaterEqual(counters['cpu-c']['requests'], 6)  # both join parts per request

    def test_branches_really_overlap_on_different_devices(self):
        self.c.delays.update({('npu-c', 'vision'): 0.4, ('gpu-e', 'speech'): 0.4})
        ledger = self.c.trace('t-overlap', MIXED, [0.0])
        self.assertEqual(ledger['rows'][0]['status'], 'late_ok' if ledger['rows'][0]['latency_ms'] > 2000 else 'ok')
        v = next(i for i in self.c.intervals['npu-c'] if i[2] == 'vision')
        s = next(i for i in self.c.intervals['gpu-e'] if i[2] == 'speech')
        self.assertTrue(overlap(v, s), 'vision and speech branches must run concurrently')
        self.assertLess(ledger['rows'][0]['latency_ms'], 800 + 400)

    def test_open_loop_dispatch_does_not_wait_for_completion_and_late_answers_are_labelled(self):
        self.c.delays[('npu-c', 'vision')] = 0.6
        offsets = [i * 0.1 for i in range(8)]
        ledger = self.c.trace('t-open', MIXED, offsets, deadline_s=0.3, cancel_s=8.0, max_inflight=16)
        lags = [r['dispatch_lag_ms'] for r in ledger['rows']]
        self.assertLess(max(lags), 150, lags)
        sends = [r['send_offset_s'] for r in ledger['rows']]
        dones = [r['completed_offset_s'] for r in ledger['rows']]
        self.assertLess(sends[-1], dones[0] + 0.5)  # later arrivals were sent before earlier ones finished queueing
        self.assertTrue(all(r['status'] == 'late_ok' for r in ledger['rows']), [r['status'] for r in ledger['rows']])
        queue_waits = [rc['queue_wait_ms'] for r in ledger['rows'] for rc in r['branches']['image']['reply']['receipts'] if rc['stage'] == 'vision']
        self.assertGreater(max(queue_waits), 100)  # one-unit NPU queue is visible in receipts

    def test_admission_drop_timeout_and_no_leak_after_drain(self):
        self.c.delays[('npu-c', 'vision')] = 0.8
        ledger = self.c.trace('t-drop', MIXED, [i * 0.05 for i in range(6)], deadline_s=0.3, cancel_s=0.6, max_inflight=2)
        statuses = [r['status'] for r in ledger['rows']]
        self.assertIn('admission_drop', statuses)
        self.assertIn('timeout', statuses)
        self.assertTrue(all(s in generator.TERMINAL for s in statuses), statuses)
        self.assertTrue(self.c.idle(timeout=15), 'joins/queues must drain to zero after cancelled requests')

    def test_failed_branch_ends_in_join_timeout_without_fusion(self):
        self.c.fail['gpu-e'] = ('speech',)
        ledger = self.c.trace('t-fail', MIXED, [0.0], deadline_s=0.5, cancel_s=1.0)
        row = ledger['rows'][0]
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['branches']['audio']['reply']['failed_stage'], 'speech')
        self.assertEqual(row['branches']['image']['reply'].get('error'), 'join_timeout')
        self.assertNotIn('fusion', [i[2] for i in self.c.intervals.get('cpu-c', [])])
        self.assertTrue(self.c.idle())

    def test_tampering_breaks_validation(self):
        row = self.c.trace('t-valid', MIXED, [0.0])['rows'][0]
        workers, created, corpus, run_id = self.c.validation()
        broken = json.loads(json.dumps(row))
        broken['branches']['image']['reply']['hops'][0]['output_sha256'] = '0' * 64
        self.assertEqual(analyze.validate_row(broken, workers, created, corpus, run_id), 'hash_discontinuity')
        broken = json.loads(json.dumps(row))
        broken['branches']['audio']['reply']['receipts'][1]['pod_uid'] = 'uid-other'
        self.assertEqual(analyze.validate_row(broken, workers, created, corpus, run_id), 'identity_mismatch')
        broken = json.loads(json.dumps(row))
        broken['decision']['plan']['vision'] = 'cpu-c'  # plan says server CPU, receipt is the NPU worker
        self.assertEqual(analyze.validate_row(broken, workers, created, corpus, run_id), 'identity_mismatch')
        cpu_as_npu = dict(workers, **{'npu-c': dict(workers['npu-c'], backend='cuda-torch')})
        self.assertIn(analyze.validate_row(row, cpu_as_npu, created, corpus, run_id), ('identity_mismatch', 'device_mismatch'))
        self.assertEqual(analyze.validate_row(row, workers, created, dict(corpus, **{str(row['question_id']): dict(image='x', audio='y')}), run_id),
                         'input_mismatch')

    def test_dynamic_and_static_policies_get_the_same_observation_stream(self):
        time.sleep(0.5)
        static = self.c.trace('t-static', None, [0.0, 0.1], policy=dict(name='dag_static'))
        dynamic = self.c.trace('t-dyn', None, [0.0, 0.1], policy=dict(name='dag_reopt'))
        for row in static['rows'] + dynamic['rows']:
            self.assertIsNotNone(row['observation']['age_ms'])
        self.assertIn('queues', dynamic['rows'][0]['observation'])
        self.assertGreater(dynamic['poll_after']['polls'], dynamic['poll_before']['polls'])
        self.assertEqual(static['quality_gate'], dynamic['quality_gate'])

    def test_profile_and_route_probe_jobs(self):
        result = self.c.gen.profile(dict(question_ids=['11', '12'], warmup=1, producers={t: 'cpu-c' if 'cpu-c' in self.c.candidates[t]
                                                                                       else self.c.candidates[t][0] for t in policies.TASKS}))
        self.assertEqual(set(result['compute_ms']), {f'{t}@{w}' for t in policies.TASKS for w in self.c.candidates[t]})
        self.assertTrue(all(r['status'] == 'ok' for r in result['rows']), [r for r in result['rows'] if r['status'] != 'ok'][:2])
        self.assertTrue(any(r['warmup'] for r in result['rows']))
        probes = self.c.gen.probes(dict(pairs=[['origin', 'cpu-c'], ['cpu-c', 'origin'], ['gpu-e', 'cpu-c'], ['cpu-e', 'cpu-e']],
                                        sizes=[1024, 65536], reps=2, timeout_s=5))
        self.assertTrue(all(v['complete'] for v in probes['links'].values()), probes)
        self.assertEqual(probes['links']['cpu-c->origin']['route'], 'gateway')
        self.assertEqual(probes['links']['cpu-e->cpu-e']['route'], 'direct')
        direct = self.c.gen.probes(dict(pairs=[['origin', 'cpu-c']], sizes=[1024, 4096], reps=1, timeout_s=5, force_direct=True))
        self.assertEqual(direct['links']['origin->cpu-c']['route'], 'direct_control')


class Analysis(unittest.TestCase):
    def test_goodput_counts_only_valid_in_deadline_answers_of_the_frozen_cohort(self):
        c = Cluster(LAYOUT)
        try:
            c.delays[('npu-c', 'vision')] = 0.5
            ledger = c.trace('t-goodput', MIXED, [0.0, 0.05, 0.1, 0.15, 1.5], deadline_s=0.45, cancel_s=3.0, max_inflight=3)
            statuses = [r['status'] for r in ledger['rows']]
            self.assertIn('admission_drop', statuses)
            gt_pairs = json.loads((ROOT/'artifacts/topology-profile-assets/20261019T091725Z/selected-data/pairs.json').read_text())['pairs']
            gt = {q: dict(gt_pairs[i], question_id=q) for i, q in enumerate(c.questions)}
            workers, created, corpus, run_id = c.validation()
            tampered = json.loads(json.dumps(ledger))
            first_ok = next(r for r in tampered['rows'] if r['status'] in ('ok', 'late_ok'))
            first_ok['branches']['image']['reply']['hops'][1]['output_sha256'] = 'f' * 64
            cohort = dict(arrivals=[dict(index=r['index'], offset_s=r['scheduled_offset_s'], question_id=r['question_id'])
                                    for r in ledger['rows']], deadline_s=0.45)
            s = analyze.trace_summary(tampered, cohort, 2.0, workers, created, corpus, gt, run_id)
            self.assertEqual(s['requests'], 5)
            self.assertEqual(sum(s['counts'].values()), 5)
            self.assertEqual(s['invalid_reasons'], {'hash_discontinuity': 1})
            self.assertAlmostEqual(s['goodput_rps'], s['counts'].get('ok', 0) / 2.0)
            self.assertEqual(s['offered_rps'], 2.5)
            self.assertLessEqual(s['quality']['all_offered_timely_zero_imputed'] or 0, s['quality']['returned'] or 0)
            for row in ledger['rows']:
                if row['status'] in ('ok', 'late_ok'):
                    for b in row['branches'].values():
                        for est in generator.hop_transfers(b, b['reply']['receipts'], b['reply']['hops']):
                            self.assertTrue(-50 < est['transfer_ms'] < 3000, est)  # sender rpc minus receiver work (loopback)
        finally:
            c.close()


class SingleDevice(unittest.TestCase):
    def test_self_forwarding_and_self_join_do_not_deadlock_and_lock_is_exclusive(self):
        c = Cluster({'cpu-e': ('edge', 'cpu-torch', ALL)}, max_active={'cpu-e': 32})
        try:
            ledger = c.trace('t-self', {t: 'cpu-e' for t in policies.TASKS}, [0.0, 0.01, 0.02], wait_s=20)
            self.assertEqual([r['status'] for r in ledger['rows']], ['ok'] * 3)
            spans = sorted(c.intervals['cpu-e'])
            self.assertTrue(all(a[1] <= b[0] + 1e-6 for a, b in zip(spans, spans[1:])), 'one-unit lock must serialize model calls')
            waits = [rc['queue_wait_ms'] for r in ledger['rows'] for b in r['branches'].values() for rc in b['reply']['receipts']]
            self.assertGreater(max(waits), 5)
            self.assertTrue(c.idle())
        finally:
            c.close()

    def test_worker_admission_cap_answers_503_and_is_recorded(self):
        layout = {'pre': ('edge', 'cpu-torch', ['image_preprocess', 'audio_preprocess']), 'cpu-c': ('cloud', 'cpu-torch', ALL)}
        c = Cluster(layout, max_active={'pre': 1})
        c.delays[('pre', 'image_preprocess')] = 0.3
        try:
            plan = dict(image_preprocess='pre', vision='cpu-c', audio_preprocess='pre', speech='cpu-c', fusion='cpu-c')
            ledger = c.trace('t-503', plan, [0.0, 0.01], deadline_s=2, cancel_s=2)
            self.assertIn('worker_rejected', [r['status'] for r in ledger['rows']])
            self.assertTrue(c.idle())
        finally:
            c.close()


class Gateway(unittest.TestCase):
    def test_peers_freeze_once_allow_list_reset_and_budget(self):
        relay = gateway.Relay(allowed_prefixes=('http://127.0.0.1',))
        self.assertEqual(relay.relay('a', 'run', b'{}', 'application/json')[0], 503)  # not frozen yet
        with self.assertRaises(ValueError):
            relay.freeze(dict(x=dict(url='http://8.8.8.8:1')))
        self.assertEqual(relay.freeze(dict(a=dict(url='http://127.0.0.1:9'))) [0], 200)
        self.assertEqual(relay.freeze(dict(b=dict(url='http://127.0.0.1:9')))[0], 409)
        self.assertEqual(relay.relay('b', 'run', b'{}', 'application/json')[0], 403)
        relay.active = 1
        self.assertEqual(relay.reset('x')[0], 409)
        relay.active = 0
        self.assertEqual(relay.reset('x')[0], 200)
        from http_runtime import encode_body
        raw, ctype = encode_body(dict(budget_s=0.7, data=b'abc'))
        self.assertAlmostEqual(gateway.budget_of(raw, ctype), 0.7)
        self.assertIsNone(gateway.budget_of(b'{"x":1}', 'application/json'))

    def test_upstream_timeout_follows_request_budget(self):
        c = Cluster({'cpu-c': ('cloud', 'cpu-torch', ALL)})
        c.delays[('cpu-c', 'image_preprocess')] = 1.5
        try:
            body = dict(mode='dag', run_id='qnb-test', request_id='r', stage='image_preprocess', input='raw', data=b'abc', forward=[], budget_s=0.4)
            started = time.monotonic()
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                request(c.gateway_url + '/to/cpu-c/run', body, timeout=10)
            self.assertEqual(ctx.exception.code, 504)
            self.assertLess(time.monotonic() - started, 1.4)
            self.assertEqual(c.relay.health()['counters']['cpu-c']['timeouts'], 1)
        finally:
            c.close()


class Routing(unittest.TestCase):
    def test_cross_zone_without_gateway_is_refused(self):
        g = generator.Generator()
        g.config = dict(workers={'c': dict(url='http://10.0.0.1:8080', zone='cloud')}, origin_zone='edge', gateway_url=None)
        with self.assertRaises(ValueError):
            g.route('origin', 'c')


if __name__ == '__main__':
    unittest.main()
