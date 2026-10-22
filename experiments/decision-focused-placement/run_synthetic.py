"""synthetic-v1 benchmark 실행 CLI. 기존 output 디렉터리는 덮어쓰지 않는다."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fixtures import BENCHMARK_CONFIG, BENCHMARK_VERSION, FIXTURES  # noqa: E402
from regret import Placement, total_regret  # noqa: E402
from synthetic import POLICIES, _up, bound_status, build_placements, run_policy, true_p75  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

JSON_CONTRACT = ("strict JSON (allow_nan=False): a non-finite bound is null with a sibling *_status field "
                 "(finite|infinite|not_computed); probe_records[].batch null entries are timeout-censored samples")


def source_hashes():
    """재현용: 이 디렉터리 모든 .py 의 sha256."""
    out = {}
    for name in sorted(n for n in os.listdir(HERE) if n.endswith(".py")):
        with open(os.path.join(HERE, name), "rb") as f:
            out[name] = hashlib.sha256(f.read()).hexdigest()
    return out


def _regret_json(r):
    return {"status": r["status"], "candidate": r["candidate"], "upper": _up(r["upper"]),
            "upper_status": bound_status(r["upper"]),
            "certified": r["certified"], "lp_complete": r["lp_complete"],
            "competitors": [{"id": c["id"], "upper": _up(c["upper"]), "upper_status": bound_status(c["upper"]),
                             "method": c["method"]}
                            for c in r["competitors"]]}


def deterministic_checks(eps):
    """known-interval 검증: 통계 없이 정확 regret 만 확인한다."""
    out = []
    examples = [
        ("design_shared_cancellation", [Placement("p", [([1], 10)]), Placement("q", [([1], 12)])], [(0, 100)]),
        ("design_path_switch", [Placement("p", [([1], 10), ([0], 30)]), Placement("q", [([1], 12), ([0], 20)])],
         [(0, 40)]),
    ]
    for name, pls, box in examples:
        for cand in [p.id for p in pls]:
            out.append({"case": name, "box": "given", "requested_candidate": cand,
                        **_regret_json(total_regret(pls, box, epsilon=0, candidate=cand))})
    for fx in FIXTURES:
        pls = build_placements(fx)
        support = [tuple(a["support"]) for a in fx["arms"]]
        truth = [true_p75(a["dist"]) for a in fx["arms"]]
        out.append({"case": fx["name"], "box": "known_support",
                    **_regret_json(total_regret(pls, support, epsilon=eps))})
        out.append({"case": fx["name"], "box": "degenerate_true_p75",
                    **_regret_json(total_regret(pls, [(t, t) for t in truth], epsilon=eps))})
    return out


def summarize(runs):
    rows = []
    for fx in dict.fromkeys(r["fixture"] for r in runs):
        for pol in POLICIES:
            rs = [r for r in runs if r["fixture"] == fx and r["policy"] == pol]
            if not rs:
                continue
            gaps = [r["true_proxy_gap"] for r in rs if r["true_proxy_gap"] is not None]
            # 순서 통계용으로만 null(infinite/not_computed) 을 +∞ 로 본다.
            uppers = [r["regret_upper"] if r["regret_upper_status"] == "finite" else math.inf for r in rs]
            median = statistics.median(uppers)
            term = {}
            for r in rs:
                term[r["termination"]] = term.get(r["termination"], 0) + 1
            rows.append({
                "fixture": fx, "policy": pol, "runs": len(rs),
                "certification_rate": sum(r["certified"] for r in rs) / len(rs),
                "probe_count_mean": statistics.mean(r["probe_count"] for r in rs),
                "probe_count_median": statistics.median(r["probe_count"] for r in rs),
                "simulated_probe_cost_mean": statistics.mean(r["simulated_probe_cost"] for r in rs),
                "planner_wall_time_s_mean": statistics.mean(r["planner_wall_time_s"] for r in rs),
                "true_proxy_gap_mean": statistics.mean(gaps) if gaps else None,
                "true_proxy_gap_max": max(gaps) if gaps else None,
                "regret_upper_median": _up(median), "regret_upper_median_status": bound_status(median),
                "regret_upper_infinite_runs": sum(1 for r in rs if r["regret_upper_status"] == "infinite"),
                "regret_upper_not_computed_runs": sum(1 for r in rs if r["regret_upper_status"] == "not_computed"),
                "terminations": term,
            })
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
        for pol in POLICIES:
            for seed in config["seeds"]:
                if time.perf_counter() - started > config["max_total_seconds"]:
                    skipped.append({"fixture": fx["name"], "policy": pol, "seed": seed})
                    continue
                runs.append(run_policy(fx, pol, seed=seed, config=config))
    result = {
        "benchmark": BENCHMARK_VERSION,
        "claim_boundary": ("synthetic proxy-model validation only; simulated probe cost/time is not a "
                           "cluster saving; no end-to-end latency or novelty claim"),
        "json_contract": JSON_CONTRACT,
        "started_at": started_at,
        "environment": {"python": platform.python_version(), "argv": sys.argv},
        "source_files": source_hashes(),
        "config": config, "overrides": overrides, "fixtures": FIXTURES, "policies": list(POLICIES),
        "deterministic": deterministic_checks(config["epsilon"]),
        "runs": runs, "skipped_runs": skipped, "summary": summarize(runs),
        "total_wall_time_s": time.perf_counter() - started,
    }
    os.makedirs(args.output)
    with open(os.path.join(args.output, "results.json"), "w") as f:
        json.dump(result, f, indent=1, ensure_ascii=False, allow_nan=False)
    for row in result["summary"]:
        print(f"{row['fixture']:22s} {row['policy']:12s} cert={row['certification_rate']:.1f} "
              f"probes={row['probe_count_mean']:6.1f} cost={row['simulated_probe_cost_mean']:7.1f} "
              f"gap_max={row['true_proxy_gap_max']} upper_med={row['regret_upper_median']} "
              f"term={row['terminations']}")
    print(f"total_wall_time_s={result['total_wall_time_s']:.2f} skipped={len(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
