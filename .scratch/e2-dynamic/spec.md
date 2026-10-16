# E2: 입력 위치와 모델 준비 상태 변화

Status: ready-for-human

## 질문과 범위

ARM 엣지 CPU와 Intel NPU 서버에서 같은 실제 3-Task 워크플로를 실행한다. 입력 발생 위치가 바뀔 때 고정 배치와 최신 동일 프로파일 기반 재최적화를 비교하고, 실제 NPU 재준비 비용을 반영한 기존 정확 열거와 단순 greedy/hysteresis 사이에 잔여 차이가 있는지 확인한다. E1의 공통 HTTP 실행기와 입력/hash/품질 계약을 재사용한다. GPU는 이번 부분 실험에서 제외한다.

물리 네트워크를 변경하지 않는다. 입력 client Pod를 실제 엣지/서버에서 번갈아 실행하므로 데이터 경로가 변한다. 이 변화는 대역폭 변동 실험이 아니다. 전용 NPU 워커의 Python infer request/compiled model 참조를 해제하고 다음 T2에서 실제 read/compile/request 생성·검증을 수행한다. 드라이버·OS 캐시는 제거하지 않는다. 모델 파일·패키지는 이미 준비되어 있으므로 전체 배포 cold start가 아니다.

## 수집 전 고정 설계

- 후보: arm/intel_npu, 모든 3 Task 이동 가능, 8개 배치 정확 열거.
- calibration: source 2개 × 배치 8개 × warmup 2 + measured 5. seed 20261018. 준비 비용은 NPU eviction→단일 실제 workflow 5회 별도 측정의 중앙값. calibration은 평가 분모와 분리한다.
- warm 시나리오와 eviction 시나리오를 별도 실행한다. 후자는 각 server phase 시작과 trajectory 시작에 NPU만 eviction한다. 모든 정책에 동일 일정 적용.
- source/요청 수: edge8 → server1 → edge4 → server12 → edge4 → server1 → edge4 → server12. 총 46개 요청. 정책은 현재 source와 현재 health만 알며 미래 phase 길이·순서를 읽지 않는다.
- 정책: initial-source 최적 배치 고정; 최신 warm 비용만 쓰는 DAG(준비 비용 제거 ablation); 현재 준비 상태와 준비 비용을 H=4 요청에 상각하는 DAG; 같은 정보를 쓰는 greedy H=4; 현재 warm 최선으로 전환할 누적 예상 절감이 cold 준비 비용을 넘으면 전환하는 hysteresis. ready인 후보 전환에는 추가 threshold를 두지 않는다. H=4는 미래를 알고 정한 horizon이 아니다.
- 각 block에서 2 scenario × 5 policy 순서를 seed 20261019+block으로 무작위 배정한다. 3 block, 동일 worker 공유. 독립 배포 반복이나 검정력을 갖춘 우월성 실험으로 해석하지 않는다.
- trajectory 시작 ARM warm/NPU는 시나리오에 맞게 warm 또는 cold로 초기화. warm 시나리오 각 phase 시작 NPU warm을 확인한다. eviction control 비용은 환경 변화 비용으로 별도 기록하며 primary 요청 시간에서는 제외한다. 준비 비용은 cold T2 요청 내부에 포함한다.
- 요청마다 NPU health 관측·정책 결정·실제 workflow 종단 지연을 합한 total_ms가 주 지표다. 평균/합·최대·중앙값, 준비 횟수/시간, 실제 plan 변경 수, 오류·시도 전체 분모를 보고한다. pacing 0.25초, 초과를 숨기지 않는다. phase 사이 exec/프로세스 시작·관측 외 calibration·SDK 설치·worker 시작 비용은 제외했다고 명시한다.
- 응답 hash chain/top5, CPU/NPU 실행 장치, 각 요청 3 stage receipt, 실제 cold 준비 trace와 health state를 검증한다. 실패하면 원시 시도 로그를 보존하고 완주 결과와 분리한다.

## 구현·검증 경계

기존 승인된 실제 HTTP workflow 인터페이스에서 health→evict→cold 첫 요청→warm 후속 요청을 테스트한다. mock backend로 실제 NPU 준비 시간을 대체하지 않는다. CPU HTTP 통합 테스트를 먼저 red/green으로 확인하고 NPU 장치는 클러스터 실행으로 검증한다. 과거 실행의 ConfigMap/source manifest는 동결 상태로 보존한다.

## 완료 조건

고정 설계와 소스 manifest, 실제 측정·검증·한계 Markdown, 문서 진입점 갱신, 전용 리소스·임시 패키지 정리, Semantica 결과 기록. kube-scheduler/Koordinator/논문 원 구현/DRL 비교 완료로 부르지 않는다.

## 결과

run20261016t082220에서 평가1,380회/전체1,514회와4,542 stage receipt 검증. 동일 배치 궤적의 정책별 실행은 사전 지정 반복으로만 보고하고 서로 다른 알고리즘 효과나 독립 배포 표본으로 세지 않았다. 결과와 미검증 범위는 [실행 문서](../../docs/experiments/e2-dynamic.md)를 따른다.
