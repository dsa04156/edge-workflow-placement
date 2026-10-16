# 배치 순위 안정성과 상태 확인 비용의 교차 실행 진단

Status: ready-for-human

## Material Passport

- Origin Skill: experimental-design; academic-research-suite / experiment-agent
- Origin Mode: run
- Origin Date: 2026-10-16
- Verification Status: UNVERIFIED
- Version Label: placement_cost_diagnostic_v1

## 수집 전 계약

fresh v2에서 발견한 ARM-source hubble_deep_field의 예측 순위 변화와 chelsea의 workflow/total 역전을 대상으로 한다. 결과를 보고 고른 두 입력의 표적 후속 진단이며 전체 입력·장비 일반화나 논문 정책 실패 검정이 아니다.

- 기존 두 노드·모델·이미지·CPU reference·HTTP 실행기·품질 규칙을 유지한다. ARM source만 사용하고 worker2/client2를 순차 3회 새 배포한다. 같은 host/date/cache의 반복이며 독립 물리 환경이 아니다. 기존 workload·EdgeCore·network 설정을 변경하지 않는다.
- 각 배포에서 보정은 입력2 × 배치8 × (warmup2 sweep + 측정6 sweep) = 128회다. 매 sweep에서 두 입력의 순서를 randomization.py의 assign_factorial_runs로 배정한다. 각 입력의 AAA/NNN을 먼저 연속 실행하고 나머지6배치를 무작위 순서로 실행한다. anchor 선후는 warmup2에서 각각1회, 측정6에서 각각3회로 균형 배정한다. seed는 20261201 + block*100 (보정), 20261251 + block*100 (평가)다. 정확한 스케줄은 실행 전에 파일로 보존한다.
- 보정의 모든 출력(warmup 포함)이 CPU reference Top5 순서와 일치해야 해당 T2 alias가 후보가 된다. 입력은 사후 제외하지 않는다. 보정 protocol 오류·불완전 표·anchor 추론 후보 제외 시 중단하고 원자료/정리 결과를 보존한다.
- 보정 후 기존 task/ingress/edge 중앙값 비용표와 입력별 T2 alias의 state_validation 중앙값을 동결한다. 데이터 인지 greedy, workflow DAG 열거, workflow+selected-state-check DAG 열거에 동일 정보와 후보를 제공한다. 공통 observation은 배치별 고정 비용으로 학습하지 않는다. 보완 모델은 total 전체의 최적해를 보장하지 않는다. service_prepare 필드에 state 비용을 혼입하지 않고 별도 동결 항목으로 보관한다.
- 정책 선택은 동결 시 1회다. 제어 호스트 선택시간을 별도 보고하며 평가 callback은 고정 배치만 적용한다. 평가 응답으로 정책을 다시 선택하거나 튜닝하지 않는다.
- 평가도 전체8배치를 사용한다: 입력2 × 배치8 × (warmup2 sweep + 측정10 sweep) = 192회/배포. 측정 anchor 선후 각5회. 전체960workflow, 평가576회/측정480회다. 같은 계획을 고른 정책은 동일 관측을 공유한다. 전체8배치 관측은 예측 오차 진단용이며 평가 최저값을 사전 정책처럼 제시하지 않는다.
- 최소 주기0.25초, 오류·초과·극단값 전부 보존한다. 표본 수는 고정 진단 예산이고 검정력/MDE·유의성 주장을 위한 산정이 아니다. 표적 입력별·배포별·sweep별 paired 차이, n/mean/median/SD/range, 오류 및 유효 완료를 보고한다. 주 지표 total_ms, 보조 workflow와 state_validation 및 공통 observation. p값/CI/p95/SLO/통계적 우위·동등성 주장은 하지 않는다.
- hubble: 보정 additive 예측과 직접 배치별 workflow 중앙값, 평가 anchor 차이를 나란히 제시한다. chelsea: workflow/state/observation/기타 시간의 차이를 중복 없이 분해한다. 보정 상태 비용이 계획을 바꾸지 않으면 보완 효과는 식별되지 않았다고 보고한다. 반복해서 손실이 남더라도 실제 동적 네트워크·runtime 변화를 조작한 증거로 해석하지 않는다.
- SDK/Pod 준비·보정·선택·평가·정리를 별도 기록한다. 원자료 artifacts/placement-cost-diagnostic, 새 orchestration/client/policy 파일을 사용하여 기존 실행 source와 결과를 보존한다. 실측 실행기와 schema는 변경하지 않는다. 소유 label 확인 후 자기 자원만 정리한다.

## 구현 검증

기존 합의된 PolicyRunner 실제 CPU HTTP 경계에서 알려진 비용 사례로 상태 확인 비용 때문에 배치가 바뀌고, 선택 경로의 receipt·출력이 유지됨을 red/green 검증한다. 외부 SDK/API 추가 없이 기존 버전과 배포 경로를 재사용한다. Git 저장소가 없어 code-review 스킬의 fixed-point diff 전제는 충족되지 않으며 source snapshot·계약/receipt 검증을 기록한다.

## 완료

[후속 결과](../../docs/experiments/placement-cost-diagnostic.md): 960/960유효, receipt2880, protocol오류0. 원래 두 평균 역전은 재현되지 않았고 state 보완은6조건 모두 기존 DAG와 같은 계획이었다. 이는 표적 진단의 완료이며 연구 전체 종료가 아니다. 사용자가 센서 source를 엣지로 명확히 고정했고 다음은 노드 간 전달 조건별 배치 경계 검증이다.
