# developer-report — 불안정 엣지 네트워크 재실험 구현 (task_8f1d16677946 / ctx_3ff6b2b0ce99)

작성: Claude Code `claude-opus-5-5`, 2026-10-27. 기준 commit은 `d87bde1fde21f014c1baebd1132673984e960a51`이다. 기존 dirty 변경은 보존했다. commit/push는 하지 않았다.

## 1. 결과 요약 (수준 구분)

| 수준 | 상태 |
|---|---|
| 코드 존재 | `experiments/unstable-network/` 8개 모듈, 테스트 8개 |
| 관련 테스트 통과 | 86/86 OK (`logs/green-all.txt`) |
| 실제 커널 동작 | 비특권 netns에서 in-place change가 큐와 카운터를 보존함 |
| 읽기 전용 plan/dry-run | 완료, drift `[]` |
| 실제 운영 배포 / 실장비 수집 / 공식 수용 | **미실행.** Pod mutation 0회, kubectl 호출 0회 |

- 실제 커널 동작은 7.0.0-38-generic(ser0002와 같은 버전)에서 확인했다.
- plan: `artifacts/unstable-network/plan-20261027T091525Z`, plan sha256 `407abd4912c21303a396f1fa0b6ff1b224da08d5336cff7a65729c0ae920987e`.

## 2. 인증·모델 증거 (비밀값 제외)

- `claude auth status`의 안전 필드: `loggedIn=true`, `authMethod=claude.ai`, `apiProvider=firstParty`, `subscriptionType=max`.
- email, org 등 식별값은 출력 단계에서 가렸고 저장하지 않았다.
- `ANTHROPIC_*`, `CLAUDE_CODE_USE_*` 환경변수는 설정되어 있지 않다(이름 검사만 했다).
- 실행 모델: `claude-opus-5-5`(세션 system 정보). API, 다른 공급자, fallback은 쓰지 않았다.
- coordinator도 transcript와 auth status로 독립 확인했다(mail 3번째 메시지).
- Orca: `/home/jinuk/.orca/cli/bin/orca` 1.4.235. `orca skills get orchestration`(version-matched)의 worker obligations를 읽었다.

## 3. 변경 파일 (모두 신규; 기존 파일 수정 없음)

**모듈과 테스트** (`experiments/unstable-network/`)

| 파일 | sha256 | 비고 |
|---|---|---|
| `traces.py` | `a32533c6…13d8` | 신규. trace 표와 검증 |
| `netctl.py` | `a4dd125a…e193` | 신규. sidecar HTTP 서버와 pyroute2 백엔드 |
| `policies.py` | `f6ecfade…8870` | baselines `policies.py`(`c9f39b43…`)의 사본. diff 151줄 |
| `generator.py` | `48522047…a7` | baselines `generator.py`(`8391450e…`)의 사본. diff 48줄 |
| `design.py` | `1a500b51…5a49` | baselines `design.py`(`22076fbe…`)의 순수 함수 사본 + 신규 |
| `live.py` | `b8e24238…1386` | baselines `live.py`(`af914126…`)의 수정 사본 |
| `analyze.py` | `6b7659c0…39d8` | baselines `analyze.py`(`e415d67d…`)의 사본 + 신규 |
| `unstable.py` | `010deb96…6b34` | baselines `baselines.py`(`fddea1db…`)의 수정 사본 |
| `test_traces.py` | `0d6e920b…` | 13 tests |
| `test_netctl.py` | `06f28691…` | 16 tests, 실제 커널 4개 포함 |
| `test_policies.py` | `fc71ef0f…` | 14 tests |
| `test_executor.py` | `add3aee8…` | 8 tests, loopback HTTP |
| `test_design.py` | `e44c14e6…` | 12 tests, 6개는 numpy/pandas 필요 |
| `test_analyze.py` | `e20a4ac3…` | 9 tests |
| `test_live.py` | `1ea09c02…` | 9 tests |
| `test_cell_loopback.py` | `ebb1bf81…` | 5 tests, 약 70 s |

**문서**: `docs/experiments/unstable-network-20261027.md` (`ab09a37b…48e8`)

**이 보고서와 로그** (`.scratch/unstable-network-20261027/`)
- `developer-report.md`(이 파일)
- `logs/`: `red-*.txt`, `green-*.txt`, `plan.txt`, `dry-run.json`, `kernel-change-probe.json`

**새 artifact**: `artifacts/unstable-network/plan-20261027T091525Z/`
- 파일: `plan.json`, `assets-manifest.json`, `corpus.json`, `catalog.{json,csv}`, `jellybean.tar`
- create-exclusive로 만들었다.

**보존 확인**
- coordinator `coordinator-provenance.json`의 preserved_sources 75개 hash가 모두 그대로다. 구현 후 재검사에서 `changed []`였다.
- `git status`로 보면 baselines, qnh, real-topology-live와 기존 artifact에 변경이 없다.

**사본과 원본의 관계**
- 사본은 이 디렉터리에서 import되고 Pod에 배포된다. 원본은 hash만 기록한다(plan `copied_sources`). 원본이 바뀌면 release drift가 된다.
- 변경 없는 재사용 파일은 plan `shipped_sources`와 `reused_frozen_sources`로 결속했다.
  - 배포: baselines `dag_worker.py`, `gateway.py`, `bounded_http.py`; qnh `worker.py`, `stages.py`, `bootstrap.py`, `proc.py`; `http_runtime.py`
  - 결속: `qualify.py`, `catalog.py`, `collect.py`, `run_baseline_comparison.py`, `vqa_eval.py`, `capture.py`
- `net_control.py`는 이 실험에서 쓰지 않는다. 대체 파일은 `netctl.py`다.

## 4. TDD 기록 (공개 seam; tdd skill의 tests.md·mocking.md를 따름)

seam은 spec이 미리 확정했다. 정책결정, generator HTTP/ledger, 네트워크 제어 spec, release-bound collector, 오프라인 분석이다. mock은 시스템 경계(tc 커널 백엔드, Pod HTTP, kubectl pod states, 시계)에만 썼다.

**red→green 순서**

| slice | red | green |
|---|---|---|
| 1 `traces` | `red-01`: 모듈 없음 | `green-01`: 13 OK |
| 2 `netctl` | `red-02`: 모듈 없음 | `green-02`: 16 OK, 실제 커널 포함 |
| 3 `policies` | `red-03`: **수정 전 사본**에서 14 errors | `green-03`: 14 OK |
| 4 `generator` | `red-04`: **수정 전 사본**에서 7 errors + 1 failure | `green-04`: 8 OK |
| 5 `design`/`unstable` | `red-05`: 모듈 없음 | `green-05`: 12 OK |
| 6 `analyze` | `red-06`: 모듈 없음 | `green-06`: 9 OK |

- slice 3의 red는 기존 결함을 재현한다. 실패한 poll을 빈 큐로 읽는 동작이다.
- slice 4는 처음에 harness import 경로 오류가 있었다. 경로를 고친 뒤 행동 red를 다시 기록했다.

**테스트 작성 오류로 고친 것** (구현 수정 아님)
- netctl ops index 오류(t=28 단절은 3번째 change)
- executor delay 주입: tie-break로 cpu-c가 선택되는데 cpu-e에만 delay를 걸었다.
- live fake의 worker `/health`에 pod_uid가 없었다.
- design 테스트의 plan 형태(helper 위치)를 green 이전에 인터페이스에 맞췄다.

**정직한 편차**
- `live.py`(baselines 사본 수정)는 `test_live.py`보다 먼저 썼다. `test_live` 첫 실행의 1 error는 fake 결함이었다.
- `unstable.py`의 release 테스트는 red-05에 포함되었다.
- `test_cell_loopback.py`(70 s 통합)와 Pod 보안 manifest 테스트는 구현 뒤에 추가한 회귀/통합 시험이다.

**최종 전체 실행**: `logs/green-all.txt`, 86 tests OK, 97 s.
- 실행 명령은 uv(numpy 2.2.6, pandas 2.2.3)를 쓰고 `PYTHONDONTWRITEBYTECODE=1`을 둔다.
- 기존 import를 재사용하는 시험은 필요한 범위만 돌렸다. 기존 파일을 바꾸지 않았으므로 기존 suite는 재실행하지 않았다. 재사용한 unchanged `dag_worker`·`gateway`는 `test_executor`와 `test_cell_loopback`에서 실제 HTTP로 실행된다.

**필수 검증 대응**

| spec 항목 | 테스트 |
|---|---|
| trace가 실행 구간을 정확히 덮음, 범위 밖·중복·공백·비승인값 거부 | `test_traces.Validation` |
| 미래 설정이 정책 payload에 없음 | `test_executor.TraceContract`(400 거부), `test_live` generator 입력 키 검사 |
| owned netns만, 실행 중 flush 없는 change, 종료·오류 시 restore | `test_netctl.RunnerSchedule`, `test_netctl.RealKernelChange`, `test_live.FailureHandling`, `test_cell_loopback`; netns guard는 `RESEARCH_LINK_CONTROL` |
| 같은 trace·자원·품질·관측 계약 | `test_design.Schedule`(같은 seed cohort), `test_live`(동일 body 계약) |
| no-network은 비용 항만 제거 | `test_policies.NetworkCostAblation`(손계산 2050/50 ms, 같은 열거 32, 큐 반영 유지) |
| 실패 poll이 0큐·새 성공시각이 되지 않음, stale fallback 기록 | `test_policies.FailedPolls`/`StaleFallback`, `test_executor.ObservationContract`(실제 worker 종료) |
| absolute deadline, inflight/drain, salvage | `test_executor.TraceContract`(cancel timeout 후 inflight 0, stop 후 arrival당 1행), `test_cell_loopback`(drain idle) |
| cleanup UID | 변경 없는 `ownership.cleanup`(baselines 경로 그대로) |
| plan/dry-run hash 결속 | `test_design.ReleaseBinding`(trace 표, 예산, 배포 소스, 사본 원본 drift; release 없으면 kubectl 0회로 rc 2) |

## 5. 네트워크 제어·trace·시간 정렬·관측 (요약; 상세는 문서 3–5절)

**제어 방식**
- netctl sidecar(:8081)가 trace를 가진 유일한 구성요소다.
- cell마다 phase 0을 설치하고, 경계마다 pyroute2 `change`로 tbf와 netem을 바꾼다. root를 삭제하지 않는다.
- 복원은 good으로의 change다. teardown은 drain 후와 종료 시에만 한다.
- 승인된 trace 이름과 hash, run_id, 10.x Pod IP만 받는다.

**1차 근거**
- pinned wheel 소스: `sched_netem.py`(loss는 percent2u32, jitter·delay는 µs time2tick, PRNG seed 속성 없음), `iproute/linux.py`의 tc `change`(RTM_NEWQDISC, NLM_F_REPLACE)
- 실제 커널 시험(`logs/kernel-change-probe.json`)
  - in-place change: netem qlen 64→64, drop 보존
  - 삭제 후 재생성: qlen 64→0, 카운터 0
- Context7와 GitHub MCP는 사용하지 않았다. 외부 다운로드도 없다.

**재현 한계**
- seed가 없으므로 설정 수준 재현만 가능하다. 실제 카운터를 보존한다.
- jitter는 균일 분포이고 재정렬이 생길 수 있다.
- tbf change가 token을 다시 채우는지는 미확인이다. 영향 상한은 burst 4096 B다.

**정렬**: 문서 4절의 midpoint 방식이다.
- cell마다 `align-<label>.json`(오차 상한, 허용 250 ms 여부)을 남긴다.
- loopback 상한은 허용값 미만이었다. 실장비 값은 미측정이다.

**관측**: worker별 마지막 성공 상태를 보존하고, 실패 poll은 오류만 기록한다. 성공 기록이 없거나 3 s를 넘은 stale 상태이면 해당 정책의 static plan을 쓰고 fallback으로 기록한다. 모든 정책이 같은 규칙을 따른다.

## 6. 실행 시간·자원 추정 (계획값, 측정 아님)

**자원**: baselines와 같은 Pod 구성이다.
- worker 4개
  - cpu-c: ser0002 CPU
  - npu-c: ser0002 Intel NPU
  - cpu-e0: dev0003 CPU
  - jgpu-dev0009-cu130: dev0009 Jetson CUDA
- cache: ser0002
- gateway + netctl: ser0002. netctl은 NET_ADMIN만 가지며 limit 128 Mi다.
- generator: dev0003, limit 1 Gi
- worker 자원과 메모리 상한(cpu-c 8 Gi 포함)은 바꾸지 않았다. asset은 3,985,174,922 B다.

**시간**
- cell당 약 89 s, 36 cell이면 약 53분이다.
- 준비 단계(배포, 업로드, readiness, 보정 profile, probe)는 약 10–20분이다. 이전 compare run을 근거로 했다.
- 합계 약 65–80분으로 추정한다. 상한은 `collection_s` 6,000 s, Pod activeDeadline 12,600 s, wall 예산 3 h다.

**요청량**: 약 3,456건(36 cell × 60 s × 1.6 rps)이다.

## 7. 제안 live 명령 (독립 리뷰 + coordinator release 후에만; 이번 dispatch에서 실행하지 않음)

**1) (선택) 새 읽기 전용 preflight**: 노드 상태가 바뀌었으면 새 plan이 필요하다.

```bash
rtk proxy python3 experiments/quality-network-baselines/capture.py --out artifacts/unstable-network/preflight-<UTC>
```

**2) dry-run 재확인**: drift `[]`이어야 한다.

```bash
PYTHONDONTWRITEBYTECODE=1 rtk proxy python3 experiments/unstable-network/unstable.py dry-run --plan artifacts/unstable-network/plan-20261027T091525Z --run-id un-collect-r1
```

**3) coordinator가 release 파일을 작성한다**: `.scratch/unstable-network-20261027/release-collect-r1.json`. 내용은 dry-run `release_binding`과 같아야 한다.

```json
{"release":"RELEASE","stage":"collect","run_id":"un-collect-r1",
 "plan_sha256":"407abd4912c21303a396f1fa0b6ff1b224da08d5336cff7a65729c0ae920987e",
 "shipped_sha256":"0c527b55a58035c31709234e76559e1faa503802b1cb401a7132671176d08693",
 "budgets_sha256":"dddf9692ad1df7973fb746dcb290b5ee6b7b024f7f2aceeac1a8679f087d8157",
 "traces_sha256":"cec8c6d4f61b47c214f0bbb2bf6f42917d4087bd153e587f204ca43100067fb3",
 "schedule_sha256":"00eb908d62f2b6355099d73751d04d78cb6da0749d4a881f2bc603a78852dc40"}
```

**4) 수집**: 출력은 `artifacts/unstable-network/un-collect-r1/`이고 exclusive로 만든다.

```bash
PYTHONDONTWRITEBYTECODE=1 rtk proxy python3 experiments/unstable-network/unstable.py execute --plan artifacts/unstable-network/plan-20261027T091525Z --run-id un-collect-r1 --release .scratch/unstable-network-20261027/release-collect-r1.json
```

**5) 오프라인 분석**

```bash
PYTHONDONTWRITEBYTECODE=1 rtk proxy python3 experiments/unstable-network/unstable.py analyze --plan artifacts/unstable-network/plan-20261027T091525Z --run artifacts/unstable-network/un-collect-r1 --out artifacts/unstable-network/un-collect-r1-analysis.json
```

**순서표**(seed 20261027, `assign_factorial_runs`)는 dry-run `order`에 있다. b0에서는 burst → slow → stable, b1에서도 burst → slow → stable이다. trace 순서는 helper의 결과이며 조정하지 않았다. 각 정책은 6개 group에서 각 위치를 1번씩 차지한다.

## 8. 명령·절차 편차 (보존)

- coordinator 지시(`rtk proxy` 사용) 전에, 초기 조사에서 rtk 없이 실행한 명령이 있다.
  - 읽기: `cat`, `sed`, `head`, `grep`, `ls`, `python3 -c`
  - 도구 확인: `which`, `orca orchestration check/send`, `orca skills list/get`, `claude auth status`(안전 필드만 출력), `env` 이름 검사
  - 환경 확인: 비특권 `unshare -rn` tc 시험, pyroute2 wheel을 scratchpad에 압축 해제
- 광범위한 `find /` 검색을 2회 했다(tdd skill 위치, pyroute2 wheel 위치). 이후에는 하지 않았다.
- initial preflight는 다시 수집하지 않고 coordinator의 `preflight-initial`(2026-10-27T08:50:26Z)을 재사용했다. coordinator 지시에 따른 것이다.
- `PYTHONDONTWRITEBYTECODE`를 설정하기 전의 테스트 실행이 재사용 디렉터리의 gitignored bytecode cache를 갱신했을 수 있다.
  - 해당 파일: `experiments/quality-network-baselines/__pycache__/{gateway,dag_worker}.cpython-312.pyc`, `experiments/quality-network-heterogeneous/__pycache__/worker.cpython-312.pyc`
  - 소스가 아니므로 삭제하지 않았다. 이후 모든 실행에는 `PYTHONDONTWRITEBYTECODE=1`을 썼다.
  - 이 디렉터리 자신의 `__pycache__`는 지웠다.

## 9. 남은 제한

- live 미실행이다. 다음은 실장비에서 확인되지 않았다.
  - Pod veth의 pyroute2 change
  - netctl HTTP sidecar 기동(wheel 추출 후 `runpy`)
  - 실제 정렬 오차
  - 단절 시 TCP와 gateway 동작
  - Jetson/NPU의 이번 소스 기반 실행
- netctl endpoint는 무인증이다(run_id 결속만). 영향은 owned netns로 제한된다.
- stale fallback은 단절 중 cloud를 쓰는 static 계획을 고를 수 있다. 사전 규칙이며 결과에 그대로 남는다.
- max_inflight 8로 낮췄지만 cpu-c OOM 가능성은 남아 있다. fault 감지 시 이후 cell을 중단하고 장애 영향으로 기록한다.
- 품질은 diagnostic이다(eval 0.355 < 0.40). 독립 holdout이 아니다.
- 2 block 탐색 실험이며 유의성 주장은 없다. 정책 이름은 논문 재현이 아니다.
