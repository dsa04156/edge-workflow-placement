# E3 입력 다양화와 새 worker 배포 재현

Status: ready-for-human

## 범위

E2의 단일 dog 기반 source별 동결 계획이 새 실제 입력에서도 유지되는지 확인한다. AnchorDL 공식 초록이 데이터 전송·모델 로딩 joint cost와 look-ahead를 이미 다루므로, 이번 실행을 신규 알고리즘·AnchorDL 재현/실패 검증으로 부르지 않는다. 본문 미확보 상태에서 수식을 추정 구현하지 않는다. 별도 검토: docs/research/ieee-11662529-review.md.

## 수집 전에 고정

- 입력8개: 기존 dog bridge와 scikit-image commite8a42ba85aaf5fd9322ef9ca51bc21063b22fcae의 astronaut.png/camera.png/chelsea.png/coffee.png/hubble_deep_field.jpg/retina.jpg/rocket.jpg. 출력 보기 전 선정. 원본 asset bytes와 공식 registry hash 확인, 추가 압축/resize/padding 없음. 전처리는 기존 RGB/Resize256/CenterCrop224/float32 NCHW 유지.
- 동일 SqueezeNet ONNX. CPU reference 점수·tensor hash·top5를 입력 계약으로 동결한다. 이 CPU 예측은 정답 라벨/accuracy가 아니다. 모든 NPU 불일치를 보존하고 top1 agreement/top5 set·순서·overlap/maxabs/RMSE를 보고한다. 프로토콜/shape/finite/hash 무결성과 수치 일치를 구분한다.
- ARM edge CPU와 Intel NPU 서버. T1/T3는 worker CPU, T2만 CPU/NPU. 전용 worker2/client2를 순차3회 전부 재배포하여 Pod UID·컨테이너 ID가 달라야 한다. cache Pod는 SDK 전달용으로 공유한다. 같은 host·driver/OS cache·날짜라 환경 독립성을 보장하지 않는다.
- source2 × 입력8 × allARM/allNPUserver2 × warm/eviction2 =64조건/배포. 조건마다 warmup2+측정5, 총1,344 workflow/측정960회. seed20261021+deployment로 source내 조건, seed20261025+deployment로 source순서를 experimental-design 스크립트로 배정한다. source grouping은 whole-plot, 같은 배포 안의 paired 비교다.
- 모든 조건은 먼저 warmup2. eviction 조건은 warmup 뒤 전용 NPU 객체를 내려 첫 측정 요청에서 실제 재준비하도록 한다. allARM은 NPU를 사용하지 않아 추가 준비 없음. 준비·driver cache 경계는 E2와 같다. 0.25초 최소주기, 초과/오류 모두 보존.
- 두 anchor만 실측한다. E2의 모든 적응 정책은 동결 source별 같은 계획이므로 관측을 공유하여 source=edge이면 allARM, source=server이면 allNPUserver 기록을 사용한다. E2 비용표는 이번 결과로 갱신하지 않는다. 새 입력에서 더 빠른 anchor가 발견되어도8배치 global optimum이나 AnchorDL 실패로 부르지 않는다.
- 주 지표: 5요청 episode의 평균/합 total_ms(health 관측+workflow), cold 준비 포함. deployment별 paired차이, 입력별 source/state별 역전 여부와 수치 품질을 보고한다. 단일 날짜3배포 기술통계이며 p값/신뢰구간/p95/SLO 우위 주장 없음. SDK/Pod시작/phase exec/환경eviction 제어 비용 별도 제외.
- HTTP에서 입력 ID와 bytes/tensor/reference가 맞는지, A→B→A 전달에서도 ID/hash를 유지하는지 기존 테스트 경계로 red/green 확인. corpus 미설정인 과거 경로 회귀 유지. E0~E2는 당시 동결 ConfigMap 기준으로 해석한다.

## 완료 조건

동결 corpus/design/source, 새3배포 실측과 전 stage receipt, 품질/재현성 결과 Markdown·진입점 갱신, 전용 리소스/임시wheel 정리·운영 상태 확인·Semantica 기록. AnchorDL 검토 상태와 본 비교 미완료를 명시한다.

## 결과

run20261016t085016에서1,344 workflow/측정960회와4,032 stage receipt,새Pod/컨테이너12개를 확인했다. 입력별 anchor 순위가 달랐고 camera는 NPU Top-5 집합이 CPU와 달랐다. [실행 문서](../../docs/experiments/e3-input-replication.md)에 모든 입력·배포별 결과와 한계를 보존했다. AnchorDL은 공식 초록만 확인했으며 본문/코드 재현으로 부르지 않는다.
