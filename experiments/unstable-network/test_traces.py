"""Network control spec seam: the frozen time-varying traces (traces.py). Expected values are the spec's literal table
(.scratch/unstable-network-20261027/spec.md, 사전 고정 네트워크 trace), never recomputed from the module."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import traces  # noqa: E402

GOOD = dict(mbps=100, delay_ms=0, jitter_ms=0, loss_pct=0.0)


def windows(name):
    return [(p['start_s'], p['end_s'], dict(mbps=p['mbps'], delay_ms=p['delay_ms'], jitter_ms=p['jitter_ms'], loss_pct=p['loss_pct']))
            for p in traces.TRACES[name]]


class FrozenTraceTable(unittest.TestCase):
    def test_stable_is_one_good_phase_over_the_whole_window(self):
        self.assertEqual(windows('stable'), [(0, 60, GOOD)])

    def test_slow_has_a_10mbps_20ms_middle_third(self):
        self.assertEqual(windows('slow'), [(0, 20, GOOD), (20, 40, dict(mbps=10, delay_ms=20, jitter_ms=0, loss_pct=0.0)), (40, 60, GOOD)])

    def test_burst_matches_the_spec_table_including_the_2s_outage(self):
        mid = dict(mbps=10, delay_ms=20, jitter_ms=5, loss_pct=0.5)
        self.assertEqual(windows('burst'), [
            (0, 10, GOOD), (10, 20, mid), (20, 28, dict(mbps=2, delay_ms=20, jitter_ms=5, loss_pct=1.0)),
            (28, 30, dict(mbps=2, delay_ms=20, jitter_ms=5, loss_pct=100.0)), (30, 40, GOOD), (40, 50, mid), (50, 60, GOOD)])

    def test_every_frozen_trace_validates_against_the_60s_window(self):
        for name in ('stable', 'slow', 'burst'):
            traces.validate(traces.TRACES[name], 60)


class Validation(unittest.TestCase):
    def phases(self, *spans):
        return [dict(start_s=a, end_s=b, **GOOD) for a, b in spans]

    def test_gap_is_refused(self):
        with self.assertRaises(ValueError):
            traces.validate(self.phases((0, 20), (21, 60)), 60)

    def test_overlap_is_refused(self):
        with self.assertRaises(ValueError):
            traces.validate(self.phases((0, 30), (29, 60)), 60)

    def test_not_starting_at_zero_is_refused(self):
        with self.assertRaises(ValueError):
            traces.validate(self.phases((1, 60)), 60)

    def test_running_past_the_window_is_refused(self):
        with self.assertRaises(ValueError):
            traces.validate(self.phases((0, 30), (30, 61)), 60)

    def test_ending_before_the_window_is_refused(self):
        with self.assertRaises(ValueError):
            traces.validate(self.phases((0, 30), (30, 59)), 60)

    def test_empty_phase_is_refused(self):
        with self.assertRaises(ValueError):
            traces.validate(self.phases((0, 30), (30, 30), (30, 60)), 60)

    def test_unapproved_impairment_value_is_refused(self):
        bad = [dict(start_s=0, end_s=60, mbps=5, delay_ms=0, jitter_ms=0, loss_pct=0.0)]
        with self.assertRaises(ValueError):
            traces.validate(bad, 60)


class PhaseLookup(unittest.TestCase):
    def test_boundaries_are_half_open(self):
        burst = traces.TRACES['burst']
        self.assertEqual(traces.phase_at(burst, 0.0), 0)
        self.assertEqual(traces.phase_at(burst, 27.999), 2)
        self.assertEqual(traces.phase_at(burst, 28.0), 3)
        self.assertEqual(traces.phase_at(burst, 59.999), 6)

    def test_outside_the_window_has_no_phase(self):
        self.assertIsNone(traces.phase_at(traces.TRACES['burst'], 60.0))
        self.assertIsNone(traces.phase_at(traces.TRACES['burst'], -0.001))


if __name__ == '__main__':
    unittest.main()
