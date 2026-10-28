# benchmark_live 측정 호출 중 중단 회계 수정 보고 (fix R1, 2026-10-28)

- 구현자: Claude Code, 모델 `claude-opus-5-5`(Opus 5.5). `claude auth status`: `loggedIn=true`, `authMethod=claude.ai`, `apiProvider=firstParty`, `subscriptionType=max` — 공식 구독 로그인(API 종량제 아님). 원문 `logs/claude-auth-status-fix-r1.json`.
- 수준: **코드 수정 + 관련 mock 테스트 통과**. 클러스터·실제 가속기 호출·다운로드·Git 커밋은 하지 않았다.
- 대상 지적: review-spec-r1 / review-standards-r1 공통 P2 — 측정 호출 중 `KeyboardInterrupt`가 나면 해당 요청이 ledger에 남지 않고 summary에서 unattempted로 계산됨.

## 최종 hash

| 파일 | 이전 sha256 (review-hashes.json) | 최종 sha256 |
|---|---|---|
| `experiments/gpu-mobilint-vlm/benchmark_live.py` | `acd0f7ee…20d93` | `62fb1107031acbde41ceabe6a5f45fdfb5c2599a0f4f090912c9b2db5013f48c` |
| `experiments/gpu-mobilint-vlm/test_benchmark_live.py` | `214d632b…83ba44` | `d5e28ec99cdb7277aaf51e0a2162303413974f6e5e20b12387884c6b393a9a7d` |
| `experiments/gpu-mobilint-vlm/live.py` | — | `a8f11764bcbca9365230a2e51cc12f414811a05d1048b5c51fdb301c6e62efc1` (**변경 없음**) |
| `.scratch/vlm-slide-benchmark-20261028/plan.json` | — | `e7fc2487a50aab0f5dcc00966783965fc93980e8fc315cad15d946507ddafafc` (변경 없음) |

collector hash가 바뀌었으므로 release의 `benchmark.collector_sha256`은 **새 `--print-release-draft`로 다시 만들어야 한다**(이전 draft/release는 binding 불일치로 Session 생성 전에 거부된다 — 의도된 동작).

## 변경 내용 (최소 diff)

`collect_benchmark`:
- `live.call_arm` 호출 **전에** 행 식별자(`measurement_id/round/pair/plan_index/started_at`)와 monotonic 시작 시각을 잡아 둔다.
- 호출~ledger append 구간을 `try/except BaseException`으로 감싼다. 중단(또는 그 구간의 임의 예외) 시:
  - 응답 전: `status='interrupted'`, `outcome='unknown'`, `reply_status=None`, `collector_partial_ms`(시작~중단 경과), `error`, `finished_at`을 arm/input과 함께 append+fsync.
  - 응답 수신 후 append 전: 받은 응답 전체(receipt, `collector_wall_ms` 등)를 보존하고 `status='interrupted'`, `reply_status=<원래 status>`, `outcome='reply received, not ledgered before the interrupt'`, `collector_partial_ms`를 덧붙여 기록.
  - **중복 방지**: append가 행을 쓴 뒤 반환 전에 중단된 경우를 위해, ledger 마지막 행이 이미 같은 `measurement_id`이면 추가로 쓰지 않는다(`ledgered()`).
  - 그 뒤 원래 예외를 그대로 re-raise → 기존 `main`의 finally에서 summary 저장 → UID cleanup/부재 검증/protected-after snapshot이 그대로 실행된다.
- 첫 non-ok/sequence gap 중단 경로(`RuntimeError`)는 try 바깥이라 기존 동작 그대로.

`summary`:
- `interrupted` 수(전체·per_arm)를 추가하고 `failed`에서 분리했다. 이제 `completed + failed + interrupted + unattempted = planned`이며, 중단된 요청은 attempted로 계산된다(unattempted = planned − ledger 행 수, 기존 식 유지).
- `stopped_at`은 모든 non-ok 행(failed + interrupted)이며 `status` 키를 포함한다.

호환 영향: `per_arm` 항목에 `interrupted` 키가 추가되어 기존 테스트 1건의 dict 동등 비교를 `interrupted=0` 포함으로 갱신했다. 그 외 수용된 동작(plan 검증, release/collector/plan binding, fresh target, sequence 검사, 첫 실패 중단, cleanup)은 변경하지 않았다. 의존성 추가 없음(stdlib).

## 추가 테스트 (2건, 그중 1건은 subTest 2개)

1. `test_an_interrupt_inside_a_measured_call_is_ledgered_as_attempted_with_unknown_outcome` — 실제 `bounded_http.post`가 worker 응답을 받은 직후 3번째 측정 호출에서 `KeyboardInterrupt`(call_arm 반환 전). 확인: ledger = ok, ok, interrupted(`outcome=unknown`, `reply_status=None`, `plan_index=2`, `collector_partial_ms≥0`, receipt 없음); worker `/run` 총 3건(**다음 호출 없음**); summary `completed=2, failed=0, interrupted=1, unattempted=77`, `stopped_at[0]`이 해당 measurement; benchmark phase `error`, `benchmark-final` 없음; `KeyboardInterrupt`가 호출자까지 전파; `cleanup.json.verified=true`, 클러스터 객체 0, `protected-after` snapshot.
2. `test_an_interrupt_after_a_reply_keeps_the_reply_once` — ledger append에서 중단:
   - `before_write`: 응답(receipt sequence = qualification 4 + 2) 보존, `status=interrupted`, `reply_status=ok`, `collector_wall_ms` 유지; `completed=2, interrupted=1`.
   - `after_write`: 행이 이미 기록된 뒤 중단 → **중복 행 없음**(ledger measurement_id = 계획 첫 3개 그대로), 그 행은 `ok`; `completed=3, interrupted=0`.
   - 두 경우 모두 위와 같은 다음 호출 없음·카운트 합·cleanup 검증.

## 실행한 테스트

```bash
cd /home/jinuk/codex-work/scheduler
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=experiments/gpu-mobilint-vlm:experiments/quality-network-heterogeneous:experiments/real-topology-live:experiments/multi-edge-sensors:experiments/quality-network-baselines \
  python3 -m unittest -v test_benchmark_live
```

결과: **12/12 OK**(약 8–9초). 로그 `logs/test_benchmark_live-fix-r1.log`(로그 저장을 위해 같은 스위트를 한 번 더 실행했다). live.py 등 기존 코드는 변경하지 않았으므로 `test_vlm.py`/`test_prepared_runtime.py`는 재실행하지 않았다.

## 제한

- mock(loopback worker + fake kubectl) 검증이다. 실제 클러스터에서의 중단 동작·가속기 성능은 확인하지 않았다.
- 중단 기록 자체가 두 번째 중단으로 다시 끊기거나, 신호가 `write` 중간에 들어와 부분 행이 남는 경우는 다루지 않는다(그 경우에도 예외는 전파되어 cleanup은 실행된다).
- `interrupted` 행은 결과가 불확실하므로 반복 통계(성공 호출 기준)에서 제외해야 한다. 분석·슬라이드는 coordinator 범위.
- 작업 트리의 기존 다른 변경(`experiments/sensehat-placement/run.py`, `program-registration/ready-source/cluster.json`)은 건드리지 않았다.
