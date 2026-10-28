"""Offline analysis seam (analyze.py): phase cohorts, recovery and network timeline, on hand-made ledgers.

Expected values are worked by hand from the pre-registered definitions (design.RECOVERY, traces.TRACES), never
recomputed the way the module does.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze  # noqa: E402


def arrival(i, offset, status, latency_ms=None):
    return dict(index=i, offset_s=offset, status=status, latency_ms=latency_ms)


class PhaseCohorts(unittest.TestCase):
    def test_every_arrival_is_counted_once_in_the_phase_it_arrived_in(self):
        classified = [arrival(0, 5.0, 'ok', 100), arrival(1, 19.9, 'late_ok', 2500), arrival(2, 20.0, 'timeout'),
                      arrival(3, 39.0, 'ok', 900), arrival(4, 41.0, 'ok', 120)]
        phases = analyze.phase_summary(classified, 'slow', bound_ms=0.0)
        self.assertEqual([p['requests'] for p in phases], [2, 2, 1])
        self.assertEqual([p['counts'] for p in phases], [dict(ok=1, late_ok=1), dict(timeout=1, ok=1), dict(ok=1)])
        self.assertEqual([p['name'] for p in phases], ['good', 'slow', 'good'])

    def test_arrivals_within_the_alignment_bound_of_a_boundary_are_flagged_not_dropped(self):
        classified = [arrival(0, 5.0, 'ok', 100), arrival(1, 19.9, 'ok', 100), arrival(2, 20.05, 'ok', 100)]
        phases = analyze.phase_summary(classified, 'slow', bound_ms=150.0)
        self.assertEqual([p['requests'] for p in phases], [2, 1, 0])
        self.assertEqual([p['boundary_uncertain'] for p in phases], [1, 1, 0])

    def test_in_deadline_fraction_per_phase(self):
        classified = [arrival(0, 1.0, 'ok', 100), arrival(1, 2.0, 'timeout'), arrival(2, 3.0, 'admission_drop'), arrival(3, 4.0, 'ok', 300)]
        phase = analyze.phase_summary(classified, 'stable', bound_ms=0.0)[0]
        self.assertEqual(phase['in_deadline_fraction'], 0.5)
        self.assertEqual(phase['latency_ms']['p50'], 200.0)


class Recovery(unittest.TestCase):
    def test_first_recovery_and_the_fixed_10s_window_after_the_slow_phase(self):
        classified = [arrival(0, 39.0, 'timeout'), arrival(1, 40.5, 'timeout'), arrival(2, 41.0, 'ok', 300),
                      arrival(3, 45.0, 'ok', 200), arrival(4, 50.9, 'late_ok', 2600), arrival(5, 51.5, 'ok', 100)]
        [r] = analyze.recovery(classified, 'slow')
        self.assertEqual(r['boundary_s'], 40)
        self.assertEqual(r['first_recovery_offset_s'], 41.0)
        self.assertEqual(r['time_to_recovery_s'], 1.0)
        self.assertEqual((r['window_requests'], r['window_in_deadline']), (3, 2))
        self.assertAlmostEqual(r['window_in_deadline_fraction'], 2 / 3)

    def test_no_in_deadline_answer_after_the_boundary_is_not_recovered(self):
        [r] = analyze.recovery([arrival(0, 41.0, 'timeout'), arrival(1, 55.0, 'late_ok', 3000)], 'slow')
        self.assertEqual(r['state'], 'not_recovered')
        self.assertIsNone(r['first_recovery_offset_s'])
        self.assertIsNone(r['window_in_deadline_fraction'])

    def test_burst_has_two_recovery_boundaries_and_stable_none(self):
        classified = [arrival(0, 30.2, 'ok', 100), arrival(1, 50.1, 'ok', 100)]
        self.assertEqual([r['boundary_s'] for r in analyze.recovery(classified, 'burst')], [30, 50])
        self.assertEqual(analyze.recovery(classified, 'stable'), [])

    def test_the_window_is_cut_at_the_trace_end(self):
        [_, r] = analyze.recovery([arrival(0, 30.5, 'ok', 100), arrival(1, 55.0, 'ok', 100), arrival(2, 59.0, 'timeout')], 'burst')
        self.assertEqual((r['first_recovery_offset_s'], r['window_end_s'], r['window_requests']), (55.0, 60, 2))


TRACE_SHA = {k: analyze.traces.trace_sha256(k) for k in analyze.traces.TRACES}


def netctl_status(trace, start=1000.0, lag_s=0.002, call_s=0.001, late=None, fail_phase=None, sha=None, window_end=True):
    """netctl /status as the Runner records it (its own monotonic clock). late: {phase index: extra lag s}."""
    phases = analyze.traces.TRACES[trace]
    events = [dict(kind='install', phase=0, condition=analyze.traces.condition(phases[0]), stats={})]
    for i, p in enumerate(phases[1:], 1):
        planned = start + p['start_s']
        begun = planned + lag_s + (late or {}).get(i, 0.0)
        ok = i != fail_phase
        events.append(dict(kind='change', phase=i, planned_offset_s=p['start_s'], planned_local=planned, condition=analyze.traces.condition(p),
                           ok=ok, apply_lag_ms=(begun - planned) * 1000,
                           **(dict(applied_start_local=begun, applied_end_local=begun + call_s, stats={}) if ok else dict(error='netlink'))))
        if not ok:
            break
    if window_end and fail_phase is None:
        events.append(dict(kind='window_end', planned_offset_s=phases[-1]['end_s'], stats={}))
    return dict(trace=trace, trace_sha256=sha or TRACE_SHA[trace], start_local=start, events=events,
                state='failed' if fail_phase else 'completed')


def all_ok(offsets):
    return [arrival(i, o, 'ok', 100) for i, o in enumerate(offsets)]


COMPLETE = dict(state='done', partial=False)


class TimingValidity(unittest.TestCase):
    """R1 spec F1: phase/recovery comparisons need validated timing; anything else is explicit invalid/unknown."""

    def test_complete_small_lag_timing_is_valid_with_boundary_intervals_from_actual_application(self):
        # bound 30 ms; each change starts 2 ms late and takes 1 ms: B in [B+0.002-0.030, B+0.003+0.030] -> 28 / 33 ms.
        t = analyze.timing_validity('slow', dict(bound_ms=30.0), netctl_status('slow'), 250)
        self.assertEqual(t['status'], 'valid')
        self.assertEqual(t['reasons'], [])
        [b20, b40] = t['boundaries']
        self.assertAlmostEqual(b20['lo_s'], 19.972, places=9)
        self.assertAlmostEqual(b20['hi_s'], 20.033, places=9)
        self.assertAlmostEqual(t['total_uncertainty_ms'], 33.0, places=6)

    def test_missing_alignment_or_netctl_evidence_is_unknown_not_zero_uncertainty(self):
        self.assertEqual(analyze.timing_validity('burst', None, netctl_status('burst'), 250)['status'], 'unknown')
        self.assertEqual(analyze.timing_validity('burst', dict(bound_ms=20.0), None, 250)['status'], 'unknown')

    def test_an_alignment_bound_that_is_absent_non_finite_negative_or_over_tolerance_is_invalid(self):
        for align in (dict(), dict(bound_ms=float('nan')), dict(bound_ms=-1.0), dict(bound_ms=1000.0), dict(bound_ms=True)):
            self.assertEqual(analyze.timing_validity('burst', align, netctl_status('burst'), 250)['status'], 'invalid', align)

    def test_a_failed_or_missing_transition_is_invalid(self):
        self.assertEqual(analyze.timing_validity('burst', dict(bound_ms=20.0), netctl_status('burst', fail_phase=1), 250)['status'], 'invalid')
        cut = netctl_status('burst')
        cut['events'] = cut['events'][:4]  # a restore cancelled the remaining phases before the window end
        self.assertEqual(analyze.timing_validity('burst', dict(bound_ms=20.0), cut, 250)['status'], 'invalid')

    def test_another_trace_or_hash_is_invalid(self):
        self.assertEqual(analyze.timing_validity('burst', dict(bound_ms=20.0), netctl_status('slow'), 250)['status'], 'invalid')
        wrong = netctl_status('burst', sha='0' * 64)
        self.assertEqual(analyze.timing_validity('burst', dict(bound_ms=20.0), wrong, 250)['status'], 'invalid')

    def test_application_lag_counts_toward_the_250ms_tolerance(self):
        # bound 20 ms (fine alone) + a change 240 ms late taking 1 ms: hi - B = 0.240 + 0.001 + 0.020 = 261 ms > 250.
        t = analyze.timing_validity('slow', dict(bound_ms=20.0), netctl_status('slow', lag_s=0.0, late={2: 0.240}), 250)
        self.assertEqual(t['status'], 'invalid')
        self.assertAlmostEqual(t['total_uncertainty_ms'], 261.0, places=6)

    def test_a_restoration_applied_5s_late_is_invalid(self):
        t = analyze.timing_validity('burst', dict(bound_ms=20.0), netctl_status('burst', lag_s=0.0, late={4: 5.0}), 250)
        self.assertEqual(t['status'], 'invalid')


class ComparableOutputs(unittest.TestCase):
    def test_invalid_or_unknown_timing_suppresses_phase_and_recovery_outputs(self):
        classified = all_ok([5.0, 15.0, 31.0, 45.0, 55.0])
        for status in (None, netctl_status('burst', fail_phase=1)):
            t = analyze.timing_validity('burst', dict(bound_ms=20.0), status, 250)
            out = analyze.comparable_outputs(classified, 'burst', t, COMPLETE)
            self.assertFalse(out['comparable'])
            self.assertIsNone(out['phases'])
            self.assertIsNone(out['recovery'])
            self.assertTrue(out['suppressed_because'])

    def test_a_partial_or_missing_ledger_is_not_comparable_either(self):
        t = analyze.timing_validity('slow', dict(bound_ms=30.0), netctl_status('slow'), 250)
        for ledger in (dict(state='running', partial=True), None, dict(state='error', partial=False)):
            self.assertFalse(analyze.comparable_outputs(all_ok([41.0]), 'slow', t, ledger)['comparable'], ledger)

    def test_valid_timing_flags_arrivals_inside_the_actual_boundary_interval(self):
        # interval of the t=20 boundary: [19.972, 20.033]; 19.98 and 20.03 inside, 19.95 and 20.05 outside.
        t = analyze.timing_validity('slow', dict(bound_ms=30.0), netctl_status('slow'), 250)
        out = analyze.comparable_outputs(all_ok([19.95, 19.98, 20.03, 20.05]), 'slow', t, COMPLETE)
        self.assertTrue(out['comparable'])
        self.assertEqual([p['boundary_uncertain'] for p in out['phases']], [1, 1, 0])

    def test_recovery_is_never_claimed_before_the_applied_restoration(self):
        # With the 5 s late restoration admitted (tolerance lifted only to inspect the rule), the t=30 restoration
        # was applied at offset 35.000-35.000+0.001 -> latest 35.021 with the 20 ms bound; the 30.5 and 33.0 arrivals
        # cannot be a recovery, the first possible one is 36.0.
        t = analyze.timing_validity('burst', dict(bound_ms=20.0), netctl_status('burst', lag_s=0.0, late={4: 5.0}), 1e9)
        out = analyze.comparable_outputs(all_ok([30.5, 33.0, 36.0, 51.0]), 'burst', t, COMPLETE)
        r30 = out['recovery'][0]
        self.assertEqual(r30['first_recovery_offset_s'], 36.0)
        self.assertAlmostEqual(r30['search_from_s'], 35.021, places=9)
        self.assertEqual(r30['time_to_recovery_s'], 6.0)


class NetworkTimeline(unittest.TestCase):
    def snap(self, drops, packets, backlog=0):
        return dict(qdisc_summary=[dict(handle=0x110000, parent=0x100001, kind='netem', drops=drops, packets=packets, bytes=packets * 1000,
                                        backlog=backlog, qlen=0, overlimits=0)])

    def test_application_lag_and_per_phase_counter_deltas(self):
        status = dict(trace='slow', events=[
            dict(kind='install', phase=0, stats=self.snap(0, 0)),
            dict(kind='change', phase=1, planned_offset_s=20, apply_lag_ms=1.5, call_ms=0.8, ok=True, stats=self.snap(2, 300)),
            dict(kind='change', phase=2, planned_offset_s=40, apply_lag_ms=3.0, call_ms=0.9, ok=True, stats=self.snap(10, 500)),
            dict(kind='window_end', planned_offset_s=60, stats=self.snap(10, 900))])
        timeline = analyze.network_timeline(status)
        self.assertEqual(timeline['max_apply_lag_ms'], 3.0)
        self.assertEqual(timeline['changes_ok'], 2)
        cloud = [p['qdiscs']['0x110000'] for p in timeline['phases']]
        self.assertEqual([(c['drops'], c['packets']) for c in cloud], [(2, 300), (8, 200), (0, 400)])

    def test_a_failed_change_is_reported(self):
        status = dict(trace='slow', events=[dict(kind='install', phase=0, stats=self.snap(0, 0)),
                                            dict(kind='change', phase=1, planned_offset_s=20, apply_lag_ms=1.0, ok=False, error='netlink')])
        self.assertEqual(analyze.network_timeline(status)['changes_failed'], 1)


if __name__ == '__main__':
    unittest.main()
