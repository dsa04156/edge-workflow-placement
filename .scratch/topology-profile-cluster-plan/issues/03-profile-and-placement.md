# 03 실측 보정과 동일 조건의 여섯 배치 정책

Status: ready-for-agent
Blocked by: 01, 02

## 작업

prepare/profile/capture의 manifest·실제 노드/hash 검사·유효 requests·limits·readiness·rtk 계약을 보완한다. 동일 후보·자원에서 resource, topology_only, profile_only, topology_profile 및 task_fastest/data_greedy를 연결한다. compute와 실제 payload 전달의 비용 경계를 고정하고 같은 노드 동시 분기 오차를 진단한다.

## 완료 조건

실제 default-scheduler의 노드 선택 증거, API server dry-run 통과, synthetic gate 유지, helper와 sidecar를 포함한 자원 검증, 품질 통과 measured profile/hash·동결 계획. 비용 모델을 수정하면 모든 관련 성분 제거군에 적용하고 새로운 평가로 분리한다. 공유 계획·실행군 계약과 정책별 startup 비용을 분리한다.
