"""공개 seam (1): 비용모형 + 전체 regret 결과.

기대값은 설계 문서의 손계산 예제에서 온 literal이다.
"""
import math
import unittest
from fractions import Fraction

from regret import Placement, pairwise_regret_upper, total_regret


def P(pid, *paths):
    """paths: (coeffs, const) 쌍들."""
    return Placement(pid, [(list(a), b) for a, b in paths])


class SharedAdditiveCancellation(unittest.TestCase):
    # p=z+10, q=z+12, z in [0,100]
    def setUp(self):
        self.p = P("p", ([1], 10))
        self.q = P("q", ([1], 12))
        self.box = [(0, 100)]

    def test_pairwise_exact_keeps_shared_parameter(self):
        self.assertEqual(pairwise_regret_upper(self.p, self.q, self.box)["upper"], Fraction(-2))
        self.assertEqual(pairwise_regret_upper(self.q, self.p, self.box)["upper"], Fraction(2))

    def test_total_regret_of_p_is_zero_and_certifies(self):
        r = total_regret([self.p, self.q], self.box, epsilon=0)
        self.assertEqual(r["candidate"], "p")
        self.assertEqual(r["upper"], Fraction(0))
        self.assertTrue(r["certified"])

    def test_simple_box_bound_is_98(self):
        r = total_regret([self.p, self.q], self.box, epsilon=0, candidate="p", lp=False)
        self.assertEqual(r["upper"], Fraction(98))
        self.assertFalse(r["certified"])


class CriticalPathSwitch(unittest.TestCase):
    # p=max(z+10,30), q=max(z+12,20), z in [0,40]; D(p,q)=10, D(q,p)=2
    def setUp(self):
        self.p = P("p", ([1], 10), ([0], 30))
        self.q = P("q", ([1], 12), ([0], 20))
        self.box = [(0, 40)]

    def test_pairwise_literals(self):
        self.assertEqual(pairwise_regret_upper(self.p, self.q, self.box)["upper"], Fraction(10))
        self.assertEqual(pairwise_regret_upper(self.q, self.p, self.box)["upper"], Fraction(2))

    def test_default_candidate_rule_picks_p_although_q_certifies(self):
        # b = argmin L(.;u): L(p;u)=50 < L(q;u)=52, but R(p)=10 > R(q)=2.
        r = total_regret([self.p, self.q], self.box, epsilon=2)
        self.assertEqual((r["candidate"], r["upper"], r["certified"]), ("p", Fraction(10), False))
        r = total_regret([self.p, self.q], self.box, epsilon=2, candidate="q")
        self.assertEqual((r["upper"], r["certified"]), (Fraction(2), True))

    def test_total_regret_of_p_is_10(self):
        r = total_regret([self.p, self.q], self.box, epsilon=2, candidate="p")
        self.assertEqual(r["upper"], Fraction(10))
        self.assertFalse(r["certified"])


class InteriorMaximum(unittest.TestCase):
    """box 꼭짓점 목적 평가만으로는 과소평가되는 사례."""

    def test_one_dimensional_kink(self):
        # p=z, q=max(2z,10), z in [0,10]: max(p-q)=-5 at z=5; box ends give -10
        p, q = P("p", ([1], 0)), P("q", ([2], 0), ([0], 10))
        self.assertEqual(pairwise_regret_upper(p, q, [(0, 10)])["upper"], Fraction(-5))

    def test_two_dimensional_diagonal(self):
        # p=x+y, q=max(2x+1,2y+1), x in [0,10], y in [2,4]: max = -1 on x=y; corners give <= -3
        p = P("p", ([1, 1], 0))
        q = P("q", ([2, 0], 1), ([0, 2], 1))
        r = pairwise_regret_upper(p, q, [(0, 10), (2, 4)])
        self.assertEqual((r["upper"], r["method"]), (Fraction(-1), "lp_exact"))


class EdgeCases(unittest.TestCase):
    def setUp(self):
        self.p = P("p", ([1], 10))
        self.q = P("q", ([1], 12))

    def test_invalid_inputs_raise(self):
        with self.assertRaises(ValueError):
            P("bad", ([-1], 0))
        with self.assertRaises(ValueError):
            Placement("nopath", [])
        with self.assertRaises(ValueError):
            total_regret([], [(0, 1)], epsilon=0)
        with self.assertRaises(ValueError):
            total_regret([self.p, P("p", ([1], 3))], [(0, 1)], epsilon=0)
        with self.assertRaises(ValueError):
            total_regret([self.p, self.q], [(0, 1), (0, 1)], epsilon=0)

    def test_empty_uncertainty_set_is_not_certified(self):
        r = total_regret([self.p, self.q], [(5, 4)], epsilon=100)
        self.assertEqual(r["status"], "empty_uncertainty_set")
        self.assertFalse(r["certified"])

    def test_unbounded_box_is_conservative(self):
        r = total_regret([self.p, self.q], [(0, None)], epsilon=100, candidate="p")
        self.assertEqual(r["status"], "unbounded")
        self.assertEqual(r["upper"], math.inf)
        self.assertFalse(r["certified"])

    def test_unbounded_unused_coordinate_keeps_exact_lp(self):
        # w 는 p,q 어디에도 쓰이지 않는다: 무한 상한이어도 공유 z 상쇄는 정확히 계산된다.
        p, q = P("p", ([1, 0], 10)), P("q", ([1, 0], 12))
        r = pairwise_regret_upper(p, q, [(0, 100), (0, None)])
        self.assertEqual((r["upper"], r["method"]), (Fraction(-2), "lp_exact"))
        r = total_regret([p, q], [(0, 100), (0, None)], epsilon=0)
        self.assertEqual((r["candidate"], r["upper"], r["certified"]), ("p", Fraction(0), True))

    def test_single_plan_unbounded_box_has_zero_regret(self):
        # 하위 solver 의 one-plan zero regret 은 타당하다(인증 금지는 runner 계약).
        r = total_regret([self.p], [(0, None)], epsilon=0)
        self.assertEqual((r["upper"], r["certified"]), (Fraction(0), True))

    def test_lp_budget_keeps_simple_upper(self):
        r = total_regret([self.p, self.q], [(0, 100)], epsilon=0, candidate="p", combination_cap=1)
        self.assertEqual(r["upper"], Fraction(98))
        self.assertFalse(r["lp_complete"])
        self.assertFalse(r["certified"])

    def test_combination_cap_counts_all_paths_of_the_pair(self):
        # 1차 review 예제: 경로마다 C(3,2)=3, 쌍 전체 6 조합. cap=3 이면 LP 를 시작하지 않는다.
        p = Placement("p", [([1], 0), ([0], 10)])
        q = Placement("q", [([1], 1)])
        r = pairwise_regret_upper(p, q, [(0, 10)], combination_cap=3)
        self.assertEqual((r["upper"], r["method"]), (Fraction(9), "simple_lp_combination_cap"))
        r = pairwise_regret_upper(p, q, [(0, 10)], combination_cap=6)
        self.assertEqual((r["upper"], r["method"]), (Fraction(9), "lp_exact"))

    def test_lp_deadline_in_past_keeps_simple_upper(self):
        r = total_regret([self.p, self.q], [(0, 100)], epsilon=0, candidate="p", deadline=0.0)
        self.assertEqual(r["upper"], Fraction(98))
        self.assertFalse(r["certified"])

    def test_identical_plans_have_zero_regret(self):
        twin = P("twin", ([1], 10))
        r = total_regret([self.p, twin], [(0, 100)], epsilon=0)
        self.assertEqual((r["candidate"], r["upper"], r["certified"]), ("p", Fraction(0), True))


if __name__ == "__main__":
    unittest.main()
