# 04 pilot 수집·무결성 분석·복구

Status: ready-for-agent
Blocked by: 03

## 작업

먼저 split/native 한 조건 smoke, 이후 계획의3regime×6정책×3block 배정표를 실행한다. phase start/end와 timeout 부분출력, 모든 attempt·receipt·actual placement·비용·품질 실패를 보존한다. 기존 artifact는 덮어쓰지 않는다.

## 완료 조건

예정/실제 발행·완료/실패 분모, 동일 실행군 대조, 모델/노드/입력/profile hash, cap 대상/counter/복구, 소유 resource cleanup, 보호 workload 전후 확인. 반례 및 예측 오차 포함 보고. pilot으로 통계적 우월성을 선언하지 않고 본 실험 block 수·기간은 분산/의미 있는 효과·다중비교를 기준으로 별도 산정한다.
