"""공유 파라미터 pairwise/전체 regret 상한 (설계 v0.1, 합성 검증용).

비용모형: L(p;θ) = max_r (a_pr·θ + b_pr), a_pr ≥ 0.
불확실성 집합: box U = Π_j [l_j, u_j], u_j=None 은 +∞.

인증 근거는 항상 유효한 상한이다.
- simple: L(b;u) − L(q;l). a ≥ 0 이므로 box 전체에서 유효한 상한.
- lp_exact: 각 r 에 대해 max z s.t. z ≤ f_s(θ) ∀s, θ∈U 를 Fraction 정확 vertex 열거로
  풀고 r 별 최댓값을 취한다. 유한·비어 있지 않은 box 에서 모든 조합 열거를 끝낸 경우에만
  exact maximum 으로 쓴다. 차원/조합/시간 한도에 걸리면 simple 상한을 유지한다.
"""
from __future__ import annotations

import itertools
import math
import time
from fractions import Fraction

# 정확 LP 열거를 허용하는 primitive 차원 상한. 합성 fixture 는 최대 2 이다.
LP_DIMENSION_CAP = 3
# 한 pairwise 계산(b 의 모든 경로 r 합계)에서 시도할 활성 제약 조합 수의 상한.
LP_COMBINATION_CAP = 20000

INF = math.inf


def as_fraction(x):
    if isinstance(x, Fraction):
        return x
    if isinstance(x, float):
        if not math.isfinite(x):
            raise ValueError(f"non-finite numeric value: {x}")
        return Fraction(x)  # float 는 정확히 표현된다
    return Fraction(x)


class Placement:
    """실행 가능성 검증을 외부에서 마친 배치. 여기서는 비용 경로만 다룬다."""

    def __init__(self, pid, paths):
        if not paths:
            raise ValueError(f"placement {pid!r} has no paths")
        self.id = str(pid)
        self.paths = []
        dim = None
        for coeffs, const in paths:
            a = [as_fraction(c) for c in coeffs]
            if any(c < 0 for c in a):
                raise ValueError(f"placement {pid!r} has a negative coefficient")
            if dim is None:
                dim = len(a)
            elif len(a) != dim:
                raise ValueError(f"placement {pid!r} has paths of different dimension")
            self.paths.append((tuple(a), as_fraction(const)))
        self.dim = dim

    def uses(self, j):
        return any(a[j] != 0 for a, _ in self.paths)


def _check_box(box):
    out = []
    for lo, hi in box:
        if lo is None:
            raise ValueError("lower bound must be finite")
        lo = as_fraction(lo)
        hi = None if hi is None or (isinstance(hi, float) and hi == INF) else as_fraction(hi)
        out.append((lo, hi))
    return out


def box_is_empty(box):
    return any(hi is not None and lo > hi for lo, hi in box)


def cost_at(p, theta):
    """θ 가 None(+∞) 성분을 가지면 해당 성분을 쓰는 경로는 +∞."""
    best = None
    for a, b in p.paths:
        val = b
        for aj, tj in zip(a, theta):
            if aj == 0:
                continue
            if tj is None:
                return INF
            val += aj * tj
        best = val if best is None or val > best else best
    return best


def _simple_upper(p, q, box):
    lp = cost_at(p, [hi for _, hi in box])
    if lp == INF:
        return INF
    return lp - cost_at(q, [lo for lo, _ in box])


def _solve(rows, rhs):
    """Fraction Gauss-Jordan. 특이 행렬이면 None."""
    n = len(rows)
    m = [list(r) + [v] for r, v in zip(rows, rhs)]
    for col in range(n):
        piv = next((i for i in range(col, n) if m[i][col] != 0), None)
        if piv is None:
            return None
        m[col], m[piv] = m[piv], m[col]
        pv = m[col][col]
        m[col] = [x / pv for x in m[col]]
        for i in range(n):
            if i != col and m[i][col] != 0:
                f = m[i][col]
                m[i] = [x - f * y for x, y in zip(m[i], m[col])]
    return [m[i][n] for i in range(n)]


class _Abort(Exception):
    pass


def _lp_combinations(p, q, d):
    """쌍 (p,q) 의 정확 LP 가 열거할 활성 조합 수: 모든 경로 r 의 합계."""
    return len(p.paths) * math.comb(2 * d + len(q.paths), d + 1)


def _lp_path_max(p, r, q, box, deadline, clock):
    """max z s.t. z ≤ (a_pr − a_qs)θ + b_pr − b_qs ∀s, θ∈box (finite, nonempty).

    변수 x = (θ_1..θ_d, z). 제약을 g·x ≤ h 로 두고 d+1 개 활성 조합을 열거한다.
    """
    d = len(box)
    ar, br = p.paths[r]
    cons = []
    for j, (lo, hi) in enumerate(box):
        e = [Fraction(0)] * (d + 1)
        e[j] = Fraction(1)
        cons.append((e, hi))
        e2 = [Fraction(0)] * (d + 1)
        e2[j] = Fraction(-1)
        cons.append((e2, -lo))
    for aq, bq in q.paths:
        g = [-(x - y) for x, y in zip(ar, aq)] + [Fraction(1)]
        cons.append((g, br - bq))
    best = None
    for k, idx in enumerate(itertools.combinations(range(len(cons)), d + 1)):
        if deadline is not None and k % 64 == 0 and clock() > deadline:
            raise _Abort("deadline")
        sol = _solve([cons[i][0] for i in idx], [cons[i][1] for i in idx])
        if sol is None:
            continue
        if all(sum(g * x for g, x in zip(gv, sol)) <= h for gv, h in cons):
            if best is None or sol[d] > best:
                best = sol[d]
    if best is None:
        # 유한·비어 있지 않은 box 에서는 일어나면 안 된다. 상한으로 쓰지 않는다.
        raise _Abort("no_vertex")
    return best


def pairwise_regret_upper(p, q, box, *, lp=True, deadline=None, combination_cap=LP_COMBINATION_CAP,
                          clock=time.perf_counter):
    """D(p,q) 의 유효한 상한과 그 근거. deadline 은 clock() 기준 시각이다."""
    box = _check_box(box)
    if box_is_empty(box):
        raise ValueError("empty uncertainty set")
    if p.id == q.id:
        return {"upper": Fraction(0), "method": "identical"}
    simple = _simple_upper(p, q, box)
    if not lp:
        return {"upper": simple, "method": "simple"}
    # p,q 모두 쓰지 않는 좌표는 계수가 0 이므로 하한에 고정해도 LP 값이 같다.
    box = [(lo, lo if hi is None and not (p.uses(j) or q.uses(j)) else hi) for j, (lo, hi) in enumerate(box)]
    if any(hi is None for _, hi in box):
        return {"upper": simple, "method": "simple_unbounded_box"}
    if len(box) > LP_DIMENSION_CAP:
        return {"upper": simple, "method": "simple_dimension_cap"}
    if _lp_combinations(p, q, len(box)) > combination_cap:
        return {"upper": simple, "method": "simple_lp_combination_cap"}
    try:
        exact = max(_lp_path_max(p, r, q, box, deadline, clock) for r in range(len(p.paths)))
    except _Abort as exc:
        return {"upper": simple, "method": f"simple_lp_{exc.args[0]}"}
    return {"upper": exact, "method": "lp_exact"}


def recommend(placements, box):
    """b = argmin L(p;u), 동률은 입력 ID 순서."""
    ups = [hi for _, hi in box]
    return min(placements, key=lambda p: (cost_at(p, ups), placements.index(p))).id


def total_regret(placements, box, *, epsilon, candidate=None, lp=True,
                 deadline=None, combination_cap=LP_COMBINATION_CAP, clock=time.perf_counter):
    """R(b) = max_q D(b,q) 의 유효한 상한. 모든 경쟁 배치를 검사한다.

    simple 상한이 ε 이하인 쌍은 통과시키고 나머지만 LP 로 정밀화한다.
    """
    if not placements:
        raise ValueError("empty feasible set")
    ids = [p.id for p in placements]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate placement id")
    dims = {p.dim for p in placements}
    box = _check_box(box)
    if dims != {len(box)}:
        raise ValueError("placement dimension does not match box")
    epsilon = as_fraction(epsilon)
    if box_is_empty(box):
        return {"status": "empty_uncertainty_set", "candidate": None, "upper": INF,
                "certified": False, "competitors": [], "lp_complete": False}
    b_id = candidate if candidate is not None else recommend(placements, box)
    if b_id not in ids:
        raise ValueError(f"unknown candidate {b_id!r}")
    b = placements[ids.index(b_id)]
    comps = []
    lp_complete = True
    for q in placements:
        res = pairwise_regret_upper(b, q, box, lp=False)
        if lp and res["method"] != "identical" and res["upper"] > epsilon:
            res = pairwise_regret_upper(b, q, box, lp=True, deadline=deadline,
                                        combination_cap=combination_cap, clock=clock)
            if res["method"] != "lp_exact":
                lp_complete = False
        comps.append({"id": q.id, **res})
    upper = max(c["upper"] for c in comps)
    status = "ok" if upper != INF else "unbounded"
    return {"status": status, "candidate": b_id, "upper": upper,
            "certified": upper <= epsilon, "competitors": comps, "lp_complete": lp_complete}
