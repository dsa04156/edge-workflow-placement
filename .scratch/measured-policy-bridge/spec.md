# E2 실측 비용표와 공통 정책 연결

Status: ready-for-human

## 범위와 수집 전 계약

기존 E2 dog/source별 동결 비용표를 PolicyRunner profile에 연결한다. E2 정책 계산은 당시 `dynamic_client.choose`를 그대로 재사용하며 Task-fastest를 별도 기준선으로 둔다. 원문 비용식이 없는 AnchorDL 구현·새 최적화 정책은 추가하지 않는다.

- 변환 입력은 E2 selection/run/동결 ConfigMap·보정 로그다. source/Task/ingress/간선 수치를 그대로 보존하고 실제 사용한 파일·코드 hash를 검증한다. ARM 재준비 비용은 미측정이므로 `null`, NPU 재준비는 E2 중앙값27.011536993086338ms다. 이를 최신 비용·가중치 부재 비용으로 부르지 않는다.
- 정책에는 현재 요청 source의 변환 profile, 현재 health와 필요한 초기 계획/준비 추정값만 제공한다. 미래 source/eviction 일정·E2 평가 결과는 계약에 넣지 않는다. E2 H4는 요청 간 비용 상각으로 이름을 유지한다.
- E2 fixed/warm-DAG/ready-DAG-H4/ready-greedy-H4/credit-hysteresis와 Task-fastest의 결정을 동일한 읽기 전용 snapshot에서 비교한다. 이 단계는 초기 상태에서의 결정 호환성 검증이다. 장기 hysteresis 궤적·정책별 성능 비교는 아니다.
- 실제 KubeEdge ARM edge CPU/Intel NPU 서버에서 source2 × warm/서비스 객체 eviction2를 확인한다. 각 snapshot에서 여섯 정책의 결정을 기록하고 같은 계획은 대표 하나만 실행한다. 예상 고유 계획은 edge에서2개, server에서3개이므로 적용 검증10회다. 두 warm 준비용 workflow를 별도 기록한다. cold 조건은 적용 시도마다 전용 NPU 객체를 해제한다.
- 출력은 기존 dog Top-5 및 모든 stage hash/device를 확인한다. 이번 단일 입력의 일치로 E3 camera 품질 차이를 지우지 않는다. 고정 과거 비용표의 예측과 실제 지연을 같은 값으로 기대하지 않는다.
- 새 run 전용 리소스만 생성한다. 기존 pinned image/14 wheel·worker/client·cache·EdgeCore 읽기 전용 관측 경로를 재사용한다. 실제 NPU 여유·기존 workload 상태를 확인하고 종료 후 소유 리소스/임시 wheel을 정리한다.

## 검증

기존에 합의된 실제 HTTP 경계에서 red/green 검증한다. 로컬 CPU fixture의 alias는 E2 논리 alias를 사용하더라도 실제 backend는 CPU로 기록하며, 이를 NPU 실행 검증으로 세지 않는다. 실제 가속기 실행은 별도 클러스터 로그와 대조한다. 새로운 불확실성 추정·SLO/성능 우위 결론은 만들지 않는다.

## 완료

run20261016t100338960422에서 같은 snapshot의 여섯 정책 결정60건, 대표10+준비2 workflow와36receipt를 확인했다. 실제NPU8/ARM CPU4추론, 객체 재준비3회, 실패0이었다. 전체22개 HTTP 회귀를 통과했다. 소유 리소스 잔여0/wheel14삭제, 기존3workload Running/Ready와 EdgeCore1프로세스/RSS112800kB/15threads·eth0100Mbps/full을 확인했다. [실행 문서](../../docs/experiments/measured-policy-bridge.md)를 따른다. 원문 정책·장기 궤적·성능 우월성을 검증한 것은 아니다.
