"""synthetic-v2 benchmark: 최초 실행 전에 고정한 설정과 fixture (.scratch/decision-focused-placement-v2/spec.md).

v1 결과를 본 뒤 설계한 사후 후속 검증이다. 결과를 본 뒤 예산/fixture 를 바꾸지 않는다.
v1 의 모든 fixture 가 p 최적이었던 선택 편향을 줄이려고 label-swap·q/r 최적 fixture 를 더한다.
"""
import copy

from fixtures import BENCHMARK_CONFIG as V1_CONFIG, FIXTURES as V1_FIXTURES, _plan, _u

BENCHMARK_VERSION = "synthetic-v2"

BENCHMARK_CONFIG = dict(copy.deepcopy(V1_CONFIG), seeds=list(range(10, 20)))


def _label_swap(fx):
    """배치 id p/q 만 교환한다. list 순서·계수·상수·arm·fallback(p)는 유지하고 원본 stream 을 공유한다."""
    swap = {"p": "q", "q": "p"}
    out = copy.deepcopy(fx)
    out["name"] = f"{fx['name']}_label_swap"
    out["description"] = f"{fx['name']} with placement ids p/q swapped (fallback id stays p)"
    out["label_swap_of"] = fx["name"]
    out["stream_key"] = fx["name"]
    for pl in out["placements"]:
        pl["id"] = swap[pl["id"]]
    return out


def _three_way(name, x, y, description):
    return {"name": name, "description": description,
            "arms": [_u("x", *x), _u("y", *y)],
            "placements": [_plan("p", ({"x": 1}, 0)), _plan("q", ({"y": 1}, 8)), _plan("r", ({}, 33))],
            "fallback": "p"}


_SHARED_Q = copy.deepcopy(next(fx for fx in V1_FIXTURES if fx["name"] == "shared_additive"))
_SHARED_Q.update(name="shared_q_optimal",
                 description="shared_additive with y~U[12,32]; true p75 z=75,y=27 -> p=102,q=93")
_SHARED_Q["arms"][1] = _u("y", 12, 32)

FIXTURES = (copy.deepcopy(V1_FIXTURES)
            + [_label_swap(fx) for fx in V1_FIXTURES if fx["name"] != "timeout_censoring"]
            + [_SHARED_Q,
               _three_way("three_way_p_optimal", (0, 40), (0, 40), "p=x,q=y+8,r=33; x,y~U[0,40]; p=30,q=38,r=33"),
               _three_way("three_way_q_optimal", (10, 50), (0, 20),
                          "p=x,q=y+8,r=33; x~U[10,50],y~U[0,20]; p=40,q=23,r=33"),
               _three_way("three_way_r_optimal", (20, 60), (20, 60),
                          "p=x,q=y+8,r=33; x,y~U[20,60]; p=50,q=58,r=33")])
