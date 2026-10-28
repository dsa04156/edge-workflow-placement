"""dag_deadline_guard through the public build()/decide() seam (deadline_policies.py).

Hand-computed worked example (synthetic profile; plumbing of the cost model only, never a performance claim).
Workers: e (edge CPU), g (edge GPU), s (server CPU), n (server NPU, vision only). Candidates:
  image/audio_preprocess {e, s}, vision {e, g, n}, speech {e, g, s}, fusion {e, g, s}  -> 108 plans.
Compute ms: e 20 for every task; g 10 (vision/speech/fusion); s 10 (preprocess, fusion), 50 (speech); n 5 (vision).
Links (latency only, 0 ms/byte): origin<->e 0, same worker 0, s<->n 0, e<->g and origin<->g 100, every other pair 200.
  frozen profile, no queue: all-e = 5 x 20 serialized = 100 ms (the anchor, unique best).
  e queue 500 ms (25 x 20): anchor 600 ms; best = s/n/s/s/s server plan: 200 + 10 + 10 + 50 + 10 + 200 = 480 ms.
  e queue 2000 ms (100 x 20): anchor 2100 ms; the same server plan 480 ms.
  e queue 2000 ms and n queue 1000 ms (200 x 5): best = s pre, vision g, speech g, fusion g:
    image s 200-210, audio s 210-220, vision g 410-420, speech g 420-430, fusion g 430-440, + 100 = 540 ms.
"""
from pathlib import Path
import math
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deadline_policies as dp  # noqa: E402
import frozen_unstable_policies as base  # noqa: E402

TASKS = base.TASKS
ALL_E = {t: 'e' for t in TASKS}
SERVER_NPU = dict(image_preprocess='s', vision='n', audio_preprocess='s', speech='s', fusion='s')
SERVER_PRE_GPU = dict(image_preprocess='s', vision='g', audio_preprocess='s', speech='g', fusion='g')
CANDIDATES = dict(image_preprocess=['e', 's'], audio_preprocess=['e', 's'], vision=['e', 'g', 'n'], speech=['e', 'g', 's'],
                  fusion=['e', 'g', 's'])
WORKERS = ('e', 'g', 's', 'n')
IDLE = dict(waiting=0, active=0, joins_open=0)


def profile(names=None):
    r = (names or {}).get
    name = lambda w: r(w, w)
    compute = {(t, 'e'): 20.0 for t in TASKS}
    compute.update({(t, 'g'): 10.0 for t in ('vision', 'speech', 'fusion')})
    compute.update({('image_preprocess', 's'): 10.0, ('audio_preprocess', 's'): 10.0, ('speech', 's'): 50.0, ('fusion', 's'): 10.0,
                    ('vision', 'n'): 5.0})
    near = {frozenset(p): ms for p, ms in ((('origin', 'e'), 0.0), (('s', 'n'), 0.0), (('e', 'g'), 100.0), (('origin', 'g'), 100.0))}
    locs = ('origin',) + WORKERS
    links = {(name(a), name(b)): dict(latency_ms=0.0 if a == b else near.get(frozenset((a, b)), 200.0), ms_per_byte=0.0)
             for a in locs for b in locs}
    sizes = dict(image=1, image_tensor=1, feature=1, audio=1, input_values=1, text=1, answer=1)
    return base.Profile({(t, name(w)): v for (t, w), v in compute.items()}, links, sizes, source='synthetic-test')


def candidates(names=None):
    r = (names or {}).get
    return {t: [r(w, w) for w in ws] for t, ws in CANDIDATES.items()}


def guard(deadline_ms=2000.0, anchor_fraction=0.75, names=None):
    spec = dict(name='dag_deadline_guard', deadline_ms=deadline_ms, anchor_fraction=anchor_fraction)
    return dp.build(spec, candidates(names), dp.ExecutionOnly(), profile(names), stale_after_s=3.0)


def observed(queues=None, service=None, at=100.0, names=None):
    """Every worker answered at `at`; queues {worker: depth}; service {worker: observed mean service ms}."""
    r = (names or {}).get
    obs = base.Observation()
    for w in WORKERS:
        depth = (queues or {}).get(w, 0)
        obs.record_poll(r(w, w), dict(IDLE, waiting=max(0, depth - 1), active=min(1, depth)), None, at)
    obs.service_ms.update({r(w, w): v for w, v in (service or {}).items()})
    return obs


E500 = dict(queues=dict(e=25), service=dict(e=20.0))
E2000 = dict(queues=dict(e=100), service=dict(e=20.0))


class AnchorHold(unittest.TestCase):
    def test_the_anchor_is_held_within_budget_although_reopt_would_move_to_a_faster_plan(self):
        d = guard().decide(observed(**E500), now=100.5)
        self.assertEqual(d['plan'], ALL_E)
        self.assertEqual(d['choice_reason'], 'anchor_within_budget')
        self.assertEqual((d['anchor_predicted_ms'], d['threshold_ms']), (600.0, 1500.0))

    def test_existing_reopt_on_the_same_observation_does_move(self):
        d = dp.build(dict(name='dag_reopt'), candidates(), dp.ExecutionOnly(), profile()).decide(observed(**E500), now=100.5)
        self.assertEqual(d['plan'], SERVER_NPU)


class OverBudget(unittest.TestCase):
    def test_a_high_observed_anchor_queue_moves_to_the_server_cpu_npu_plan(self):
        d = guard().decide(observed(**E2000), now=100.5)
        self.assertEqual(d['plan'], SERVER_NPU)
        self.assertEqual(d['choice_reason'], 'over_budget_reoptimized')
        self.assertEqual((d['anchor_predicted_ms'], d['selected_predicted_ms'], d['predicted_ms']), (2100.0, 480.0, 480.0))

    def test_a_busy_npu_too_moves_vision_and_speech_to_the_gpu(self):
        d = guard().decide(observed(queues=dict(e=100, n=200), service=dict(e=20.0, n=5.0)), now=100.5)
        self.assertEqual(d['plan'], SERVER_PRE_GPU)
        self.assertEqual(d['selected_predicted_ms'], 540.0)

    def test_relief_returns_to_the_anchor_on_the_next_decision(self):
        policy = guard()
        moved = policy.decide(observed(**E2000), now=100.5)
        back = policy.decide(observed(at=101.0), now=101.2)
        self.assertEqual((moved['plan'], back['plan']), (SERVER_NPU, ALL_E))
        self.assertEqual(back['choice_reason'], 'anchor_within_budget')

    def test_two_deadlines_give_two_decisions_on_one_observation(self):
        obs = observed(**E500)
        loose, tight = guard(deadline_ms=2000.0).decide(obs, now=100.5), guard(deadline_ms=600.0).decide(obs, now=100.5)
        self.assertEqual((loose['plan'], tight['plan']), (ALL_E, SERVER_NPU))  # thresholds 1500 vs 450 against anchor 600


class Threshold(unittest.TestCase):
    def test_an_anchor_prediction_equal_to_the_threshold_keeps_the_anchor(self):
        d = guard(deadline_ms=200.0, anchor_fraction=0.5).decide(observed(), now=100.5)  # anchor 100 == 200 x 0.5
        self.assertEqual(d['choice_reason'], 'anchor_within_budget')

    def test_just_above_the_threshold_reoptimizes_and_may_still_pick_the_anchor(self):
        d = guard(deadline_ms=199.99, anchor_fraction=0.5).decide(observed(), now=100.5)
        self.assertEqual((d['plan'], d['choice_reason']), (ALL_E, 'over_budget_anchor_still_best'))


class Fallback(unittest.TestCase):
    def test_a_stale_observation_falls_back_to_the_anchor_without_a_freshness_guarantee(self):
        obs = observed(**E2000, at=100.0)
        obs.record_poll('n', None, 'timed out', 103.5)
        for w in ('e', 'g', 's'):
            obs.record_poll(w, dict(IDLE, waiting=99, active=1) if w == 'e' else IDLE, None, 103.9)
        d = guard().decide(obs, now=104.0)  # n last answered 4 s ago > 3 s
        self.assertEqual((d['plan'], d['choice_reason']), (ALL_E, 'observation_fallback'))
        self.assertEqual((d['fallback']['reason'], d['fallback']['stale']), ('stale_observation', ['n']))
        self.assertIs(d['fallback']['freshness_guarantee'], False)

    def test_a_never_observed_worker_falls_back_to_the_anchor(self):
        obs = base.Observation()
        obs.record_poll('e', IDLE, None, 1.0)
        d = guard().decide(obs, now=1.5)
        self.assertEqual((d['plan'], d['fallback']['reason'], d['fallback']['never']), (ALL_E, 'no_successful_poll', ['g', 'n', 's']))

    def test_no_observation_object_falls_back_to_the_anchor(self):
        self.assertEqual(guard().decide(None, now=1.0)['choice_reason'], 'observation_fallback')


class Config(unittest.TestCase):
    def test_nonfinite_or_out_of_range_values_are_refused(self):
        bad = [dict(deadline_ms=v) for v in (math.nan, math.inf, 0.0, -1.0, '2000', True, None)] + \
              [dict(anchor_fraction=v) for v in (math.nan, 0.0, -0.1, 1.01, math.inf, '0.75')]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                guard(**{**dict(deadline_ms=2000.0, anchor_fraction=0.75), **value})

    def test_an_invalid_stale_after_s_is_refused_instead_of_disabling_the_fallback(self):
        spec = dict(name='dag_deadline_guard', deadline_ms=2000.0, anchor_fraction=0.75)
        for value in (math.nan, math.inf, -1.0, 0.0, True, '3'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                dp.build(spec, candidates(), dp.ExecutionOnly(), profile(), stale_after_s=value)

    def test_a_valid_integer_stale_after_s_still_triggers_the_fallback(self):
        obs = observed(**E2000, at=100.0)
        d = dp.build(dict(name='dag_deadline_guard'), candidates(), dp.ExecutionOnly(), profile(), stale_after_s=3).decide(obs, now=1000.0)
        self.assertEqual((d['plan'], d['choice_reason'], d['fallback']['reason']), (ALL_E, 'observation_fallback', 'stale_observation'))

    def test_baseline_builds_keep_the_original_stale_after_s_handling(self):
        d = dp.build(dict(name='dag_reopt'), candidates(), dp.ExecutionOnly(), profile(), stale_after_s=math.inf).decide(observed(**E2000), now=1000.0)
        self.assertIsNone(d['fallback'])  # unchanged base: no validation added outside the new guard

    def test_an_unknown_spec_key_is_refused(self):
        with self.assertRaises(ValueError):
            dp.build(dict(name='dag_deadline_guard', deadline_s=2.0), candidates(), dp.ExecutionOnly(), profile())

    def test_the_exact_default_spec(self):
        self.assertEqual(dp.policy_spec(), dict(name='dag_deadline_guard', deadline_ms=2000.0, anchor_fraction=0.75))


class NoGeography(unittest.TestCase):
    NAMES = dict(e='zz-server-a100', g='aa-cloud-npu', s='mm-edge-pi', n='edge-npu-0')  # misleading names on purpose

    def test_renamed_workers_give_the_renamed_decisions(self):
        n = self.NAMES
        rename = lambda plan: {t: n[w] for t, w in plan.items()}
        cases = [(observed(names=n, **E500), ALL_E), (observed(names=n, **E2000), SERVER_NPU),
                 (observed(queues=dict(e=100, n=200), service=dict(e=20.0, n=5.0), names=n), SERVER_PRE_GPU)]
        for obs, expected in cases:
            self.assertEqual(guard(names=n).decide(obs, now=100.5)['plan'], rename(expected))


class Baselines(unittest.TestCase):
    SPECS = (dict(name='task_fastest'), dict(name='greedy_comm'), dict(name='dag_static'), dict(name='dag_reopt'),
             dict(name='dag_reopt_hysteresis', rel=0.15, abs_ms=50.0, dwell_s=5.0), dict(name='dag_reopt_no_network'),
             dict(name='fixed', plan=SERVER_NPU))

    def test_baseline_specs_decide_exactly_as_the_frozen_base(self):
        strip = lambda d: {k: v for k, v in d.items() if k != 'decision_ms'}
        for spec in self.SPECS:
            ours = dp.build(spec, candidates(), dp.ExecutionOnly(), profile())
            frozen = base.build(spec, candidates(), base.QualityGate('diagnostic', 0.4), profile())
            for obs, now in ((observed(**E500), 100.5), (observed(**E2000), 100.6), (None, 101.0)):
                with self.subTest(spec=spec['name'], now=now):
                    self.assertEqual(strip(ours.decide(obs, now)), strip(frozen.decide(obs, now)))
                    self.assertIs(type(ours), base.POLICIES[spec['name']])

    def test_the_frozen_base_is_byte_identical_to_the_plan_bound_source(self):
        import hashlib
        import json
        here = Path(__file__).resolve().parent
        sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        entry = json.loads((here/'SOURCES.json').read_text())['frozen_unstable_policies.py']
        self.assertEqual({sha(here/'frozen_unstable_policies.py'), sha(here.parent/'unstable-network'/'policies.py'), entry['plan_bound_sha256']},
                         {'f6ecfade9f52b6b2e70d4d39417b8086ac62f7e9a5e6ff9c6d9fd7cee0e88870'})

    def test_the_frozen_base_registry_is_not_extended(self):
        guard()
        self.assertNotIn('dag_deadline_guard', base.POLICIES)


if __name__ == '__main__':
    unittest.main()
