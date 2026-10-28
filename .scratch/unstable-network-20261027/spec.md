# 불안정 네트워크에서 이기종 DAG 프로파일링·스케줄링 재실험

Status: ready-for-agent

## 목표와 사용자 승인

사용자: “엣지컴퓨팅환경이라 네트워크는 불안정하다는 가정을 미리 깔고가서 다시 실험해줘”. 이는 기존 실장비 CPU/GPU/NPU DAG의 재실험 승인이다. 미래 네트워크가 알려진 정적 regime별 실험을 시간에 따라 변하는 실제 데이터 경로 실험으로 확장한다. 제안 방법은 기존 워크플로 프로파일링 + 네트워크 인지 스케줄링이다. 새 예약 알고리즘·DRL·모델 교체를 도입하지 않는다.

실험은 불안정한 엣지 링크를 가정한 통제된 stress test다. 아래 합성 trace가 모든 현실 엣지 네트워크의 대표 분포라는 주장은 하지 않는다. 안정 trace는 대조군으로 유지한다.

## 역할과 허용 범위

- coordinator Astra: 이 명세·수용 기준, 독립 검토, release, 실제 수집 승인·최종 검증.
- developer Claude Opus 5.5: 구현·테스트·디버깅. 공식 Claude Code 구독 로그인(authMethod=claude.ai, apiProvider=firstParty)만. 모델은 claude-opus-5-5, API/타 공급자/fallback 금지. actual model/auth를 값 노출 없이 확인한다.
- 독립 리뷰: Codex gpt-6-astra, Standards/Spec 두 축, 최대 2라운드. 구현 파일 수정 금지.
- 당신은 혼자 작업하지 않는다. 다른 작업자의 변경을 되돌리거나 덮어쓰지 않는다.
- 구현 소유: 새 `experiments/unstable-network/`, 새 `.scratch/unstable-network-20261027/` 내부 구현 보고/테스트 자료, 새 `docs/experiments/unstable-network-20261027.md`. coordinator의 spec.md는 수정하지 않는다.
- 기존 `experiments/quality-network-baselines/`, `quality-network-heterogeneous/`, `real-topology-live/` 및 기존 artifact는 변경 금지. 필요한 모듈은 새 디렉터리로 복사/명시적 재사용하고 출처 hash를 남긴다. frozen-source와 현재 import/shipped-source의 관계를 명시한다. 대규모 refactor 금지.
- 새 artifact는 `artifacts/unstable-network/`의 신규 run 디렉터리만. 파일은 create-exclusive. commit/push는 요청하지 않음.
- 모든 shell 명령은 rtk 또는 rtk proxy. 프로젝트 작업은 반드시 `/home/jinuk/codex-work/scheduler`에서 수행.

## 재사용 근거

`docs/experiments/quality-network-baseline-results.md`, `quality-network-baselines.md`, `experiments/quality-network-baselines/{live,design,baselines,generator,policies,analyze,capture}.py`를 먼저 읽는다. 기존 plan은 `artifacts/quality-network-baselines/plan-20261026T035720Z/plan.json`. 모델/데이터와 deps는 해당 manifest와 로컬 asset을 재사용한다. 현재 git 기준점 d87bde1fde21f014c1baebd1132673984e960a51, 기존 dirty 변경은 보존한다.

현재 실제 범위: source ARM dev0003, CPU ser0002/dev0003, Jetson GPU dev0009, Intel NPU ser0002. 노드/가용성은 새 preflight로 확인. 전체 12노드 후보 감사는 유지하되 점유·장애·모델 미검증 장치를 실행 성공으로 세지 않는다. Mobilint 별도 ResNet50 성공을 이 VQA 후보로 전용하지 않는다.

## 설계 결정

### 고정 항목

- 5-task 실제 VQA DAG, 기존 모델/precision/전처리/입력60쌍과 2s deadline 유지. 기존 출력 재사용 대신 실제 모든 단계 실행.
- quality=diagnostic, Qmin=.40, 기존 eval .355. 기존 자료로 품질 적격이나 독립 holdout을 주장하지 않는다. 품질 미달은 네트워크 진단 실행을 막지 않되 결과에 명시한다.
- 새로운 서비스/모델 품질 회복은 이 실험 범위 밖.
- open-loop 1.6rps, trace당60s, Poisson seeded arrivals; 실제 발생한 같은 block/trace의 요청 cohort를 모든 정책이 공유. cancel10s, max_inflight8로 고정(기존32 대비 OOM 위험과 admission 폭주를 제한; 변경 사실과 모든 drop 기록). 기존 worker connection/gateway 제한 유지, 메모리 상한 변경은 preflight와 coordinator 승인 후에만.
- 2 block의 탐색 반복. 통계적 유의성·capacity 추정 없음. 반복 단위는 block/run이며 수천 요청을 독립 환경 반복으로 세지 않는다. 사후 결과에 따라 표본·조건 변경 금지.

### 정책 6개

1. task_fastest (초기 compute only 고정)
2. greedy_comm (관측 기반 기존 통신 greedy)
3. dag_static (초기 정상 profile의 DAG 계획을 전체 trace 동안 고정)
4. dag_reopt (현재 관측 기반 동일 열거)
5. dag_reopt_hysteresis (기존 15%/50ms/5s)
6. dag_reopt_no_network (dag_reopt와 같은 관측·큐·계산 추정·feasibility, 선택 비용에서만 전송비용0; 실제 데이터 경로와 비용은 그대로)

통신 ablation을 통해 네트워크 반영 효과를 분리한다. 원 논문 이름의 재현이라고 부르지 않는다. 프로파일 초기 측정·관측 poll은 동일. 미래 phase, 미래 rate/delay/loss, phase별 미리 측정한 비용표를 정책 입력으로 주지 않는다. 실제 관측만으로 적응한다.

### 사전 고정 네트워크 trace

실험 소유 gateway Pod netns의 edge↔server 경로에만 적용. host/물리 링크/다른 workload 네트워크 변경·터널 금지. edge↔edge LAN은 기존대로 유지하고 범위를 명시한다.

- stable: t0..60 = 100Mbps, 추가 delay0ms, jitter0ms, loss0%.
- slow: t0..20 good; t20..40 = 10Mbps, delay20ms, jitter0, loss0; t40..60 good.
- burst: [0,10) good; [10,20) 10Mbps/delay20ms/jitter5ms/loss0.5%; [20,28) 2Mbps/delay20ms/jitter5ms/loss1%; [28,30) 같은 rate/delay/jitter, loss100%(2s단절); [30,40) good; [40,50) 10Mbps/delay20ms/jitter5ms/loss0.5%; [50,60) good.
- 3trace×6policy×2block=36cell, 총 요청 발생구간36분. 단계 적용은 inflight가 있는 동안 이루어지며 phase 사이에 drain하지 않는다. cell 사이에는 복원→drain/idle barrier→reset.
- tc root를 phase마다 삭제/재생성해 큐를 몰래 flush하지 말 것. 기존 owned root·filters를 유지한 tbf/netem change 등 실제 변경 동작을 1차 docs/source로 확인. 변경이 불가하면 제한을 보고하고 임의로 다른 실험으로 바꾸지 않는다.
- netem 난수 손실은 동일 schedule/seed가 packet-by-packet 동일 실현을 보장하지 않는다. 지원되는 seed를 동결하고 없으면 설정 수준 재현 한계를 기록. 실제 drops/backlog/qdisc stats 보존.
- 네트워크 적용시각과 요청 발생시각은 교차 호스트 monotonic을 직접 빼지 않는다. 실제 시작 정렬과 오차를 계측하고, 변경 예정/실제 시각·stdout/stderr·qdisc 상태를 남긴다. 정책에 미래 trace는 숨긴다. 동기화 방식은 구현 제안서에 설명하고 coordinator가 release 전 검증한다.

### 기존 관측 오류의 기본 보완

현재 poll 오류 `{error}`를 waiting/active0으로 읽는 문제는 새 복사본에서 수정한다. 마지막 성공 큐 상태/시각을 보존하고 error/age를 노출한다. 신규 성공 관측 없이 failed poll 시각을 갱신하지 않는다. 마지막 성공이 전혀 없거나 stale한 경우 보수적 처리(검증된 static fallback 등)를 명시적으로 기록하며 실패를 빈 큐로 해석하지 않는다. 모든 관측 기반 정책에 같은 규칙. 이는 기본 결함 보완이며 새 알고리즘 기여가 아니다. worker별 시간과 snapshot 요약을 구분한다.

### 기록과 분석

- 모든 입력의 scheduled/released/completed/terminal status·plan·장치/hash·관측 나이·decision ms를 유지.
- phase 타임라인·적용 지연·진짜 qdisc counters·경로 bytes를 기록. 설정값을 실효 throughput으로 부르지 않는다.
- 전체 cohort deadline goodput, quality/coverage, latency, drop/timeout/failure, plan switches; phase별 도착 cohort의 성능과 recovery(첫 회복 이후 고정10s 창의 완료율 등)도 사전 정의. phase경계 걸친 요청 누락 금지.
- 같은 trace와 정책 순서를 block화/균형 무작위화. experimental-design의 `assign_factorial_runs` 등 실제 helper를 사용하여 seed20261027 schedule 생성·동결; helper 의존성은 기존 환경 우선.
- 잘 동작하는 조건, 이득 없는 조건, 실패까지 모두 보존. OOM/장치 상실이면 해당 cell을 장애 영향으로 기록하고 다음 cell을 무리하게 실행하지 않는다.

## 테스트와 수용 기준

기존에 승인된 공개 경계(정책결정, generator HTTP/ledger, 네트워크 제어 spec, release-bound collector, 오프라인 분석)를 재사용한다. tdd 스킬의 tests.md/mocking.md를 읽고 해당 경계에서 red→green을 보존한다. 새 승인 질문 없이 이 확정 범위에서 진행.

필수 검증:
- 시간변화 trace는 정확히 실행구간을 덮고 범위밖/중복 phase를 거부. 미래 설정이 정책 payload에 없음.
- 실제 network control 변경은 owned netns만, 실행 중 큐 flush 없는 change, 종료/오류 시 restore.
- 같은 입력 trace/자원/품질/관측 계약. no-network는 비용 항만 제거하고 실행 경로 유지.
- failed poll은0큐/새 성공시각으로 바뀌지 않음; 마지막 성공·stale fallback 기록.
- absolute deadline, inflight/drain, 모든 결과 salvage 및 cleanup UIDs 유지.
- fake clock/unit tests와 실제 loopback HTTP integration(가짜 연산 허용, 성능 근거로 사용 금지). 기존 import 재사용 영향 tests 필요한 범위만.
- plan/dry-run에서 모든 변경 소스·모델·trace·budgets hash 결속. source 변경 뒤 새 profile. public CLI 예시/정확한 명령 기록.

## 단계와 승인 경계

이번 개발 dispatch는 구현·로컬 테스트·읽기전용 preflight·plan/dry-run까지만 수행한다. **실제 Pod 생성/실행은 독립 리뷰와 coordinator release 후 별도 dispatch**다. 이는 추가 사용자 승인이 아니라 내부 검증 순서다. 사용자 실행 승인은 이미 있다.

개발 완료 때 실제 diff/파일 목록, tests red/green 결과, 실행시간/자원 예상, profile/trace 제어 방식, auth/model 증거(비밀값 제외), 남은 제한을 `.scratch/unstable-network-20261027/developer-report.md`로 제출. `worker_done` 후 idle. 기존 실험 파일 변경 금지.

전체 실행 wall 예산은 준비/보정/수집/복구 포함 최대3시간으로 시작한다. 진행 상태를 숨기지 않고 단계별 수집·부분 실패를 남긴다. 단순 시간이 걸린다는 이유로 CPU synthetic 결과를 본 실행 대신 제출하지 않는다.

## 도구 확인

Orca1.4.235 version-matched orchestration guide와 coordinator-loop를 읽었음. executable `/home/jinuk/.orca/cli/bin/orca` 고정. Coordinator runtime gpt-6-astra high 확인. Claude auth는 claude.ai/firstParty/max 확인(개인 식별값은 보고서에 저장하지 않음).
Context7 pyroute2 조회는 Monthly quota exceeded. GitHub MCP svinota/pyroute2 netem 검색은0결과. 필요 API는 공식 pyroute2 docs 및 고정0.9.6 wheel 소스를 확인하고 fallback 근거를 기록한다.
