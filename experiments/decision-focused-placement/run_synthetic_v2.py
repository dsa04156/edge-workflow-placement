"""synthetic-v2 benchmark 실행 CLI. 기존 output 디렉터리는 덮어쓰지 않는다."""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fixtures_v2 import BENCHMARK_CONFIG, BENCHMARK_VERSION, FIXTURES  # noqa: E402
from policies_v2 import CANCEL_COMMON, POLICIES_V2, make_policy  # noqa: E402
from regret import cost_at, total_regret  # noqa: E402
from run_synthetic import JSON_CONTRACT, _regret_json, source_hashes  # noqa: E402
from synthetic import _up, bound_status, build_placements, run_policy, true_p75  # noqa: E402

# 비용 절감 비교 쌍 (a, b): 양쪽 모두 인증한 seed 에서만 짝지어 계산한다.
PAIRS = (("contrast", CANCEL_COMMON), ("contrast", "round_robin"), (CANCEL_COMMON, "round_robin"))
# label-swap 원본과 같아야 하는 결과 필드 (선택 id 는 별도로 대응 변환해 비교한다)
SWAP_INVARIANT = ("probe_records", "probe_sequence", "probe_rules", "certified", "termination", "regret_upper",
                  "regret_upper_status", "probe_count", "simulated_probe_cost", "arms", "selection_basis")
ID_SWAP = {"p": "q", "q": "p"}


def truth(fx):
    """평가자 전용: 참 p75 에서의 배치 비용, 최적 배치(동률 포함), 항상-p 선택의 gap."""
    pls = build_placements(fx)
    theta = [true_p75(a["dist"]) for a in fx["arms"]]
    costs = {p.id: cost_at(p, theta) for p in pls}
    best = min(costs.values())
    return {"true_costs": {k: float(v) for k, v in costs.items()},
            "optimal": [k for k, v in costs.items() if v == best],
            "always_p_gap": float(costs["p"] - best)}


def deterministic_checks(eps):
    out = []
    for fx in FIXTURES:
        pls = build_placements(fx)
        support = [tuple(a["support"]) for a in fx["arms"]]
        theta = [true_p75(a["dist"]) for a in fx["arms"]]
        out.append({"case": fx["name"], "box": "known_support", **_regret_json(total_regret(pls, support, epsilon=eps))})
        out.append({"case": fx["name"], "box": "degenerate_true_p75",
                    **_regret_json(total_regret(pls, [(t, t) for t in theta], epsilon=eps))})
    return out


def _no_wall_time(key, value):
    """probe_records 의 planner_time_used_before_s 는 실제 측정 시간이라 비교에서 뺀다."""
    if key != "probe_records":
        return value
    return [{k: v for k, v in rec.items() if k != "planner_time_used_before_s"} for rec in value]


def label_swap_check(runs):
    """swap fixture 의 각 정책·seed 결과를 원본과 비교한다. 독립 표본으로 합산하지 않는다."""
    by = {(r["fixture"], r["policy"], r["seed"]): r for r in runs}
    mismatches, fallback_cases, compared = [], [], 0
    for fx in FIXTURES:
        if not fx.get("label_swap_of"):
            continue
        for (name, pol, seed), b in by.items():
            if name != fx["name"] or (fx["label_swap_of"], pol, seed) not in by:
                continue
            a = by[(fx["label_swap_of"], pol, seed)]
            compared += 1
            ctx = {"fixture": name, "policy": pol, "seed": seed}
            diff = [k for k in SWAP_INVARIANT if _no_wall_time(k, a[k]) != _no_wall_time(k, b[k])]
            if a["selection_basis"] == "fallback":  # 무관측 fallback 은 id 그대로(p): 대응 변환 대상이 아니다
                fallback_cases.append({**ctx, "selected": [a["selected"], b["selected"]]})
            elif b["selected"] != ID_SWAP.get(a["selected"]) or a["true_proxy_gap"] != b["true_proxy_gap"]:
                diff.append("selected")
            if diff:
                mismatches.append({**ctx, "fields": diff})
    return {"compared": compared, "mismatches": mismatches, "fallback_cases": fallback_cases}


def summarize(runs, truths):
    rows = []
    for fx in dict.fromkeys(r["fixture"] for r in runs):
        for pol in POLICIES_V2:
            rs = [r for r in runs if r["fixture"] == fx and r["policy"] == pol]
            if not rs:
                continue
            gaps = [r["true_proxy_gap"] for r in rs if r["true_proxy_gap"] is not None]
            uppers = [r["regret_upper"] if r["regret_upper_status"] == "finite" else math.inf for r in rs]
            median = statistics.median(uppers)
            term, sel = {}, {}
            for r in rs:
                term[r["termination"]] = term.get(r["termination"], 0) + 1
                sel[str(r["selected"])] = sel.get(str(r["selected"]), 0) + 1
            rows.append({
                "fixture": fx, "policy": pol, "runs": len(rs),
                "certification_rate": sum(r["certified"] for r in rs) / len(rs),
                "terminations": term, "selected_counts": sel,
                "optimal_selected_rate": sum(r["selected"] in truths[fx]["optimal"] for r in rs) / len(rs),
                "true_proxy_gap_mean": statistics.mean(gaps) if gaps else None,
                "true_proxy_gap_max": max(gaps) if gaps else None,
                "probe_count_mean": statistics.mean(r["probe_count"] for r in rs),
                "probe_count_median": statistics.median(r["probe_count"] for r in rs),
                "simulated_probe_cost_mean": statistics.mean(r["simulated_probe_cost"] for r in rs),
                "planner_wall_time_s_mean": statistics.mean(r["planner_wall_time_s"] for r in rs),
                "regret_upper_median": _up(median), "regret_upper_median_status": bound_status(median),
            })
    return rows


def paired_cost(runs, seeds):
    """가상 획득 비용 비교: 양쪽 정책이 모두 인증한 seed 만 짝짓는다. 미인증의 적은 지출은 개선이 아니다."""
    by = {(r["fixture"], r["policy"], r["seed"]): r for r in runs}
    rows = []
    for fx in dict.fromkeys(r["fixture"] for r in runs):
        for a, b in PAIRS:
            both = [s for s in seeds if (fx, a, s) in by and (fx, b, s) in by
                    and by[(fx, a, s)]["certified"] and by[(fx, b, s)]["certified"]]
            ca = [by[(fx, a, s)]["simulated_probe_cost"] for s in both]
            cb = [by[(fx, b, s)]["simulated_probe_cost"] for s in both]
            rows.append({"fixture": fx, "a": a, "b": b, "seeds": both, "n": len(both),
                         "mean_cost_a": statistics.mean(ca) if both else None,
                         "mean_cost_b": statistics.mean(cb) if both else None,
                         "mean_cost_diff_a_minus_b": statistics.mean(x - y for x, y in zip(ca, cb)) if both else None,
                         "a_cheaper_seeds": sum(x < y for x, y in zip(ca, cb)),
                         "b_cheaper_seeds": sum(x > y for x, y in zip(ca, cb))})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", required=True)
    ap.add_argument("--seeds", type=int, default=None, help="검증용: 앞의 N 개 seed 만 실행")
    ap.add_argument("--max-probes", type=int, default=None, help="검증용: probe 예산 override")
    args = ap.parse_args(argv)
    if os.path.exists(args.output):
        print(f"refusing to overwrite existing output: {args.output}", file=sys.stderr)
        return 2
    config = copy.deepcopy(BENCHMARK_CONFIG)
    overrides = {}
    if args.seeds is not None:
        config["seeds"] = config["seeds"][: args.seeds]
        overrides["seeds"] = args.seeds
    if args.max_probes is not None:
        config["max_probes"] = args.max_probes
        overrides["max_probes"] = args.max_probes

    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    runs, skipped = [], []
    for fx in FIXTURES:
        for pol in POLICIES_V2:
            for seed in config["seeds"]:
                if time.perf_counter() - started > config["max_total_seconds"]:
                    skipped.append({"fixture": fx["name"], "policy": pol, "seed": seed})
                    continue
                runs.append(run_policy(fx, pol, seed=seed, config=config, policy_factory=make_policy))
    finished_at = datetime.now(timezone.utc).isoformat()
    truths = {fx["name"]: truth(fx) for fx in FIXTURES}
    result = {
        "benchmark": BENCHMARK_VERSION,
        "claim_boundary": ("synthetic proxy-model validation only; post-hoc follow-up designed after seeing "
                           "synthetic-v1; simulated probe cost/time is not a cluster saving; the budget is an equal "
                           "probe-count cap, not an equal-cost cap; no end-to-end latency or novelty claim"),
        "json_contract": JSON_CONTRACT,
        "started_at": started_at, "finished_at": finished_at,
        "environment": {"python": platform.python_version(), "argv": sys.argv},
        "source_files": source_hashes(),
        "config": config, "overrides": overrides, "fixtures": FIXTURES, "policies": list(POLICIES_V2),
        "truth": truths,  # 평가자 전용: 정책에는 공개되지 않는다
        "deterministic": deterministic_checks(config["epsilon"]),
        "run_counts": {"expected": len(FIXTURES) * len(POLICIES_V2) * len(config["seeds"]),
                       "completed": len(runs), "skipped": len(skipped)},
        "runs": runs, "skipped_runs": skipped,
        "label_swap_check": label_swap_check(runs),
        "summary": summarize(runs, truths),
        "paired_cost": paired_cost(runs, config["seeds"]),
        "total_wall_time_s": time.perf_counter() - started,
    }
    os.makedirs(args.output)
    with open(os.path.join(args.output, "results.json"), "w") as f:
        json.dump(result, f, indent=1, ensure_ascii=False, allow_nan=False)
    for row in result["summary"]:
        print(f"{row['fixture']:28s} {row['policy']:26s} cert={row['certification_rate']:.1f} "
              f"opt={row['optimal_selected_rate']:.1f} probes={row['probe_count_mean']:6.1f} "
              f"cost={row['simulated_probe_cost_mean']:7.1f} gap_max={row['true_proxy_gap_max']} "
              f"term={row['terminations']}")
    chk = result["label_swap_check"]
    print(f"runs={result['run_counts']} swap_compared={chk['compared']} swap_mismatches={len(chk['mismatches'])} "
          f"total_wall_time_s={result['total_wall_time_s']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
