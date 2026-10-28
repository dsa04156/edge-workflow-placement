"""Observational audit seam (audit.py): snapshot reconstruction, no outcome leakage, malformed input, CLI on the frozen run.

Synthetic snapshots use the worked example of test_deadline_policies (all-e anchor, 108 plans). A snapshot is produced
exactly as the generator does it: Observation.to_json(now, stale_after_s) of a live Observation.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import audit  # noqa: E402
import deadline_policies as dp  # noqa: E402
import frozen_unstable_policies as base  # noqa: E402
from test_deadline_policies import ALL_E, IDLE, SERVER_NPU, WORKERS, candidates, guard, observed, profile  # noqa: E402

RUN = ROOT/'artifacts/unstable-network/un-collect-r3'
PLAN = ROOT/'artifacts/unstable-network/plan-20261027T111532Z'
WRONG_PLAN = ROOT/'artifacts/unstable-network/plan-20261027T091525Z'
NOW = 100.5


def learned(obs):
    """Receipt and transfer estimates as the generator's learn() writes them (record_receipt also writes service_ms)."""
    obs.record_receipt('image_preprocess', 'e', 30.0)
    obs.record_receipt('vision', 'e', 25.0)
    obs.record_receipt('vision', 'n', 7.0)
    obs.record_transfer('origin', 's', 260.0, 1, 200.0)
    return obs


def row(obs, policy, **outcome):
    snapshot = obs.to_json(NOW, 3.0)
    decision = policy.decide(obs, NOW)
    return dict(dict(index=0, scheduled_offset_s=0.5, question_id=1, observation=snapshot, decision=dict(decision), status='ok',
                     latency_ms=123.0), **outcome)


def reopt():
    return dp.build(dict(name='dag_reopt'), candidates(), dp.ExecutionOnly(), profile())


class Reconstruction(unittest.TestCase):
    def test_an_idle_snapshot_with_learned_estimates_reproduces_the_live_decisions(self):
        obs = learned(observed())
        record = audit.audit_row(guard(), row(obs, reopt()), WORKERS, reopt())
        live = guard().decide(obs, NOW)
        self.assertEqual(record['replay_fidelity'], 'match')
        self.assertEqual((record['guard']['category'], record['guard']['plan_label'], record['guard']['anchor_predicted_ms']),
                         ('exact', live['plan_label'], live['anchor_predicted_ms']))

    def test_a_busy_anchor_worker_with_receipts_is_not_reconstructed(self):
        obs = learned(observed(queues=dict(e=3)))  # e has receipts, so its unlogged service_ms existed
        record = audit.audit_row(guard(), row(obs, reopt()), WORKERS, reopt())
        self.assertEqual(record['guard'], dict(category='non_reconstructible', reason='anchor_worker_busy_unlogged_service', worker='e'))
        self.assertEqual(record['replay_fidelity'], 'non_reconstructible')
        self.assertNotIn('agreement', record)

    def test_a_busy_worker_without_receipts_uses_the_frozen_profile_mean_exactly(self):
        obs = observed(queues=dict(e=100))  # no receipt anywhere: base queue_ms uses the profile mean of e (20 ms)
        record = audit.audit_row(guard(), row(obs, reopt()), WORKERS, reopt())
        self.assertEqual((record['guard']['plan_label'], record['guard']['anchor_predicted_ms']), (base.plan_label(SERVER_NPU), 2100.0))
        self.assertEqual(record['replay_fidelity'], 'match')

    def test_a_stale_snapshot_is_an_exact_fallback_even_with_busy_workers(self):
        obs = learned(observed(queues=dict(e=3), at=96.0))  # 4.5 s old at NOW
        record = audit.audit_row(guard(), row(obs, reopt()), WORKERS, reopt())
        self.assertEqual((record['guard']['category'], record['guard']['fallback_reason'], record['guard']['plan_label']),
                         ('fallback', 'stale_observation', base.plan_label(ALL_E)))

    def test_a_never_answered_worker_in_the_snapshot_is_a_fallback(self):
        snapshot = observed().to_json(NOW, 3.0)
        snapshot['workers']['g'].update(waiting=None, active=None, joins_open=None, age_ms=None)
        record = audit.guard_on_snapshot(guard(), snapshot, WORKERS)
        self.assertEqual((record['category'], record['fallback_reason']), ('fallback', 'no_successful_poll'))

    def test_a_row_without_snapshot_is_counted_not_decided(self):
        record = audit.audit_row(guard(), dict(index=3, scheduled_offset_s=1.0, status='admission_drop'), WORKERS)
        self.assertEqual(record['guard'], dict(category='no_snapshot', reason='no_snapshot'))


class NoLeakage(unittest.TestCase):
    def test_outcome_logged_decision_and_trace_phase_do_not_change_the_guard_decision(self):
        r = row(observed(queues=dict(e=100)), reopt())
        other = dict(copy.deepcopy(r), status='timeout', latency_ms=99999.0, branches=dict(image=dict(status='timeout')),
                     decision=dict(r['decision'], plan_label='image_preprocess=e|vision=g|audio_preprocess=e|speech=e|fusion=e',
                                   predicted_ms=1.0), trace_phase='degraded', completed_offset_s=50.0)
        self.assertEqual(audit.audit_row(guard(), r, WORKERS)['guard'], audit.audit_row(guard(), other, WORKERS)['guard'])

    def test_row_order_does_not_change_any_guard_decision(self):
        rows = [dict(row(o, reopt()), index=i) for i, o in enumerate((observed(), observed(queues=dict(e=100)), observed(**dict(at=96.0))))]
        policy = guard()
        forward = [audit.audit_row(policy, r, WORKERS)['guard'] for r in rows]
        backward = [audit.audit_row(policy, r, WORKERS)['guard'] for r in reversed(rows)]
        self.assertEqual(forward, list(reversed(backward)))


class Malformed(unittest.TestCase):
    def snapshot(self):
        return learned(observed()).to_json(NOW, 3.0)

    def test_malformed_snapshots_are_refused(self):
        cases = dict(missing_worker=lambda s: s['workers'].pop('n'), text_age=lambda s: s['workers']['e'].update(age_ms='1'),
                     negative_age=lambda s: s['workers']['e'].update(age_ms=-1.0), bool_queue=lambda s: s['workers']['e'].update(active=True),
                     unknown_task=lambda s: s['compute_est'].update({'resize@e': 1.0}),
                     unknown_link=lambda s: s['link_ms_per_byte'].update({'origin->x': 1.0}),
                     nan_estimate=lambda s: s['compute_est'].update({'vision@e': float('nan')}), not_a_dict=lambda s: s.clear())
        for name, damage in cases.items():
            s = self.snapshot()
            damage(s)
            with self.subTest(name), self.assertRaises(ValueError):
                audit.guard_on_snapshot(guard(), s, WORKERS)

    def test_missing_null_or_non_dict_estimate_tables_are_refused_not_read_as_no_receipts(self):
        busy = learned(observed(queues=dict(e=3))).to_json(NOW, 3.0)  # non_reconstructible while its tables are logged
        for table in ('compute_est', 'link_ms_per_byte'):
            for name, damage in dict(missing=lambda s: s.pop(table), null=lambda s: s.update({table: None}),
                                     list=lambda s: s.update({table: []})).items():
                s = copy.deepcopy(busy)
                damage(s)
                with self.subTest(table=table, damage=name), self.assertRaises(ValueError):
                    audit.guard_on_snapshot(guard(), s, WORKERS)

    def test_negative_estimate_durations_are_refused(self):
        for name, damage in dict(compute=lambda s: s['compute_est'].update({'vision@e': -1.0}),
                                 link=lambda s: s['link_ms_per_byte'].update({'origin->s': -0.5})).items():
            s = self.snapshot()
            damage(s)
            with self.subTest(name), self.assertRaises(ValueError):
                audit.guard_on_snapshot(guard(), s, WORKERS)

    def test_logged_empty_estimate_tables_remain_valid(self):
        s = observed(queues=dict(e=100)).to_json(NOW, 3.0)  # no receipt yet: the generator logs {} for both tables
        self.assertEqual((s['compute_est'], s['link_ms_per_byte']), ({}, {}))
        record = audit.guard_on_snapshot(guard(), s, WORKERS)
        self.assertEqual((record['category'], record['anchor_predicted_ms']), ('exact', 2100.0))


def cli(*args, env=None):
    return subprocess.run([sys.executable, str(HERE/'audit.py'), *args], capture_output=True, text=True, timeout=600, env=env)


class Cli(unittest.TestCase):
    def test_spec_prints_the_exact_policy_spec(self):
        out = cli('spec')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)['policy'], dict(name='dag_deadline_guard', deadline_ms=2000.0, anchor_fraction=0.75))

    def test_spec_refuses_an_invalid_fraction(self):
        out = cli('spec', '--anchor-fraction', 'nan')
        self.assertEqual((out.returncode, 'spec refused' in out.stderr), (2, True))

    def test_audit_refuses_a_plan_the_run_was_not_released_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = cli('audit', '--run', str(RUN), '--plan', str(WRONG_PLAN), '--out', str(Path(tmp)/'a'))
            self.assertEqual(out.returncode, 2)
            self.assertIn('not released for this plan', out.stderr)
            self.assertFalse((Path(tmp)/'a').exists())

    def test_audit_refuses_an_existing_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = cli('audit', '--run', str(RUN), '--plan', str(PLAN), '--out', tmp)
            self.assertEqual(out.returncode, 2)
            self.assertIn('exists', out.stderr)

    def test_audit_of_the_frozen_run_accounts_for_every_arrival_and_leaves_the_run_unchanged(self):
        ledgers = sorted(RUN.glob('ledger-*.json'))
        before = {p.name: audit.sha_file(p) for p in ledgers}
        with tempfile.TemporaryDirectory() as tmp:
            out = cli('audit', '--run', str(RUN), '--plan', str(PLAN), '--out', str(Path(tmp)/'a'))
            self.assertEqual(out.returncode, 0, out.stderr)
            report = json.loads((Path(tmp)/'a'/'audit.json').read_text())
            rows = json.loads((Path(tmp)/'a'/'rows.json').read_text())
        total = report['total']
        self.assertEqual((report['cell_count'], report['arrivals'], len(rows), sum(total['category'].values())), (36, 3462, 3462, 3462))
        self.assertEqual(report['anchor_check']['dag_static_logged_rows'], report['anchor_check']['dag_static_logged_equal_anchor'])
        self.assertEqual(total['guard_plan_known'] + total['missing_or_unsupported'], 3462)
        self.assertEqual({k for k in report['input_sha256'] if 'ledger-' in k}, {f'artifacts/unstable-network/un-collect-r3/{n}' for n in before})
        self.assertEqual({p.name: audit.sha_file(p) for p in ledgers}, before)

    def test_audit_rows_are_byte_identical_across_hash_seeds(self):
        # R1: the base predictor visits a plan's workers in set order, so the reported missing worker followed PYTHONHASHSEED
        digests = {}
        with tempfile.TemporaryDirectory() as tmp:
            for seed in ('0', '1', '3'):
                out = cli('audit', '--run', str(RUN), '--plan', str(PLAN), '--out', str(Path(tmp)/seed),
                          env=dict(os.environ, PYTHONHASHSEED=seed))
                self.assertEqual(out.returncode, 0, out.stderr)
                digests[seed] = (json.loads((Path(tmp)/seed/'audit.json').read_text())['rows_sha256'],
                                 audit.sha_file(Path(tmp)/seed/'rows.json'))
        self.assertEqual(len(set(digests.values())), 1, digests)


if __name__ == '__main__':
    unittest.main()
