# 실제 topology-profile DAG 실험 구현 명세

Status: ready-for-agent

사용자 요청: 새 Git 실험을 분석하고 현 Kubernetes에서 실행 가능한 계획을 작성한다. 이번 산출물은 계획이며 신규 배포/AI 성능 실행은 포함하지 않는다.

기준 source: e110d8b431c7c7d7c185897fe3429cda20c63257. [전체 실행 계획](../../docs/experiments/topology-profile-cluster-plan.md).

부하 관련 후속 기준은 [재분석](../../docs/experiments/topology-profile-load-reanalysis.md)과 [edge-load-validation 명세](../edge-load-validation/spec.md)를 따른다. 초기 압축 파일/Pod별cap/closed-loop 연결 검증에서 원 decoded 경계·공유 WAN·open-loop 검증으로 확대한다. 기존 결과를 덮어쓰지 않는다.

실행 후속 순서:

1. [모델·입력 계약](issues/01-model-reference.md)
2. [분기·합류 실행기](issues/02-fork-join-runtime.md)
3. [보정·공정한 배치 계약](issues/03-profile-and-placement.md)
4. [파일럿 수집·분석·정리](issues/04-pilot-collection.md)

고정 범위: E0 dev0003, E1 dev0002, C ser0002 CPU 우선; vision/speech 가변, 첫 fusion C고정. 실제 scheduler+3성분 비교 및2단순 기준선. 입력은 엣지 파일 재생. 동적 정책·GPU/NPU 확장은 후속이다.

계획 완료 기준: 원자료 정합성 재확인, 현 노드·scheduler·requests 점검, 모델 자산/실행기 차단 조건, 단계별 gate, 배정표, 비용/품질/실패·반례 해석, 실행 티켓이 문서화되어야 한다.
