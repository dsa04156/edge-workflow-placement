"""Policy decision seam (policies.py): observation contract and the network-cost ablation.

Hand-computed worked example (synthetic profile; plumbing of the cost model only, never a performance claim):
two workers e (edge, slow compute 100 ms) and c (cloud, fast compute 10 ms); origin<->c and e<->c cost 1000 ms each
way, origin<->e and same-worker hops cost 0. Tasks on one worker serialize.
  all-e plan: 5 x 100 ms serialized = 500 ms; any plan touching c pays >= 1000 ms each way.
  all-c plan with network: 1000 + 5 x 10 serialized + 1000 = 2050 ms; without transfer cost: 50 ms (the minimum).
So dag_reopt (network-aware) must pick all-e, and dag_reopt_no_network must pick all-c while recording 2050 ms as its
full predicted cost and 50 ms as its selection cost.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import policies  # noqa: E402

TASKS = policies.TASKS
ALL_E = {t: 'e' for t in TASKS}
ALL_C = {t: 'c' for t in TASKS}
CANDIDATES = {t: ['e', 'c'] for t in TASKS}
OBSERVATION_POLICIES = ('greedy_comm', 'dag_reopt', 'dag_reopt_hysteresis', 'dag_reopt_no_network')


def profile():
    compute = {(t, 'e'): 100.0 for t in TASKS}
    compute.update({(t, 'c'): 10.0 for t in TASKS})
    far, near = dict(latency_ms=1000.0, ms_per_byte=0.0), dict(latency_ms=0.0, ms_per_byte=0.0)
    links = {('origin', 'e'): near, ('e', 'origin'): near, ('e', 'e'): near, ('c', 'c'): near,
             ('origin', 'c'): far, ('c', 'origin'): far, ('e', 'c'): far, ('c', 'e'): far}
    sizes = dict(image=1, image_tensor=1, feature=1, audio=1, input_values=1, text=1, answer=1)
    return policies.Profile(compute, links, sizes, source='synthetic-test')


def gate():
    return policies.QualityGate('diagnostic', 0.40)


def build(name):
    spec = dict(name=name, **(dict(rel=0.15, abs_ms=50.0, dwell_s=5.0) if name == 'dag_reopt_hysteresis' else {}))
    return policies.build(spec, CANDIDATES, gate(), profile(), stale_after_s=3.0)


def fresh(at=100.0, queues=None):
    obs = policies.Observation()
    for w in ('e', 'c'):
        obs.record_poll(w, (queues or {}).get(w, dict(waiting=0, active=0, joins_open=0)), None, at)
    return obs


class FailedPolls(unittest.TestCase):
    def test_a_failed_poll_keeps_the_last_successful_queue_and_its_time(self):
        obs = policies.Observation()
        obs.record_poll('c', dict(waiting=3, active=1, joins_open=0), None, 10.0)
        obs.record_poll('c', None, 'ConnectionRefusedError()', 11.0)
        state = obs.to_json(11.5)['workers']['c']
        self.assertEqual((state['waiting'], state['active']), (3, 1))
        self.assertEqual(state['age_ms'], 1500.0)
        self.assertEqual(state['consecutive_errors'], 1)
        self.assertIn('ConnectionRefused', state['last_error'])

    def test_a_failed_poll_is_never_read_as_an_empty_queue(self):
        obs = policies.Observation()
        obs.record_poll('c', dict(waiting=2, active=0, joins_open=0), None, 10.0)
        obs.record_poll('c', None, 'timed out', 10.5)
        obs.service_ms['c'] = 40.0
        self.assertEqual(obs.queue_ms('c', profile()), 80.0)

    def test_a_worker_that_never_answered_has_no_queue_estimate(self):
        obs = policies.Observation()
        obs.record_poll('c', None, 'timed out', 10.0)
        with self.assertRaises(policies.PolicyError):
            obs.queue_ms('c', profile())

    def test_a_success_after_errors_resets_the_error_streak(self):
        obs = policies.Observation()
        obs.record_poll('c', None, 'timed out', 10.0)
        obs.record_poll('c', dict(waiting=0, active=0, joins_open=0), None, 11.0)
        self.assertEqual(obs.to_json(11.0)['workers']['c']['consecutive_errors'], 0)


class StaleFallback(unittest.TestCase):
    def stale(self):
        obs = fresh(at=100.0)
        obs.record_poll('c', None, 'timed out', 103.5)  # c last succeeded at 100.0; at 104.0 it is 4 s old > 3 s
        obs.record_poll('e', dict(waiting=0, active=0, joins_open=0), None, 103.9)
        return obs

    def test_every_observation_policy_records_a_stale_fallback(self):
        for name in OBSERVATION_POLICIES:
            d = build(name).decide(self.stale(), now=104.0)
            self.assertEqual(d['fallback']['reason'], 'stale_observation', name)
            self.assertEqual(d['fallback']['stale'], ['c'], name)

    def test_dag_reopt_falls_back_to_the_dag_static_plan(self):
        static = build('dag_static').decide(None, now=104.0)['plan']
        self.assertEqual(build('dag_reopt').decide(self.stale(), now=104.0)['plan'], static)

    def test_no_successful_poll_at_all_falls_back_too(self):
        obs = policies.Observation()
        obs.record_poll('c', None, 'refused', 1.0)
        d = build('dag_reopt').decide(obs, now=1.5)
        self.assertEqual(d['fallback']['reason'], 'no_successful_poll')
        self.assertEqual(sorted(d['fallback']['never']), ['c', 'e'])

    def test_fresh_observation_is_used_without_fallback(self):
        d = build('dag_reopt').decide(fresh(at=100.0), now=101.0)
        self.assertIsNone(d['fallback'])

    def test_static_policies_never_fall_back(self):
        for name in ('task_fastest', 'dag_static'):
            self.assertIsNone(build(name).decide(self.stale(), now=104.0)['fallback'], name)


class NetworkCostAblation(unittest.TestCase):
    def test_network_aware_reopt_keeps_the_dag_on_the_edge(self):
        self.assertEqual(build('dag_reopt').decide(fresh(), now=100.5)['plan'], ALL_E)

    def test_no_network_reopt_ignores_transfer_only_and_picks_the_fast_cloud(self):
        d = build('dag_reopt_no_network').decide(fresh(), now=100.5)
        self.assertEqual(d['plan'], ALL_C)
        self.assertEqual(d['selection_cost_ms'], 50.0)
        self.assertEqual(d['predicted_ms'], 2050.0)  # the recorded prediction keeps the real transfer cost

    def test_both_reopt_variants_enumerate_the_same_feasible_plans(self):
        a, b = build('dag_reopt').decide(fresh(), now=100.5), build('dag_reopt_no_network').decide(fresh(), now=100.5)
        self.assertEqual(a['enumerated'], 32)
        self.assertEqual(b['enumerated'], 32)

    def test_no_network_still_sees_the_queue(self):
        busy = fresh(queues=dict(c=dict(waiting=100, active=1, joins_open=0)))
        busy.service_ms['c'] = 10.0  # 101 queued x 10 ms on c makes c worse than all-e even without transfer cost
        self.assertEqual(build('dag_reopt_no_network').decide(busy, now=100.5)['plan'], ALL_E)

    def test_the_six_policies_are_buildable(self):
        for name in ('task_fastest', 'greedy_comm', 'dag_static', 'dag_reopt', 'dag_reopt_hysteresis', 'dag_reopt_no_network'):
            self.assertEqual(build(name).decide(fresh(), now=100.5)['policy'], name)


if __name__ == '__main__':
    unittest.main()
