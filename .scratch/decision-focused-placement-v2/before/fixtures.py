"""synthetic-v1 benchmark: 최초 실행 전에 고정한 설정과 fixture.

결과를 본 뒤 예산/fixture 를 바꾸지 않는다. 바꾸려면 새 버전 이름으로 별도 저장한다.
"""

BENCHMARK_VERSION = "synthetic-v1"

BENCHMARK_CONFIG = {
    "epsilon": 1.0,                 # proxy regret 허용치 (ms 단위로 해석한 합성 값)
    "delta": 0.05,                  # 모든 arm·표본 수에 걸친 실패확률
    "batch_size": 20,               # probe 1회의 표본 수
    "max_probes": 150,              # probe 횟수 예산
    "planner_time_budget_s": 10.0,  # 실행당 planner wall time 예산
    "initial_cost_metadata": 1.0,   # 모든 arm 에 동일하게 공개되는 초기 probe 비용
    "cost_floor": 0.01,
    "contrast_round_robin_every": 5,
    "lp_combination_cap": 20000,
    "seeds": [0, 1, 2, 3, 4],
    "max_total_seconds": 900.0,     # CLI 전체 실행 시간 상한
}


def _u(aid, lo, hi, cost=1.0, timeout=None):
    return {"id": aid, "dist": {"kind": "uniform", "lo": lo, "hi": hi}, "support": [lo, hi],
            "probe_cost": cost, "timeout": timeout}


def _plan(pid, *paths):
    return {"id": pid, "paths": [{"coeffs": c, "const": k} for c, k in paths]}


FIXTURES = [
    {   # z 는 두 배치에 같은 계수로 더해져 상쇄된다. z 구간이 가장 넓고 probe 도 비싸다.
        "name": "shared_additive",
        "description": "p=z+y, q=z+18; true p75 z=75,y=15 -> p=90,q=93",
        "arms": [_u("z", 0, 100, cost=4.0), _u("y", 0, 20)],
        "placements": [_plan("p", ({"z": 1, "y": 1}, 0)), _plan("q", ({"z": 1}, 18))],
        "fallback": "p",
    },
    {   # 임계경로 전환. x 는 이산 분포라 p75 상한 band 가 40 에서 줄지 않는다.
        "name": "critical_path_switch",
        "description": "p=max(z+10,x), q=max(z+12,20); z~U[0,40], x~{20,30,35,40}; p=40,q=42",
        "arms": [_u("z", 0, 40),
                 {"id": "x", "dist": {"kind": "discrete", "values": [20, 30, 35, 40], "probs": [0.25] * 4},
                  "support": [20, 40], "probe_cost": 1.0, "timeout": None}],
        "placements": [_plan("p", ({"z": 1}, 10), ({"x": 1}, 0)), _plan("q", ({"z": 1}, 12), ({}, 20))],
        "fallback": "p",
    },
    {   # 참 차이 0.5 < epsilon. 인증에는 매우 좁은 구간이 필요하다.
        "name": "near_tie",
        "description": "p=x, q=y; x~U[0,20], y~U[0.5,20.5]; p=15,q=15.5",
        "arms": [_u("x", 0, 20), _u("y", 0.5, 20.5)],
        "placements": [_plan("p", ({"x": 1}, 0)), _plan("q", ({"y": 1}, 0))],
        "fallback": "p",
    },
    {   # x probe 가 y 보다 10배 비싸다.
        "name": "unequal_probe_cost",
        "description": "p=x, q=y+5; x,y~U[0,40]; cost x=10,y=1; p=30,q=35",
        "arms": [_u("x", 0, 40, cost=10.0), _u("y", 0, 40, cost=1.0)],
        "placements": [_plan("p", ({"x": 1}, 0)), _plan("q", ({"y": 1}, 5))],
        "fallback": "p",
    },
    {   # 대칭: 두 항목이 똑같이 필요하고 비용도 같다. 균등 순환이 이미 적절한 경우.
        "name": "no_advantage",
        "description": "p=x, q=y+15; x,y~U[0,40], equal cost; p=30,q=45",
        "arms": [_u("x", 0, 40), _u("y", 0, 40)],
        "placements": [_plan("p", ({"x": 1}, 0)), _plan("q", ({"y": 1}, 15))],
        "fallback": "p",
    },
    {   # x 에 timeout(36) 검열. v0.1 은 검열 항목을 인증하지 않는다.
        "name": "timeout_censoring",
        "description": "no_advantage with x censored above 36 (P=0.1)",
        "arms": [_u("x", 0, 40, timeout=36), _u("y", 0, 40)],
        "placements": [_plan("p", ({"x": 1}, 0)), _plan("q", ({"y": 1}, 15))],
        "fallback": "p",
    },
]
