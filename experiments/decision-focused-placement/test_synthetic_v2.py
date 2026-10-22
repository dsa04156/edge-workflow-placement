"""synthetic-v2 공개 seam: run_policy(주입: policy_factory, fixture stream_key) 결과와 v2 CLI."""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

from synthetic import run_policy
from policies_v2 import POLICIES_V2, make_policy

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


def fixture(name, arms, placements, **extra):
    return {"name": name, "arms": arms, "placements": placements, "fallback": placements[0]["id"], **extra}


def cfg(**kw):
    c = dict(CONFIG)
    c.update(kw)
    return c


def strip_wall_time(r):
    r = copy.deepcopy(r)
    r.pop("planner_wall_time_s")
    for rec in r["probe_records"]:
        rec.pop("planner_time_used_before_s")
    return r


NO_ADV = fixture("no_adv", [uniform("x", 0, 40), uniform("y", 0, 40)],
                 [plan("p", ({"x": 1}, 0)), plan("q", ({"y": 1}, 15))])


class StreamKey(unittest.TestCase):
    def test_fixture_with_shared_stream_key_sees_same_samples_as_original(self):
        renamed = dict(NO_ADV, name="no_adv_renamed", stream_key="no_adv")
        a = run_policy(NO_ADV, "round_robin", seed=3, config=cfg(max_probes=4))
        b = run_policy(renamed, "round_robin", seed=3, config=cfg(max_probes=4))
        self.assertEqual([r["batch"] for r in a["probe_records"]], [r["batch"] for r in b["probe_records"]])

    def test_without_stream_key_a_renamed_fixture_draws_other_samples(self):
        renamed = dict(NO_ADV, name="no_adv_renamed")
        a = run_policy(NO_ADV, "round_robin", seed=3, config=cfg(max_probes=1))
        b = run_policy(renamed, "round_robin", seed=3, config=cfg(max_probes=1))
        self.assertNotEqual(a["probe_records"][0]["batch"], b["probe_records"][0]["batch"])


def run(fx, policy, seed=0, **kw):
    return run_policy(fx, policy, seed=seed, config=cfg(**kw), policy_factory=make_policy)


SHARED_PLUS_DIFF = fixture("shared_plus_diff", [uniform("z", 0, 100, cost=4.0), uniform("y", 0, 20)],
                           [plan("p", ({"z": 1, "y": 1}, 0)), plan("q", ({"z": 1}, 18))])
BASELINE = "cancel_common_round_robin"


class CancelCommonBaseline(unittest.TestCase):
    def test_global_common_term_is_never_measured_and_y_certifies(self):
        r = run(SHARED_PLUS_DIFF, BASELINE)
        self.assertEqual((r["certified"], r["termination"], r["selected"]), (True, "certified", "p"))
        self.assertGreater(r["probe_count"], 0)
        self.assertEqual(set(r["probe_sequence"]), {"y"})
        self.assertEqual(set(r["probe_rules"]), {BASELINE})
        self.assertEqual(r["arms"]["z"]["probes"], 0)

    def test_coordinate_common_to_only_some_paths_is_kept(self):
        # z 는 p 의 한 경로와 q 에만 있다: 전 경로 공통이 아니므로 측정 대상으로 남는다.
        fx = fixture("switch", [uniform("z", 0, 40), uniform("x", 20, 40)],
                     [plan("p", ({"z": 1}, 10), ({"x": 1}, 0)), plan("q", ({"z": 1}, 12), ({}, 20))])
        r = run(fx, BASELINE, max_probes=4)
        self.assertEqual(r["probe_sequence"], ["z", "x", "z", "x"])

    def test_coordinate_differing_only_in_third_placement_is_kept(self):
        # z 는 p·q 에서 계수 1, r 에서만 2: 전 배치 공통이 아니므로 남는다.
        fx = fixture("third", [uniform("z", 0, 40), uniform("y", 0, 40)],
                     [plan("p", ({"z": 1, "y": 1}, 0)), plan("q", ({"z": 1}, 18)), plan("r", ({"z": 2}, 0))])
        r = run(fx, BASELINE, max_probes=4)
        self.assertEqual(r["probe_sequence"], ["z", "y", "z", "y"])

    def test_all_common_terms_with_finite_support_certify_with_zero_probes(self):
        fx = fixture("all_common", [uniform("z", 0, 100), uniform("w", 0, 50)],
                     [plan("p", ({"z": 1, "w": 2}, 10)), plan("q", ({"z": 1, "w": 2}, 12))])
        r = run(fx, BASELINE)
        self.assertEqual((r["certified"], r["termination"], r["probe_count"], r["selected"]),
                         (True, "certified", 0, "p"))

    def test_all_common_terms_with_unknown_support_fall_back_to_round_robin(self):
        # 모든 좌표가 공통이어도 사용 primitive 의 무한 구간은 인증 gate 를 막는다: v1 공통 규칙대로 순환.
        fx = fixture("all_common_inf", [uniform("z", 0, 100, support=[0, None])],
                     [plan("p", ({"z": 1}, 10)), plan("q", ({"z": 1}, 12))])
        r = run(fx, BASELINE, max_probes=2)
        self.assertEqual((r["probe_sequence"], r["probe_rules"]), (["z", "z"], ["round_robin", "round_robin"]))

    def test_infinite_common_interval_is_still_measured(self):
        fx = copy.deepcopy(SHARED_PLUS_DIFF)
        fx["arms"][0]["support"] = [0, None]
        r = run(fx, BASELINE, max_probes=4)
        self.assertEqual(r["probe_sequence"][:2], ["z", "y"])

    def test_finite_common_term_stays_excluded_when_only_non_common_support_is_unknown(self):
        # review P2 재현: z 는 유한 공통항, y 만 support 상한 미상 -> z 를 복원하지 않는다.
        fx = copy.deepcopy(SHARED_PLUS_DIFF)
        fx["arms"][1]["support"] = [0, None]
        r = run(fx, BASELINE, max_probes=2)
        self.assertEqual((r["probe_sequence"], r["simulated_probe_cost"]), (["y", "y"], 2.0))
        self.assertEqual(r["probe_rules"], [BASELINE, BASELINE])

    def test_only_finite_common_terms_are_excluded_alongside_infinite_common_terms(self):
        # w 유한 공통, z 무한 공통, y 비공통: w 만 제외하고 z, y 를 원래 순서로 순환한다.
        fx = fixture("mixed_common", [uniform("w", 0, 50), uniform("z", 0, 100, support=[0, None]),
                                      uniform("y", 0, 20)],
                     [plan("p", ({"w": 1, "z": 1, "y": 1}, 0)), plan("q", ({"w": 1, "z": 1}, 18))])
        r = run(fx, BASELINE, max_probes=4)
        self.assertEqual(r["probe_sequence"], ["z", "y", "z", "y"])
        self.assertEqual(r["arms"]["w"]["probes"], 0)

    def test_rule_label_without_common_terms_matches_frozen_v2(self):
        # 공통항이 없고 무한 구간도 없으면 동결 v2 처럼 cancel_common_round_robin 라벨을 유지한다.
        r = run(NO_ADV, BASELINE, max_probes=2)
        self.assertEqual(r["probe_rules"], [BASELINE, BASELINE])


V1_FINAL = os.path.join(HERE, "..", "..", "artifacts", "decision-focused-placement", "synthetic-v1-final",
                        "results.json")


class V1Compatibility(unittest.TestCase):
    """동결 synthetic-v1-final 결과(같은 regret/p75/fixtures 소스)를 wall-time 필드만 빼고 재현한다."""

    def test_frozen_v1_runs_are_reproduced_with_and_without_policy_factory(self):
        with open(V1_FINAL) as f:
            frozen = json.load(f)
        fixtures = {fx["name"]: fx for fx in frozen["fixtures"]}
        for old in frozen["runs"]:
            fx = fixtures[old["fixture"]]
            for factory in (None, make_policy):
                new = run_policy(fx, old["policy"], seed=old["seed"], config=frozen["config"],
                                 policy_factory=factory)
                self.assertEqual(strip_wall_time(json.loads(json.dumps(new, allow_nan=False))),
                                 strip_wall_time(old), (old["fixture"], old["policy"], old["seed"]))


SWAP = {"p": "q", "q": "p"}


class LabelSwap(unittest.TestCase):
    """label-swap 은 배치 id p/q 만 바꾼다: 관측 prefix·인증·비용은 같고 선택 id 만 대응 변환된다."""

    def test_v2_fixture_set_matches_the_frozen_contract(self):
        from fixtures_v2 import BENCHMARK_CONFIG, FIXTURES
        from fixtures import FIXTURES as V1
        self.assertEqual(len(FIXTURES), 15)
        self.assertEqual(BENCHMARK_CONFIG["seeds"], list(range(10, 20)))
        by_name = {fx["name"]: fx for fx in FIXTURES}
        for v1 in V1:
            self.assertEqual(by_name[v1["name"]], v1)
        swaps = [fx for fx in FIXTURES if fx.get("label_swap_of")]
        self.assertEqual(sorted(fx["label_swap_of"] for fx in swaps),
                         sorted(v["name"] for v in V1 if v["name"] != "timeout_censoring"))
        for fx in swaps:
            orig = by_name[fx["label_swap_of"]]
            self.assertEqual(fx["stream_key"], orig["name"])
            self.assertEqual((fx["arms"], fx["fallback"]), (orig["arms"], "p"))
            self.assertEqual([SWAP[pl["id"]] for pl in fx["placements"]], [pl["id"] for pl in orig["placements"]])
            self.assertEqual([pl["paths"] for pl in fx["placements"]], [pl["paths"] for pl in orig["placements"]])
        keys = [fx.get("stream_key", fx["name"]) for fx in FIXTURES if fx["name"].startswith("three_way")]
        self.assertEqual(len(set(keys)), 3)

    def test_swapped_runs_match_original_up_to_selected_label(self):
        from fixtures_v2 import BENCHMARK_CONFIG, FIXTURES
        by_name = {fx["name"]: fx for fx in FIXTURES}
        fallback_cases = 0
        for fx in (f for f in FIXTURES if f.get("label_swap_of")):
            orig = by_name[fx["label_swap_of"]]
            for pol in POLICIES_V2:
                for seed in BENCHMARK_CONFIG["seeds"][:2]:
                    a = strip_wall_time(run_policy(orig, pol, seed=seed, config=BENCHMARK_CONFIG,
                                                   policy_factory=make_policy))
                    b = strip_wall_time(run_policy(fx, pol, seed=seed, config=BENCHMARK_CONFIG,
                                                   policy_factory=make_policy))
                    ctx = (fx["name"], pol, seed)
                    for key in ("probe_records", "probe_sequence", "certified", "termination", "regret_upper",
                                "probe_count", "simulated_probe_cost", "arms", "selection_basis"):
                        self.assertEqual(a[key], b[key], (key, *ctx))
                    if a["selection_basis"] == "fallback":  # 무관측 fallback 은 id 그대로 p: 대응 변환 아님
                        fallback_cases += 1
                        self.assertEqual((a["selected"], b["selected"]), ("p", "p"), ctx)
                    else:
                        self.assertEqual(b["selected"], SWAP[a["selected"]], ctx)
                        self.assertEqual(a["true_proxy_gap"], b["true_proxy_gap"], ctx)
        self.assertEqual(fallback_cases, 0)  # v2 의 유한 support fixture 에서는 fallback 이 일어나지 않는다


class CliV2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = os.path.join(cls.tmp.name, "run")
        cls.proc = cls.run_cli(cls.out, "--seeds", "2", "--max-probes", "5")
        with open(os.path.join(cls.out, "results.json")) as f:
            cls.res = json.load(f, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @staticmethod
    def run_cli(out, *extra):
        return subprocess.run([sys.executable, os.path.join(HERE, "run_synthetic_v2.py"), "--output", out, *extra],
                              capture_output=True, text=True, timeout=600)

    def test_run_matrix_and_provenance(self):
        res = self.res
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)
        self.assertEqual(res["benchmark"], "synthetic-v2")
        self.assertEqual(res["config"]["seeds"], [10, 11])
        self.assertEqual(res["policies"], list(POLICIES_V2))
        self.assertEqual(len(res["runs"]), 15 * 5 * 2)
        self.assertEqual(res["run_counts"], {"expected": 150, "completed": 150, "skipped": 0})
        self.assertEqual(res["skipped_runs"], [])
        self.assertLessEqual(res["started_at"], res["finished_at"])
        self.assertTrue(all("probe_records" in r for r in res["runs"]))
        self.assertIn("post-hoc", res["claim_boundary"])
        names = sorted(n for n in os.listdir(HERE) if n.endswith(".py"))
        self.assertEqual(sorted(res["source_files"]), names)
        for n in names:
            with open(os.path.join(HERE, n), "rb") as f:
                self.assertEqual(res["source_files"][n], hashlib.sha256(f.read()).hexdigest())

    def test_evaluator_truth_matches_hand_computed_values(self):
        truth = self.res["truth"]
        expect = {  # spec.md 수작업 수치 (shared_q_optimal 은 정정된 p102)
            "shared_q_optimal": ({"p": 102.0, "q": 93.0}, ["q"], 9.0),
            "three_way_p_optimal": ({"p": 30.0, "q": 38.0, "r": 33.0}, ["p"], 0.0),
            "three_way_q_optimal": ({"p": 40.0, "q": 23.0, "r": 33.0}, ["q"], 17.0),
            "three_way_r_optimal": ({"p": 50.0, "q": 58.0, "r": 33.0}, ["r"], 17.0),
            "shared_additive": ({"p": 90.0, "q": 93.0}, ["p"], 0.0),
            "shared_additive_label_swap": ({"q": 90.0, "p": 93.0}, ["q"], 3.0),
        }
        for name, (costs, best, gap) in expect.items():
            t = truth[name]
            self.assertEqual((t["true_costs"], t["optimal"], t["always_p_gap"]), (costs, best, gap), name)
        self.assertNotIn("truth", json.dumps(self.res["policies"]))

    def test_label_swap_check_reports_no_mismatch(self):
        chk = self.res["label_swap_check"]
        self.assertEqual(chk["mismatches"], [])
        self.assertEqual(chk["compared"], 5 * 5 * 2)

    def test_paired_cost_uses_only_seeds_certified_by_both(self):
        runs = {(r["fixture"], r["policy"], r["seed"]): r for r in self.res["runs"]}
        rows = self.res["paired_cost"]
        self.assertTrue(rows)
        for row in rows:
            seeds = [s for s in self.res["config"]["seeds"]
                     if runs[(row["fixture"], row["a"], s)]["certified"] and runs[(row["fixture"], row["b"], s)]["certified"]]
            self.assertEqual((row["seeds"], row["n"]), (seeds, len(seeds)), row)
            if not seeds:
                self.assertIsNone(row["mean_cost_diff_a_minus_b"])

    def test_refuses_to_overwrite(self):
        p = self.run_cli(self.out, "--seeds", "1", "--max-probes", "1")
        self.assertEqual(p.returncode, 2)
        self.assertIn("refusing", p.stderr)


if __name__ == "__main__":
    unittest.main()
