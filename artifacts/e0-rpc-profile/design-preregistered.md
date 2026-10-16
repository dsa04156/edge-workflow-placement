# E0: 엣지–가속기 전달 프로파일과 정적 위치 선택

설계 고정일: 2026-10-16. 상태: 설계·manifest 동결, worker 준비 중. 앞선 [가속기 실행 검증](e0-accelerators.md)의 모델과 입력을 유지한다. 아래 조건은 요청 측정 전에 정했다.

## 질문과 실행 경계

엣지의 T1 전처리 → 선택한 worker의 T2 분류 → 같은 엣지의 T3 결과 검사·후처리를 실행한다. 후보 T2는 같은 ARM 엣지 노드의 별도 CPU Pod, x86 서버 CPU, 서버 GB10 GPU, 서버 Intel NPU다. GPU·NPU는 EdgeCore 엣지 가속기가 아닌 서버 장치다. 입력 602,112byte, 출력 4,000byte, 원본 모델·전처리 hash·top-5 계약을 유지한다.

host 입력→host 출력의 backend 시간과 전체 HTTP/RPC·워크플로 시간을 구분한다. GPU는 동기화한 H2D/D2H host wall 구간과 CUDA compute event도 기록한다. 복사 구간에는 할당·framework 처리 등이 포함되며 순수 DMA 시간이 아니다. OpenVINO NPU는 plugin 내부 복사·연산을 개별 분해하지 않고 host 경계의 통합 비용으로 기록한다. CPU는 ORT1.23.2 한 thread다.

## 사전 고정한 배정

- 네 worker에 HTTP/1.1과 같은 헤더/본문 쓰기 순서를 적용한다. 새 TCP/같은 TCP 재사용 × 양끝 TCP_NODELAY=0/1의 2×2를 모두 실행한다. 클라이언트 기본 NODELAY에 의존하지 않고 명시 설정·조회한다.
- 각 client 실행 블록에서 16개 조건의 순서를 `experimental-design`의 `assign_factorial_runs`로 무작위 배정한다. seed는 20261016+block이며 [배정표](../../artifacts/e0-rpc-profile/schedule.json)를 동결했다.
- 블록 3개를 별도 client Job으로 **순차 실행**한다. 조건마다 warmup2·측정10: 총 576요청, 측정480요청이다. 네 worker는 계속 상주하며 다시 배포하지 않는다. 요청을 독립 반복으로 세거나 세 블록을 독립적인 배포 재현으로 부르지 않는다.
- 동시 요청은 1개, 각 처리 사이클은 최소 0.2초로 pacing한다. 목표 상한은 5요청/초이며 처리시간이 넘으면 기다림을 생략하고 overrun을 기록한다. open-loop 고정 도착률·처리량/포화도 실험은 아니다. 세 블록과 요청 수는 탐색용 실행 예산이며 검정력/MDE에 근거한 본 실험 표본 수가 아니다.
- 신규군은 매 요청 연결을 만들고 응답 완독 후 닫는다. 재사용군은 각 조건의 warmup부터 같은 연결을 유지한다. 서버 connection ID·클라이언트 local/peer 주소·port로 검증한다. 접속과 RPC 시간을 나누고 전체 workflow에는 모두 포함한다.
- 정상 요청은 입력·출력 길이/hash·원본 모델·실행 node/backend·request ID·top-5를 검증한다. GPU 출력 CUDA와 양의 timing event, NPU 실행 device를 매 요청 receipt로 남긴다. 오류를 자동 재시도로 숨기거나 통계에서 제외하지 않고 블록의 미완료를 표시한다.
- worker 준비와 pip 설치·download·compile·warmup은 요청 측정 밖이다. 양끝 NODELAY를 함께 바꾸므로 방향별 원인 분리는 못 한다. 패킷·ACK 추적이 없어 지연 원인을 특정 TCP 현상으로 확정하지 않는다. [공식 HTTP 계약 조사](../research/kubeedge-e0-http-notes.md)를 참고한다.

## 이어서 실행할 정적 선택 검증

주 전달 조건은 **TCP 재사용·NODELAY=1**로 사전 선택한다. 나머지 조건은 프로토콜 민감도 분석이다. 블록0·1의 주 조건만으로 후보별 중앙값을 만들고 다음 위치를 고른다.

1. B2 fastest-feasible: server의 host 입력→host 출력 backend 중앙값 최소.
2. C1 데이터 인지 greedy: 접속을 포함한 RPC 중앙값 최소. T1/T3 위치와 처리 계약은 고정이다.

동률은 alias 사전순으로 결정한다. 블록2를 실행하기 **전에** 선택 결과와 근거를 파일로 고정하고, 새 요청인 블록2의 해당 위치 성능을 비교한다. 블록2의 가장 빠른 결과를 사후에 정책으로 선택하지 않는다. 실패·정확성 불일치는 유효 완료로 세지 않는다. 추론 통계 검정·신뢰구간·p95 우위를 주장하지 않고 블록별 중앙값과 요청 범위를 기술한다.

이 DAG는 T1/T3가 고정이고 T2만 이동하므로 가능한 배치가 네 개다. 같은 목적과 정보를 쓰는 DAG 열거 최적화는 C1과 동일한 선택 문제가 된다. 이를 JellyBean·Atlas 재현이나 DAG 공동 결정의 일반적 효과로 부르지 않는다. B2와 C1의 선택이 같으면 그 사실을 그대로 보고한다. 이후 공동 배치 효과를 검증하려면 복수 Task의 위치가 함께 바뀌는 실제 워크플로가 필요하다.

## 실행 근거

- [run·고정 조건](../../artifacts/e0-rpc-profile/run.json), [동결 source·worker·Service](../../artifacts/e0-rpc-profile/workers.json)
- [준비 도구](../../experiments/e0/prepare_rpc_profile.py), [HTTP 실행 코드](../../experiments/e0/rpc_profile.py), [HTTP 통합 검사](../../experiments/e0/test_rpc_profile.py)

기존 workload·EdgeCore/CloudCore·driver·클러스터 네트워크 설정은 변경하지 않는다. 이 run의 정확한 이름으로 만든 리소스만 증거 수집 뒤 삭제한다. kube-scheduler·Koordinator·기존 DAG 구현·DRL의 필수 비교는 이번 단계에서 완료됐다고 주장하지 않는다.
