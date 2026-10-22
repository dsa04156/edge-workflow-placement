"""synthetic-v2 비교 정책: v1 네 규칙 + 공통항 제거 균등 순환 기준선 cancel_common_round_robin.

기준선은 Observation view 의 배치 계수만 본다(분포·참 p75·참 비용·미래 표본 접근 없음).
좌표 j 의 계수가 모든 배치의 모든 경로에서 정확히 같을 때만 전역 공통 가산항으로 보고 측정에서
제외한다(보수적 판별: 일부 경로/일부 배치만 같은 좌표는 제외하지 않는다). 나머지 사용 좌표를
ID 순서로 균등 순환한다. 비용모형·인증 계산은 runner 의 원래 것을 그대로 쓴다.
"""
from __future__ import annotations

from synthetic import INF, POLICIES, _Policy

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
        # 상한이 유한한 공통항만 제외한다: 무한 구간 공통항은 runner 인증 gate 를 막으므로 측정 대상으로 남긴다.
        # 다른(비공통) 좌표의 구간이 무한이어도 유한 공통항은 복원하지 않는다.
        excluded = {j for j in common_coordinates(obs.placements) if obs.intervals[j][1] != INF}
        targets, rule = [j for j in used if j not in excluded], CANCEL_COMMON
        # 모든 사용 좌표가 유한 공통인데 인증되지 않으면 사용 좌표 전체 순환으로 되돌아간다.
        # 제외할 좌표 없이 무한 구간이 있으면 v1 공통 규칙(round_robin)과 같은 순환·라벨이다.
        if not targets:
            targets, rule = used, "round_robin"
        elif obs.any_infinite() and not excluded:
            rule = "round_robin"
        j = targets[self.rr % len(targets)]
        self.rr += 1
        return j, rule


def make_policy(name):
    if name == CANCEL_COMMON:
        return CancelCommonRoundRobin()
    return _Policy(name)
