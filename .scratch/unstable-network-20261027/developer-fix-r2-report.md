# developer-fix-r2-report — R2 잔여 결함 2건 수정 (task_988d65897b5e / ctx_4b51cc2b6c79)

작성: Claude Code, 2026-10-27. 기준 commit `d87bde1f…`(이후 commit 없음). commit/push, kubectl, live 실행, cluster 쓰기, 중첩 worker, 세 번째 리뷰는 하지 않았다.

## 1. 요약 (수준 구분)

| 수준 | 상태 |
|---|---|
| 코드 수정 | `live.py`(+89 −6), `unstable.py`(1줄), `test_live.py`(+190 −17), 실험 문서 4곳 |
| 관련 테스트 | red 기록 후 green. `test_live` 35 + `test_analyze` 20 OK, `test_cell_loopback` 8 OK(81 s, 실제 loopback) |
| 리뷰 재현 | 두 리뷰의 R2 probe를 수정 후 동작을 단언하도록 각색. rc 0, stderr 0 B |
| 새 plan / dry-run | `artifacts/unstable-network/plan-20261027T111532Z`, plan sha256 `ee0aba54e2dd92775e2e06526dcfa25e27562efbfc0c2b410beab6bd12e3c1e7`, drift `[]` |
| 보존 | preserved source 75개 변화 0, review-source-r1(16)/r2(17) drift `[]`, 이전 plan 2개 불변 |
| live / 실장비 성능 / 공식 수용 | **미실행.** coordinator 최종 검증과 별도 release 뒤 |

## 2. 모델·인증 (비밀값 제외)

- `claude auth status` 안전 필드: `loggedIn=True`, `authMethod=claude.ai`, `apiProvider=firstParty`, `subscriptionType=max`.
- `ANTHROPIC_*`, `CLAUDE_CODE_USE_*` 환경변수 없음(이름만 검사).
- 이 세션 transcript(`~/.claude/projects/-home-jinuk-codex-work-scheduler/bc9ec865-f8bc-4fbd-8196-79ca62a46d83.jsonl`)의 assistant `message.model`: `{'claude-opus-5-5': 99}`, 다른 모델 0. API, fallback은 쓰지 않았다.
- Orca guide: `/home/jinuk/.orca/cli/bin/orca` 1.4.235의 `skills get orchestration`을 읽었다.

## 3. 수정

### F1 (spec R2 P1) — 정상 완료 시 동결 60 s window 전체를 기다림

- `Live.trace_cell`: generator가 terminal이면(정상 완료) restore 전에 `wait_window`를 호출한다. 시작 이후 오류 경로(`/trace` 유실, netctl start 실패, `/status` 오류)는 기다리지 않는다. 기존대로 restore → `/stop` → salvage로 바로 간다.
- `Live.wait_window`: netctl `/status`를 `poll_s`(5 s) 간격으로 읽어 `completed`(window_end) 또는 `failed`(netctl이 스스로 good 복원)까지 기다린다.
  - 상한: `min(net_recv + netctl_delay_s + window + WINDOW_SLACK_S(10 s), wall 한도)`. 앞의 세 항은 수집기 시계에서 netctl t0가 가장 늦을 때의 window 끝이다.
  - 각 호출은 control timeout과 wall 한도로 잘린다. 무한 대기는 없다.
  - 기록: `window-<label>.json`(`completed` | `failed` | `incomplete` | `unknown`, `until_s`, polls).
  - `completed`가 아니면 `timing_invalid`에 넣는다. analyze의 window_end 조건은 바꾸지 않았다(약화 없음).
- generator와 정책 입력에는 trace 정보를 더하지 않았다(`generator.py`, `policies.py` 변경 없음).
- `cell_need_s`: 마지막 status 호출 1회 몫으로 control timeout 1회(30 s)를 더했다.

### F2 (spec R2 P2, standards R2 P2) — 절대 wall 예산

- `UnstableSession.call`: stream verb는 먼저 `stream_guard`를 통과한다. 그 뒤 실제 kubectl 시작 시점의 남은 시간으로 timeout을 다시 자르고 `ownership.Session.call`을 부른다. 부분 stdout은 그대로 남는다.
- `stream_guard`: `qualify.HetSession.call`의 stream guard와 규칙·기록 파일·문구가 같다(소유 run Pod, edge Pod 거부, 새 UID 소유 GET, logs skip / 그 외 refuse).
  - 상속된 `HetSession.call`은 GET 전의 timeout을 그대로 넘긴다. 그래서 동결 모듈을 바꾸지 않고, 그 분기만 이 모듈에 두었다.
  - 이 분기 밖의 동작은 바뀌지 않는다.
- `live.cleanup(session)`(`unstable.py`가 `ownership.cleanup` 대신 호출, 1줄): protected-after snapshot은 session 호출이 아니며 자체 timeout 40 s를 쓴다.
  - `cleanup_until`(끝 − 40 s) 전이면 동결 `collect.cleanup`을 그대로 실행한다. 모든 session 호출이 `cleanup_until`까지 끝나므로 snapshot은 wall 끝까지 끝난다.
  - 이미 지났으면 collect.cleanup에 들어가지 않는다. 그 시각에는 어떤 cleanup 호출도 시작할 수 없기 때문이다. `cleanup.json`에 `skipped=true`, `cleanup_verified=false`, 생성 객체 전체, 사유를 남긴다.
- 동결 의존성, 전역 signal, subprocess 함수는 patch하지 않았다.

## 4. TDD와 테스트

| 단계 | 로그 (`fix-r2/logs/`) | 결과 |
|---|---|---|
| red | `red-live.txt` | 35 tests, 4 failures + 7 errors. 행동 실패: 빠른 generator의 restore가 t0+60 전(102.26 < 164.21), status poll 없이 restore, logs timeout이 재계산되지 않아 30 s를 다 씀(TimeoutExpired 미발생), 남은 시간 0에도 logs 실행. 나머지는 `window-*.json`/`live.cleanup` 부재 |
| green 1 | `green-live-1.txt` | 1 failure: 테스트 glob이 guard GET의 stdout까지 잡음. 테스트를 고침(구현 변경 없음) |
| green | `green-live-analyze.txt` | `test_live test_analyze` 55 tests OK |
| green | `green-cell-loopback.txt` | `test_cell_loopback` 8 tests OK, 81.2 s(실제 generator/netctl HTTP, 60 s window 대기 포함) |

새·수정 테스트 (`test_live.py`)
- `WindowCompletion` 5개: 빠른 generator도 netctl window 끝 뒤 restore. 0.1/30/70 s 완료가 모두 offset ≥ 60에서 restore. 응답 없는 netctl은 상한 안에서 끝나고 `unknown` + timing_invalid + teardown. `failed`이면 즉시 종료. 오류 경로는 window를 기다리지 않음.
- `StreamBudget` 5개: 59 s guard GET 뒤 logs timeout = 남은 3 s. 60 s GET으로 남은 시간이 0이면 kubectl 전에 거부(`.deadline.json`). 두 R2 cleanup 시작점(−62, −65)이 wall 끝 안에 끝나고 unverified. 남은 시간 25 s면 snapshot 포함 skip, unverified. 시간이 충분하면 동결 cleanup이 검증까지 감.
- 수정
  - `Pods` fake netctl이 실제 Runner처럼 시계로 running/completed/restored를 답한다.
  - `Kubectl` fake에 호출별 소요시간을 넣었고, timeout 초과 시 부분 stdout을 쓰고 kill된다. 존재 조회 응답에 `spec.nodeName`을 더했다.
  - 순서 테스트에 window status poll을 넣었다(`test_arm_trace_start_window_restore_salvage_teardown_in_order`).
- 기존 단언은 약화하지 않았다.
- 전체 117-test suite는 다시 돌리지 않았다. 바뀌지 않은 코드에는 fix-r1 `green-all.txt`를 재사용했다. 바뀐 동작에 닿는 3개 모듈만 실행했다.

## 5. 리뷰 재현 (각색본, 원본·증거는 수정·재실행하지 않음)

```sh
rtk proxy bash -c 'PYTHONDONTWRITEBYTECODE=1 timeout 300 python3 .scratch/unstable-network-20261027/fix-r2/reproduce_r2_after_fix.py artifacts/unstable-network/plan-20261027T111532Z > .scratch/unstable-network-20261027/fix-r2/reproduce-after-fix.stdout 2> .scratch/unstable-network-20261027/fix-r2/reproduce-after-fix.stderr'
# rc 0, stderr 0 B; 결과 fix-r2/probe-r5lx_5g_/results.json
```

- 원본 스크립트는 리뷰어 디렉터리에 쓰므로 그 자리에서 다시 돌리지 않았다.
- 각색은 두 가지뿐이다.
  - fake netctl `/status`가 시계에 따라 running → completed로 답한다. 수정된 수집기가 restore 전에 polling하기 때문이다.
  - 단언을 수정 후 동작으로 바꿨다.

| 시나리오 (b1-burst, seed 2471424978, 85 도착, 마지막 58.558427 s) | 수정 전 (리뷰) | 수정 후 |
|---|---|---|
| 마지막 지연 0.1 s (generator terminal 58.658 s) | restore 59.45 s, invalid(window end 미도달) | restore 64.65 s, window `completed`, **valid**, 151 ms |
| 마지막 지연 2.0 s | restore 64.55 s, valid | restore 64.65 s, **valid**, 151 ms |
| (추가) netctl 응답 없음, 0.1 s | — | window `unknown`, restore 89.45 s(상한 70.1 + 호출 1회 안), timing `unknown`, timing_invalid, teardown 수행 |

- 두 valid cell의 netctl status: trace `burst`, sha256 `88a16cca…cddc6`, ok change 6개(phase 1–6), `window_end` 있음, fault 없음.

wall 예산 (fake clock은 100에서 시작하므로 wall 끝 = 10,900 = 경과 10,800 s)

| probe | 수정 전 | 수정 후 |
|---|---|---|
| spec 중첩 cleanup (−62) | 10,829 s 경과 | logs timeout 1 s, snapshot 시작 = cleanup_until, **10,800 s**, unverified |
| standards cleanup (−65) | 10,826 s | logs 4 s, **10,800 s**, unverified(snapshot timeout은 진단 오류로 기록) |
| standards stream (−65) | logs timeout 30, 예비 시간 24 s 침범 | logs timeout 6(= GET 뒤 남은 시간), 침범 0, 부분 출력 보존 |
| (추가) 남은 시간 없음 (끝 − 25 s) | snapshot 40 s 시작 | kubectl 0회, `skipped`, unverified, 생성 객체 기록 |

- cleanup probe는 `live.cleanup`(unstable.py 경로)과 리뷰어가 쓴 `ownership.cleanup` 직접 호출 두 경로 모두에서 같은 결과다.

## 6. plan과 보존

```sh
rtk proxy bash -c 'PYTHONDONTWRITEBYTECODE=1 uv run --python /usr/bin/python3 --no-build --with numpy==2.2.6 --with pandas==2.2.3 python experiments/unstable-network/unstable.py plan --inventory .scratch/unstable-network-20261027/preflight-before-live-20261027T1103Z/node-inventory.json --export artifacts/quality-network-heterogeneous/export-20261023T075427Z --deps artifacts/quality-network-heterogeneous/deps-20261023T075919Z --deps artifacts/quality-network-heterogeneous/deps-20261023T080028Z'
rtk proxy bash -c 'PYTHONDONTWRITEBYTECODE=1 python3 experiments/unstable-network/unstable.py dry-run --plan artifacts/unstable-network/plan-20261027T111532Z --run-id un-collect-r3'
rtk proxy bash -c 'PYTHONDONTWRITEBYTECODE=1 python3 .scratch/unstable-network-20261027/fix-r2/preserved_check.py'
```

- plan rc 0, stderr 0 B: workers cpu-c/npu-c/cpu-e0/jgpu-dev0009-cu130, coverage ok, 144 plans, 36 cells.
- dry-run rc 0, drift `[]`. `release_binding`:
  - plan `ee0aba54…c1e7`
  - shipped `e358c10d…4bc1`
  - budgets `56dd2a43…85f9`
  - traces `cec8c6d4…67fb3`
  - schedule `00eb908d…dc40`
  - shipped/budgets/traces/schedule는 이전 R2 plan과 같다.
- 이전 R2 plan(`105645Z`)과 같은 항목: schedule, traces, trace_table, workers, candidates, model_provenance, pairs, quality, policies, recovery, design, inputs, corpus, asset_bytes, budgets, reused_frozen_sources, shipped_sources.
- 다른 항목
  - inventory(새 preflight sha256 `fd3b409f…87cb`)
  - planner/copied hash: `live.py` `494ba013…b0cd`, `unstable.py` `390f641f…d1c3`
  - `assets_manifest_sha256`: 차이는 `jellybean.tar` source 경로뿐이고 내용 hash는 같다
  - `created_utc`
- `preserved-hash-check.json`
  - preserved 75개 변화 0
  - review-source r1/r2 drift `[]`
  - `091525Z` plan 파일이 fix-r1 기록과 같고, `105645Z` plan.json은 `e57600bb…`
  - 현재 R2 대비 변경 파일은 위 4개뿐이다
- 소스가 바뀌었으므로 `105645Z`의 dry-run은 이제 drift다. execute에 쓰지 않는다.

## 7. 산출물

- diff: `fix-r2/diff-vs-review-source-r2.patch`(R2 snapshot 대비 4파일)
- 수정 전 사본: `fix-r2/before-{live,test_live,unstable}.py`(R2와 hash 동일)
- 재현: `fix-r2/reproduce_r2_after_fix.py`, `reproduce-after-fix.{stdout,stderr}`, `probe-r5lx_5g_/`
- 로그: `fix-r2/logs/{red-live,green-live-1,green-live-analyze,green-cell-loopback,plan,plan.stderr,dry-run.json,dry-run.stderr}`
- plan: `artifacts/unstable-network/plan-20261027T111532Z/`(새 디렉터리)

## 8. 편차

- coordinator 알림 전에는 일부 shell을 `rtk` 없이 실행했다.
  - 읽기: `cat`, `sed`, `grep`, `ls`, `wc`, `sha256sum`, `which`
  - `python3` heredoc 편집 스크립트: `test_live.py`, `live.py`, `unstable.py`, 문서
  - `mkdir`/`cp`/`mv`로 `fix-r2/before-*.py` 사본 생성
  - lifecycle은 처음부터 `rtk proxy /home/jinuk/.orca/cli/bin/orca`만 썼다.
- 알림 뒤의 모든 명령은 `rtk proxy`로 실행했다(ack `msg_8c0b1b878677`).

## 9. 남은 제한

- live 미실행. 실제 netctl window_end 시각, 실장비 poll 지연, 정렬 오차는 측정하지 않았다.
- restore는 window_end 뒤 최대 약 poll_s(5 s) 늦을 수 있다. 그동안 마지막 phase 조건이 유지된다(generator는 이미 terminal). 분석 window는 60 s로 그대로다.
- netctl이 응답하지 않거나 failed인 cell은 timing invalid로만 표시하고 fence하지 않는다(기존 restore 실패 처리와 같은 수준). 수집은 계속한다.
- snapshot 상한은 fake clock에서 정확히 지켜진다. 실제 시간에서는 subprocess kill/wait와 Python 처리 몇 ms가 더해질 수 있다. `rtk proxy` kill 뒤 kubectl 자식의 종료는 실장비에서 확인하지 않았다.
- `stream_guard`는 동결 `HetSession.call` guard의 사본이다. 동결 원본이 바뀌면 같이 맞춰야 한다(동결이므로 바뀌지 않아야 한다).
- `cell_need_s`가 30 s 늘어, 예산 끝 근처에서 cell이 약간 일찍 skip될 수 있다.
- 이 보고는 독립 리뷰가 아니다. coordinator 최종 검증과 별도 release 전에는 live를 실행하지 않는다.
