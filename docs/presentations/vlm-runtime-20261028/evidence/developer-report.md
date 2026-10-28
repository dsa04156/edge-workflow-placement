# VLM 반복 실측 collector 구현 보고 (developer, 2026-10-28)

- 구현자: Claude Code, 모델 `claude-opus-5-5`. `claude auth status` 확인 결과 `authMethod=claude.ai`, `apiProvider=firstParty`, `subscriptionType=max`(공식 구독 로그인, API 종량제 아님).
- 범위: **코드 존재 + 관련 mock 테스트 통과** 수준. 클러스터·실제 호출·다운로드·커밋/푸시는 하지 않았다. 실제 가속기 실측은 coordinator가 수행한다.

## 산출물과 hash

| 파일 | sha256 | 비고 |
|---|---|---|
| `experiments/gpu-mobilint-vlm/benchmark_live.py` | `acd0f7ee5e435d19abc593f22abae5de95cb742f6bb9bfd7962c2a20ab420d93` | 신규 collector (release `benchmark.collector_sha256`) |
| `experiments/gpu-mobilint-vlm/test_benchmark_live.py` | `214d632b82064d528894b1cf10e841dbb6b2975005a7eb483df308148a83ba44` | 신규 테스트 10건 |
| `experiments/gpu-mobilint-vlm/live.py` | `a8f11764bcbca9365230a2e51cc12f414811a05d1048b5c51fdb301c6e62efc1` | **변경 없음** (참조용) |
| `.scratch/vlm-slide-benchmark-20261028/plan.json` | `e7fc2487a50aab0f5dcc00966783965fc93980e8fc315cad15d946507ddafafc` | coordinator 제공, 변경 없음 |

기존 소스·동결 모델/입력·SD 다운로드 관련 파일은 수정하지 않았다(`git status`상 기존 `M experiments/sensehat-placement/run.py`는 이전부터 있던 변경).

## 실행 명령 (coordinator용)

live.py와 같은 입력을 그대로 받는다(`--prepared/--cuda-cache-pvc/--io-profile/--arm` 동일 의미). `--stage`는 없다: 항상 live.execute의 qualification을 먼저 실행한다.

```bash
cd /home/jinuk/codex-work/scheduler
F=<frozen dir>; PLAN=.scratch/vlm-slide-benchmark-20261028/plan.json
LIVE_OPTS="--prepared <prepared dir> --cuda-cache-pvc <binding.json> --io-profile sd-adaptive-v1"   # live 실행과 동일하게

# 1) release draft: live.py 자체 draft(frozen/code/prepared/cuda_cache_pvc/prepared_deadlines/io_profile) + benchmark 결합
python3 experiments/gpu-mobilint-vlm/benchmark_live.py --frozen $F --benchmark-plan $PLAN $LIVE_OPTS --print-release-draft > release.json
#    coordinator가 approved_by 와 stages=["benchmark"] 를 채운다. --arm 실행이면 live.py 규칙대로 "arms": ["<arm>"] 도 넣는다.

# 2) 실행 (새 Session/run-id)
python3 experiments/gpu-mobilint-vlm/benchmark_live.py --frozen $F --benchmark-plan $PLAN $LIVE_OPTS \
  --runs <runs dir> --release release.json --run-id gmv-<yyyymmdd>t<hhmmss>-bench
```

로컬 확인: 위 1)을 prepared 없이 `developer-frozen-r1`로 실행해 성공(rc=0, code 28개, `logs/draft-old-mode-check.json`). 실제 prepared 모드 draft는 live.py와 동일하게 prepared tar를 hash한다.

## 설계 요약

1. **계획 검증**: plan은 `rows`를 가진 JSON 객체여야 한다(coordinator 답변: bare list 불필요). **전체 80행을 pair 기준으로 검증**: measurement_id 유일, arm별 동결 입력 4개 각 10회, `(round,pair)`는 연속 그룹이며 arm당 1행·같은 입력, round는 연속이며 모든 (arm,input)을 정확히 1회(coordinator 확인: strict-balanced-rounds). 그 뒤 `--arm`이면 해당 arm 40행만 effective schedule로 실행.
2. **release 결합**: `benchmark = {collector_sha256, plan_sha256(원본 bytes), plan_rows, repeats, selected_arms, effective_rows, effective_sha256}`. 나머지는 `live.release_problems(..., live.dependency_hashes(), ...)` 그대로. binding 누락/변경, `stages`에 `benchmark` 없음, approved_by 없음 → Session 생성 전 `SystemExit`. 계획은 한 번만 읽은 bytes로 검증·hash·사본 저장을 한다(재읽기 없음).
3. **실행 순서**: 새 `live.Session` → `release.json`, `selection.json`, `benchmark-plan.json`(승인 bytes 그대로, fsync), `benchmark-binding.json` 저장 → `live.execute(session, 'qualification', arms, prepared)`(probe 1 + qualification 3 = arm당 4입력, 기존 `ledger.jsonl`; 반복 통계에서 제외되는 warm-up) → benchmark.
4. **fresh target**: qualification 후 각 arm에 대해 생성 UID record 존재, `session.pod_state`(UID 교체 거부)에서 `Running`+`Ready=True`+podIP 확인, URL은 그 podIP에서만 유도. `/health`로 `live.identity` 재검증(파일은 `benchmark-identity-<arm>.json`; `Session.save`가 create-only라 prefix 사용) 후 qualification 때의 `identities.json`과 동일해야 함. queue는 `{waiting:0, active:0, completed:= 이 run의 ledger.jsonl 해당 arm 호출 수}`이어야 함(외부 호출 없음). `check_occupancy`(start/final) 재사용.
5. **호출**: 행마다 `live.call_arm`(기존 receipt 정합 검증 그대로; 같은 request_id 재사용 — worker는 응답 캐시가 없고 매 호출 stage.run 실행). ok receipt의 `sequence`는 arm별 직전값+1이어야 함(엄격 증가 + completed 정합). 응답 전체(receipt.timing, worker_wall_ms, collector_wall_ms, 출력 base64/token ids)에 measurement_id/round/pair/plan_index/started_at/finished_at을 붙여 `benchmark-ledger.jsonl`에 append+fsync 후 다음 요청. qualification ledger와 분리.
6. **중단**: 첫 non-ok 또는 sequence 불일치에서 중단, `benchmark-health-after-failure-<arm>.json` 저장. 정상 종료 시 final health의 completed == 마지막 sequence, queue idle 확인.
7. **항상 저장**: `benchmark-summary.json`(planned/completed/failed/unattempted, per_arm, stopped_at, error, phase별 start/end/status/duration, sequence_start/last, 전체 started/finished/duration) — 예외·KeyboardInterrupt 포함 모든 경로에서 cleanup 전에 저장. 이어서 live.main과 같은 cleanup(UID precondition delete → `live.verify_cleanup` → `protected-after` snapshot → `cleanup.json`). phase 파일은 `collect.phase`로 `live-qualification`(내부에 live의 preflight/cache/workers/probe/qualification/final), `benchmark-ready`, `benchmark`, `benchmark-final`, `cleanup`.
8. CPU fallback·edge streaming 없음: 추가 kubectl 호출은 `get pod`/`get pods -A`뿐이며, 장치 판정은 기존 `receipt_problems`가 그대로 수행한다.

## 테스트

```bash
cd /home/jinuk/codex-work/scheduler
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=experiments/gpu-mobilint-vlm:experiments/quality-network-heterogeneous:experiments/real-topology-live:experiments/multi-edge-sensors:experiments/quality-network-baselines \
  python3 -m unittest -v test_benchmark_live
```

결과: **10/10 OK** (Python 3 시스템 인터프리터, stdlib만, 약 6초). 로그 `logs/test_benchmark_live.log`.

Fake 범위: loopback HTTP worker 2개(podIP 127.0.0.1/127.0.0.2, shared Worker의 `/health`·`/run` 응답 형태와 sequence 의미), kubectl seam(`collect.Session.call`), `live.execute`(qualification은 test_vlm이 담당), `protected_snapshot` 기록기, `live.dependency_hashes` stub. 실제로 실행되는 기존 코드: `live.Session`(pod_state UID 검사, delete_owned/owned_present), `call_arm`/`receipt_problems`, `identity`, `check_occupancy`, `verify_cleanup`, `collect.phase`, `bounded_http`.

| 테스트 | 검증 내용 |
|---|---|
| plan 결함 8종 + 빈/불완전 행 | 중복 id, 행 누락, 미지 입력, pair 내 같은 arm, pair 분리, pair 내 입력 혼합, round 분리, round 불균형 거부 |
| arm-only binding | 전체 plan hash 동일, effective 40행·별도 hash |
| draft | live draft 키 + benchmark binding |
| 승인 누락/변경 7종 + plan 사후 변경 + bare list | Session·kubectl·execute 미실행 상태로 거부 |
| 정상 80회 | 계획 순서 그대로, arm별 sequence 5..44 연속, final completed 44, summary 80/80/0/0, plan 사본 bytes 동일, URL=podIP, cleanup verified |
| stale/skip sequence | 해당 호출에서 중단, error 기록, 부분 summary, health-after-failure, cleanup |
| 첫 500 실패 | 이후 미실행, per_arm/unattempted, cleanup |
| qualification 중 interrupt | summary(0/80 unattempted) + cleanup + protected-after |
| qualification 뒤 외부 호출 1건 | benchmark-ready에서 거부, benchmark 호출 0 |
| `--arm cuda` | cuda 40행만, mobilint 호출 0, scope 문구 |

기존 `test_vlm.py`/`test_prepared_runtime.py`는 이번에 기존 코드를 변경하지 않았으므로 재실행하지 않았다(정책: 변경 코드 관련 테스트만).

## 제한·주의

- 실제 클러스터/가속기에서 미실행. 실제 worker의 sequence가 오류(500) 응답에서도 증가하는 점은 기존 worker 소스로 확인했으며, 그 경우는 어차피 첫 실패 중단이다.
- 같은 Pod 내 반복이며 독립 표본이 아니다(spec.md의 기술통계 한정 범위). 분석/통계·슬라이드 생성은 이번 범위 밖.
- cleanup 블록은 live.main 안에 inline이라 함수 호출로 재사용할 수 없어 동일 로직을 `cleanup()`으로 옮겨 적었다(live.py 무변경 원칙).
- `--arm` 실행의 release에는 live.py 기존 규칙대로 `arms` 키가 필요하다(draft에는 live.py와 마찬가지로 자동 포함되지 않음).
