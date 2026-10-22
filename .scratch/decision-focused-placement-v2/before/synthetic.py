"""합성 fixture 에서 측정 선택 규칙을 같은 전체-regret 종료 조건으로 실행한다.

정책은 Observation view 만 본다: 배치/경로, 현재 구간, 과거 probe 의 관측 비용(없으면 모든
arm 에 동일한 초기 cost metadata). 참 분포·참 p75·probe 의 참 비용·미래 표본은 평가자만 쓴다.
probe 비용과 시간은 모두 simulated 값이며 클러스터 측정이 아니다.

반환값은 그대로 JSON(allow_nan=False)으로 쓸 수 있다: 비유한 상한은 null + *_status 필드.
probe_records 는 probe 별 관측 batch(None=검열)·검열 위치·simulated 비용을 보존한다.
"""
from __future__ import annotations

import math
import random
import time
from fractions import Fraction

from p75 import SequentialP75
from regret import INF, Placement, as_fraction, cost_at, total_regret

POLICIES = ("round_robin", "width_cost", "witness", "contrast")


# ---------------------------------------------------------------- fixture / truth (평가자 전용)

def _vector(coeffs, arm_ids):
    unknown = set(coeffs) - set(arm_ids)
    if unknown:
        raise ValueError(f"unknown arms in coefficients: {sorted(unknown)}")
    return [coeffs.get(a, 0) for a in arm_ids]


def build_placements(fixture):
    arm_ids = [a["id"] for a in fixture["arms"]]
    return [Placement(p["id"], [(_vector(path["coeffs"], arm_ids), path["const"]) for path in p["paths"]])
            for p in fixture["placements"]]


def true_p75(dist):
    if dist["kind"] == "uniform":
        return as_fraction(dist["lo"]) + Fraction(3, 4) * (as_fraction(dist["hi"]) - as_fraction(dist["lo"]))
    if dist["kind"] == "discrete":
        acc = Fraction(0)
        for v, pr in sorted(zip(dist["values"], dist["probs"])):
            acc += as_fraction(pr)
            if acc >= Fraction(3, 4):
                return as_fraction(v)
        raise ValueError("discrete probabilities do not reach 0.75")
    raise ValueError(f"unknown distribution {dist['kind']!r}")


class _ArmStream:
    """arm 별 독립 seeded stream. 정책이 요청한 표본만 순서대로 공개된다."""

    def __init__(self, seed, fixture_name, arm):
        self.rng = random.Random(f"{seed}/{fixture_name}/{arm['id']}")
        self.dist = arm["dist"]
        self.timeout = arm.get("timeout")

    def draw(self):
        d = self.dist
        if d["kind"] == "uniform":
            x = self.rng.uniform(d["lo"], d["hi"])
        else:
            x = self.rng.choices(d["values"], weights=d["probs"])[0]
        if self.timeout is not None and x > self.timeout:
            return None  # timeout: 검열
        return x


# ---------------------------------------------------------------- policy view

class Observation:
    def __init__(self, arm_ids, placements, intervals, cost_obs, regret, probe_index, config):
        self.arm_ids = arm_ids
        self.placements = placements
        self.intervals = intervals  # [(lo, hi)] (hi 는 math.inf 가능)
        self._cost_obs = cost_obs
        self.regret = regret
        self.probe_index = probe_index  # 이번 probe 의 1-based 번호
        self.config = config

    def cost_estimate(self, j):
        obs = self._cost_obs[j]
        c = sum(obs) / len(obs) if obs else self.config["initial_cost_metadata"]
        return max(c, self.config["cost_floor"])

    def used(self, j):
        return any(p.uses(j) for p in self.placements)

    def any_infinite(self):
        return any(hi == INF for j, (_, hi) in enumerate(self.intervals) if self.used(j))


def _argmax(scores):
    """최댓값 index, 동률은 낮은 index(고정 arm ID 순서). 모두 0 이하면 None."""
    best = max(range(len(scores)), key=lambda j: (scores[j], -j))
    return best if scores[best] > 0 else None


def _width(iv):
    lo, hi = iv
    return as_fraction(hi) - as_fraction(lo)


def _witness_pair(obs):
    reg = obs.regret
    b = next(p for p in obs.placements if p.id == reg["candidate"])
    order = [p.id for p in obs.placements]
    comps = [c for c in reg["competitors"] if c["id"] != b.id]
    if not comps:
        return b, None
    w = max(comps, key=lambda c: (c["upper"], -order.index(c["id"])))
    return b, next(p for p in obs.placements if p.id == w["id"])


def _contrast_widths(b, q, intervals):
    """W_j: 다른 항목을 midpoint 에 고정한 1-좌표 차이 g_j 의 변동폭 (Fraction).

    b·q 어느 쪽도 쓰지 않는 좌표는 계수가 0 이므로 하한(유한 상수)에 고정한다(W_j=0).
    다른 배치가 그 좌표를 쓰더라도, 상한이 +∞ 이어도 midpoint 산술에 들어가지 않는다.
    """
    box = [(as_fraction(lo), as_fraction(hi)) if (b.uses(j) or q.uses(j)) else (as_fraction(lo),) * 2
           for j, (lo, hi) in enumerate(intervals)]
    mid = [(lo + hi) / 2 for lo, hi in box]
    out = []
    for j, (lo, hi) in enumerate(box):
        def lines(p):
            return [(a[j], c + sum(a[k] * mid[k] for k in range(len(mid)) if k != j)) for a, c in p.paths]

        lb, lq = lines(b), lines(q)
        pts = {lo, hi}
        for ls in (lb, lq):  # 각 배치 내부 경로끼리의 교차점
            for i in range(len(ls)):
                for k in range(i + 1, len(ls)):
                    (s1, c1), (s2, c2) = ls[i], ls[k]
                    if s1 != s2:
                        x = (c2 - c1) / (s1 - s2)
                        if lo < x < hi:
                            pts.add(x)
        g = [max(s * x + c for s, c in lb) - max(s * x + c for s, c in lq) for x in pts]
        out.append(max(g) - min(g))
    return out


class _Policy:
    def __init__(self, name):
        if name not in POLICIES:
            raise ValueError(f"unknown policy {name!r}")
        self.name = name
        self.rr = 0

    def round_robin(self, obs):
        used = [j for j in range(len(obs.arm_ids)) if obs.used(j)] or list(range(len(obs.arm_ids)))
        j = used[self.rr % len(used)]
        self.rr += 1
        return j, "round_robin"

    def choose(self, obs):
        if self.name == "round_robin" or obs.any_infinite():
            return self.round_robin(obs)
        n = len(obs.arm_ids)
        if self.name == "width_cost":
            scores = [_width(obs.intervals[j]) / as_fraction(obs.cost_estimate(j)) if obs.used(j) else 0
                      for j in range(n)]
        elif self.name == "witness":
            b, q = _witness_pair(obs)
            scores = [_width(obs.intervals[j]) if (b.uses(j) or (q is not None and q.uses(j))) else 0
                      for j in range(n)]
        else:  # contrast
            if obs.probe_index % obs.config["contrast_round_robin_every"] == 0:
                return self.round_robin(obs)
            b, q = _witness_pair(obs)
            if q is None:
                return self.round_robin(obs)
            w = _contrast_widths(b, q, obs.intervals)
            scores = [w[j] / as_fraction(obs.cost_estimate(j)) for j in range(n)]
        j = _argmax(scores)
        if j is None:
            return self.round_robin(obs)
        return j, self.name


# ---------------------------------------------------------------- runner

def _up(x):
    """Fraction 상한을 바깥 방향으로 float 변환. JSON 계약: +∞ 는 null(상태 필드는 bound_status)."""
    if x == INF:
        return None
    f = float(x)
    return math.nextafter(f, INF) if Fraction(f) < x else f


def bound_status(x):
    """JSON 계약의 수치 상태: None=계산 안 함, +∞=infinite, 그 외 finite. NaN 은 계약 위반."""
    if x is None:
        return "not_computed"
    if x != x:
        raise ValueError("NaN bound")
    return "infinite" if x == INF else "finite"


def run_policy(fixture, policy, *, seed, config, clock=time.perf_counter):
    """clock 은 planner 시간 측정 경계이다(테스트에서 결정적 시계로 대체 가능)."""
    arm_ids = [a["id"] for a in fixture["arms"]]
    d = len(arm_ids)
    placements = build_placements(fixture)
    ids = [p.id for p in placements]
    if fixture["fallback"] not in ids:
        raise ValueError("fallback is not a feasible placement")
    streams = [_ArmStream(seed, fixture["name"], a) for a in fixture["arms"]]
    seqs = [SequentialP75(d=d, delta=config["delta"], support=tuple(a["support"])) for a in fixture["arms"]]
    raw = [[] for _ in arm_ids]
    cost_obs = [[] for _ in arm_ids]
    pol = _Policy(policy)
    eps = as_fraction(config["epsilon"])
    planner_used = 0.0
    probes, sim_cost = 0, 0.0
    sequence, rules, records = [], [], []
    reg = None
    budget = config["planner_time_budget_s"]

    # planner 예산 checkpoint: 시작 전, regret 계산 후 인증 판정 전, acquisition 후 probe 전.
    # 계산 도중의 CPU 작업은 LP deadline(64 조합마다) 외에는 선점하지 않는다.
    while True:
        if budget <= 0:
            termination = "budget_planner_time"
            break
        t0 = clock()
        intervals = [(s.result()["lo"], s.result()["hi"]) for s in seqs]
        statuses = [s.status for j, s in enumerate(seqs) if any(p.uses(j) for p in placements)]
        if "empty" in statuses:
            termination = "empty_interval"
            reg = None
            planner_used += clock() - t0
            break
        box = [(lo, None if hi == INF else hi) for lo, hi in intervals]
        remaining = budget - planner_used
        reg = total_regret(placements, box, epsilon=eps, deadline=t0 + max(remaining, 0.0),
                           combination_cap=config["lp_combination_cap"], clock=clock)
        planner_used += clock() - t0
        if "censored" in statuses:
            termination = "censored_uncertified"
            break
        if planner_used >= budget:
            termination = "budget_planner_time"
            break
        # 사용되는 primitive 에 무한 구간이 있으면 regret 상한이 유한해도 인증하지 않는다.
        if reg["certified"] and "infinite" not in statuses:
            termination = "certified"
            break
        if probes >= config["max_probes"]:
            termination = "budget_probes"
            break
        t1 = clock()
        obs = Observation(arm_ids, placements, intervals, cost_obs, reg, probes + 1, config)
        j, rule = pol.choose(obs)
        planner_used += clock() - t1
        if planner_used >= budget:
            termination = "budget_planner_time"  # 소진 후에는 유료 probe 를 실행하지 않는다
            break
        records.append({"index": probes + 1, "arm": arm_ids[j], "rule": rule,
                        "planner_time_used_before_s": planner_used})
        # simulated probe (planner 시간에 포함하지 않음)
        batch = [streams[j].draw() for _ in range(config["batch_size"])]
        raw[j].extend(batch)
        cost = float(fixture["arms"][j]["probe_cost"])
        cost_obs[j].append(cost)
        records[-1].update({"batch": list(batch),  # None = timeout 검열
                            "censored_positions": [i for i, x in enumerate(batch) if x is None],
                            "simulated_cost": cost, "cost_kind": "simulated"})
        sim_cost += cost
        probes += 1
        sequence.append(arm_ids[j])
        rules.append(rule)
        t2 = clock()
        for x in batch:
            seqs[j].add(x)
        planner_used += clock() - t2

    certified = termination == "certified"
    upper = reg["upper"] if reg is not None else None
    if termination == "empty_interval":
        selected, basis = None, "validation_failed"
    elif upper is not None and upper != INF:
        selected, basis = reg["candidate"], "regret_candidate"
    elif all(any(x is not None for x in raw[j]) for j in range(d) if any(p.uses(j) for p in placements)):
        point = []
        for j in range(d):
            xs = sorted(x for x in raw[j] if x is not None)
            point.append(as_fraction(xs[math.ceil(0.75 * len(xs)) - 1]) if xs else as_fraction(fixture["arms"][j]["support"][0]))
        selected = min(placements, key=lambda p: (cost_at(p, point), ids.index(p.id))).id
        basis = "point_estimate"
    else:
        selected, basis = fixture["fallback"], "fallback"

    truth = [true_p75(a["dist"]) for a in fixture["arms"]]
    true_costs = {p.id: cost_at(p, truth) for p in placements}
    best = min(true_costs.values())
    gap = float(true_costs[selected] - best) if selected is not None else None
    arms = {}
    for j, a in enumerate(arm_ids):
        r = seqs[j].result()
        arms[a] = {"n": r["n"], "censored": r["censored"], "lo": float(r["lo"]),
                   "hi": _up(r["hi"]), "hi_status": bound_status(r["hi"]),
                   "status": r["status"], "probes": sequence.count(a),
                   # 평가자 전용 감사: 참 p75 가 최종 구간 안에 있는가 (검열/빈 집합은 None)
                   "true_p75": float(truth[j]),
                   "true_p75_covered": (r["lo"] <= truth[j] <= r["hi"]) if r["status"] in ("ok", "infinite") else None}
    return {
        "fixture": fixture["name"], "policy": policy, "seed": seed,
        "selected": selected, "selection_basis": basis,
        "certified": certified, "proxy_certified": certified, "termination": termination,
        "regret_upper": _up(upper) if upper is not None else None,
        "regret_upper_status": bound_status(upper),
        "lp_complete": reg["lp_complete"] if reg is not None else False,
        "true_proxy_gap": gap,
        "probe_count": probes, "samples": probes * config["batch_size"],
        "simulated_probe_cost": sim_cost,
        "planner_wall_time_s": planner_used,
        "arms": arms, "probe_sequence": sequence, "probe_rules": rules,
        "probe_records": records,
    }
