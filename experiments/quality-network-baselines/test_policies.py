"""Policy and cost-model contracts on SYNTHETIC profiles (tests only; live decisions use measured profiles).

Covers: quality eligibility independent of parity, shared-device serialization, decisions under network reversal,
task_fastest's transfer blindness, hysteresis hold/switch, static policies ignoring observation, prior adapters refusing.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import policies  # noqa: E402

CANDS = dict(image_preprocess=['cpu-e', 'cpu-c'], vision=['gpu-e', 'npu-c', 'cpu-c'], audio_preprocess=['cpu-e', 'cpu-c'],
             speech=['gpu-e', 'cpu-c'], fusion=['gpu-e', 'cpu-c'])
ZONE = {'origin': 'edge', 'cpu-e': 'edge', 'gpu-e': 'edge', 'cpu-c': 'cloud', 'npu-c': 'cloud'}
COMPUTE = {('image_preprocess', 'cpu-e'): 60, ('image_preprocess', 'cpu-c'): 20, ('vision', 'gpu-e'): 40, ('vision', 'npu-c'): 10,
           ('vision', 'cpu-c'): 120, ('audio_preprocess', 'cpu-e'): 15, ('audio_preprocess', 'cpu-c'): 5, ('speech', 'gpu-e'): 50,
           ('speech', 'cpu-c'): 45, ('fusion', 'gpu-e'): 20, ('fusion', 'cpu-c'): 10}
SIZES = dict(image=820_000, image_tensor=2_408_576, feature=2_176, audio=107_000, input_values=107_000, text=60, answer=200)


def links(cross_mbps, cross_latency_ms=20.0, local_mbps=900.0):
    out = {}
    for a in ZONE:
        for b in ZONE:
            same = ZONE[a] == ZONE[b]
            mbps = local_mbps if same else cross_mbps
            out[(a, b)] = dict(latency_ms=0.5 if a == b else (1.0 if same else cross_latency_ms), ms_per_byte=8 / (mbps * 1000))
    return out


def profile(cross_mbps, **kw):
    return policies.Profile(COMPUTE, links(cross_mbps, **kw), SIZES, source='synthetic-test')


DIAG = policies.QualityGate('diagnostic', 0.40)


class QualityGate(unittest.TestCase):
    def test_strict_needs_pipeline_quality_evidence_not_parity(self):
        plan = dict(image_preprocess='cpu-c', vision='npu-c', audio_preprocess='cpu-e', speech='gpu-e', fusion='cpu-c')
        key = '|'.join(policies.plan_key(plan))
        # NPU parity failed (FP16) but the measured pipeline quality passes -> eligible
        gate = policies.QualityGate('strict', 0.40, {key: dict(source='measured', quality=0.45, parity='failed')})
        self.assertTrue(gate.eligible(plan))
        # parity passed everywhere but no pipeline quality evidence -> not eligible
        other = dict(plan, vision='cpu-c')
        self.assertFalse(policies.QualityGate('strict', 0.40, {'|'.join(policies.plan_key(other)): dict(parity='passed')}).eligible(other))
        # measured below qmin -> not eligible; synthetic/unknown source -> not eligible
        self.assertFalse(policies.QualityGate('strict', 0.40, {key: dict(source='measured', quality=0.355)}).eligible(plan))
        self.assertFalse(policies.QualityGate('strict', 0.40, {key: dict(source='guess', quality=0.9)}).eligible(plan))

    def test_strict_without_eligible_plans_refuses_instead_of_falling_back(self):
        with self.assertRaises(policies.PolicyError):
            policies.DagReopt(CANDS, policies.QualityGate('strict', 0.40, {}), profile(100))

    def test_restricted_eligibility_limits_every_policy(self):
        only = dict(image_preprocess='cpu-e', vision='gpu-e', audio_preprocess='cpu-e', speech='gpu-e', fusion='gpu-e')
        gate = policies.QualityGate('strict', 0.40, {'|'.join(policies.plan_key(only)): dict(source='identical_outputs', quality=0.5)})
        for cls in (policies.TaskFastest, policies.GreedyComm, policies.DagStatic, policies.DagReopt):
            self.assertEqual(cls(CANDS, gate, profile(100)).decide(policies.Observation())['plan'], only, cls.name)

    def test_diagnostic_mode_is_labelled(self):
        self.assertIn('diagnostic', DIAG.label())


class CostModel(unittest.TestCase):
    def test_tasks_on_one_worker_serialize(self):
        prof = profile(1000)
        split = dict(image_preprocess='cpu-e', vision='gpu-e', audio_preprocess='cpu-e', speech='cpu-c', fusion='gpu-e')
        shared = dict(split, speech='gpu-e')
        t = policies.predict(shared, prof)['tasks']
        (vs, ve), (ss, se) = t['vision'], t['speech']
        self.assertTrue(ve <= ss or se <= vs, 'vision and speech on one device must not overlap')

    def test_parallel_branches_overlap_on_different_workers(self):
        plan = dict(image_preprocess='cpu-e', vision='gpu-e', audio_preprocess='cpu-c', speech='cpu-c', fusion='gpu-e')
        t = policies.predict(plan, profile(1000))['tasks']
        self.assertLess(t['speech'][0], t['vision'][1])

    def test_missing_measurement_is_an_error_not_zero(self):
        prof = policies.Profile({}, links(100), SIZES, 'synthetic-test')
        with self.assertRaises(policies.PolicyError):
            policies.predict({t: 'cpu-c' for t in policies.TASKS}, prof)

    def test_profile_json_roundtrip(self):
        prof = profile(10)
        again = policies.Profile.from_json(prof.to_json())
        plan = {t: CANDS[t][0] for t in policies.TASKS}
        self.assertAlmostEqual(policies.predict(plan, prof)['makespan_ms'], policies.predict(plan, again)['makespan_ms'])

    def test_link_fit(self):
        fit = policies.link_fit((4096, 2.0 + 4096 * 8 / 10_000), (1_048_576, 2.0 + 1_048_576 * 8 / 10_000))
        self.assertAlmostEqual(fit['latency_ms'], 2.0, places=6)
        self.assertAlmostEqual(fit['effective_mbps'], 10.0, places=6)


class NetworkReversal(unittest.TestCase):
    """Good cross-zone network -> server NPU/CPU for vision; limited network -> stay at the edge. task_fastest ignores it."""

    def decide(self, cls, mbps):
        return cls(CANDS, DIAG, profile(mbps)).decide(policies.Observation())['plan']

    def test_dag_and_greedy_follow_the_network_task_fastest_does_not(self):
        good = {c.name: self.decide(c, 1000) for c in (policies.TaskFastest, policies.GreedyComm, policies.DagStatic)}
        bad = {c.name: self.decide(c, 2) for c in (policies.TaskFastest, policies.GreedyComm, policies.DagStatic)}
        self.assertEqual(good['task_fastest'], bad['task_fastest'])
        self.assertEqual(good['task_fastest']['vision'], 'npu-c')
        self.assertEqual(good['dag_static']['vision'], 'npu-c')
        self.assertEqual(bad['dag_static']['vision'], 'gpu-e')
        self.assertEqual(ZONE[bad['dag_static']['image_preprocess']], 'edge')
        self.assertEqual(ZONE[bad['greedy_comm']['image_preprocess']], 'edge')

    def test_dag_static_is_never_worse_than_greedy_under_its_own_model(self):
        for mbps in (2, 10, 100, 1000):
            prof = profile(mbps)
            dag = policies.DagStatic(CANDS, DIAG, prof).decide()['predicted_ms']
            greedy = policies.GreedyComm(CANDS, DIAG, prof).decide(policies.Observation())['predicted_ms']
            self.assertLessEqual(dag, greedy + 1e-9, mbps)

    def test_greedy_has_no_lookahead_counterexample(self):
        """Greedy picks the cheaper first step even when it forces a costly transfer later (documented limitation)."""
        compute = {**COMPUTE, ('image_preprocess', 'cpu-c'): 1, ('image_preprocess', 'cpu-e'): 5}
        prof = policies.Profile(compute, links(10), SIZES, 'synthetic-test')
        cands = dict(CANDS, vision=['gpu-e'])
        greedy = policies.GreedyComm(cands, DIAG, prof).decide(policies.Observation())
        dag = policies.DagStatic(cands, DIAG, prof).decide()
        self.assertGreaterEqual(greedy['predicted_ms'], dag['predicted_ms'])


class Observation(unittest.TestCase):
    def test_reopt_moves_away_from_a_queued_worker_static_does_not(self):
        prof = profile(1000)
        obs = policies.Observation()
        static = policies.DagStatic(CANDS, DIAG, prof)
        reopt = policies.DagReopt(CANDS, DIAG, prof)
        first = reopt.decide(obs)['plan']
        obs.set_queues({first['vision']: dict(waiting=40, active=1)}, at=0.0)
        self.assertNotEqual(reopt.decide(obs)['plan']['vision'], first['vision'])
        self.assertEqual(static.decide(obs)['plan'], static.decide(policies.Observation())['plan'])

    def test_hysteresis_holds_small_gains_and_respects_dwell(self):
        prof = profile(1000)
        h = policies.DagReoptHysteresis(CANDS, DIAG, prof, rel=0.15, abs_ms=50, dwell_s=5)
        obs = policies.Observation()
        d0 = h.decide(obs, now=0.0)
        self.assertEqual(d0['hold_reason'], 'initial')
        obs.set_queues({d0['plan']['vision']: dict(waiting=0, active=1)}, at=0.0)  # small queue: gain below threshold
        d1 = h.decide(obs, now=10.0)
        self.assertFalse(d1['switched'])
        obs.set_queues({d0['plan']['vision']: dict(waiting=60, active=1)}, at=1.0)
        self.assertEqual(h.decide(obs, now=1.0)['hold_reason'], 'dwell')
        d3 = h.decide(obs, now=20.0)
        self.assertTrue(d3['switched'])
        self.assertEqual(h.switches, 1)

    def test_receipt_and_transfer_estimates_feed_observation_policies_only(self):
        prof = profile(1000)
        obs = policies.Observation()
        greedy = policies.GreedyComm(CANDS, DIAG, prof)
        before = greedy.decide(obs)['plan']
        for _ in range(10):
            obs.record_receipt('vision', before['vision'], 5000.0)
        after = greedy.decide(obs)['plan']
        self.assertNotEqual(after['vision'], before['vision'])
        fastest = policies.TaskFastest(CANDS, DIAG, prof)
        self.assertEqual(fastest.decide(obs)['plan'], fastest.decide(policies.Observation())['plan'])


class Registry(unittest.TestCase):
    def test_prior_adapters_are_not_implemented_and_not_aliased(self):
        for name in ('jellybean', 'octopinf'):
            with self.assertRaises(NotImplementedError):
                policies.build(dict(name=name), CANDS, DIAG, profile(100))
            self.assertNotIn(name, policies.POLICIES)

    def test_fixed_plan_must_be_a_candidate(self):
        with self.assertRaises(policies.PolicyError):
            policies.build(dict(name='fixed', plan={t: 'npu-c' for t in policies.TASKS}), CANDS, DIAG, profile(100))

    def test_decision_records_cost_and_plan(self):
        d = policies.build(dict(name='dag_reopt'), CANDS, DIAG, profile(100)).decide(policies.Observation())
        self.assertEqual(set(d['plan']), set(policies.TASKS))
        self.assertGreaterEqual(d['decision_ms'], 0)
        self.assertEqual(d['enumerated'], 2 * 3 * 2 * 2 * 2)


if __name__ == '__main__':
    unittest.main()
