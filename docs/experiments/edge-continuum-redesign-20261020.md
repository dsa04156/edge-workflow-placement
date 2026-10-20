# 이기종 다중 엣지 환경의 DAG 배치 실험 재설계

작성: 2026-10-20 22시대 KST. 상태: 설계 완료 / 구현·장비 재점검·새 실험 미실시.
이 문서가 현재 실험 기준이다. 기존 owned-hardware-scenarios-20261020.md의 Pi 2대 우선 본실험과 서버 슬롯 예산 중심 후속 연구를 대체한다. 과거 결과와 실패 기록은 보존한다.

## 1. 문제와 검증할 주장

연구 주제: **센서 위치·네트워크 토폴로지·노드별 작업 성능 프로파일을 고려한 이기종 엣지 워크플로우 배치**.
영문: **Topology- and Profile-Aware DAG Placement in Heterogeneous Edge Computing Environments**.

엣지에서 발생한 데이터를 처리할 때 가까운 노드의 낮은 전송비, 다른 엣지 가속기의 처리 성능, 원격 서버의 처리 성능과 WAN 전송비를 함께 비교한다. DAG 전체의 최종 응답 지연을 줄이는 것이 목적이다. 엣지에서 네트워크 비용이 큰 상황을 주 관심 환경으로 삼되, 실제 payload와 링크 측정으로 통신 비중을 확인하고 빠른 네트워크에서 이득이 없는 경우도 보고한다.

핵심 질문:
- RQ1: 실제 가용 자원 내에서 센서 위치·토폴로지·작업×노드 성능 정보를 사용하면 기본 Kubernetes 배치와 서버 집중보다 유리한가?
- RQ2: 센서 위치·링크 경로·전처리 전후 데이터량·이기종 실행 성능 각각이 배치에 어떤 영향을 주는가?
- RQ3: 동일 정보를 사용하는 greedy, HEFT 서비스 변형, greedy-swap-move와 비교했을 때 전체 DAG 선택의 추가 효과와 결정 비용은 얼마인가?

기여를 처음부터 “정확 열거가 단순 보완을 이김”으로 고정하지 않는다. 시스템의 효과와 새로운 알고리즘의 기여를 별도로 판단한다. 단순 보완과 동률이어도 그 사실을 그대로 보고한다. 이 문서는 새 알고리즘의 신규성을 확정하지 않는다.

## 2. 기존 설계의 수정

| 이전 선택 | 문제 | 새 기준 |
|---|---|---|
| Pi 2대+CPU 서버를 중심으로 확장 연기 | 엣지 GPU/NPU와 다른 서버가 본 문제에서 빠짐 | 전체 보유 인벤토리를 먼저 점검하고 통과한 모든 작업×노드 후보를 사용 |
| 서버 작업 슬롯을 임의로2개로 제한 | 실제 자원 부족·공존 간섭과 다른 admission 정책 | 주 실험에서 제거. 실제 allocatable/요청량/가속기 메모리·실행 제약으로 판정 |
| 하나의 작은 VQA fork/join | DAG 형태·중간 산출물 변화의 범위가 좁음 | 기존 VQA 회귀 + 5작업 영상 DAG + 두 센서의 다중 분기·합류 DAG |
| 엣지를 같은 영역으로 묶고 WAN만 변경 | 엣지 간 경로와 센서 위치 효과가 약함 | 사이트 A/B, 사이트 간 링크, 공유 원격 uplink를 분리 |
| greedy를 이기는 조건 중심 해석 | 시스템 효용과 알고리즘 신규성을 혼동 | 운영 기준선 효과와 동일 정보 비교를 별도 보고 |
| 6요청·완주 못한 한 block | 꼬리 지연·반복성 검증 부족 | 미사용 평가 입력·사전 동결 길이·시간 block·중단 전부 보고 |

기존 슬롯2 실험은 “임의 admission 제약에 대한 기전 진단”으로만 인용한다. 서버 과부하 또는 엣지 환경 전체의 대표 결과로 인용하지 않는다. 슬롯 제한을 없앤다고 자원 제약을 없애는 것은 아니다.

## 3. 장비: 전체 인벤토리에서 시작

다음은 [2026-10-16 기록](e0-kubeedge.md)의 10노드이며 현재 Ready 상태가 아니다. 새 실행 전에 read-only snapshot과 장치 매핑을 갱신한다.

| 노드 | 과거 분류/등록 | 본 실험 역할 후보 |
|---|---|---|
| etri-dev0001-jetorn | ARM edge, NVIDIA shared 자원 | 엣지 GPU 추론; 정확한 Jetson 모델/메모리 재확인 |
| etri-dev0002-raspi5 | Pi5, Hailo H8 | 센서·전처리·CPU 및 검증된 Hailo 작업 |
| etri-dev0003-raspi5 | Pi5 CPU | 센서·전처리·CPU 작업 |
| etri-dev0004-tedger | ARM edge, RK3399Pro | 복구·품질 gate 후 CPU/NPU 후보 |
| etri-dev0005-jetagx | ARM edge, NVIDIA shared 자원 | 엣지 GPU 추론; 모델/장치 매핑 재확인 |
| etri-dev0006-raspi5 | Pi5, Hailo H8 | 추가 센서·전처리·CPU 및 검증된 Hailo 작업 |
| etri-ser0001-cg0msb | x86 server, NVIDIA GPU | 원격 CPU/GPU 후보 |
| etri-ser0002-cgnmsb | x86 server, Intel NPU/NVIDIA GPU | 원격 CPU 및 검증된 가속기 후보 |
| etri-ser0003-cg0ms0 | ARM server, NVIDIA GPU | 원격 후보; 과거 GB10 fixture 기록 대조 |
| etri-ser0004-cgnms0 | x86 server, Mobilint NPU | 원격 CPU 및 검증된 NPU 후보 |

추가 사용자 관측: etri-dev0007-jetagx에 Ubuntu/SSH 세션이 있었다. 기존 dev0005와 동일 장치의 이름 변경인지 추가 장치인지 UUID/serial로 확인하며 중복 계수하지 않는다. Orin Nano·AGX·RTX5080 이름을 기존 노드에 추정 매핑하지 않는다. Tinker Edge R의 부팅 문제 해결 여부도 재확인한다.

점검 출력 필드: node/physical_device_id, arch, CPU/RAM, allocatable, 기존 requests/limits, 사용률, device model/UUID, 메모리/공유 설정, runtime/image/driver, Ready, task별 qualification 상태·제외 이유, source 가능 여부, 실제 링크 계측. nvidia.com/gpu.shared=2는 GPU 두 장을 뜻하지 않는다.

**등록 자원·보유 장비·실행 성공은 구분한다.** 모델이 미지원인 NPU를 억지로 포함하거나 다른 모델의 성공을 같은 작업 지원으로 인정하지 않는다. CPU 실행이 통과하면 CPU 후보로만 포함할 수 있다.

모든 검증된 후보를 주 비교에 제공한다. 불가용 장비는 평가 전 사유를 기록하고, 결과가 나쁘다는 이유로 제외하지 않는다. Pi 2대+서버만 준비된 경우 그 실행은 회귀 smoke이며 이기종 엣지 본 실험 완료로 표시하지 않는다. 본 실험 시작 관문은 두 source 위치, 두 개 이상 엣지 처리 노드, 적어도 하나의 검증된 엣지 가속기와 원격 처리 후보, 두 가지 이상 작업별 실행 성능 유형의 확보다. 본 관문은 장비 수를 고정하는 제한이 아니며 통과한 나머지 장비도 후보에 유지한다.

## 4. 배치 대상과 토폴로지

물리 연결을 먼저 측정한다. 실험실이 하나의 스위치라면 분산 사이트가 실재한다고 쓰지 않고 실험 전용 경로로 구성한 논리 사이트 에뮬레이션이라고 명시한다.

- Site A: 이미지/영상 source가 있는 Pi와 일부 엣지 처리 노드.
- Site B: 두 번째 source가 있는 Pi와 다른 엣지 처리 노드.
- Remote: 검증된 서버 집합 전체. 한 서버만 항상 정답으로 두지 않는다.
- Edge backbone: A↔B 링크. Remote uplink와 별도로 제한/계측한다.
- Shared uplink: A/B의 원격 전송이 실제로 합류하는 경계. 각 flow별 제한으로 대체하지 않는다.
- 사이트 안과 사이, 원격 경로의 라우팅/방향별 bandwidth/delay/queue를 manifest로 고정한다. 돌아오는 최종 결과와 원본을 재사용하는 DAG edge도 포함한다.

기본 A/B 할당은 장비 연결·배치 가능성을 조사한 뒤 성능 결과를 보기 전에 동결한다. 동일 노드 집합에서 엣지 가속기의 A/B 위치를 맞바꾸는 교차 조건도 둔다. 논리 배치 이동은 물리 장비를 옮긴 것으로 표현하지 않는다.

Source 위치는 조건 내 고정, 연산 위치만 정책이 선택한다. source-local 압축 해제를 항상 강제해 네트워크 비용을 키우지 않는다. 입력 표현과 decode의 DAG 경계를 명시한다.

## 5. 워크플로우

| ID | 실제 연산 구성 | 목적/상태 |
|---|---|---|
| W0 | ResNet18 영상 특징 + Wav2Vec2 음성 → MUTAN 합류,3작업 | 기존 구현 회귀/문헌 연속성; 단독 주 결과로 삼지 않음 |
| W1 | decode/resize → detection → ROI 생성 → crop 분류 → 결과 결합,5작업 | 전처리 위치와 원본/ROI 이동비를 보는 주 워크플로; 신규 구현 필요 |
| W2 | 서로 다른 두 Pi의 W1 가지(각5작업) → 양쪽 결과 집계,11작업 | 다중 source와 동시 분기·합류; 신규 구현 필요 |

W1 의존성은 decode→detection, decode→ROI, detection→ROI, ROI→분류, detection→결합, 분류→결합이다. ROI 생성에는 프레임과 box가 둘 다 필요하며 프레임 이동/보관/해제를 누락하지 않는다. 하나의 ROI task가 해당 입력의 모든 crop을 처리하며 객체 수에 따른 데이터/처리량을 저장한다.

W2 집계는 카메라별 탐지·분류 목록의 공통 시간 창 결합으로 정의한다. 동일 개체의 카메라 간 식별/중복 제거를 수행했다고 주장하지 않는다. 실제 pipeline 의미를 사전에 정하고 독립 반복 가지를 의미 없이 늘려 큰 DAG로 포장하지 않는다.

W1/W2의 detection/classifier 모델, 공개 데이터셋, 영상 sampling, 해상도·객체 수 구간, 정밀도, compiler artifact와 출력 품질 규칙은 qualification pilot에서 고정한다. 현재 미선정 항목을 구현 완료로 표시하지 않는다. 동일 condition에서 정책마다 모델을 다르게 쓰지 않는다. NPU 양자화로 품질 계약이 달라지면 별도 model variant 실험이며 주 배치 효과와 합치지 않는다.

압축 원본 전달을 주 입력으로 사용한다. decoded 입력 대조는 실제 응용 경계를 설명할 수 있을 때 별도 수행한다. 모든 입력은 실제 데이터이며 tensor padding/가짜 sleep으로 효과를 만들지 않는다. calibration과 최종평가의 파일·scene/time segment를 분리하고 기존6입력은 최종 holdout으로 재사용하지 않는다.

## 6. 자원과 프로파일

가용 자원은 Kubernetes allocatable에서 기존 예약과 명시적 실험 외 예약을 반영한다. CPU requests 잔량과 실제 CPU 이용률은 다른 항목이다. task별 CPU/RAM/GPU/NPU 요구량, 장치 메모리, 공유 실행 정책과 공존 가능 조합으로 feasible 판정을 한다. 실제 관측 근거 없는 “서버2슬롯”·모든 작업1슬롯 같은 제한은 주 실험에서 사용하지 않는다.

노드가 달라도 같은 task의 자원 설정·backend 옵션은 사전 고정하며 각 정책이 동일한 task×node 구성 후보를 받는다. GPU/NPU 지원은 실제 resource 할당·실행 장치·출력 검증으로 확인한다. 비선택 모델을 상주시켜 용량을 소비하지 않는다. 배포 직전 자원 snapshot이 동결 조건과 달라지면 run을 중단/별도 기록하고 해당 정책에만 유리하게 후보를 바꾸지 않는다.

사전 저장:
- task×node×backend×입력 크기: 처리시간 분포, 메모리 peak, 출력 bytes, 준비 비용.
- 공존 조합과 동시 요청 수: 처리시간 증가, queue, 메모리/GPU 공유 상태.
- 노드 쌍/실제 경로/표현/크기: 전송시간 분포, 직렬화·host/device 복사, 동시 전송 시 공유 링크 영향.
- Jetson 전력 모드/온도/클럭·스로틀링, 드라이버·런타임·가중치 hash.
- profile 획득 소요시간과 추가 트래픽.

스케줄러가 간섭이나 공유 링크 경쟁을 실제 비용 계산에 반영하는지 버전별로 표시한다. 측정 항목이 존재한다는 것과 정책 입력으로 사용한다는 것을 구분한다. 현재 p75 고립 실행+echo critical-path 모델은 queue·경합을 고려하지 않는 기존 proxy이며, 새 실험의 관측 종단 최적해로 취급하지 않는다.

초기 주 목표는 고정 도착률에서의 평균 warm E2E 감소, 성공률 비열화다. 계획기의 예측 비용과 실제 평균/p95를 별도로 비교한다. 독립 task 평균으로 만든 critical path도 실제 fork/join 평균과 같지 않을 수 있으므로 오차를 검증한다. 고부하 모델은 calibration만으로 보정하고 평가 trace의 미래 도착/측정값을 선택에 제공하지 않는다.

## 7. 구체적 조건표

네트워크는 native 대조와 다음 동일 relay 경로 조건을 사용한다. 모든 수치는 목표 cap/추가 단방향 delay이며 달성값을 측정한다. 100Mbps cap이 실효100Mbps를 보장하지 않는다.

| ID | A↔B cap / 추가 지연 | 공유 Remote cap / 추가 지연 | 질문 |
|---|---|---|---|
| N0 | 물리 native, relay 없음 | 물리 native | 기준·경로 검증 |
| N1 | 제한 없는 relay /0ms | 제한 없는 relay /0ms | relay 오버헤드 |
| N2 | 100Mbps /2ms | 100Mbps /20ms | 빠른 원격 경로 대조 |
| N3 | 100Mbps /2ms | 10Mbps /20ms | 원격 전송과 엣지 처리의 교환관계 |
| N4 | 100Mbps /2ms | 2Mbps /20ms | 큰 WAN 비용 |
| N5 | 10Mbps /2ms | 10Mbps /20ms | 엣지 간 전송의 추가 비용 |
| N6 | 100Mbps /2ms | 10Mbps /0ms | N3와 지연만 비교 |
| N7 | 100Mbps /2ms | 10Mbps /50ms | N3와 지연 민감도 |

장비 연결상 목표보다 native가 느리면 실제값을 보존하고 불가능한 환경을 달성했다고 쓰지 않는다. 임의로 네트워크를 불리하게 만든 결과만 강조하지 않는다. 두 방향 cap은 별도이고 uplink 총합 검증·우회 차단·원상 복구 기록을 남긴다.

실험군:
- E1 본 비교: W0/W1/W2 × N0~N7, source 분산(A/B), 동일 낮은 open-loop 요청률. 해당 workflow를 실제 지원하는 전체 후보 사용.
- E2 source 효과: W0/W2에서 N3 고정, source 같은 site/다른 site를 대조. 파일·도착률·계산 후보는 동일.
- E3 topology×성능: W1/W2에서 N3/N5, 엣지 가속기의 논리 A/B 소속 교차. 원래 할당과 교차할당을 모두 보고.
- E4 입력 크기: W1/W2, N3에서 실제 고정 해상도 구간과 객체 수 구간. 장면 선택/stratification은 calibration으로 사전 정의.
- E5 부하: W1/W2, N3에서 공통 낮음/중간/높음 open-loop 도착률. 조용한 노드 조건과 지정된 실험 소유 background workload 조건을 분리.

E5의 background workload는 승인된 가용 범위에서 동일 연산·입력·시간표를 모든 정책에 재생한다. 실제 CPU/GPU 사용과 queue를 측정하며 정책별로 부하량을 달리하지 않는다. 기존 사용자 서비스에 부하를 추가하거나 종료하지 않는다. 여유가 없으면 E5-background만 미실시로 기록한다. 슬롯 제한으로 대체하지 않는다.

본 비교에서 지역 집중과 서버 집중을 모두 허용한다. site별 edge-only 후보 제약은 고정 운영 기준선이며 ours 후보 제한이 아니다. “분산했으니 좋다”는 결론을 전제하지 않는다.

## 8. 비교군과 알고리즘 범위

운영 기준선: 실제 kube-default, remote-only(가용 서버들 중 공통 프로파일 선택), edge-local(입력 site의 실행 가능한 노드 중심).
동일 정보 기준선: greedy, HEFT-serving, greedy-swap-move.
제안 정책: DAG+토폴로지+노드 profile 공동 비용에 따른 선택.
제거 실험: no-network, no-node-profile; 추가 source 정보 제거는 출발 노드를 바꾸지 않고 선택기 정보만 제거한 진단으로 명시한다.

remote-only를 CPU 서버1대에 고정해 약하게 만들지 않는다. 동일 후보별 resource 설정과 품질 제약을 적용하며 server-only infeasible은 억지 배치하지 않는다. 기본 Kubernetes에는 같은 호환성 필터를 주고 설정/version/weights를 보존한다. 기본 스케줄러가 CPU만 본다고 설명하지 않는다.

HEFT-serving과 원 HEFT의 실행 의미 차이를 기록한다. 현재 greedy의 완성 가능성 검사에 전체 배치 열거가 들어가 있으므로 이를 scalable baseline으로 보고하지 않는다. 큰 DAG의 비교 전 공통 resource feasibility 검사와 탐색 budget을 명시한 구현이 필요하다.

정확 열거는 작은 문제의 offline 참고값이다. W2의 11작업×여러 노드에서 무조건 전수 탐색하지 않는다. 현실적인 탐색기(예: beam/local search)의 규칙·budget·동률 처리를 calibration 단계에서 고정하고, 작은 DAG에서 정확 해 대비 오차/시간을 확인한다. 특정 새 탐색기를 이미 구현했다고 주장하지 않는다. 주요 문헌 비교를 직접 재현했는지 변형인지 구분한다.

단순 보완이 제안 방식과 동률이면 그대로 보고하고 추가 복잡성의 이점이 없다는 결과를 수용한다. 그 경우 시스템 통합/프로파일 비용/예측 신뢰성 등 실제 증거가 있는 기여만 검토한다.

## 9. 실행/분석 계약과 성공 기준

각 condition의 모든 정책은 같은 source·파일·도착 trace·실행기·전송 프로토콜·CPU/GPU 제한·실험 기간·deadline을 사용한다. 같은 배치를 택하면 동일 block의 관측을 공유하고 결정 비용은 개별 기록한다. block 간 관측은 재사용하지 않는다.

준비 단계에서 모델/runtime warmup 후 프로파일을 동결한다. 평가용 미래 입력/평가 실행시간으로 정책을 다시 튜닝하지 않는다. 성능이 나쁜 condition을 삭제하지 않는다. E1~E5 결과를 한 개 평균 개선율로 합쳐 이득을 부풀리지 않는다.

초기 계획은 시간 block 5회, 가능하면 2일 이상으로 분산하며 block 내 policy/고유 배치 순서를 무작위화한다. 최종 block 수·요청률·측정 길이·최소 의미 효과는 독립 pilot 변동성/수집비용으로 최종평가 전에 동결한다. 5회가 통계적으로 충분하다고 선언하지 않는다. 낮은 요청률에서도 p95에 필요한 샘플 수를 확보하도록 길이를 정하고 부족하면 불안정하다고 표시한다.

보고: block별 평균/p95, 계획 요청 대비 성공·timeout·reject, 처리량, 실제 placement, 예측 오차, 경로별 bytes·전송 대기, 노드별 처리·대기, 결정/프로파일/배포·준비 비용, 품질, runtime/resource provenance. block paired 차이와 불확실성을 보고하며 요청 수를 독립 반복 수로 쓰지 않는다. 평균 지연 감소와 실패 증가를 숨기지 않는다.

종단은 source coordinator의 동일 monotonic clock에서 시작~최종 반환으로 측정한다. 예정 release 지연도 포함한 지표를 따로 남긴다. 서로 다른 시계의 timestamp 차이와 중첩 RPC 합으로 순수 통신시간을 만들지 않는다.

작은 DAG의 전수 배치 평가 최선은 hindsight이며 사전 선택기에 주지 않는다. 실행 도중 전환은 이번 주 실험이 아니다. warm E2E와 프로파일/배포를 포함한 전체 비용을 별도로 보고한다.

## 10. 구현 인계와 현재 막힌 사항

우선순위:
1. 새 전체 inventory + 물리 장치 중복 확인 + 보호 workload 상태 + topology 수집.
2. E1 Pi exec EOF 원인/대체 제어 경로를 별도 검증. 제어 실패는 scheduler 실패와 분리하고 경로 상태를 확인하지 못하면 새 수집을 시작하지 않는다.
3. task별 엣지 GPU/NPU 실행·품질·resource gate, W1/W2 실행기와 payload 계약 구현.
4. 노드명을3개로 고정한 collector/selector/validator를 inventory 입력으로 일반화. 현재 baseline의 offline execution_ready=false를 무시하지 않는다.
5. 실제 자원·profile·전체 후보를 policy에 연결; 작은 DAG에서 원인별 비용과 적용 배치를 검산.
6. pilot 후 condition/model/profile/seed/run-order/측정 길이·통계 분석을 동결.
7. E1~E5 수집과 보고. 이 순서의 완료 전 “엣지 본 실험 완료”를 표시하지 않는다.

이 변경은 연구 설계/에이전트 지침/문서 우선순위 수정이다. 코드 구현·원격 장비 접속·새 profile 획득·성능 실험은 수행하지 않았다. 구현 역할/인증 규칙은 기존 AGENTS.md를 유지한다.

## 11. 이력과 출처

- [기존 10노드 인벤토리](e0-kubeedge.md), [서버 가속기 fixture 검증](e0-accelerators.md): 날짜·모델 범위 밖으로 확대 해석하지 않음.
- [자동 배치 파일럿](owned-hardware-placement-pilot.md), [슬롯2 부분 진단](budget-completion-attempt.md), [단순 보완](swap-move-baseline.md): 근거로 보존, 본 설계 대표실험으로 승격하지 않음.
- Scientific Agent Skills experimental-design의 block·randomization·data separation 지침을 적용했다. Kassis, T., Agarwal, V., He, Y., Patel, D., & Brueckner, A. M. (2026). Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents. https://doi.org/10.48550/arXiv.2609.00065 (이 세션 원문 확인 최신 v2).
- Semantica 도구가 없어 그래프 조회/기록은 미수행. 기존 GLOSSARY/ADR은 원격 tree에 없었다.
