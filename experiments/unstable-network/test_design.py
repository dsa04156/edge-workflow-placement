"""Release-bound design seam (design.py, unstable.py): the frozen block/order schedule and the release binding.

Schedule tests need numpy/pandas for the experimental-design helper (assign_factorial_runs) and are skipped without
them; run them with the uv command in docs/experiments/unstable-network-20261027.md. The schedule is generated once at
plan time and frozen in plan.json; execute/analyze only re-check it with the stdlib check_schedule.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import design  # noqa: E402
import traces  # noqa: E402
import unstable  # noqa: E402

try:
    import numpy  # noqa: F401
    import pandas  # noqa: F401
    HAVE_HELPER_DEPS = True
except ImportError:
    HAVE_HELPER_DEPS = False

POLICIES = ['task_fastest', 'greedy_comm', 'dag_static', 'dag_reopt', 'dag_reopt_hysteresis', 'dag_reopt_no_network']


class FrozenContract(unittest.TestCase):
    def test_the_six_policies_and_fixed_parameters(self):
        self.assertEqual([s['name'] for s in design.POLICY_SPECS], POLICIES)
        self.assertEqual(next(s for s in design.POLICY_SPECS if s['name'] == 'dag_reopt_hysteresis'),
                         dict(name='dag_reopt_hysteresis', rel=0.15, abs_ms=50.0, dwell_s=5.0))
        self.assertEqual((design.DESIGN['rate_rps'], design.DESIGN['window_s'], design.DESIGN['blocks'], design.DESIGN['seed']),
                         (1.6, 60, 2, 20261027))
        self.assertEqual(design.DESIGN['traces'], ('stable', 'slow', 'burst'))
        b = design.BUDGETS
        self.assertEqual((b['deadline_s'], b['cancel_s'], b['max_inflight'], b['obs_poll_s']), (2.0, 10.0, 8, 1.0))
        self.assertEqual((design.QMIN, design.QUALITY_MODE), (0.40, 'diagnostic'))

    def test_arrivals_are_shared_by_every_policy_of_a_block_and_trace(self):
        a = design.arrivals(1.6, 60, design.trace_seed(20261027, 0, 'burst', 1.6), ['1', '2', '3'])
        b = design.arrivals(1.6, 60, design.trace_seed(20261027, 0, 'burst', 1.6), ['1', '2', '3'])
        self.assertEqual(a, b)
        self.assertTrue(all(0 <= x['offset_s'] < 60 for x in a))
        self.assertNotEqual(design.trace_seed(20261027, 0, 'burst', 1.6), design.trace_seed(20261027, 1, 'burst', 1.6))


@unittest.skipUnless(HAVE_HELPER_DEPS, 'numpy/pandas for the experimental-design helper are not installed')
class Schedule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen = design.schedule()
        cls.cells = cls.frozen['cells']

    def test_36_cells_each_block_holds_every_trace_policy_pair_once(self):
        self.assertEqual(len(self.cells), 36)
        for b in (0, 1):
            pairs = sorted((c['trace'], c['policy']) for c in self.cells if c['block'] == b)
            self.assertEqual(pairs, sorted((t, p) for t in ('stable', 'slow', 'burst') for p in POLICIES))

    def test_blocks_run_in_order_and_a_trace_group_is_contiguous(self):
        self.assertEqual([c['block'] for c in self.cells], [0] * 18 + [1] * 18)
        groups = [(c['block'], c['trace']) for c in self.cells]
        self.assertEqual(len({g for g in groups}), 6)
        self.assertEqual([groups[i] for i in range(0, 36, 6)], list(dict.fromkeys(groups)))

    def test_policy_order_is_a_latin_square_over_the_six_groups(self):
        for p in POLICIES:
            positions = sorted(c['position'] for c in self.cells if c['policy'] == p)
            self.assertEqual(positions, [0, 1, 2, 3, 4, 5], p)

    def test_one_arrival_seed_per_block_and_trace(self):
        seeds = {}
        for c in self.cells:
            seeds.setdefault((c['block'], c['trace']), set()).add(c['seed'])
        self.assertTrue(all(len(v) == 1 for v in seeds.values()))
        self.assertEqual(len({next(iter(v)) for v in seeds.values()}), 6)

    def test_the_schedule_is_reproducible_and_records_the_helper(self):
        self.assertEqual(design.schedule()['cells'], self.cells)
        self.assertEqual(self.frozen['helper']['function'], 'assign_factorial_runs')
        self.assertEqual(len(self.frozen['helper']['sha256']), 64)
        self.assertEqual(design.check_schedule(self.cells), [])

    def test_a_tampered_schedule_is_detected_without_the_helper(self):
        swapped = [dict(c) for c in self.cells]
        swapped[0]['policy'], swapped[1]['policy'] = swapped[1]['policy'], swapped[0]['policy']
        self.assertNotEqual(design.check_schedule(swapped), [])


class ReleaseBinding(unittest.TestCase):
    def plan_value(self):
        value = dict(budgets_sha256=design.digest(design.BUDGETS), traces={k: traces.trace_sha256(k) for k in traces.TRACES},
                     shipped_sha256=design.digest(unstable.shipped_hashes()), planner_sources=unstable.planner_hashes(),
                     copied_sources=unstable.copied_sources(),
                     schedule=dict(helper=dict(path=str(design.HELPER.relative_to(design.ROOT)), sha256=unstable.sha(design.HELPER))))
        return value

    def test_current_sources_have_no_source_drift(self):
        self.assertEqual(unstable.source_drift(self.plan_value()), [])

    def test_a_changed_trace_table_budget_or_shipped_source_is_drift(self):
        for key, value in (('traces', {'burst': '0' * 64}), ('budgets_sha256', '0' * 64), ('shipped_sha256', '0' * 64)):
            plan = dict(self.plan_value(), **{key: value})
            self.assertTrue(unstable.source_drift(plan), key)

    def test_a_changed_copy_origin_is_drift(self):
        plan = self.plan_value()
        first = next(iter(plan['copied_sources']))
        plan['copied_sources'][first] = dict(plan['copied_sources'][first], origin_sha256='0' * 64)
        self.assertTrue(unstable.source_drift(plan))

    def test_execute_without_a_release_returns_2_before_any_kubectl_call(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(subprocess, 'run') as run, mock.patch.object(subprocess, 'Popen') as popen:
            plan_dir = Path(tmp)/'plan'
            plan_dir.mkdir()
            (plan_dir/'plan.json').write_text(json.dumps(self.plan_value()))
            rc = unstable.main(['execute', '--plan', str(plan_dir), '--run-id', 'un-test', '--release', str(Path(tmp)/'missing.json'),
                                '--output-root', tmp])
            self.assertEqual(rc, 2)
            run.assert_not_called()
            popen.assert_not_called()
            self.assertFalse((Path(tmp)/'un-test').exists())


if __name__ == '__main__':
    unittest.main()
