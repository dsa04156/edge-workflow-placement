"""Generator HTTP/ledger seam over real loopback HTTP: unchanged quality-network-baselines DagWorker + gateway and the
unstable-network generator. Model stages are deterministic FAKES (plumbing evidence only, never a performance claim).

Covers the observation contract end to end (a worker that stops answering keeps its last successful state, is reported
stale with its error, and observation policies record the fallback; a worker that never answered is never an empty
queue), the trace contract (no network trace or future condition can be sent to the generator), the delayed start used
for clock alignment, the max_inflight=8 admission cap, absolute cancel timeouts with inflight back to 0, and salvage of
a stopped trace (exactly one ledger row per scheduled arrival).
"""
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
# Unchanged reused modules are appended AFTER this directory, so its own generator/policies win the name lookup.
sys.path.append(str(ROOT/'experiments/quality-network-baselines'))
sys.path.append(str(ROOT/'experiments/quality-network-heterogeneous'))
sys.path.append(str(ROOT/'experiments/real-topology-live'))
import generator  # noqa: E402  (imported before dag_worker, which prepends the baselines directory to sys.path)
import policies  # noqa: E402
import dag_worker  # noqa: E402
import gateway  # noqa: E402
from http_runtime import request  # noqa: E402

DELAYS = dict(image_preprocess=0.01, audio_preprocess=0.01, vision=0.03, speech=0.03, fusion=0.01)


def sha(b):
    return hashlib.sha256(b).hexdigest()


class FakeStage:
    def __init__(self, stage, backend, cluster, key):
        self.stage, self.backend, self.cluster, self.key = stage, backend, cluster, key
        self.device = {'cpu-torch': 'cpu', 'cuda-torch': 'cuda:0', 'openvino-npu': 'NPU'}[backend]

    def describe(self):
        return dict(stage=self.stage, backend=self.backend, device=self.device, model='fake-'+self.stage, model_sha256='fake-model-'+self.stage)

    def _work(self, out):
        time.sleep(self.cluster.delays.get((self.key, self.stage), DELAYS[self.stage]))
        timing = dict(output_device=self.device)
        if self.backend == 'openvino-npu':
            timing['execution_devices'] = ['NPU']
        return out, timing

    def run(self, data):
        if self.stage == 'fusion':
            answer = dict(answers=['yes', 'no'], answer_ids=[1, 2], scores=[.6, .4], inputs=sha(data['vision'] + data['speech']))
            return self._work(json.dumps(answer, separators=(',', ':')).encode())
        return self._work(b'tensor:' + sha(data).encode())

    def run_tensor(self, data):
        return self._work((b'feature:' if self.stage == 'vision' else b'what color ') + sha(data)[:8].encode())


ALL = list(policies.TASKS)
LAYOUT = {'cpu-e': ('edge', 'cpu-torch', ALL), 'cpu-c': ('cloud', 'cpu-torch', ALL)}


class Cluster:
    def __init__(self, layout=LAYOUT, dead=(), poll_s=0.1):
        self.delays, self.servers, self.worker_servers = {}, [], {}
        self.workers, self.urls, self.zones = {}, {}, {}
        for key, (zone, backend, stages) in layout.items():
            self.zones[key] = zone
            if key in dead:  # nothing listens there: every poll fails from the start
                self.urls[key] = 'http://127.0.0.1:9'
                continue
            w = dag_worker.DagWorker(backend, stages, loader=lambda name, backend, key=key, **kw: FakeStage(name, backend, self, key))
            w.node, w.pod_uid = 'node-'+key, 'uid-'+key
            srv = dag_worker.serve(w, host='127.0.0.1', port=0)
            self.start(srv)
            self.worker_servers[key] = srv
            self.workers[key], self.urls[key] = w, f'http://127.0.0.1:{srv.server_address[1]}'
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
        self.candidates = {t: [k for k, (z, b, st) in layout.items() if t in st] for t in policies.TASKS}
        self.gen.configure(dict(run_id='un-test', workers={k: dict(url=self.urls[k], zone=self.zones[k], node='node-'+k, pod_uid='uid-'+k)
                                                           for k in layout},
                                gateway_url=self.gateway_url, origin_url=self.origin_url, origin_zone='edge', candidates=self.candidates,
                                poll_s=poll_s, poll_timeout_s=0.3))

    def start(self, srv):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers.append(srv)

    def kill(self, key):
        srv = self.worker_servers.pop(key)
        srv.shutdown()
        srv.server_close()
        self.servers.remove(srv)

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

    def body(self, label, offsets, policy, cancel_s=5.0, max_inflight=8, stale_after_s=0.5, start_delay_s=0.2):
        arrivals = [dict(index=i, offset_s=o, question_id=self.questions[i % 3]) for i, o in enumerate(offsets)]
        return dict(label=label, policy=policy, arrivals=arrivals, deadline_s=2.0, cancel_s=cancel_s, max_inflight=max_inflight,
                    profile=self.profile(), quality=dict(mode='diagnostic', qmin=0.4), observation_contract=dict(stale_after_s=stale_after_s),
                    start_delay_s=start_delay_s)

    def trace(self, label, offsets, policy, wait_s=30, **kw):
        request(self.origin_url + '/trace', self.body(label, offsets, policy, **kw), timeout=10)
        return self.wait(label, wait_s)

    def wait(self, label, wait_s=30):
        stop = time.monotonic() + wait_s
        while time.monotonic() < stop and self.gen.status()['traces'][label]['state'] == 'running':
            time.sleep(0.05)
        return self.gen.ledger(label)

    def inflight(self):
        return sum(w.run(dict(mode='queue'))[1]['inflight'] for w in self.workers.values())


EDGE_ONLY = dict(name='dag_static')


class ObservationContract(unittest.TestCase):
    def test_a_worker_that_stops_answering_keeps_its_last_state_and_triggers_a_recorded_fallback(self):
        c = Cluster()
        try:
            time.sleep(0.4)  # a few successful polls of both workers
            c.kill('cpu-c')
            time.sleep(1.0)  # > stale_after_s 0.5 without a success from cpu-c
            ledger = c.trace('stale', [0.0, 0.1], dict(name='dag_reopt'))
            row = ledger['rows'][0]
            self.assertEqual(row['decision']['fallback']['reason'], 'stale_observation')
            self.assertEqual(row['decision']['fallback']['stale'], ['cpu-c'])
            seen = row['observation']['workers']['cpu-c']
            self.assertEqual(seen['waiting'], 0)            # the last successful reading, not a fabricated empty queue
            self.assertGreater(seen['age_ms'], 500)
            self.assertGreater(seen['consecutive_errors'], 0)
            self.assertIsNotNone(seen['last_error'])
        finally:
            c.close()

    def test_a_worker_that_never_answered_is_not_an_empty_queue(self):
        c = Cluster(dead=('cpu-c',))
        try:
            time.sleep(0.4)
            ledger = c.trace('never', [0.0], dict(name='greedy_comm'))
            row = ledger['rows'][0]
            self.assertEqual(row['decision']['fallback']['reason'], 'no_successful_poll')
            self.assertIsNone(row['observation']['workers']['cpu-c']['waiting'])
        finally:
            c.close()

    def test_static_policies_record_the_same_observation_without_using_it(self):
        c = Cluster()
        try:
            time.sleep(0.3)
            row = c.trace('static', [0.0], EDGE_ONLY)['rows'][0]
            self.assertIsNone(row['decision']['fallback'])
            self.assertIn('cpu-c', row['observation']['workers'])
            self.assertIn('summary', row['observation'])
        finally:
            c.close()


class TraceContract(unittest.TestCase):
    def setUp(self):
        self.c = Cluster()

    def tearDown(self):
        self.c.close()

    def test_a_network_trace_or_future_condition_cannot_be_sent_to_the_generator(self):
        for extra in (dict(network=dict(trace='burst')), dict(phases=[]), dict(future_mbps=2)):
            body = dict(self.c.body('leak', [0.0], EDGE_ONLY), **extra)
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                request(self.c.origin_url + '/trace', body, timeout=10)
            self.assertEqual(ctx.exception.code, 400)

    def test_the_trace_starts_after_the_requested_delay_on_the_generator_clock(self):
        ledger = self.c.trace('delay', [0.0], EDGE_ONLY, start_delay_s=0.5)
        self.assertAlmostEqual(ledger['t0_local'] - ledger['received_local'], 0.5, places=6)
        self.assertEqual(ledger['start_delay_s'], 0.5)

    def test_admission_cap_of_eight_drops_and_records_the_excess(self):
        self.c.delays = {(w, s): 0.3 for w in ('cpu-e', 'cpu-c') for s in policies.TASKS}
        ledger = self.c.trace('cap', [0.0] * 12, EDGE_ONLY, max_inflight=8)
        statuses = [r['status'] for r in ledger['rows']]
        self.assertEqual(statuses.count('admission_drop'), 4)
        self.assertEqual(ledger['max_seen_inflight'], 8)
        self.assertTrue(all(r['inflight_at_arrival'] == 8 for r in ledger['rows'] if r['status'] == 'admission_drop'))

    def test_cancel_is_an_absolute_timeout_and_no_work_leaks(self):
        self.c.delays = {(w, 'vision'): 1.5 for w in ('cpu-e', 'cpu-c')}
        ledger = self.c.trace('cancel', [0.0], EDGE_ONLY, cancel_s=0.8)
        self.assertEqual(ledger['rows'][0]['status'], 'timeout')
        self.assertLess(ledger['rows'][0]['latency_ms'], 0.8 * 1000 + generator.FUTURE_SLACK_S * 1000)
        stop = time.monotonic() + 5
        while self.c.inflight() and time.monotonic() < stop:
            time.sleep(0.05)
        self.assertEqual(self.c.inflight(), 0)

    def test_a_stopped_trace_is_salvaged_with_one_row_per_scheduled_arrival(self):
        request(self.c.origin_url + '/trace', self.c.body('stop', [0.0, 0.1, 5.0, 6.0], EDGE_ONLY), timeout=10)
        time.sleep(0.6)
        request(self.c.origin_url + '/stop', {}, timeout=10)
        ledger = self.c.wait('stop')
        self.assertEqual([r['index'] for r in ledger['rows']], [0, 1, 2, 3])
        self.assertEqual([r['status'] for r in ledger['rows']][2:], ['not_sent', 'not_sent'])

    def test_a_running_trace_is_readable_only_as_a_marked_partial_ledger(self):
        request(self.c.origin_url + '/trace', self.c.body('partial', [0.0, 5.0, 6.0], EDGE_ONLY), timeout=10)
        time.sleep(0.6)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            opener.open(self.c.origin_url + '/ledger?label=partial', timeout=10)
        self.assertEqual(ctx.exception.code, 400)
        with opener.open(self.c.origin_url + '/ledger?label=partial&partial=1', timeout=10) as response:
            partial = json.load(response)
        self.assertTrue(partial['partial'])
        self.assertEqual(partial['state'], 'running')
        self.assertEqual([r['index'] for r in partial['rows']], [0, 1, 2])
        self.assertIsNone(partial['rows'][2]['status'])
        request(self.c.origin_url + '/stop', {}, timeout=10)
        self.assertFalse(self.c.wait('partial')['partial'])


if __name__ == '__main__':
    unittest.main()
