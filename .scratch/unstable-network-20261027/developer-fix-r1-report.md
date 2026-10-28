# developer-fix-r1-report — R1 리뷰 결함 수정 (task_54f01ab05073 / ctx_f24a2e168825)

작성: Claude Code, 2026-10-27. 기준 commit `d87bde1fde21f014c1baebd1132673984e960a51`(이후 commit 없음). commit/push, kubectl, live Pod 변경, 커널 변경, 중첩 worker는 하지 않았다. 기존 dirty 변경과 다른 worker 산출물은 건드리지 않았다.

## 1. 결과 요약 (수준 구분)

| 수준 | 상태 |
|---|---|
| 코드 수정 | `live.py`, `analyze.py`, `generator.py`, `design.py`, `unstable.py` + 테스트 4개, 실험 문서 |
| 관련 테스트 | 회귀 31개 추가. red 기록 후 green. 전체 unstable-network suite **117 tests OK, 111.2 s**(이전 86) |
| 리뷰 재현 | 두 리뷰의 재현 시나리오를 수정 후 동작을 단언하도록 각색해 재실행. rc 0, stderr 0 B |
| 새 plan / dry-run | `artifacts/unstable-network/plan-20261027T105645Z`, plan sha256 `e57600bb95e986cad976b083206cc51e2bef160e5b2387b2a0cf4ce34bdce57e`, drift `[]` |
| 보존 확인 | preserved source 75개 hash 변화 0. 이전 plan `407abd49…987e` 불변. review snapshot drift `[]` |
| live 수집 / 실장비 성능 / 공식 수용 | **미실행.** 별도 dispatch(R2 리뷰 + coordinator release 뒤) |

## 2. 인증·모델 증거 (비밀값 제외)

- `claude auth status` 안전 필드: `loggedIn=True`, `authMethod=claude.ai`, `apiProvider=firstParty`, `subscriptionType=max`. email/org는 출력하지 않았다.
- `ANTHROPIC_*`, `CLAUDE_CODE_USE_*` 환경변수 없음(이름만 검사).
- **실제 모델(transcript)**: 이 세션 transcript `~/.claude/projects/-home-jinuk-codex-work-scheduler/1ddf2ad3-bcfb-440e-b8e9-3cc66b62ba11.jsonl`의 assistant 메시지 `message.model`을 셌다. 측정 시점에 `{'claude-opus-5-5': 40}`, 다른 모델 0이다. 세션 system 정보도 `claude-opus-5-5`다. API, 다른 공급자, fallback은 쓰지 않았다.
- lifecycle: coordinator 지시 후에는 `/home/jinuk/.orca/cli/bin/orca`만 `rtk proxy`로 썼다(8절 편차 참조).

## 3. 결함별 수정과 근거

### F1 (standards P1) — 수락/불확실 시작 오류 뒤 generator 정지·salvage

- 시작 이후 모든 오류(`/trace` 응답 유실, netctl `/start` 실패, `/status` timeout)는 `trace-<label>.error.json`(stage `generator_start` | `netctl_start` | `wait`)에 원래 오류로 남는다. 이후 단계가 이 기록을 덮어쓰지 않는다.
- `Live.stop_generation`: generator `/stop` → `cancel_s + 30 s` 안에서 `/status` poll. label이 running이면 `/stop`을 다시 보낸다(늦게 처리된 `/trace`가 stop flag를 지울 수 있음). 결과는 `generation-<label>.json`의 `terminal` | `not_admitted` | `unresolved`.
- `Live.salvage`: terminal이면 전체 ledger. 아니거나 그 읽기가 실패하면 generator의 새 `/ledger?label=L&partial=1`(표시된 부분 ledger, 예정 도착마다 행 1개, 미완료 status None). 실패한 읽기는 `ledger-<label>.error.json`.
- `quiescence`가 generator `/status`도 본다. running trace나 inflight가 있으면 idle이 아니다. worker idle을 generator 정지로 보지 않는다.
- `generation_unresolved`, `salvage_failed`는 fence(5절)다. 해결된 경우(terminal, not_admitted)에는 다음 cell로 진행한다.
- generator 변경: `ledger(label, partial=False)`. 일반 `/ledger`는 여전히 running trace를 400으로 거부한다. 모든 ledger에 `state`, `partial`이 있다. 실행 중 행 복사는 동시 변경 시 재시도하는 JSON snapshot이다.

### F2 (spec P1, standards P2) — 실패/불명 drain이 teardown과 이후 cell을 막음

- `finish_cell`: restore → stop/settle → salvage → gateway → drain → netctl status → **teardown은 generation 해결 + salvage 성공 + idle barrier일 때만**.
- 그 외에는 `fault-<label>-cell.json`(kinds), `netctl-teardown-<label>.skipped.json`을 남기고 이후 cell을 모두 skip한다. 응답 불가 drain도 idle이 아니다(unknown = fence).
- cell 전 drain 실패도 같은 fence로 바꿨다. 이전에는 skip만 하고 다음 cell을 시도했다.
- run 끝 `close_network`: fence나 중단 뒤에는 `netctl-emergency-teardown.json`(`emergency=true`, `idle_barrier=false`, `fenced_by`). fence가 없으면 `netctl-final-teardown.json`(`emergency=false`). 비상 정리는 owned network control 삭제일 뿐 idle barrier가 아니다.

### F1 (spec P1) — timing 유효성 (수집 전 문서 고정)

- `analyze.timing_validity`: `unknown`(align 또는 netctl status 없음) / `invalid` / `valid`. valid 조건:
  - 정렬 상한이 유한한 0 이상의 수
  - 동결 trace 이름과 hash
  - 경계마다 성공한 change가 순서대로 1개, 동결 조건, 실제 적용 시작·종료 시각
  - window 끝 도달
  - **총 불확실성 ≤ 250 ms**. 경계 B의 generator 기준 구간은 [s − bound, e + bound](s, e = 실제 적용 시작·종료, netctl 시작 기준 offset)다. 총값 = max(bound, 경계별 max(B−lo, hi−B)). 그래서 적용 지연도 허용값에 들어간다.
- `analyze.comparable_outputs`: timing valid이고 ledger가 완전(`state=done`, partial 아님)할 때만 phase/회복을 계산한다. 아니면 둘 다 `None` + `suppressed_because`. 불확실성 0으로 계산하지 않는다. 전체 cohort의 결과 수는 항상 남는다.
- phase cohort의 `boundary_uncertain`은 실제 [lo_s, hi_s] 구간을 쓴다.
- 회복: R은 `max(B, hi_s)` 이후에서만 찾는다(`search_from_s`). 늦은 복원 전에 회복을 주장하지 않는다. `time_to_recovery_s = R − B`는 유지한다.
- 수집기: 시작 정렬이 250 ms를 넘으면 `align.comparable_timing=false`, `collection.json`의 `timing_invalid`. fleet 장애가 아니므로 수집은 계속한다.
- 문서 `docs/experiments/unstable-network-20261027.md` 4·6·7·8절에 live 수집 전 규칙으로 기록했다.

### F3 (spec P2) — 하나의 절대 3 h wall 예산

- `live.WallBudget`: execute 진입 직후(release 검증·준비 전) 시작. `work_until = 끝 − 300 s`(`cleanup_reserve_s` 900 → **300**), `cleanup_until = 끝 − 40 s`(collect.cleanup 안 진단 snapshot의 자체 timeout 40 s 몫).
- `UnstableSession.call`: 모든 kubectl timeout을 남은 시간으로 자른다. 남은 시간이 없으면 kubectl을 부르지 않고 거부를 기록한다(`*.deadline.json`, 빈 stdout, stderr에 이유). timeout된 호출은 기존대로 부분 stdout/stderr를 남긴다. 상속된 `pod_states`/`owned_state`(고정 60 s, clamp 불가)는 60 s가 남을 때만 시작한다.
- `Live.control`: 모든 HTTP 제어 호출이 wall 한도로 잘린다. `job`, readiness loop, `wait_trace`, `stop_generation`도 같은 한도를 쓴다.
- 수집 deadline = `min(work 한도, 배포 후 + collection_s)`. 준비 시간은 수집 시간에서 빠진다.
- cell은 최악 소요(사전 barrier + 시작·window·cancel·대기 여유 + 마무리 전체)가 들어갈 때만 시작한다. 아니면 `wall budget` 사유로 skip. 그래서 cell 마무리는 예비 시간이 필요 없다.
- run 끝 netctl teardown과 Pod cleanup만 예비 시간을 쓴다(`session.begin_cleanup()`). 한도 안에 확인하지 못한 cleanup은 `cleanup_verified=false`와 남은 객체로 보고한다. `run-status.json`에 `cleanup_verified`, `wall_budget`을 추가했다.
- 다른 모듈의 signal handler는 바꾸지 않았다.

## 4. TDD 기록

seam은 기존 승인 경계를 그대로 썼다: release-bound collector(`test_live`, Pod HTTP·kubectl·시계만 fake), 오프라인 분석(`test_analyze`, 손으로 계산한 예), generator HTTP/ledger(`test_executor`, 실제 loopback), 전체 cell loopback(`test_cell_loopback`). kubectl은 subprocess 경계에서만 fake로 두어 실제 `collect.cleanup` 코드를 실행했다.

| 단계 | 로그 | 결과 |
|---|---|---|
| red 1 | `fix-r1/logs/red-live-analyze.txt` | 45 tests, 35 errors (인터페이스 부재: `budget`, `timing_validity`, `WallBudget`) |
| red 1 | `fix-r1/logs/red-executor-loopback.txt` | partial ledger HTTP 400, loopback setUpClass가 주입 timeout을 그대로 전파 |
| red 2 (행동) | `fix-r1/logs/red-live-behavior.txt` | `budget` 인자만 추가한 뒤 25 tests: 4 failures + 12 errors. 예: 실패 drain 뒤 `('netctl','/teardown')`이 호출됨, 불확실 시작 뒤 `/stop` 0회, running generator가 있어도 idle, salvage 실패가 fault 아님 |
| green | `fix-r1/logs/green-all.txt` | 117 tests OK, 111.212 s, rc 0 |

새 테스트 31개
- `test_live` 16: `GeneratorStopAndSalvage` 6, `DrainFence` 4, `TimingMark` 1, `WallBudget` 3, `CleanupReserve` 2
- `test_analyze` 11: `TimingValidity` 7, `ComparableOutputs` 4
- `test_executor` 1: running trace는 표시된 partial ledger로만 읽힘
- `test_cell_loopback` 3: standards F1 재현을 실제 HTTP로 각색(`StatusTimeoutAfterStart`)

테스트 수정과 약화 여부
- 기존 테스트의 단언은 바꾸지 않았다. `test_live.Pods` fake에 generator 상태(label별 trace, `/stop`, partial ledger)를 더했고, 기본 동작은 이전과 같다.
- 구현 중 고친 테스트 결함 1건: loopback `/status` timeout 주입이 새로 생긴 cell 전 barrier의 generator poll에 먼저 걸렸다. `/trace` 이후에만 주입하도록 고쳤다. fake `status_fail`도 같은 이유로 trace 수락 뒤에만 적용한다.
- fake clock 증명: `WallBudget.test_preparation_time_is_taken_from_the_collection_budget`(준비가 work 한도 200 s 전까지 쓰면 36 cell 모두 `wall budget` skip, arm 0회), `CleanupReserve`(work 한도 뒤 +10 s에도 cleanup 검증 성공, 모든 kubectl timeout ≤ 남은 wall; `cleanup_until − 2.5 s`에서는 `cleanup_verified=false`, 객체 남음, deadline 기록 있음).
- 시간 경계 stub 1건: `WallBudget` 첫 테스트는 `deploy/configure/profile/calibrate_network`를 경과 시간만 내는 stub으로 바꿨다. 이 단계들은 kubectl/Pod HTTP 경계이고, 검사 대상은 run의 단일 wall deadline이다.

## 5. 리뷰 재현의 처리

원본 재현 스크립트와 증거는 수정하지 않았다(`review-spec-evidence/`, `review-standards-evidence/`). 원본 스크립트는 결함을 단언하므로 이제 실패하는 것이 정상이다. 같은 fixture와 주입을 쓰고 수정 후 동작을 단언하는 각색본을 만들었다.

```sh
rtk proxy bash -c 'cd /home/jinuk/codex-work/scheduler && PYTHONDONTWRITEBYTECODE=1 timeout 300 python3 .scratch/unstable-network-20261027/fix-r1/reproduce_after_fix.py > .scratch/unstable-network-20261027/fix-r1/reproduce-after-fix.stdout 2> .scratch/unstable-network-20261027/fix-r1/reproduce-after-fix.stderr'
# rc=0, stderr 0 B; 결과 fix-r1/probe-s18aezrh/reproduction-results.json
```

| 시나리오(리뷰 원문) | 수정 전(리뷰 결과) | 수정 후 |
|---|---|---|
| 정상 대조(새로 추가) | — | timing valid, 총 21.0 ms, 비교 가능, 회복 2개 계산 |
| alignment/status 없음 | 87 ok, phase/회복 정상 출력, 불확실성 0 | `unknown`, phase/회복 `None`, counts `{ok: 87}` 유지 |
| 1,000 ms 상한 | 회복 비율 1.0 | `invalid`(1001.0 ms > 250), 억제 |
| 첫 change 실패 | 두 회복 모두 `recovered` | `invalid`(1/6 change, 실패, window 끝 없음), 억제 |
| t=30 복원 5 s 지연 | 복원(35 s) 전인 30.917 s에 회복 보고 | `invalid`(5021.0 ms), 억제. valid로 가정해도 회복은 hi_s 35.021 이후에서만 찾음(`test_recovery_is_never_claimed_before_the_applied_restoration`) |
| collector `/start` 1 s 지연 | 1,030 ms, fault 없이 계속 | `comparable_timing=false`, `timing_invalid=[label]` |
| drain 내내 큐 비어 있지 않음 | `idle=false` 뒤 teardown, 다음 cell arm | teardown 없음, `drain_not_idle` fault, 다음 cell skip, arm 1회, 끝 teardown은 `emergency=true, idle_barrier=false` |
| 수락 직후 worker busy | teardown 실행 | teardown 없음, fault |
| 실제 loopback `/status` timeout 1회 | `/stop` 없음, generator running, 109행 pending, ledger 없음, drain idle, netctl 해제 | `/stop`이 `/ledger`보다 먼저, generator `done`, ledger 109행(모두 `not_sent`), 원래 오류 보존(stage `wait`), drain에서 generator running `[]`, fault 없음 |

## 6. 명령과 결과 (모두 `/home/jinuk/codex-work/scheduler`, `rtk proxy`)

```sh
# 전체 suite (최종 변경 뒤 1회): rc 0, Ran 117 tests in 111.212s, OK
rtk proxy bash -c 'cd experiments/unstable-network && PYTHONDONTWRITEBYTECODE=1 uv run --python /usr/bin/python3 --no-build --with numpy==2.2.6 --with pandas==2.2.3 python -m unittest discover -s . -p "test_*.py" > ../../.scratch/unstable-network-20261027/fix-r1/logs/green-all.txt 2>&1'

# 새 plan: rc 0, plan sha256 e57600bb…ce57e, workers cpu-c/npu-c/cpu-e0/jgpu-dev0009-cu130, coverage ok, 144 plans, asset 3,985,174,922 B, 36 cells
rtk proxy bash -c 'PYTHONDONTWRITEBYTECODE=1 uv run --python /usr/bin/python3 --no-build --with numpy==2.2.6 --with pandas==2.2.3 python experiments/unstable-network/unstable.py plan --inventory .scratch/unstable-network-20261027/preflight-after-reset-20261027T1030Z/node-inventory.json --export artifacts/quality-network-heterogeneous/export-20261023T075427Z --deps artifacts/quality-network-heterogeneous/deps-20261023T075919Z --deps artifacts/quality-network-heterogeneous/deps-20261023T080028Z'

# dry-run: rc 0, stderr 0 B, drift []
rtk proxy bash -c 'PYTHONDONTWRITEBYTECODE=1 python3 experiments/unstable-network/unstable.py dry-run --plan artifacts/unstable-network/plan-20261027T105645Z --run-id un-collect-r2'
```

**dry-run `release_binding`** (`fix-r1/logs/dry-run.json`; release 파일은 coordinator가 작성)

```json
{"release": "RELEASE", "stage": "collect", "run_id": "un-collect-r2",
 "plan_sha256": "e57600bb95e986cad976b083206cc51e2bef160e5b2387b2a0cf4ce34bdce57e",
 "shipped_sha256": "e358c10d53cde1874b7cda9027e81245028eb50dcfe192a69f0a51b09dad4bc1",
 "budgets_sha256": "56dd2a43de55c4cb9f41ab52cc9b683989a9be9a6c6b8d28029d1038a47285f9",
 "traces_sha256": "cec8c6d4f61b47c214f0bbb2bf6f42917d4087bd153e587f204ca43100067fb3",
 "schedule_sha256": "00eb908d62f2b6355099d73751d04d78cb6da0749d4a881f2bc603a78852dc40"}
```

**이전 plan과 비교** (새 plan이 같아야 하는 항목)
- 같음: `schedule`(schedule_sha256 `00eb908d…` 동일), `traces`, `trace_table`, `workers`, `candidates`, `model_provenance`, `pairs`, `quality`, `policies`, `recovery`, `design`(seed 20261027), `inputs`, `corpus_sha256`, `asset_bytes`, `reused_frozen_sources`, `not_deployed`, `node_image_storage`.
- 다름(의도한 것)
  - `budgets.cleanup_reserve_s` 900 → 300
  - `planner_sources`/`copied_sources`의 copy hash 5개(analyze, design, generator, live, unstable)
  - `shipped_sources`의 `generator.py`
  - `inventory`: 새 preflight `node-inventory.json` sha256 `dabed0d1…4704`
  - `assets_manifest_sha256`: 차이는 plan 디렉터리 안 `jellybean.tar`의 source 경로뿐이다. 내용 sha256은 같다.
- 이전 plan `plan-20261027T091525Z`는 파일 hash가 그대로다(`fix-r1/preserved-hash-check.json`, plan.json `407abd49…987e`). 소스가 바뀌었으므로 이전 plan의 dry-run은 이제 drift이며 execute에 쓰지 않는다.

**보존 hash**: `coordinator-provenance.json`의 preserved_sources 75개를 다시 계산했다. 변화 0(`fix-r1/preserved-hash-check.json`). `review-source-r1` 16개는 manifest와 같다(drift `[]`).

## 7. review-source-r1 대비 실제 diff

전체 patch: `fix-r1/diff-vs-review-source-r1.patch`(`git diff --no-index review-source-r1/<f> <현재 파일>`; exit 1은 차이 있음이라는 뜻).

| 파일 | 변경 | 수정 후 sha256 |
|---|---|---|
| `live.py` | +253 −39 | `40c6cfdb…14a0` |
| `analyze.py` | +110 −17 | `dc091a8f…98b5` |
| `generator.py` | +22 −6 | `4adb5190…5f94` |
| `unstable.py` | +11 −4 | `2d4ce432…a763` |
| `design.py` | +4 −1 | `4b4e163f…64db` |
| `test_live.py` | +317 −10 | `24e2c07e…7de7` |
| `test_analyze.py` | +107 | `b572c5d4…74b4b` |
| `test_cell_loopback.py` | +59 | `121615a0…036c` |
| `test_executor.py` | +17 | `2d6e76d0…345e` |
| 실험 문서 | 4·6·7·8·9절 | (plan 결속 대상 아님) |
| 변경 없음 | `netctl.py`, `policies.py`, `traces.py`, `test_design.py`, `test_netctl.py`, `test_policies.py`, `test_traces.py` | manifest와 같음 |

**새 파일**
- `.scratch/unstable-network-20261027/developer-fix-r1-report.md`(이 파일)
- `.scratch/unstable-network-20261027/fix-r1/`: `reproduce_after_fix.py`, `reproduce-after-fix.{stdout,stderr}`, `probe-s18aezrh/`, `logs/{red-*,green-all,plan,plan.stderr,dry-run.json,dry-run.stderr}`, `preserved-hash-check.json`, `diff-vs-review-source-r1.patch`
- `artifacts/unstable-network/plan-20261027T105645Z/`: create-exclusive. 파일은 `plan.json`, `assets-manifest.json`, `corpus.json`, `catalog.{json,csv}`, `jellybean.tar`

## 8. 편차 (보존)

- coordinator 지시 전에는 일부 shell 명령을 `rtk` 없이 실행했다.
  - 읽기(`cat`, `sed`, `grep`, `ls`, `wc`, `sha256sum`, `cmp`)
  - `which rtk`, `claude auth status`(안전 필드만), 환경변수 이름 검사
  - bare `orca orchestration send/check`(heartbeat 2회, check 1회). 처음 두 heartbeat는 stderr를 버렸다.
  - 이후 모든 명령은 `rtk proxy`, lifecycle은 `/home/jinuk/.orca/cli/bin/orca`만 썼다.
- `rtk proxy bash -c '…'` 안의 따옴표 없는 heredoc 때문에 generator docstring의 backtick 두 개가 셸 명령으로 실행되었다(`state`, `partial` → "command not found", 부작용 없음). 문구가 빠진 것을 바로 고쳤다.
- 같은 이유의 따옴표 충돌로 `design.py` 편집 스크립트 1회가 쓰기 전에 assert로 실패했다. 아무것도 쓰지 않았고 Edit 도구로 다시 했다.
- `git diff --no-index` 결과에서 `review-source-r1` 대비 `experiment-doc.md`는 이 보고서 작성 직전 문서 9절 수정까지 반영한 patch로 다시 만들었다.

## 9. 남은 제한 (pending)

- live 미실행. 실제 정렬 오차, netctl 적용 지연, veth change, 단절 시 TCP, Jetson/NPU의 이 소스 기반 실행은 실장비에서 측정되지 않았다. 실제 cell 중 몇 개가 timing `valid`가 될지는 모른다. 250 ms에는 적용 지연이 포함되므로 실장비에서 비교 가능 cell이 줄 수 있다. 사후에 허용값을 바꾸지 않는다.
- generator `/stop`은 프로세스 전체 flag다. 늦게 도착한 `/trace`가 flag를 지우는 경쟁은 settle 창 안에서 `/stop`을 다시 보내 줄였지만, 창이 끝난 뒤 도착한 `/trace`는 막지 못한다. 그 경우 다음 cell 전 barrier가 running generator를 보고 fence한다.
- partial ledger는 snapshot 시점의 행이다. 이후 완료된 요청은 반영되지 않으며, 분석은 그런 cell을 비교 불가로 두고 `rows_without_terminal_status`를 센다.
- collect.cleanup 안의 protected-after snapshot은 이 모듈이 자를 수 없다(자체 40 s). 그 40 s를 `cleanup_until`로 남겨 두었다. 그래도 kubectl 자체의 `--request-timeout` 동작은 실장비에서 확인하지 않았다.
- cell 최악 소요 추정(약 9분)은 예산 끝 근처에서만 cell 수를 줄인다. 예상 전체(준비 10–20분 + 36 cell × 약 89 s)는 3 h 안이다. 측정값은 아니다.
- 품질은 diagnostic(eval 0.355 < Qmin 0.40)이며 독립 holdout이 아니다. 2 block 탐색 실험이고 유의성 주장은 없다.
- 이 보고는 독립 리뷰가 아니다. R2 독립 리뷰와 coordinator release 뒤에만 live를 실행한다.
