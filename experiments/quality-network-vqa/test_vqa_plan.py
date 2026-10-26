"""Calibration-only cost table, policy selection and frozen unique-plan schedule seam."""
import copy
import json
import sys
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import vqa_plan as plan  # noqa: E402

E0, E1, C = plan.E0, plan.E1, plan.C
CAPACITY = plan.deployed_capacity()


def compute_rows(vision=(900, 150), speech=(2500, 300), fusion=20, split='calibration'):
    rows = []
    for q in range(3):
        rows += [dict(task='vision', node=E0, question_id=q, split=split, compute_ms=vision[0]),
                 dict(task='vision', node=C, question_id=q, split=split, compute_ms=vision[1]),
                 dict(task='speech', node=E1, question_id=q, split=split, compute_ms=speech[0]),
                 dict(task='speech', node=C, question_id=q, split=split, compute_ms=speech[1]),
                 dict(task='fusion', node=C, question_id=q, split=split, compute_ms=fusion)]
    return rows


def transfer_rows(cross=(3000, 400, 30, 20, 40), local=1, split='calibration'):
    """cross: image, audio, feature, transcript, return ms on a cross-zone hop."""
    image, audio, feature, transcript, ret = cross
    hops = [('image', E0, E0, local, 818048), ('image', E0, C, image, 818048), ('audio', E1, E1, local, 90000),
            ('audio', E1, C, audio, 90000), ('vision', E0, C, feature, 2176), ('vision', C, C, local, 2176),
            ('speech', E1, C, transcript, 40), ('speech', C, C, local, 40), ('return', C, E0, ret, 600),
            ('control', E0, C, ret, 2)]
    return [dict(kind=k, origin=o, destination=d, question_id=q, split=split, ms=ms, bytes=b)
            for q in range(3) for k, o, d, ms, b in hops]


def profile(**kw):
    return plan.build_profile('net-2', compute_rows(**{k: v for k, v in kw.items() if k in ('vision', 'speech', 'fusion')}),
                              transfer_rows(**{k: v for k, v in kw.items() if k in ('cross', 'local')}), CAPACITY,
                              evidence_id='unit')


ALL_PASS = {key: .5 for key in plan.PLACEMENTS}


class CalibrationOnly(unittest.TestCase):
    def test_evaluation_rows_are_refused_for_costs(self):
        with self.assertRaisesRegex(ValueError, 'calibration'):
            plan.build_profile('net-2', compute_rows() + compute_rows(split='evaluation'), transfer_rows(), CAPACITY, evidence_id='u')
        with self.assertRaisesRegex(ValueError, 'calibration'):
            plan.build_profile('net-2', compute_rows(), transfer_rows(split='evaluation'), CAPACITY, evidence_id='u')

    def test_label_fields_never_enter_the_profile(self):
        rows = compute_rows()
        rows[0]['human_answers'] = [dict(answer='cat')]
        with self.assertRaisesRegex(ValueError, 'label'):
            plan.build_profile('net-2', rows, transfer_rows(), CAPACITY, evidence_id='u')

    def test_quality_for_selection_must_come_from_calibration(self):
        with self.assertRaisesRegex(ValueError, 'calibration'):
            plan.select(profile(), dict(ALL_PASS), qmin=.4, quality_split='evaluation')


class Selection(unittest.TestCase):
    def test_slow_wan_separates_task_fastest_from_network_aware_selectors(self):
        out = plan.select(profile(), ALL_PASS, qmin=.4)
        self.assertEqual(out['task-fastest']['placement'], dict(vision=C, speech=C, fusion=C))
        self.assertEqual(out['dag-exact']['placement'], dict(vision=E0, speech=C, fusion=C))
        self.assertEqual(out['greedy-network']['placement'], dict(vision=E0, speech=C, fusion=C))
        self.assertEqual(out['dag-exact-network-blind']['placement'], dict(vision=C, speech=C, fusion=C))
        for result in out.values():
            self.assertIn('predicted_ms', result)
            self.assertEqual(result['predicted_ms_cost_model'], 'topology_profile')

    def test_network_blind_equals_exact_when_communication_costs_are_zero(self):
        zero = plan.select(profile(cross=(0, 0, 0, 0, 0), local=0), ALL_PASS, qmin=.4)
        self.assertEqual(zero['dag-exact']['placement'], zero['dag-exact-network-blind']['placement'])
        slow = plan.select(profile(cross=(90000, 90000, 90000, 90000, 90000)), ALL_PASS, qmin=.4)
        self.assertEqual(slow['dag-exact-network-blind']['placement'], zero['dag-exact-network-blind']['placement'])

    def test_quality_ablation_is_equivalent_when_every_candidate_passes(self):
        out = plan.select(profile(), ALL_PASS, qmin=.4)
        for result in out.values():
            self.assertEqual(result['placement'], result['no_quality_filter']['placement'])
            self.assertFalse(result['quality_ablation_differs'])

    def test_quality_filter_moves_exact_choice_and_refuses_greedy_choice(self):
        quality = dict(ALL_PASS, **{plan.key(dict(vision=E0, speech=C, fusion=C)): .2})
        out = plan.select(profile(), quality, qmin=.4)
        self.assertEqual(out['dag-exact']['no_quality_filter']['placement'], dict(vision=E0, speech=C, fusion=C))
        self.assertNotEqual(out['dag-exact']['placement'], dict(vision=E0, speech=C, fusion=C))
        self.assertTrue(out['dag-exact']['quality_ablation_differs'])
        self.assertEqual(out['greedy-network']['status'], 'quality_rejected')

    def test_no_candidate_meeting_quality_is_reported_infeasible(self):
        out = plan.select(profile(), {k: .1 for k in plan.PLACEMENTS}, qmin=.4)
        self.assertTrue(all(r['status'] in ('quality_infeasible', 'quality_rejected') for r in out.values()))

    def test_incompatible_or_over_capacity_choice_is_refused(self):
        p = profile()
        with self.assertRaisesRegex(ValueError, 'compatible'):
            plan.validate_choice(p, dict(vision=E1, speech=C, fusion=C))
        with self.assertRaisesRegex(ValueError, 'compatible'):
            plan.validate_choice(p, dict(vision=C, speech=C, fusion=E0))
        tight = copy.deepcopy(p)
        tight['nodes'][C]['cpu_free_m'] = 1000
        with self.assertRaisesRegex(ValueError, 'capacity'):
            plan.validate_choice(tight, dict(vision=C, speech=C, fusion=C))


class Oracle(unittest.TestCase):
    def measured(self, on_time, p50):
        return {k: dict(on_time=on_time[i], arrivals=20, p50_ms=p50[i], quality=.5, split='calibration')
                for i, k in enumerate(plan.PLACEMENTS)}

    def test_oracle_maximizes_calibration_on_time_then_median_latency(self):
        out = plan.empirical_oracle(self.measured([5, 18, 18, 2], [900, 1500, 1200, 400]), qmin=.4)
        self.assertEqual(plan.key(out['placement']), list(plan.PLACEMENTS)[2])
        self.assertEqual(out['selector'], 'empirical calibration oracle (measured full-plan); not a new algorithm')

    def test_oracle_refuses_evaluation_measurements(self):
        measured = self.measured([5, 18, 18, 2], [900, 1500, 1200, 400])
        next(iter(measured.values()))['split'] = 'evaluation'
        with self.assertRaisesRegex(ValueError, 'calibration'):
            plan.empirical_oracle(measured, qmin=.4)


def chosen(placement, unfiltered=None):
    return dict(status='ok', placement=placement, no_quality_filter=dict(placement=unfiltered or placement))


def rejected(unfiltered, status='quality_rejected'):
    return dict(status=status, placement=None, no_quality_filter=dict(placement=unfiltered))


class Schedule(unittest.TestCase):
    A, B = dict(vision=E0, speech=C, fusion=C), dict(vision=C, speech=C, fusion=C)

    def test_same_placement_shares_one_job_within_a_condition_only(self):
        a, b = self.A, self.B
        plans = {'net-2-r1': {'task-fastest': chosen(b), 'dag-exact': chosen(a), 'greedy-network': chosen(a)},
                 'net-100-r1': {'task-fastest': chosen(b), 'dag-exact': chosen(b), 'greedy-network': rejected(b)}}
        jobs = plan.schedule(plans, seed=20261023)
        self.assertEqual(len(jobs), 3)
        shared = next(j for j in jobs if j['condition'] == 'net-2-r1' and j['placement'] == a)
        self.assertEqual(sorted(shared['policies']), ['dag-exact', 'dag-exact+no-quality-filter', 'greedy-network',
                                                       'greedy-network+no-quality-filter'])
        self.assertEqual(sorted(next(j for j in jobs if j['condition'] == 'net-100-r1')['policies']),
                         ['dag-exact', 'dag-exact+no-quality-filter', 'greedy-network+no-quality-filter', 'task-fastest',
                          'task-fastest+no-quality-filter'])
        self.assertEqual(jobs, plan.schedule(plans, seed=20261023))

    def test_unfiltered_choice_runs_as_its_own_alias_even_when_the_filtered_policy_is_infeasible(self):
        a, b = self.A, self.B
        plans = {'net-2-r1': {'dag-exact': chosen(b, unfiltered=a), 'greedy-network': rejected(a),
                              'task-fastest': rejected(b, 'quality_infeasible')}}
        jobs = {json.dumps(j['placement'], sort_keys=True): sorted(j['policies']) for j in plan.schedule(plans, seed=1)}
        self.assertEqual(jobs, {json.dumps(a, sort_keys=True): ['dag-exact+no-quality-filter', 'greedy-network+no-quality-filter'],
                                json.dumps(b, sort_keys=True): ['dag-exact', 'task-fastest+no-quality-filter']})

    def test_every_condition_keeps_an_evaluation_phase_when_all_filtered_policies_fail(self):
        plans = {'net-2-r1': {'dag-exact': rejected(self.A, 'quality_infeasible'), 'greedy-network': rejected(self.A)}}
        jobs = plan.schedule(plans, seed=1)
        self.assertEqual([sorted(j['policies']) for j in jobs], [['dag-exact+no-quality-filter', 'greedy-network+no-quality-filter']])

    def test_a_result_without_its_unfiltered_choice_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'no_quality_filter'):
            plan.schedule({'net-2-r1': {'dag-exact': dict(status='ok', placement=self.A)}}, seed=1)


if __name__ == '__main__':
    unittest.main()
