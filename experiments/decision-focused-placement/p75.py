"""모집단 p75 의 순차 신뢰구간 (DKW band + error spending).

표본 수 n 마다 α_n = δ/(d·n·(n+1)), e_n = sqrt(log(2/α_n)/(2n)) 를 쓰고
[Q̂(.75−e_n), Q̂(.75+e_n)] 를 과거 구간과 교집합한다. Q̂ 는 empirical generalized
inverse (k = ⌈t·n⌉ 번째 순서통계량)이다. 확률 인수가 범위를 벗어나면 알려진 support
경계(상한 미지정이면 +∞)를 쓴다. 전제: primitive 별 i.i.d. 표본, 고정 환경.

e_n 과 ⌈t·n⌉ 은 float 로 계산한다. 경계 반올림 오차는 INDEX_TOL 만큼 구간을 넓히는
방향으로 처리하지만, 이 단계는 exact rational 보장이 아니다(README 참고).
검열 표본(None)이 하나라도 있으면 v0.1 에서는 certifiable=False 이다.
잘못된 입력(빈/NaN support, −∞, 정수가 아닌 d, (0,1) 밖 δ, 비유한 비검열 표본)은 ValueError.
"""
from __future__ import annotations

import bisect
import math
import numbers

INDEX_TOL = 1e-9
INF = math.inf


def _is_real(x):
    return isinstance(x, numbers.Real) and not isinstance(x, bool)


class SequentialP75:
    def __init__(self, *, d, delta, support):
        if isinstance(d, bool) or not isinstance(d, int) or d < 1:
            raise ValueError("d must be an integer >= 1")
        if not _is_real(delta) or not (0 < delta < 1):
            raise ValueError("need 0 < delta < 1")
        lo, hi = support
        if not _is_real(lo) or not math.isfinite(lo):
            raise ValueError("support lower bound must be finite")
        if hi is None:
            hi = INF
        if not _is_real(hi) or math.isnan(hi) or hi == -INF:
            raise ValueError("support upper bound must be finite, None or +inf")
        if lo > hi:
            raise ValueError("empty support")
        self.d, self.delta = d, delta
        self.s_lo, self.s_hi = lo, hi
        self.sorted = []
        self.n = 0
        self.censored = 0
        self.lo, self.hi = self.s_lo, self.s_hi
        self.band = (self.s_lo, self.s_hi)
        self.e = None
        self.empty = False

    def add(self, x):
        # 잘못된 표본은 상태(n 포함)를 바꾸기 전에 거부한다.
        if x is not None:
            if not _is_real(x) or not math.isfinite(x):
                raise ValueError(f"sample {x!r} is not a finite number")
            if x < self.s_lo or x > self.s_hi:
                raise ValueError(f"sample {x} outside known support")
        self.n += 1
        if x is None:
            self.censored += 1
            return
        if self.censored or self.empty:
            # 검열 이후의 구간은 v0.1 에서 정의하지 않는다; 빈 집합은 회복하지 않는다.
            return
        bisect.insort(self.sorted, x)
        n = self.n
        alpha = self.delta / (self.d * n * (n + 1))
        e = math.sqrt(math.log(2 / alpha) / (2 * n))
        k_lo = math.ceil((0.75 - e) * n - INDEX_TOL)
        k_hi = math.ceil((0.75 + e) * n + INDEX_TOL)
        b_lo = self.sorted[k_lo - 1] if k_lo >= 1 else self.s_lo
        b_hi = self.sorted[k_hi - 1] if k_hi <= n else self.s_hi
        self.e, self.band = e, (b_lo, b_hi)
        self.lo, self.hi = max(self.lo, b_lo), min(self.hi, b_hi)
        if self.lo > self.hi:
            self.empty = True

    @property
    def status(self):
        if self.censored:
            return "censored"
        if self.empty:
            return "empty"
        if self.hi == INF:
            return "infinite"
        return "ok"

    def result(self):
        st = self.status
        lo, hi = (self.s_lo, self.s_hi) if st == "censored" else (self.lo, self.hi)
        return {"lo": lo, "hi": hi, "band": self.band, "e": self.e, "n": self.n,
                "censored": self.censored, "status": st, "certifiable": st == "ok"}


def sequential_p75(samples, *, d, delta, support):
    s = SequentialP75(d=d, delta=delta, support=support)
    for x in samples:
        s.add(x)
    return s.result()
