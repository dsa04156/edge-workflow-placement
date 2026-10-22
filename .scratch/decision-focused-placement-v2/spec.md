# 선택적 측정 비교 보완 v2

Status: ready-for-human

구현·검증 완료: 최종71개 테스트,750개 비교, 독립 검토2회 및 마지막 문구 정정 호스트 확인. 결과는 docs/research/decision-focused-placement-v2-20261021.md.

## 목표
공통항 제거라는 단순 보완으로 제안 contrast의 이득이 설명되는지 확인하고, 모든 사례의 최적 배치가 p였던 선택 편향을 줄인다. 신규 알고리즘 개발이나 클러스터 실행은 범위 밖이다. 결과 확인 전 아래 계약을 고정한다.

## 범위와 소유권
Claude Developer 소유: experiments/decision-focused-placement/ 내 신규 v2 모듈·테스트·README 및 필요한 최소 backward-compatible synthetic.py 확장, artifacts/decision-focused-placement/synthetic-v2/ 신규 결과, 본 폴더 developer-report.md. 기존 regret.py, p75.py, fixtures.py, run_synthetic.py와 모든 기존 artifact는 수정 금지. coordinator는 명세·분석·검증 기록만 쓴다. 다른 작업자의 변경을 되돌리지 않는다.

## 설계 결정
- 기존 run_policy(관측만 보는 정책, 표본 흐름, 전체 regret 종료, p75 통계, timeout, 자원 예산)를 재사용한다. optional policy factory와 optional fixture stream_key 등 최소 주입을 허용한다. v1의 기본값·정책 목록·기존 결과 의미는 유지한다.
- 추가 기준선 cancel_common_round_robin: 한 좌표 j의 계수가 모든 배치의 모든 경로에서 정확히 동일할 때만 그 좌표를 측정 대상에서 제외한다. 이는 전역 공통 가산항의 보수적 판별이다. 나머지 사용 좌표를 균등 순환한다. 원래 비용모형·인증 계산·d·delta는 그대로 유지한다. 모든 좌표 제거 시 유한 support 문제는 측정 없이 원래 regret 검증으로 종료해야 한다. 일부 경로/일부 배치만 공통인 좌표를 제거하면 안 된다. 분포·참 p75·참 비용·미래 표본 접근 금지.
- 비교 정책: 기존4개+cancel_common_round_robin. 알고리즘·후보 선택·순환주기 변경 금지.
- fixture 15개: (a) 원래6개 내용 복제; (b) timeout 제외5개에 대해 배치 id p/q만 교환한 label-swap 대응5개. 배치 list 순서·계수·상수·arm·fallback p는 유지하고 대응 원본과 stream_key를 공유; fallback 편향도 관찰한다. (c) shared_q_optimal: shared_additive의 y 분포/support를 U[12,32]로 바꿔 p102,q93이 되는 실질적 역전1개; (d) 3배치 p=x,q=y+8,r=33, support=uniform 범위인 3개: x,y U[0,40] (p30,q38,r33), x U[10,50],y U[0,20] (p40,q23,r33), x,y U[20,60] (p50,q58,r33). 비용 모두1, timeout없음, fallback p. 이 세 fixture stream_key는 서로 별개.
- 새 seeds 10..19; epsilon1, delta.05, batch20, max_probes150, planner10s/run, lp cap20000, 총900s 등 기존 설정 유지. 15*5*10=750 runs. 결과에 fixture·config·source hashes·원시 probe records·실행 시작/종료·누락 목록 보존. strict JSON, 출력 경로 overwrite 금지. CLI 예 python3 experiments/decision-focused-placement/run_synthetic_v2.py --output artifacts/decision-focused-placement/synthetic-v2.
- 각 fixture·seed의 정책 간 arm별 공통 표본 흐름을 보장. label-swap은 원본과 동일 표본 prefix 보장. 참 비용·최적 배치·항상 p gap은 평가자 전용으로 기록. swap은 독립 표본으로 합산하지 않는다.
- 요약은 인증률, 종료 사유, 참 gap, probe 수, 가상 획득 비용을 분리한다. 비용 절감 비교는 양쪽 모두 인증한 seed에만 짝지어 계산하고 해당 n을 표시한다. 미인증의 적은 지출을 개선이라고 부르지 않는다. 이번 예산은 같은 probe 상한이며 같은 비용 상한 실험으로 부르지 않는다. v1을 보고 설계한 사후 후속 검증임을 명시한다.

## 수용 기준과 테스트 경계
기존 승인된 run_policy 공개 결과와 CLI JSON 인터페이스를 재사용한다. 사용자 요청의 기존 합성 검증 범위 안에서 경계를 확장한다. tdd 스킬의 red→green을 작은 단위로 수행하고 실제 실패/통과 명령을 보고한다.
- 전역 공통항 사례에서 baseline은 z를 측정하지 않고 y로 인증. 전 경로 공통이 아닌 경우 필수 좌표 보존. 3번째 배치만 계수가 다른 경우도 보존. 모든 항 공통+유한 support에서는0 probe.
- v1 테스트52개 전부 통과. 고정 시계 또는 wall-time 필드 제외 비교로 기존 v1 동작 호환성 검증.
- label-swap 결과의 선택 id만 대응 변환되고 관측 prefix·인증·비용이 일치하는지 검증(동률/무관측 fallback은 별도 명시). 진짜 q/r 최적 fixture 평가값은 위 수작업 수치로 검증.
- CLI 전체750회, 누락·budget_planner_time 없음을 확인하거나 원인을 보고. strict JSON·합계·원시 기록·source hash·overwrite 거부 검증.
- 명령: rtk proxy python3 -m unittest discover -s experiments/decision-focused-placement -p 'test_*.py'. v2 실행은 결과를 본 뒤 fixture/예산 튜닝 금지. 구현 오류 수정은 별도 output 디렉터리와 기록으로 보존.

## 실행·검토 계약
기존 연구/공식 문서 조사와 표준 라이브러리 구현 근거를 재사용한다. 신규 외부 의존성/API 도입 없음. claude-implementer 및 tdd 스킬 적용. 구현은 Orca Claude Opus5.5, 기존 claude.ai firstParty 구독만 사용. 실제 모델·인증 확인. 독립 Spec/Standards 리뷰는 code-review 스킬, gpt-6-astra Orca 워커2개; 최대2라운드. 리뷰 고정점은 before/ 스냅샷이며 HEAD에 없는 기존 파일을 신규 구현처럼 취급하지 않는다.

## 금지
커밋/push/merge/배포/클러스터 변경, 의존성 추가, API 종량제·다른 모델로 전환, 기존 artifact 덮어쓰기, 결과에 따른 실험 조건 변경. 셸은 rtk/rtk proxy 사용. 상위 codex-work 디렉터리에서 프로젝트 작업 금지.

사전 명세 산술 정정: y~U[12,32]의 p75는27이므로 shared_q_optimal의 p는102다. 2026-10-21 Developer 질문을 받고 첫 v2 실행 전에 예상값99를102로 정정했다. 분포·support·예산은 변경하지 않았다.

독립 검토 후 경계 계약 명시: 사용되는 무한 구간이 있으면 인증하지 않는 기존 runner 규칙을 유지하기 위해 상한이 무한인 공통 좌표는 측정할 수 있다. 상한이 유한한 공통 좌표는 다른 좌표의 구간이 무한이어도 제외한다. 이 명시는 기존15개 유한 support fixture·통계·예산을 바꾸지 않는다. 상세 fix-task.md.
