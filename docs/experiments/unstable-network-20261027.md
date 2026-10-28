# 불안정 엣지 네트워크에서 이기종 DAG 프로파일링·스케줄링 재실험 — 구현·로컬 검증·실행 계약 (2026-10-27)

현재 상태 (2026-10-27 갱신): **실제 수집 완료.** run `un-collect-r3`, plan `plan-20261027T111532Z`. 실장비 CPU / Jetson GPU / Intel NPU에서 36/36 cell을 수집하고 UID 소유 cleanup을 검증했다. 결과는 진단 품질의 탐색적 2 block이며, 11절에 정리했다. 실행 보고는 `.scratch/unstable-network-20261027/live-report.md`다. 아래 1–10절은 수집 전에 고정한 계약이다.

(이전 상태: 구현 + 로컬 테스트 + 읽기 전용 plan/dry-run 완료, live 미실행. 실제 Pod 생성·수집은 독립 리뷰와 coordinator release 후 별도 dispatch에서 한다.) 명세는 `.scratch/unstable-network-20261027/spec.md`이고, 구현 보고는 `.scratch/unstable-network-20261027/developer-report.md`다. 구현·테스트는 Claude Code `claude-opus-5-5`(claude.ai firstParty, max 구독)가 했다.

## 1. 범위와 결론의 한계

- 사용자 전제는 "엣지 컴퓨팅 환경의 네트워크는 불안정하다"이다. 이번 실험은 기존 정적 regime 비교(good/limited)를 **시간에 따라 바뀌는 실제 데이터 경로** 실험으로 확장한다.
- 제안 방법은 기존 워크플로 프로파일링과 네트워크 인지 스케줄링이다. 새 예약 알고리즘, DRL, 모델 교체는 없다.
- 아래 trace는 통제된 stress test다. 실제 엣지 링크의 대표 분포라고 주장하지 않는다. stable trace는 대조군이다.
- 품질은 `diagnostic`이다. 재사용 60쌍의 eval 0.355는 Qmin 0.40에 못 미친다. 품질 미달은 네트워크 진단 실행을 막지 않지만, 모든 결과에 진단이라고 표시한다. 60쌍은 독립 holdout이 아니다.
- 반복은 block 2개의 탐색적 실험이다. 유의성이나 capacity를 주장하지 않는다. 반복 단위는 block/run이며, cell 안의 요청 수천 건을 독립 반복으로 세지 않는다.
- 정책 이름은 일반 기준선 이름이다. 논문 구현을 재현한 것이 아니다.

## 2. 구성 (`experiments/unstable-network/`)

| 파일 | 역할 | 출처 |
|---|---|---|
| `traces.py` | 사전 고정한 trace 3개, 검증(`[0,60)` 연속 덮음, 승인값), 구간 조회, trace hash | 신규 |
| `netctl.py` | gateway Pod netns의 netctl sidecar HTTP 서버(:8081). trace를 가진 유일한 구성요소다. 설치, 경계마다 in-place change, 복원, 해제, 커널 카운터 기록을 한다 | 신규 |
| `policies.py` | 정책 6개, 관측 계약 수정, `dag_reopt_no_network` | `quality-network-baselines/policies.py` 수정 사본 |
| `generator.py` | 엣지 origin 실행기. poll 기록 방식 수정, trace 입력 계약, 지연 시작 | baselines `generator.py` 수정 사본 |
| `design.py` | 설계 상수, 예산, 회복 정의, 실험 순서표(experimental-design helper) | baselines `design.py`의 순수 함수 사본 + 신규 |
| `live.py` | release 이후 수집기(cell 순서, 정렬, 복원·해제, 장애 시 중단) | baselines `live.py` 수정 사본 |
| `analyze.py` | 오프라인 분석(cohort 검증 규칙, phase cohort, 회복, 네트워크 timeline) | baselines `analyze.py` 사본 + 신규 |
| `unstable.py` | CLI: `plan` / `dry-run` / `execute` / `analyze` | baselines `baselines.py` 수정 사본 |

**변경 없이 재사용(hash 결속)**
- baselines의 `dag_worker.py`, `gateway.py`, `bounded_http.py`, `capture.py`
- qnh의 `worker.py`, `stages.py`, `bootstrap.py`, `proc.py`, `qualify.py`, `catalog.py`
- real-topology-live의 `http_runtime.py`, `run_baseline_comparison.py`
- multi-edge-sensors의 `collect.py`, quality-network-vqa의 `vqa_eval.py`

**사본과 원본의 관계**
- 사본은 이 디렉터리에서 import되고 Pod에 배포되는 파일이다. 원본은 수정하지 않았고 hash만 기록한다.
- plan의 `copied_sources`가 원본 sha256과 사본 sha256을 함께 묶는다. 원본이 바뀌면 release drift가 된다.
- 두 디렉터리에 같은 이름의 모듈(`policies`, `design` 등)이 있다. 그래서 이 디렉터리는 baselines 모듈을 이름으로 import하지 않는다. 변경 없는 재사용 모듈은 `sys.path` 뒤쪽에 추가한다.

## 3. 네트워크 제어

**적용 범위**
- 소유 gateway Pod netns의 egress만 바꾼다. 대상은 gateway→cloud Pod IP와 gateway→edge Pod IP, 즉 edge↔server 경로다.
- edge↔edge LAN, host, 물리 링크, 다른 workload는 바꾸지 않는다. 터널도 쓰지 않는다.

**qdisc 구조**: `real-topology-live/net_control.py`(변경 없음)와 같다.
- root `prio 1:`, band 3개. priomap은 제약 없는 기본 band(1:3)로 보낸다.
- band 1:1은 cloud IP, 1:2는 edge IP다(u32 /32 dst filter).
- 각 band는 `tbf`(rate, burst 4096 B, limit 131072 B) 아래 `netem`(delay, jitter, loss, limit 64 packets)을 둔다.

**trace** (`traces.TRACES`; good = 100 Mbps, 0 ms, jitter 0, loss 0 %)

| trace | 구간 |
|---|---|
| stable | [0,60) good |
| slow | [0,20) good; [20,40) 10 Mbps / 20 ms; [40,60) good |
| burst | [0,10) good; [10,20) 10 Mbps / 20 ms / jitter 5 ms / loss 0.5 %; [20,28) 2 Mbps / 20 ms / 5 ms / 1 %; [28,30) 2 Mbps / 20 ms / 5 ms / **loss 100 %**(2 s 단절); [30,40) good; [40,50) 10 Mbps / 20 ms / 5 ms / 0.5 %; [50,60) good |

**구간 경계에서 큐를 비우지 않는다**
- cell마다 phase 0으로 트리를 한 번 설치한다. 이후 경계마다 각 band의 tbf와 netem을 pyroute2 `tc('change', ...)`로 바꾼다(RTM_NEWQDISC + NLM_F_REPLACE).
- root를 삭제하거나 다시 만들지 않는다. 기존 `net_control.py`는 조건을 바꿀 때 root를 삭제했다.
- root 삭제(teardown)는 cell 사이 drain/idle barrier 뒤와 프로세스 종료 시에만 한다.

**실제 커널 검증** (`test_netctl.RealKernelChange`, 증거 `.scratch/unstable-network-20261027/logs/kernel-change-probe.json`)
- 환경: 비특권 user+net namespace(`unshare -rn`)의 `lo`, 커널 7.0.0-38-generic(ser0002와 같은 버전), 고정 pyroute2 0.9.6 wheel(sha256 `33340913…fcef8`).
- in-place change: root 1:과 handle이 유지되었다. netem qlen이 64→64로 유지되었고 drop 카운터가 보존되었다.
- 삭제 후 재생성: qlen 64→0, 카운터 0이 되었다.
- 한계: Pod veth(GSO/TSO 포함)에서의 동작은 live에서만 확인할 수 있다.

**재현성 한계**
- pyroute2 0.9.6 netem에는 `TCA_NETEM_PRNG_SEED`가 없다(wheel의 `sched_netem.py` nla_map 확인). 그래서 loss와 jitter 난수를 seed로 고정할 수 없다.
- 같은 trace는 **설정 수준에서만** 재현된다. 패킷 단위 동일 실현은 보장하지 않으며, 실제 drop/backlog/qdisc 카운터를 기록한다.
- jitter는 분포 테이블 없이 균일 ±5 ms다. jitter가 있으면 재정렬이 생길 수 있다.

**설정값은 실효 throughput이 아니다**
- tbf/netem의 parent·child 카운터는 겹친다. 그래서 합산하지 않는다.
- gateway relay body bytes는 따로 기록한다.

## 4. 시간 정렬 (교차 호스트 monotonic을 빼지 않음)

1. 수집기가 generator에 `/trace`를 보낸다(`start_delay_s` = 2.0). generator는 자기 시계로 t0 = 수신 시각 + 2.0을 쓴다. 수집기는 자기 시계로 송신 s2와 응답 r2를 기록한다.
2. 수집기가 바로 netctl `/start`를 보낸다. 지연값은 `delay = (s2+r2)/2 + 2.0 − s3`이다(s3 = 송신 시각). netctl은 자기 시계의 start + 구간 offset에 경계를 적용한다. 계획 시각, 실제 시작·종료 시각, 적용 지연, 호출 ms, 직후 카운터를 기록한다.
3. 오차 상한은 `(r2−s2)/2 + (r3−s3)`다. cell마다 `align-<label>.json`에 남긴다. 허용 250 ms를 넘으면 `within_tolerance=false`, `comparable_timing=false`로 표시하고 `collection.json`의 `timing_invalid`에 넣는다. 수집은 중단하지 않는다(fleet 장애가 아니다). 시작 정렬은 필요조건일 뿐이다. 비교 가능 여부는 7절의 timing 유효성 규칙(적용 지연 포함)이 오프라인에서 정한다.
4. 가정: 수 초 동안의 시계 rate 차이는 무시한다. loopback 통합 시험에서 상한은 허용값 아래였다. 실장비 값은 live에서 측정된다.

generator와 정책은 trace 이름, phase, 미래 rate/delay/loss, phase별 비용표를 받지 않는다.
- generator `/trace`는 `TRACE_KEYS` 밖의 키를 400으로 거부한다.
- 정책은 초기 정상(good) 프로파일과 실제 관측만으로 적응한다.

## 5. 관측 결함 보완 (새 알고리즘 아님)

**문제**: 기존 `poll_once`는 실패한 poll을 `{error}`로 기록했다. `queue_ms`는 이를 waiting/active 0, 즉 빈 큐로 읽었다. 실패 시각도 snapshot 시각으로 갱신했다.

**수정**
- worker마다 마지막 성공 큐 상태와 그 시각을 보존한다.
- 실패 poll은 오류, 오류 시각, 연속 오류 수만 기록한다.
- 성공한 적 없는 worker의 큐 추정은 `PolicyError`다. 0이 아니다.

**stale 규칙**: 관측 기반 정책 4개(greedy_comm, dag_reopt, hysteresis, no_network)에 같은 규칙을 적용한다.
- 조건: 후보 worker 중 하나라도 성공 기록이 없거나, 마지막 성공이 `obs_stale_s` = 3 s(poll 3회)보다 오래되었다.
- 처리: 그 요청은 해당 정책의 static plan을 쓴다. static plan은 동결 프로파일에서 큐 없이 고른 계획이다. dag_reopt의 static plan은 dag_static 계획과 같다.
- 기록: `decision.fallback{reason, never, stale, max_age_ms}`. hysteresis 상태는 fallback으로 바뀌지 않는다.

**관측 기록**: 모든 정책의 모든 요청에 worker별 값과 summary를 남긴다.
- worker별: 나이, 마지막 오류, 오류 나이, 연속 오류 수
- summary: never/stale/max_age

**한계**: poll은 데이터와 같은 경로(cross-zone은 gateway)를 지난다. 그래서 단절 구간에서는 cloud worker poll이 실패하고, stale fallback이 생길 수 있다. 이것은 사전 규칙이며, 결과는 그대로 기록한다.

## 6. 정책과 설계

**정책 6개**

| 정책 | 계약 |
|---|---|
| `task_fastest` | 초기 compute만 보고 한 번 고정 |
| `greedy_comm` | 관측 기반 통신 greedy |
| `dag_static` | 초기 good 프로파일에서 DAG 계획을 열거해 trace 동안 고정 |
| `dag_reopt` | 요청마다 현재 관측으로 같은 열거 |
| `dag_reopt_hysteresis` | 15 % / 50 ms / 5 s |
| `dag_reopt_no_network` | `dag_reopt`와 관측·큐·compute 추정·실행 가능 계획이 같다. 선택 비용에서만 전송비용을 0으로 둔다. 기록하는 `predicted_ms`는 전체(네트워크 포함) 비용이고, 실제 데이터 경로도 같다 |

**고정 조건**
- 5-task VQA DAG. 기존 모델, precision, 전처리, 입력 60쌍, deadline 2 s를 유지한다. 모든 단계를 실제로 실행한다.
- open-loop 1.6 rps, trace당 60 s, seeded Poisson 도착. 같은 (block, trace)의 6정책은 같은 요청 cohort를 공유한다.
- cancel 10 s, max_inflight 8(기존 32). 줄인 이유는 OOM과 admission 폭주 제한이다. 모든 drop을 기록한다.
- worker 연결/gateway 제한과 메모리 상한은 바꾸지 않았다.

**순서표** (`design.schedule`, seed 20261027, plan에 동결)
- block은 순서대로 진행한다. block 안의 trace 순서는 `assign_factorial_runs`로 정한다.
- (block, trace) 6개 group의 정책 순서는 6×6 Latin square의 행이다.
  - 행 배정과 기호(정책) 순서도 `assign_factorial_runs`로 정한다.
  - 그래서 각 정책은 각 순서 위치를 정확히 1번씩 차지한다.
- 총 36 cell이고 요청 발생 구간은 36분이다.
- helper: `.agents/skills/experimental-design/scripts/randomization.py`(sha256 `c7c2eab4…bce4`). numpy 2.2.6, pandas 2.2.3은 uv로 plan 시에만 쓴다. execute와 analyze는 stdlib `check_schedule`로 다시 검사한다.

**cell 순서**
1. idle barrier(+gateway reset)
2. netctl arm(phase 0 설치)
3. generator `/trace`
4. netctl `/start`(정렬)
5. 대기: generator terminal, 그 뒤 netctl의 동결 window 끝(`window_end`)까지(R2, 아래)
6. restore(in-place change → good)
7. generator가 terminal로 관측되지 않았으면 `/stop` + 제한 대기(아래)
8. ledger 회수(salvage)
9. drain barrier(worker, gateway, **generator** 모두)
10. netctl status
11. teardown — 7~9가 모두 해결되었을 때만

phase 사이에는 drain하지 않는다.

**실패 처리 (R1 수정 반영)**
- 각 단계는 따로 시도하고 따로 기록한다. 원래 실패(`trace-<label>.error.json`, stage = `generator_start` | `netctl_start` | `wait`)와 복원·회수·정리 기록이 서로를 덮어쓰지 않는다.
- generator 정지: `/trace` 응답 유실, netctl start 실패, `/status` timeout처럼 시작이 수락되었거나 불확실한 모든 오류에서 수집기는 `/stop`을 보낸다. 그 뒤 `cancel_s + 30 s` 안에서 generator `/status`를 poll하며, label이 아직 running이면 `/stop`을 다시 보낸다(늦게 처리된 `/trace`가 stop flag를 지울 수 있다). 결과는 `generation-<label>.json`의 `terminal` | `not_admitted`(창 전체에서 label 부재) | `unresolved`다.
- salvage: terminal이면 전체 ledger를 읽는다. 아니거나 그 읽기가 실패하면 `/ledger?label=L&partial=1`의 표시된 부분 ledger(`partial=true`, 예정 도착마다 행 1개, 미완료 행은 status None)를 `ledger-<label>.json`으로 저장한다. 실패한 읽기는 `ledger-<label>.error.json`에 남는다.
- worker가 idle이어도 generator가 멈췄다고 보지 않는다. drain은 generator에 running trace나 inflight가 없어야 idle이다.
- fence: `generation_unresolved`, `salvage_failed`, `drain_not_idle`(cell 전·후 모두, 응답 불가 포함) 중 하나라도 있으면 `fault-<label>-cell.json`을 남기고, 그 cell의 정상 teardown/reset을 하지 않으며(`netctl-teardown-<label>.skipped.json`), 이후 cell을 모두 skip한다. 다음 cell이 backlog 증거를 지우지 못한다.
- 비상 정리: run 끝의 netctl teardown은 fence나 중단 뒤라면 `netctl-emergency-teardown.json`(`emergency=true`, `idle_barrier=false`, `fenced_by`)으로 기록한다. owned network control 삭제일 뿐이며 idle barrier 성공으로 해석하지 않는다. fence가 없으면 `netctl-final-teardown.json`(`emergency=false`)이다.
- window 완료 대기(R2): generator가 정상 종료(terminal)하면 restore 전에 netctl `/status`를 poll_s(5 s) 간격으로 읽어 `completed`(`window_end`) 또는 `failed`(netctl이 스스로 good 복원)까지 기다린다. 상한은 netctl 시작이 가능한 가장 늦은 시각(`net_recv + delay_s`, 수집기 시계) + window 60 s + 10 s이고, 각 호출은 control timeout과 wall 한도로 잘린다. 그래서 빨리 끝난 정책도 느린 정책과 같은 60 s window를 끝까지 거친다. 결과는 `window-<label>.json`(`completed` | `failed` | `incomplete` | `unknown`)이며, `completed`가 아니면 그 cell을 `timing_invalid`에 넣는다(분석의 window 끝 조건도 그대로). 시작 이후 오류 경로는 기다리지 않고 바로 restore → `/stop` → salvage로 간다. generator나 정책 입력에는 trace 정보를 더하지 않았다.
- 각 cell 뒤에 fleet check를 한다. worker Pod가 Running이 아니거나, 재시작·terminated(OOM 등)되었거나, `/health` UID가 바뀌면 그 cell을 `fault-<label>.json`(장애 영향)으로 기록한다. 이후 cell은 실행하지 않고 skip으로 남긴다.

## 7. 분석 정의 (수집 전 고정)

**cohort 규칙**: baselines와 같다.
- 예정 도착마다 결과는 정확히 1개다.
- 지연은 generator 시계 완료 offset − 동결 예정 offset이다.
- 식별, 모델 hash, 데이터 연속성이 깨진 응답은 invalid다.

**전체 지표**
- 결과: goodput, 상태별 수, 지연 분위수, 품질/coverage
- 계획: 시간순 계획 전환 수, fallback 수, 관측 최대 나이
- 기타: 예측 잔차, decision ms, dispatch lag

**timing 유효성 (R1, live 수집 전 고정; `analyze.timing_validity`)**
- `unknown`: cell의 `align-<label>.json` 또는 `netctl-status-<label>.json`이 없다.
- `valid`: 다음을 모두 만족한다. 하나라도 어긋나면 `invalid`다.
  1. 정렬 상한이 유한한 0 이상의 수다.
  2. netctl status가 이 cell의 동결 trace 이름과 hash다.
  3. 내부 경계마다 성공한 change가 순서대로 정확히 1개 있고, 동결 조건과 실제 적용 시작·종료 시각을 가진다. window 끝(`window_end`)에 도달했다(restore나 실패로 잘리지 않음).
  4. 총 timing 불확실성 ≤ 250 ms(`align_tolerance_ms`). 경계 B가 netctl 시계에서 [s, e]에 적용되었으면(그 시작 기준 offset), generator 일정에서 B는 [s − bound, e + bound](`lo_s`, `hi_s`) 안에 있다. 경계 불확실성은 max(B − lo, hi − B)이고, 총값은 이 값들과 bound 중 최댓값이다. 즉 적용 지연도 250 ms 허용값에 포함된다.
- `invalid`/`unknown` cell(그리고 ledger가 없거나 partial이거나 state가 `done`이 아닌 cell)은 phase cohort와 회복을 `None`으로 두고 `suppressed_because`에 이유를 쓴다. 불확실성 0으로 계산하지 않는다. 비교 가능한 cell은 `comparable_cells`에 나열한다.
- 전체 cohort의 cell 결과 수(상태별 count, goodput, 지연 등)와 진단(network timeline, generation 결과, `rows_without_terminal_status`)은 항상 보고한다.

**phase cohort** (timing `valid`인 cell만)
- 예정 도착 offset이 속한 명목 구간에 정확히 1번 센다. 완료 시각과 무관하다.
- 경계의 실제 적용 구간 [lo_s, hi_s] 안에 예정된 요청은 같은 구간에 세고, `boundary_uncertain`으로도 표시한다.

**회복**: 열화→good 경계 B마다 계산한다(slow 40; burst 30, 50). timing `valid`인 cell만.
- R: max(B, 그 경계의 hi_s) 이후 첫 in-deadline 유효 도착. 실제 복원이 늦었으면 복원 전에 회복을 주장하지 않는다(`search_from_s`로 기록).
- 보고값: `R − B`, 그리고 [R, R+10 s)(trace 끝에서 자름)의 in-deadline 비율
- 그 이후 in-deadline 유효 도착이 없으면 `not_recovered`

**네트워크 timeline**
- 경계별 적용 지연과 호출 ms
- phase별 owned netem/tbf 카운터 증분(bytes, packets, drops, overlimits, 끝 backlog)

## 8. 예산과 시간 추정 (`design.BUDGETS`)

**고정값**
- deadline 2 s, cancel 10 s, max_inflight 8
- 관측: poll 1 s / timeout 0.5 s / stale 3 s
- 시작 지연 2 s, 정렬 허용 250 ms
- drain 60 s(settle 2 s)
- 수집 상한 6,000 s(아래 wall 한도 안의 내부 상한)
- Pod 수명: worker 12,600 s, cache 14,700 s
- 전체 wall 예산 10,800 s(3 h), cleanup 예비 300 s

**wall 예산 강제 (R1; `live.WallBudget`)**
- 하나의 절대 한도다. execute 진입 직후, release 검증과 준비(배포·업로드·readiness) 전에 시작한다.
- 준비, 보정, 수집, cell 복원·salvage·drain은 `끝 − 300 s`(work 한도)까지다. 모든 kubectl 호출과 HTTP 제어 호출의 timeout을 남은 시간으로 자르고, 남은 시간이 없으면 호출하지 않고 거부를 기록한다(`*.deadline.json`). timeout된 kubectl은 부분 stdout/stderr를 남긴다.
- cell은 최악 소요(사전 barrier, 시작 지연+window+cancel+대기 여유, 마무리 단계)가 한도 안에 들어갈 때만 시작한다. 아니면 `wall budget`(또는 `collection budget`) 사유로 skip한다. 준비가 오래 걸리면 그만큼 수집 시간이 줄어든다.
- run 끝 netctl teardown과 UID 소유 Pod cleanup은 예비 시간을 쓸 수 있다. kubectl cleanup은 `끝 − 40 s`까지이며, 남은 40 s는 collect.cleanup 안의 진단 snapshot(자체 timeout 40 s)용이다.
- stream(R2): server Pod의 `logs`/`exec`는 먼저 기존과 같은 새 UID 소유 GET(최대 60 s)을 통과해야 한다. stream timeout은 그 GET이 끝난 뒤, 실제 kubectl 시작 시점의 남은 시간으로 다시 자른다(`UnstableSession.stream_guard`; 규칙·기록은 HetSession과 같고 동결 모듈은 바꾸지 않음). timeout 시 부분 출력은 남는다.
- protected-after snapshot(R2): 그 snapshot은 session 호출이 아니라 자체 40 s timeout을 쓴다. cleanup의 모든 kubectl 호출이 `끝 − 40 s`까지 끝나므로 snapshot은 wall 끝까지 끝난다. cleanup 진입 시각이 이미 `끝 − 40 s`를 지났으면 `live.cleanup`은 collect.cleanup을 시작하지 않고 `cleanup.json`에 `skipped=true`, `cleanup_verified=false`, 생성 객체 전체를 남긴다(어떤 cleanup 호출도 시작할 수 없는 시각이다). fake clock 재현에서 두 R2 cleanup probe는 정확히 10,800 s에 끝나고 unverified로 기록된다. 실제 시간에서는 subprocess kill과 Python 처리 몇 ms가 더해질 수 있다.
- cleanup을 한도 전에 확인하지 못하면 `cleanup.json`/`run-status.json`에 `cleanup_verified=false`와 남은 객체·오류를 그대로 남긴다. 성공으로 기록하지 않는다.
- 다른 모듈의 signal handler는 바꾸지 않았다.

**cell 추정**
- cell당 계획 추정은 89 s다(시작 2 + window 60 + cancel 꼬리 ≤10 + drain/해제).
- 36 cell이면 약 53분이다. 예상 도착은 약 3,456건이다.

**준비 단계**
- cache 업로드 3.99 GB, readiness, 보정 profile, route probe를 더해야 한다.
- 이전 baselines compare run(72 cell, 92분)에서 역산하면 cell당 약 75 s, 준비 약 10분이다. 이번 전체는 약 65–80분으로 추정한다. 측정값이 아니다.

## 9. 명령 (실제 실행한 것과 제안)

```bash
# 테스트(이 디렉터리, stdlib + 일정 helper 의존성): R1 수정 후 117 tests OK, 111 s (2026-10-27; 이전 86).
# R2 수정 후에는 영향 받는 test_live(+10) / test_analyze / test_cell_loopback만 다시 실행했다(.scratch/.../fix-r2/logs).
cd experiments/unstable-network && PYTHONDONTWRITEBYTECODE=1 rtk proxy uv run --python /usr/bin/python3 --no-build \
  --with numpy==2.2.6 --with pandas==2.2.3 python -m unittest discover -s . -p 'test_*.py'

# R2 수정 후 새 plan (실행함; kubectl 없음; drift []) -> artifacts/unstable-network/plan-20261027T111532Z,
# plan sha256 ee0aba54…c1e7. preflight-before-live-20261027T1103Z inventory, 같은 export/deps·순서표·trace·모델·cohort·seed·예산.
PYTHONDONTWRITEBYTECODE=1 rtk proxy uv run --python /usr/bin/python3 --no-build --with numpy==2.2.6 --with pandas==2.2.3 \
  python experiments/unstable-network/unstable.py plan \
  --inventory .scratch/unstable-network-20261027/preflight-before-live-20261027T1103Z/node-inventory.json \
  --export artifacts/quality-network-heterogeneous/export-20261023T075427Z \
  --deps artifacts/quality-network-heterogeneous/deps-20261023T075919Z --deps artifacts/quality-network-heterogeneous/deps-20261023T080028Z
PYTHONDONTWRITEBYTECODE=1 rtk proxy python3 experiments/unstable-network/unstable.py dry-run \
  --plan artifacts/unstable-network/plan-20261027T111532Z --run-id un-collect-r3

# (이전, 보존·불변) R1 수정 후 plan (실행함; kubectl 없음; drift []) -> artifacts/unstable-network/plan-20261027T105645Z,
# plan sha256 e57600bb…ce57e. 새 preflight inventory, 같은 export/deps. 순서표·trace·모델·cohort·seed는 이전 plan과 같다.
PYTHONDONTWRITEBYTECODE=1 rtk proxy uv run --python /usr/bin/python3 --no-build --with numpy==2.2.6 --with pandas==2.2.3 \
  python experiments/unstable-network/unstable.py plan \
  --inventory .scratch/unstable-network-20261027/preflight-after-reset-20261027T1030Z/node-inventory.json \
  --export artifacts/quality-network-heterogeneous/export-20261023T075427Z \
  --deps artifacts/quality-network-heterogeneous/deps-20261023T075919Z --deps artifacts/quality-network-heterogeneous/deps-20261023T080028Z
PYTHONDONTWRITEBYTECODE=1 rtk proxy python3 experiments/unstable-network/unstable.py dry-run \
  --plan artifacts/unstable-network/plan-20261027T105645Z --run-id un-collect-r2

# (이전, 보존·불변) plan (실행함; kubectl 없음) -> artifacts/unstable-network/plan-20261027T091525Z, plan sha256 407abd49…987e
# R1 수정으로 소스가 바뀌었으므로 이 plan의 dry-run은 이제 drift다. execute에 쓰지 않는다.
PYTHONDONTWRITEBYTECODE=1 rtk proxy uv run --python /usr/bin/python3 --no-build --with numpy==2.2.6 --with pandas==2.2.3 \
  python experiments/unstable-network/unstable.py plan --inventory .scratch/unstable-network-20261027/preflight-initial/node-inventory.json \
  --export artifacts/quality-network-heterogeneous/export-20261023T075427Z \
  --deps artifacts/quality-network-heterogeneous/deps-20261023T075919Z --deps artifacts/quality-network-heterogeneous/deps-20261023T080028Z

# dry-run (실행함; kubectl 없음; drift [])
PYTHONDONTWRITEBYTECODE=1 rtk proxy python3 experiments/unstable-network/unstable.py dry-run \
  --plan artifacts/unstable-network/plan-20261027T091525Z --run-id un-collect-r1

# 제안(미실행; coordinator 최종 검증 + 별도 release 후에만). R2 수정으로 소스가 바뀌어 plan-20261027T105645Z는 이제 drift다.
PYTHONDONTWRITEBYTECODE=1 rtk proxy python3 experiments/unstable-network/unstable.py execute \
  --plan artifacts/unstable-network/plan-20261027T111532Z --run-id un-collect-r3 --release .scratch/unstable-network-20261027/release-collect-r3.json
PYTHONDONTWRITEBYTECODE=1 rtk proxy python3 experiments/unstable-network/unstable.py analyze \
  --plan artifacts/unstable-network/plan-20261027T111532Z --run artifacts/unstable-network/un-collect-r3 \
  --out artifacts/unstable-network/un-collect-r3-analysis.json
```

**execute 전 검증**: release JSON이 dry-run의 `release_binding`과 정확히 같아야 한다. execute는 kubectl 호출 전에 다음을 모두 다시 검사한다. 하나라도 어긋나면 rc 2다.
- plan, 배포 소스, 예산, trace 표, 순서표 hash
- 사본 원본과 helper hash
- 재사용 동결 소스, 입력, inventory, asset, corpus mapping, 모델 provenance

## 10. 미완료와 위험

- live 미실행이다. 다음은 실장비에서 확인되지 않았다.
  - Pod 안 netctl HTTP와 pyroute2 change(veth)
  - Jetson/NPU의 DAG 실행(이전 run에서는 확인)
  - 실제 정렬 오차
  - 단절 시 TCP 회복
- tbf change가 token bucket을 다시 채우는지는 확인하지 않았다(커널 소스 미확인). 다시 채운다면 영향 상한은 burst 4096 B다.
- netctl HTTP는 run_id로만 묶인다. 클러스터 내부 무인증 endpoint이며, 영향 범위는 owned netns다.
- 관측 fallback은 static 계획이다. 단절 중 cloud를 쓰는 계획이 선택될 수 있으며, 이것은 사전 규칙이다.
- cpu-c 메모리 상한(8 Gi)은 그대로다. max_inflight 8로 OOM 위험을 줄였지만 없애지는 못했다. fault 시에는 중단한다.
- 기존 preflight(2026-10-27T08:50Z, coordinator)를 재사용했다. 노드 상태가 바뀌면 새 preflight와 새 plan이 필요하다.

## 11. 실제 수집 결과 (un-collect-r3, 2026-10-27)

### 실행

- coordinator release `release-collect-r3.json`에 결속된 동결 plan `plan-20261027T111532Z`(sha256 `ee0aba54…c1e7`)를 썼다. 실행 전·후 dry-run 모두 drift `[]`였다.
- 명령은 9절의 `execute`와 `analyze` 그대로다.
- execute: 11:20:35Z–12:14:38Z (54 min 3 s), rc 0. wall 예산 10,800 s 중 3,243 s를 썼다.
- 준비 단계: cache 50 s, readiness 604 s, profile 65 s.
- 36 cell 모두 ok. skip / fault / fence / 수집 시 timing_invalid는 모두 0이었다.
- 분석: rc 0, `artifacts/unstable-network/un-collect-r3-analysis.json`(sha256 `bcabbe38…a4ac`).
- 실행 증거: `.scratch/unstable-network-20261027/live-r3/`.

### 검증된 실행 증거

**장치**: receipt의 run_id는 모두 `un-collect-r3`였다.
- vision: Intel NPU(openvino-npu @ser0002) 1,585건, Jetson cuda:0 @dev0009 1,546건, CPU 63건
- speech: Jetson 3,113건
- fusion: Jetson 4,976건, ser0002 CPU 1,412건
- preprocess: dev0003 / ser0002 CPU

**모델**: vision, speech, fusion, audio의 모델 sha256과 NPU ONNX derived hash가 plan의 `model_provenance` 기대값과 같다.

**cohort**
- 예정 도착 3,462건
- ok 2,929 / late_ok 265 / timeout 81 / admission_drop 187
- invalid 0, terminal status 없는 row 0

**timing**
- valid 36/36
- 최대 총 불확실성 122.6 ms (허용 250 ms 이하)
- netctl in-place change 성공 96 / 실패 0, 최대 적용 지연 0.76 ms

**qdisc**: 열화 phase에서 실제 drop과 backlog가 생겼다.
- slow 열화: drop 3,345, 끝 backlog 최대 149 KB
- burst narrow: drop 1,739
- burst outage: drop 1,322
- good / stable 구간에도 100 Mbps tbf drop이 있다(stable 12 cell 합 4,389). good을 무손실로 볼 수 없다.

**cleanup**
- Pod 7개와 ConfigMap 1개를 UID precondition으로 삭제했다. 남은 객체 0, `cleanup_verified=true`
- protected_changes `[]`
- 수집 후 읽기 전용 kubectl 확인에서도 run 객체 0

### 정책별 결과 (2 block 합계, in-deadline은 b0/b1 평균)

| trace | policy | ok/late/timeout/drop | in-deadline [b0, b1] | p95 ms | 품질(timely, 0 대입) |
|---|---|---|---|---|---|
| stable | 6개 모두 | 190/0/0/0 각각 | 1.000 | 246–673 | 0.361 |
| slow | dag_static | 215/0/0/0 | 1.000 | 276 | 0.337 |
| slow | greedy_comm | 206/6/0/3 | 0.956 [0.913, 1.000] | 1395 | 0.316 |
| slow | dag_reopt | 168/28/9/10 | 0.786 [0.903, 0.670] | 3757 | 0.258 |
| slow | dag_reopt_hysteresis | 155/24/8/28 | 0.724 [0.796, 0.652] | 6109 | 0.219 |
| slow | task_fastest | 150/47/0/18 | 0.698 [0.709, 0.688] | 5829 | 0.224 |
| slow | dag_reopt_no_network | 136/25/15/39 | 0.632 [0.612, 0.652] | 6302 | 0.209 |
| burst | dag_static | 172/0/0/0 | 1.000 | 339 | 0.348 |
| burst | dag_reopt_hysteresis | 161/11/0/0 | 0.935 [0.989, 0.882] | 3825 | 0.318 |
| burst | dag_reopt | 143/13/9/7 | 0.831 [0.828, 0.835] | 3259 | 0.300 |
| burst | greedy_comm | 100/37/10/25 | 0.579 [0.770, 0.388] | 3095 | 0.189 |
| burst | task_fastest | 94/44/11/23 | 0.546 [0.575, 0.518] | 6605 | 0.178 |
| burst | dag_reopt_no_network | 89/30/19/34 | 0.517 [0.540, 0.494] | 7944 | 0.167 |

phase / 회복 상세는 `live-report.md` 6절과 `live-r3/summary-tables.txt`에 있다.

### 해석 (탐색적; 유의성 없음)

- **dag_static 1.000은 적응 효과가 아니다.**
  - 초기 good profile의 고정 계획은 전부 엣지다: preprocess는 dev0003 CPU, vision/speech/fusion은 Jetson dev0009.
  - shaping은 edge↔server 경로에만 있다. 이 계획의 그 경로 relay 본문은 cell당 약 4 kB(관측 poll)뿐이었다.
  - 이 결과는 "열화 경로를 쓰지 않는 엣지 단독 계획이 deadline 안에 가능했다"는 이 토폴로지·입력의 범위 조건이다.
- 관측 기반 정책(dag_reopt, hysteresis, greedy_comm)은 server CPU/NPU 계획으로 전환한 뒤 열화 phase에서 손실을 입었다.
  - 열화 구간 cohort 예: slow [20, 40)에서 dag_reopt 0.60, hysteresis 0.26, greedy 0.92
  - burst narrow [20, 28)에서 hysteresis 0.79, reopt 0.40, 나머지 0
- 통신 ablation `dag_reopt_no_network`는 열화 trace에서 dag_reopt보다 낮았다(slow −0.154, burst −0.314). 네트워크 비용 반영의 효과와 방향은 맞지만 2 block이다.
- block 간 변동이 크다. 예: greedy_comm burst 0.770 대 0.388.
- stable에서는 모든 정책이 1.000이고, 차이는 지연뿐이다.
- 회복: 72개 경계 모두 recovered. 첫 회복 시간은 같은 block에서 정책 간 거의 같다(공유 cohort의 다음 도착). 그래서 구별력이 없다. 10 s 창의 비율만 정책별로 다르다.
- burst outage [28, 30)은 cell당 도착 1건이라 해석하지 않는다.

### 주장하지 않는 것

- 품질 적격 성공: diagnostic이고 reused60 0.355 < Qmin 0.40이다.
- 독립 holdout, 통계적 유의성, capacity
- 실제 센서 획득: 입력은 재사용 60쌍이다.
- 물리 링크 변경: owned gateway netns의 edge↔server만 바꿨고 edge↔edge LAN은 shaping하지 않았다.
- 실제 엣지 네트워크 분포의 대표성
- 다른 토폴로지·입력 크기로의 일반화
