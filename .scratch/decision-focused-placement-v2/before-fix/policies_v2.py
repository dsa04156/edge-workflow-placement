"""synthetic-v2 비교 정책: v1 네 규칙 + 공통항 제거 균등 순환 기준선 cancel_common_round_robin.

기준선은 Observation view 의 배치 계수만 본다(분포·참 p75·참 비용·미래 표본 접근 없음).
좌표 j 의 계수가 모든 배치의 모든 경로에서 정확히 같을 때만 전역 공통 가산항으로 보고 측정에서
제외한다(보수적 판별: 일부 경로/일부 배치만 같은 좌표는 제외하지 않는다). 나머지 사용 좌표를
ID 순서로 균등 순환한다. 비용모형·인증 계산은 runner 의 원래 것을 그대로 쓴다.
"""
from __future__ import annotations

from synthetic import POLICIES, _Policy

CANCEL_COMMON = "cancel_common_round_robin"
POLICIES_V2 = POLICIES + (CANCEL_COMMON,)


def common_coordinates(placements):
    """모든 배치·모든 경로에서 계수가 정확히 같고 0 이 아닌 좌표 index."""
    coeffs = [a for p in placements for a, _ in p.paths]
    return [j for j in range(len(coeffs[0])) if coeffs[0][j] != 0 and all(a[j] == coeffs[0][j] for a in coeffs)]


class CancelCommonRoundRobin:
    def __init__(self):
        self.rr = 0

    def choose(self, obs):
        used = [j for j in range(len(obs.arm_ids)) if obs.used(j)] or list(range(len(obs.arm_ids)))
        common = set(common_coordinates(obs.placements))
        targets, rule = [j for j in used if j not in common], CANCEL_COMMON
        # v1 공통 규칙: 사용 arm 에 무한 구간이 있으면 인증 gate 가 막히므로 공통항도 포함해 순환한다.
        # 모든 좌표가 공통인데 인증되지 않은 경우(무한 support 등)에도 같은 순환으로 되돌아간다.
        if obs.any_infinite() or not targets:
            targets, rule = used, "round_robin"
        j = targets[self.rr % len(targets)]
        self.rr += 1
        return j, rule


def make_policy(name):
    if name == CANCEL_COMMON:
        return CancelCommonRoundRobin()
    return _Policy(name)
