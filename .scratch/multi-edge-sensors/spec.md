# 다중 엣지 가상 센서·RTSP·일반 모델 배치 실험

Status: ready-for-agent
Date: 2026-10-22

## 목표

사용자 요청: 노드를 더 많이 사용하고 여러 엣지에서 센서 입력이 발생하는 조건으로 배치 효과를 실측한다. 해당 엣지에서 가상 데이터를 생성하며 RTSP 영상과 텍스트를 포함하고 YOLO 등 널리 쓰이는 실제 모델을 사용한다.

## 범위와 소유권

- 새 구현: `experiments/multi-edge-sensors/`.
- 새 실험 문서: `docs/experiments/multi-edge-sensors-20261022.md`.
- 개발 기록: `.scratch/multi-edge-sensors/` (spec.md는 coordinator 소유).
- 새 원자료: `artifacts/multi-edge-sensors/<unique-run>/`; 대용량 캐시는 `/mnt/data3tb/`의 전용 새 디렉터리.
- 기존 `experiments/real-topology-live/`의 공개 helper·배치 원칙을 재사용할 수 있으나 원본을 수정하지 않는다. 기존 artifact·다른 작업·복구용 Pod를 보존한다.
- 구현·테스트·디버깅은 Claude Opus 5.5, 구독 로그인 전용. 독립 검토는 Astra. 실제 모델을 기록한다.

## 설계 결정

1. 기본 후보는 현재 Ready9 topology의 9개 노드다. NotReady dev0001/dev0004/dev0007은 제외한다. 실제 자원·이미지·모델 호환성을 검사하고 탈락 이유를 기록한다. 9개 등록과 9개 모델 성공을 구분한다.
2. 입력은 5개 연결 엣지(dev0002/dev0003/dev0006/dev0008/dev0009)에서 생성한다. 최소 3개의 서로 다른 영상 source와 2개의 텍스트 source를 사용한다. 공개 영상/이미지를 seed 기반 변환·재생하는 가상 카메라와 결정적 이벤트 문장 생성기를 둔다. 실제 센서 수집으로 부르지 않는다. 동일 조건/정책에서 같은 sequence와 내용을 사용한다.
3. RTSP는 실제 MediaMTX/FFmpeg 등 공식 구현으로 publish/read하고 CPU에서 decode한다. HTTP JPEG를 RTSP라고 표시하지 않는다. 각 엣지에서 publisher가 실행됨을 Pod UID/node/process/stream evidence로 확인한다. RTSP 경로·해상도·fps·codec·sampling/drop 정책을 고정한다. 영상은 객체가 존재하는 fixture를 사용한다. 테스트 패턴만으로 탐지 품질을 주장하지 않는다.
4. 첫 모델은 YOLO11n과 all-MiniLM-L6-v2, 영어 synthetic 이벤트로 확정한다. 연구원의 공식 근거 문서(`docs/research/multi-edge-rtsp-text-sources-20261022.md`)를 사용한다. 동일 FP32 ONNX 산출물과 CPUExecutionProvider를 모든 노드에 사용하며 원본 가중치·변환 설정·변환 산출물을 hash로 연결한다. 실제 학습된 가중치로 inference한다. rule-only를 text AI라고 표시하지 않는다. fusion은 탐지와 문장 유사도를 결합하는 명시적 결정적 연산으로 두며 학습 모델로 부르지 않는다.
5. DAG는 영상 추론 V와 텍스트 추론 T가 병렬인 뒤 fusion F로 합류한다. source 위치와 V/T/F 실행 위치를 분리한다. 실행할 때 생성된 입력의 sequence/hash를 보존하고 모든 정책이 같은 입력과 모델·품질 기준을 공유한다. RTSP 연속 입력의 프레임 식별과 정책 간 공정 비교가 불가능하면 별도 RTSP 프로토콜 검증과 동결된 decoded frame 배치 비교를 명시적으로 분리하며 둘을 RTSP E2E 성능으로 합치지 않는다.
6. 정책은 실제 default-scheduler 위치, Task-fastest, 단순 네트워크-aware greedy, 작은 DAG 정확 열거를 우선 비교한다. source-local/server-fixed는 진단으로 추가 가능하다. 실제 scheduler를 구현하기 어려우면 다른 이름의 고정 기준선으로 명시하며 fake scheduler로 대체하지 않는다. exact는 기존 비용모형의 기준해이며 새 알고리즘이 아니다. 여러 source workflow에서 계획 차이를 보고하고 같은 계획은 관측을 공유한다.
7. 보정→실행 가능 후보·계획·입력 동결→별도 평가. 첫 bounded pilot은 보정 입력 3개, 평가 3개, source 조합 최소 3개, 고유 배치당 warmup1+평가3, 전체 평가 workflow 상한 72. 처음은 native LAN 고정 조건이며 WAN 변화는 별도 확장(최초 요청에 없는 호스트 qdisc 변경 금지). 단일 노드가 아닌 9개 후보 qualification과 다중 엣지 입력을 실제 확인한다. CPU부터 동일 runtime으로 검증하며 GPU/NPU 실행을 추정하지 않는다.
8. 비용은 실제 측정 compute/transfer/probe/prepare/selection/end-to-end와 bytes를 구분한다. max 병렬 경로를 단순 합산하지 않는다. RTSP setup/buffering/decode/frame age와 model compute를 구분한다. source/worker clock 차이는 monotonic duration과 sequence로 처리하고 검증 없는 cross-host timestamp subtraction을 지연이라고 부르지 않는다.
9. 품질: YOLO classes/boxes/scores와 text embedding cosine/최종 결과를 CPU reference와 정한 허용치로 비교한다. hash 동일과 수치 품질을 구분한다. 사후 실패 입력 제외 금지. 성능 변화·무효과·실패·timeout 모두 보존한다.

## 검증 경계·수용 기준

기존 수집기/감사기의 공개 CLI→artifact 경계를 재사용한다. 테스트는 내부 함수 복제가 아니라 실제 공개 명령의 입력·계획·source provenance·감사 실패/성공을 관측한다. TDD red→green vertical slice; 구현자가 현재 존재하는 정확한 명령을 README에 기록한다.

- 준비 CLI는 모델/입력/실행 파일을 hash와 원출처로 고정하고 재실행 시 과거 파일을 덮어쓰지 않는다.
- dry-run은 다중 엣지 source mapping, 9개 후보, 자원 제한과 실행량을 출력하며 클러스터를 변경하지 않는다.
- 공개 collector/validator에 대해 source가 서버로 치환된 경우, model/input mismatch, 실패 표본 누락, 같은 계획 중복 표본 계산을 거부하는 검증. fake 수치는 offline임을 명시.
- 로컬 실제 RTSP publisher→decoder, 실제 YOLO와 text 모델 smoke를 실행하고 명령과 결과를 보존한다.
- 실장비: source publisher 3개 이상+text generator 2개 이상, 9개 후보 중 실제 qualification 결과, 적어도 3 source 조합의 정책 비교. 실패하면 원인을 남기고 성공으로 부르지 않는다.
- Pod/ConfigMap/Service 등 새 실험 소유 자원만 이름/UID/run label로 정리하며 잔여0 확인. 보호 workload 변화는 인과 단정 없이 기록.
- phase 시작/끝, timeout 부분 stdout/stderr, failed stage, 모든 실패/cleanup evidence 보존.

## 실행 단계 및 금지

첫 Developer dispatch는 구현·로컬 검증까지만. 클러스터 변경은 independent review 후 coordinator 지시로 진행한다. 호스트·DaemonSet·기존 registry·EdgeCore 설정 변경, 재시작, hostNetwork/privileged/hostPath, 전역 패키지 설치, 기존 Pod 삭제, push/merge/cloud relay 금지. 새 실험용 제한된 Pod 배포는 사용자 요청 범위이나 기존 복구 작업과 동시 실행하지 않는다. 자격 증명 저장 금지. shell은 rtk/rtk proxy 사용.

전체 프로파일링이 시간이 많이 들거나 실제 제어 실패가 반복되면 새 알고리즘으로 숨기지 말고 구체적 blocker를 보고한다. 요청한 다중 입력 실측을 단순 계획 문서·synthetic 비용표만으로 완료 처리하지 않는다.
