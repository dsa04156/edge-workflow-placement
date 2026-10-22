"""공개 seam (2): 표본 → 순차 p75 구간 (DKW error spending, generalized inverse)."""
import math
import random
import unittest

from p75 import SequentialP75, sequential_p75


class P75Interval(unittest.TestCase):
    def test_band_literal_for_n200(self):
        # d=1, delta=.05, n=200: alpha=.05/(200*201), e=0.18901...
        # Q(.561)=113th order statistic, Q(.939)=188th; values are 1..200
        xs = list(range(1, 201))
        random.Random(7).shuffle(xs)
        r = sequential_p75(xs, d=1, delta=0.05, support=(0, 1000))
        self.assertAlmostEqual(r["e"], 0.18901389981, places=9)
        self.assertEqual(r["band"], (113, 188))
        self.assertEqual(r["status"], "ok")
        self.assertLessEqual(r["hi"], 188)
        self.assertGreaterEqual(r["lo"], 113)

    def test_few_samples_fall_back_to_known_support(self):
        r = sequential_p75([5, 6, 7], d=2, delta=0.05, support=(0, 50))
        self.assertEqual((r["lo"], r["hi"], r["status"]), (0, 50, "ok"))

    def test_no_samples_is_support(self):
        r = sequential_p75([], d=2, delta=0.05, support=(1, 9))
        self.assertEqual((r["lo"], r["hi"], r["n"]), (1, 9, 0))

    def test_unknown_upper_support_is_infinite(self):
        r = sequential_p75([5, 6, 7], d=1, delta=0.05, support=(0, None))
        self.assertEqual(r["hi"], math.inf)
        self.assertEqual(r["status"], "infinite")
        self.assertFalse(r["certifiable"])

    def test_censored_sample_is_not_certifiable(self):
        r = sequential_p75([5, None, 7], d=1, delta=0.05, support=(0, 50))
        self.assertEqual(r["status"], "censored")
        self.assertFalse(r["certifiable"])
        self.assertEqual(r["censored"], 1)

    def test_empty_intersection_is_reported(self):
        # n=104 은 첫 유한 상한: 0 만 관측하면 [0,0]; 이후 100 이 지배하면 하한 100
        xs = [0] * 104 + [100] * 300
        r = sequential_p75(xs, d=1, delta=0.05, support=(0, 200))
        self.assertEqual(r["status"], "empty")
        self.assertFalse(r["certifiable"])

    def test_sample_outside_support_is_invalid(self):
        with self.assertRaises(ValueError):
            sequential_p75([5, 60], d=1, delta=0.05, support=(0, 50))


class P75InvalidInput(unittest.TestCase):
    """잘못된 support·차원·확률·표본은 오류이고, 거부된 표본은 상태를 바꾸지 않는다."""

    def test_empty_or_nan_support_is_rejected(self):
        for support in [(10, 0), (math.nan, 5), (0, math.nan), (-math.inf, 5), (0, -math.inf),
                        (math.inf, math.inf), (None, 5)]:
            with self.assertRaises(ValueError, msg=support):
                sequential_p75([], d=1, delta=0.05, support=support)

    def test_unknown_upper_may_be_none_or_plus_inf(self):
        for hi in (None, math.inf):
            r = sequential_p75([1, 2], d=1, delta=0.05, support=(0, hi))
            self.assertEqual((r["hi"], r["status"], r["certifiable"]), (math.inf, "infinite", False))

    def test_degenerate_support_is_allowed(self):
        r = sequential_p75([3, 3], d=1, delta=0.05, support=(3, 3))
        self.assertEqual((r["lo"], r["hi"], r["status"], r["certifiable"]), (3, 3, "ok", True))

    def test_bad_dimension_or_probability_is_rejected(self):
        for d, delta in [(0, 0.05), (1.5, 0.05), (True, 0.05), (1, 0), (1, 1), (1, math.nan), (1, -0.1)]:
            with self.assertRaises(ValueError, msg=(d, delta)):
                sequential_p75([], d=d, delta=delta, support=(0, 1))

    def test_nonfinite_uncensored_sample_is_rejected(self):
        for bad in (math.nan, math.inf, -math.inf, "5"):
            with self.assertRaises((ValueError, TypeError), msg=bad):
                sequential_p75([bad] * 200, d=1, delta=0.05, support=(0, 100))
        with self.assertRaises(ValueError):
            sequential_p75([1, math.inf], d=1, delta=0.05, support=(0, None))

    def test_rejected_sample_does_not_advance_count(self):
        s = SequentialP75(d=1, delta=0.05, support=(0, 50))
        s.add(5)
        for bad in (math.nan, 60, -1):
            with self.assertRaises(ValueError):
                s.add(bad)
        r = s.result()
        self.assertEqual((r["n"], r["censored"], r["status"]), (1, 0, "ok"))
        s.add(None)
        self.assertEqual((s.result()["n"], s.result()["censored"]), (2, 1))


if __name__ == "__main__":
    unittest.main()
