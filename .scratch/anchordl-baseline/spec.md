# AnchorDL 동일 정보 비교 준비

Status: needs-info

## 목표와 현재 결과

AnchorDL의 원 비용식·look-ahead·cache·baseline을 확인하고 기존 KubeEdge 실행기의 상태/비용 의미와 맞춘 뒤 비교한다. [원문 탐색](../../docs/research/anchordl-source-acquisition.md)과 [재현 계약](../../docs/research/anchordl-reproduction-contract.md)을 작성했고, [동결 소스·253개 증거 파일 점검](../../artifacts/anchordl-readiness/audit.json)을 완료했다. 정책 구현·새 성능 측정은 아직 수행하지 않았다.

## 확보된 것

- Crossref 원응답으로 DOI10.1109/CLOUD72782.2026.00057와 IEEE11662529의 연결 확인.
- E2/E3의 실제 HTTP 실행기·source별 비용표·객체 준비 상태·품질/receipt 이력.
- service readiness와 model-weight residency, 요청4회 상각과 DAG look-ahead를 구분해야 한다는 비교 조건.

## 필요한 입력

원문 PDF 또는 접근 가능한 공식 본문/구현 URL. 최소한 비용식·알고리즘·cache·baseline 절이 필요하다. 원문/공식 코드의 부재를 주장하는 것이 아니라 현재 확보하지 못한 상태다. 제목·DOI 재확인은 필요하지 않다.

## 이어서 할 작업

원문 계약의 쪽수/식/코드 근거를 채우고 adapter 변경점을 고정한다. 기존 승인된 실제 HTTP 검증 경계에서 구현을 확인한 뒤, 사전에 고정한 품질·보정/평가 분리·동일 관측 조건으로 비교한다. 새 알고리즘 필요성이나 NPU 미지원을 전제하지 않는다.

원문과 독립적인 [공통 관측·정책·HTTP 연결](../common-policy-runner/spec.md)은 구현·검증했다. 이 완료가 AnchorDL 원문 계약이나 정책 재현 완료를 뜻하지는 않는다.

[E2 실측 표·정책 연결](../measured-policy-bridge/spec.md)도 같은 snapshot60결정·대표10배치의 ARM/NPU 적용으로 확인했다. 원문 계약의 미확인 상태는 유지한다.
