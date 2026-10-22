# 최종 수정 보고 — 미사용 무한 primitive 의 contrast acquisition crash (review-spec-2 P2)

작성: 2026-10-21, Orca worker task `task_9c0d47ff4a0d` / dispatch `ctx_ffb3e6ce2dc4`.
실행 환경 확인: transcript model `claude-opus-5-5`, `claude auth status` = `authMethod: claude.ai`, `apiProvider: firstParty` (provider fallback 없음).

## 범위

`review-spec-2.md`의 Unresolved P2만 수정했다. fixture·config·다른 알고리즘·`regret.py`/`p75.py`/`fixtures.py`/`run_synthetic.py`는 변경하지 않았다. 기존 `synthetic-v1`, `synthetic-v1-reviewfix`는 보존(sha256 불변).

## 원인과 수정

- 원인: `synthetic.py::_contrast_widths`가 모든 좌표의 구간 상한을 `Fraction`으로 바꿔 midpoint를 계산했다. 어떤 배치도 쓰지 않는 primitive의 상한이 `+∞`이면 `as_fraction(inf)`가 `ValueError: non-finite numeric value: inf`를 낸다. 기존 unused-infinite 테스트는 probe 없이 인증되어 acquisition 경로를 타지 않았다.
- 수정: witness 쌍 b·q 어느 쪽도 쓰지 않는 좌표는 (b·q 계수가 0이므로) 하한(유한 상수)에 고정한다 → midpoint 산술에 무한값이 들어가지 않고 해당 `W_j=0`. 세 번째 경쟁자만 쓰는 좌표에도 같은 규칙이 적용된다(그 경우 현재 runner는 `any_infinite()`로 round-robin 하므로 함수 단위 테스트로 고정).
- 다른 좌표의 W와 점수는 수학적으로 동일하다(계수 0 좌표의 midpoint 값은 어떤 경로 상수에도 기여하지 않음).

## TDD 증거

1. 실패 확인(수정 전): 새 테스트 2개 모두 `ValueError: non-finite numeric value: inf` (`synthetic.py:118`).
   - `test_unused_infinite_primitive_does_not_crash_acquisition`: `NEAR_TIE` deep copy + `uniform("unused", 0, 40, support=[0, None])`, 네 정책 모두 `max_probes=1` → `budget_probes`, probe 1회(x 또는 y), unused probe 0, strict JSON.
   - `test_contrast_widths_ignore_infinite_coordinate_unused_by_witness_pair`: 세 번째 배치만 쓰는 무한 좌표 → W=[20,18,0].
2. 수정 후: `test_synthetic` 22/22, 전체 `rtk proxy python3 -m unittest discover -s experiments/decision-focused-placement -p 'test_*.py'` **52/52 OK** (기존 50 + 신규 2; 유한/무한·unused-infinite 인증·CLI strict JSON/hash/overwrite 포함).

## 재실행 artifact

`rtk proxy python3 experiments/decision-focused-placement/run_synthetic.py --output artifacts/decision-focused-placement/synthetic-v1-final` (override 없음, 120 runs, skipped 0, 9,941 probe records).

| artifact | results.json sha256 |
|---|---|
| synthetic-v1 (보존) | `b3849eba2f77cfd61d6baff09f03b18de8514bb186d1e01b4bcdd476ccb5d2a6` |
| synthetic-v1-reviewfix (보존) | `472a5ade21d4d0a58677e2f2edaf8aedecb08f01ce7b3c053c3c3ded5180e1c2` |
| **synthetic-v1-final (신규)** | `a08d88c2a51156a83c489aee0b606f298af59b5cb4be8a5de4b10cb4df3b844b` |

비교(시간 필드 `planner_wall_time_s`/`planner_time_used_before_s`/`total_wall_time_s`/`started_at` 제외):
- `source_files` 8개 sha256 = 최종 소스 파일과 일치. reviewfix 대비 바뀐 소스는 `synthetic.py`, `test_synthetic.py`뿐.
- vs reviewfix: benchmark·claim_boundary·config·fixtures·policies·overrides·json_contract·deterministic·summary·runs(모든 raw batch·probe_sequence·rule·cost·censor 위치 포함)·skipped_runs **동일**. `environment.argv`의 output 경로만 다름.
- vs 최초 synthetic-v1: v1에 존재하는 runs/deterministic/summary 필드 모두 동일(0 diff), probe_sequence 동일. v1에는 probe_records/raw batch가 없어 raw batch는 reviewfix와만 비교 가능.

즉 고정 fixture에서는 이 경로(미사용 무한 primitive + acquisition)가 등장하지 않아 결과 수치는 변하지 않았다.

## 남은 한계

- 수정은 crash 방지·계약 일치이며 contrast 휴리스틱의 성능·EVOI 성질에 대한 새 주장은 없다. 합성 fixture 전용, 클러스터 측정 아님.
- 사용되는 primitive에 무한 구간이 있으면 여전히 모든 정책이 round-robin(설계 그대로).
- 별도 독립 검토 라운드는 요청되지 않았다(host 재테스트 예정). commit/push 하지 않음.
