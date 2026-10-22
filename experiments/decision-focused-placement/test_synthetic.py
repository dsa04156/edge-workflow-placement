"""공개 seam (3): 정책 실행 → 선택/인증/비용/종료 JSON, 그리고 CLI."""
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest

from synthetic import POLICIES, run_policy

HERE = os.path.dirname(os.path.abspath(__file__))

CONFIG = {"epsilon": 1.0, "delta": 0.05, "batch_size": 20, "max_probes": 150,
          "planner_time_budget_s": 10.0, "initial_cost_metadata": 1.0, "cost_floor": 0.01,
          "contrast_round_robin_every": 5, "lp_combination_cap": 20000}


def uniform(aid, lo, hi, cost=1.0, support=None, timeout=None):
    return {"id": aid, "dist": {"kind": "uniform", "lo": lo, "hi": hi},
            "support": support if support is not None else [lo, hi],
            "probe_cost": cost, "timeout": timeout}


def plan(pid, *paths):
    return {"id": pid, "paths": [{"coeffs": c, "const": k} for c, k in paths]}


def fixture(name, arms, placements):
    return {"name": name, "arms": arms, "placements": placements,
            "fallback": placements[0]["id"]}


SHARED_ONLY = fixture("shared_only", [uniform("z", 0, 100)],
                      [plan("p", ({"z": 1}, 10)), plan("q", ({"z": 1}, 12))])
# p=z+y, q=z+18: z 는 상쇄되고 y 만 결정에 필요하다. z 구간이 가장 넓다.
SHARED_PLUS_DIFF = fixture("shared_plus_diff", [uniform("z", 0, 100, cost=4.0), uniform("y", 0, 20)],
                           [plan("p", ({"z": 1, "y": 1}, 0)), plan("q", ({"z": 1}, 18))])
NEAR_TIE = fixture("near_tie", [uniform("x", 0, 20), uniform("y", 0.5, 20.5)],
                   [plan("p", ({"x": 1}, 0)), plan("q", ({"y": 1}, 0))])


class StepClock:
    """호출마다 step 초 진행하는 결정적 시계."""

    def __init__(self, step):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


def cfg(**kw):
    c = dict(CONFIG)
    c.update(kw)
    return c


class PolicyRun(unittest.TestCase):
    def test_shared_cancellation_certifies_without_probes_for_every_policy(self):
        for pol in POLICIES:
            r = run_policy(SHARED_ONLY, pol, seed=0, config=CONFIG)
            self.assertEqual((r["certified"], r["termination"], r["probe_count"], r["selected"]),
                             (True, "certified", 0, "p"), pol)
            self.assertEqual(r["true_proxy_gap"], 0.0)
            self.assertEqual(r["regret_upper"], 0.0)

    def test_first_probe_choice_per_rule(self):
        # 같은 초기 cost metadata: width/cost·witness 는 가장 넓은 z, contrast 는 상쇄되지 않는 y
        first = {pol: run_policy(SHARED_PLUS_DIFF, pol, seed=1, config=cfg(max_probes=1))["probe_sequence"][0]
                 for pol in POLICIES}
        self.assertEqual(first, {"round_robin": "z", "width_cost": "z", "witness": "z", "contrast": "y"})

    def test_round_robin_cycles(self):
        r = run_policy(SHARED_PLUS_DIFF, "round_robin", seed=1, config=cfg(max_probes=4))
        self.assertEqual(r["probe_sequence"], ["z", "y", "z", "y"])

    def test_probe_budget_exhaustion_is_uncertified(self):
        r = run_policy(NEAR_TIE, "contrast", seed=2, config=cfg(max_probes=3))
        self.assertEqual((r["termination"], r["certified"], r["probe_count"]), ("budget_probes", False, 3))
        self.assertIn(r["selected"], ("p", "q"))
        self.assertGreater(r["regret_upper"], 1.0)
        self.assertEqual(r["simulated_probe_cost"], 3.0)

    def test_planner_time_budget_exhaustion(self):
        r = run_policy(NEAR_TIE, "witness", seed=2, config=cfg(planner_time_budget_s=0.0))
        self.assertEqual((r["termination"], r["certified"], r["probe_count"]), ("budget_planner_time", False, 0))

    def test_zero_planner_budget_is_uncertified_even_for_one_plan(self):
        fx = fixture("one", [uniform("x", 0, 40)], [plan("p", ({"x": 1}, 0))])
        r = run_policy(fx, "round_robin", seed=0, config=cfg(planner_time_budget_s=0.0))
        self.assertEqual((r["termination"], r["certified"], r["probe_count"]), ("budget_planner_time", False, 0))

    def test_budget_exhausted_during_regret_blocks_certification(self):
        # 시계가 호출마다 1초 진행: 첫 regret 계산이 0.5초 예산을 넘는다. 인증 판정 전에 예산을 본다.
        # simple 상한(40-50<0)만으로 인증되는 fixture 라 LP deadline 경로를 타지 않는다.
        fx = fixture("dominant", [uniform("x", 0, 40), uniform("y", 0, 40)],
                     [plan("p", ({"x": 1}, 0)), plan("q", ({"y": 1}, 50))])
        self.assertTrue(run_policy(fx, "round_robin", seed=0, config=CONFIG)["certified"])
        r = run_policy(fx, "round_robin", seed=0, config=cfg(planner_time_budget_s=0.5), clock=StepClock(1.0))
        self.assertEqual((r["termination"], r["certified"], r["probe_count"]), ("budget_planner_time", False, 0))

    def test_no_paid_probe_after_planner_budget_is_exhausted(self):
        hit = 0
        for budget in [x / 2 for x in range(1, 41)]:
            r = run_policy(NEAR_TIE, "witness", seed=2, config=cfg(planner_time_budget_s=budget, max_probes=10),
                           clock=StepClock(1.0))
            for rec in r["probe_records"]:
                self.assertLess(rec["planner_time_used_before_s"], budget, (budget, rec["index"]))
            self.assertEqual(len(r["probe_records"]), r["probe_count"])
            if r["termination"] == "budget_planner_time":
                hit += 1
                self.assertFalse(r["certified"])
                self.assertGreaterEqual(r["planner_wall_time_s"], budget)
        self.assertGreater(hit, 0)

    def test_censoring_is_never_certified(self):
        fx = fixture("cens", [uniform("x", 0, 40, timeout=5), uniform("y", 0, 40)],
                     [plan("p", ({"x": 1}, 0)), plan("q", ({"y": 1}, 15))])
        for pol in POLICIES:
            r = run_policy(fx, pol, seed=3, config=CONFIG)
            self.assertFalse(r["certified"], pol)
            self.assertEqual(r["termination"], "censored_uncertified", pol)

    def test_unknown_support_starts_infinite_and_uses_round_robin(self):
        fx = fixture("inf", [uniform("x", 0, 40, support=[0, None]), uniform("y", 0, 40, support=[0, None])],
                     [plan("p", ({"x": 1}, 0)), plan("q", ({"y": 1}, 15))])
        for pol in POLICIES:
            r = run_policy(fx, pol, seed=4, config=cfg(max_probes=4))
            self.assertEqual(r["probe_sequence"], ["x", "y", "x", "y"], pol)
            self.assertFalse(r["certified"])
            self.assertEqual((r["regret_upper"], r["regret_upper_status"]), (None, "infinite"))
            self.assertEqual((r["arms"]["x"]["hi"], r["arms"]["x"]["hi_status"]), (None, "infinite"))
            json.dumps(r, allow_nan=False)
            self.assertEqual(r["selection_basis"], "point_estimate")

    def test_infinite_without_observations_uses_fallback(self):
        fx = fixture("inf0", [uniform("x", 0, 40, support=[0, None])],
                     [plan("p", ({"x": 1}, 0)), plan("q", ({"x": 1}, 15))])
        fx["fallback"] = "q"  # 사전 검증된 고정 fallback
        r = run_policy(fx, "round_robin", seed=0, config=cfg(max_probes=0))
        self.assertEqual((r["selected"], r["selection_basis"], r["certified"]), ("q", "fallback", False))
        self.assertEqual((r["regret_upper"], r["regret_upper_status"]), (None, "infinite"))
        json.dumps(r, allow_nan=False)

    def test_identical_plans_certify_immediately(self):
        fx = fixture("twin", [uniform("x", 0, 40)],
                     [plan("a", ({"x": 1}, 3)), plan("b", ({"x": 1}, 3))])
        r = run_policy(fx, "contrast", seed=0, config=CONFIG)
        self.assertEqual((r["selected"], r["certified"], r["probe_count"]), ("a", True, 0))

    def test_single_plan_with_used_infinite_interval_is_not_certified(self):
        # 수학적으로는 one-plan regret=0 이지만, 사용되는 primitive 의 무한 구간은 runner 가 인증하지 않는다.
        fx = fixture("one_inf", [uniform("x", 0, 40, support=[0, None])], [plan("p", ({"x": 1}, 0))])
        r = run_policy(fx, "round_robin", seed=0, config=cfg(max_probes=0))
        self.assertEqual((r["certified"], r["termination"], r["selected"]), (False, "budget_probes", "p"))
        self.assertEqual(r["arms"]["x"]["status"], "infinite")

    def test_single_plan_with_finite_interval_certifies(self):
        fx = fixture("one", [uniform("x", 0, 40)], [plan("p", ({"x": 1}, 0))])
        r = run_policy(fx, "round_robin", seed=0, config=cfg(max_probes=0))
        self.assertEqual((r["certified"], r["termination"], r["probe_count"]), (True, "certified", 0))

    def test_unused_infinite_primitive_does_not_block_certification(self):
        fx = fixture("unused_inf", [uniform("z", 0, 100), uniform("w", 0, 40, support=[0, None])],
                     [plan("p", ({"z": 1}, 10)), plan("q", ({"z": 1}, 12))])
        for pol in POLICIES:
            r = run_policy(fx, pol, seed=0, config=CONFIG)
            self.assertEqual((r["certified"], r["termination"], r["probe_count"]), (True, "certified", 0), pol)
            self.assertEqual(r["arms"]["w"]["status"], "infinite")

    def test_unused_infinite_primitive_does_not_crash_acquisition(self):
        # NEAR_TIE 는 probe 가 필요하다: contrast 의 midpoint 계산이 미사용 무한 좌표를 유한 상수로 다뤄야 한다.
        fx = json.loads(json.dumps(NEAR_TIE))
        fx["name"] = "near_tie_unused_inf"
        fx["arms"].append(uniform("unused", 0, 40, support=[0, None]))
        for pol in POLICIES:
            r = run_policy(fx, pol, seed=0, config=cfg(max_probes=1))
            self.assertEqual((r["termination"], r["probe_count"]), ("budget_probes", 1), pol)
            self.assertIn(r["probe_sequence"][0], ("x", "y"), pol)
            self.assertEqual(r["arms"]["unused"]["probes"], 0, pol)
            json.dumps(r, allow_nan=False)

    def test_contrast_widths_ignore_infinite_coordinate_unused_by_witness_pair(self):
        # 세 번째 경쟁자만 쓰는 무한 좌표: b·q 계수는 0 이므로 W=0, 다른 좌표 W 는 유한이어야 한다.
        from synthetic import _contrast_widths, build_placements
        fx = fixture("third", [uniform("x", 0, 20), uniform("y", 0, 20), uniform("w", 0, 40, support=[0, None])],
                     [plan("p", ({"x": 1}, 0)), plan("q", ({"y": 1}, 0)), plan("r", ({"w": 1}, 0))])
        b, q, _ = build_placements(fx)
        w = _contrast_widths(b, q, [(0, 20), (1, 19), (0, math.inf)])
        self.assertEqual([float(v) for v in w], [20.0, 18.0, 0.0])

    def test_same_seed_is_reproducible_and_json_serializable(self):
        a = run_policy(SHARED_PLUS_DIFF, "width_cost", seed=5, config=cfg(max_probes=10))
        b = run_policy(SHARED_PLUS_DIFF, "width_cost", seed=5, config=cfg(max_probes=10))
        for r in (a, b):
            r.pop("planner_wall_time_s")
            for rec in r["probe_records"]:
                rec.pop("planner_time_used_before_s")  # 실제 측정 시간: 실행마다 다르다
        self.assertEqual(a, b)
        json.dumps(a, allow_nan=False)
        for key in ("selected", "certified", "termination", "regret_upper", "true_proxy_gap",
                    "probe_count", "simulated_probe_cost", "arms", "probe_sequence"):
            self.assertIn(key, a)

    def test_probe_records_preserve_observed_batches_and_simulated_cost(self):
        fx = fixture("cens_rec", [uniform("x", 0, 40, cost=3.0, timeout=20), uniform("y", 0, 40)],
                     [plan("p", ({"x": 1}, 0)), plan("q", ({"y": 1}, 15))])
        r = run_policy(fx, "round_robin", seed=3, config=cfg(batch_size=5))
        self.assertEqual(r["termination"], "censored_uncertified")
        self.assertEqual(r["regret_upper_status"], "finite")
        recs = r["probe_records"]
        self.assertEqual([rec["arm"] for rec in recs], r["probe_sequence"])
        self.assertEqual([rec["rule"] for rec in recs], r["probe_rules"])
        self.assertEqual(sum(rec["simulated_cost"] for rec in recs), r["simulated_probe_cost"])
        for rec in recs:
            self.assertEqual(len(rec["batch"]), 5)
            self.assertEqual(rec["cost_kind"], "simulated")
            self.assertEqual(rec["simulated_cost"], 3.0 if rec["arm"] == "x" else 1.0)
            self.assertEqual(rec["censored_positions"], [i for i, v in enumerate(rec["batch"]) if v is None])
        for arm, a in r["arms"].items():
            obs = [v for rec in recs if rec["arm"] == arm for v in rec["batch"]]
            self.assertEqual((len(obs), sum(v is None for v in obs)), (a["n"], a["censored"]))
        self.assertTrue(any(rec["censored_positions"] for rec in recs))
        json.dumps(r, allow_nan=False)

    def test_regret_not_computed_is_explicit(self):
        r = run_policy(NEAR_TIE, "witness", seed=2, config=cfg(planner_time_budget_s=0.0))
        self.assertEqual((r["regret_upper"], r["regret_upper_status"]), (None, "not_computed"))
        json.dumps(r, allow_nan=False)

    def test_shared_plus_diff_contrast_certifies_with_small_true_gap(self):
        r = run_policy(SHARED_PLUS_DIFF, "contrast", seed=0, config=CONFIG)
        self.assertTrue(r["certified"])
        self.assertEqual(r["selected"], "p")  # 참 p75: p=90, q=93
        self.assertLessEqual(r["true_proxy_gap"], 1.0)


class Cli(unittest.TestCase):
    def run_cli(self, out, *extra):
        return subprocess.run([sys.executable, os.path.join(HERE, "run_synthetic.py"), "--output", out,
                               *extra], capture_output=True, text=True, timeout=600)

    def test_cli_writes_results_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "run")
            p = self.run_cli(out, "--seeds", "1", "--max-probes", "5")
            self.assertEqual(p.returncode, 0, p.stderr)
            with open(os.path.join(out, "results.json")) as f:
                res = json.load(f, parse_constant=self.fail)  # Infinity/NaN token 금지
            self.assertEqual(res["config"]["seeds"], [0])
            self.assertEqual(len(res["runs"]), len(res["fixtures"]) * len(POLICIES))
            self.assertIn("deterministic", res)
            self.assertIn("summary", res)
            self.assertIn("synthetic", res["claim_boundary"])
            names = sorted(n for n in os.listdir(HERE) if n.endswith(".py"))
            self.assertEqual(sorted(res["source_files"]), names)
            for n in names:
                with open(os.path.join(HERE, n), "rb") as f:
                    self.assertEqual(res["source_files"][n], hashlib.sha256(f.read()).hexdigest())
            self.assertTrue(all("probe_records" in r for r in res["runs"]))
            p2 = self.run_cli(out, "--seeds", "1", "--max-probes", "5")
            self.assertNotEqual(p2.returncode, 0)


if __name__ == "__main__":
    unittest.main()
