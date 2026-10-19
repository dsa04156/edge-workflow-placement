# Git 실험의 네트워크·부하 가정 재분석과 실측 보완

2026-10-19. 사용자 지적: native 클러스터는 전송 비용이 작으므로 실제 엣지의 병목·부하를 주고 Git에 올린 내용을 더 충실하게 분석해야 한다. **이번 작업은 소스/기존 관측 분석과 계획 수정이다. 새 클러스터 부하 실험을 실행하지 않았다.**

## 결론

이전24workflow는 모델 연결 smoke로는 유효하지만 연구의 부하 조건을 옮긴 비교는 아니다. native·closed-loop 외에도 **전달 데이터 경계가 달라져 입력 bytes가 줄어든 것**이 중요하다. 다음에는 source의 decoded image/PCM을 실제 전송하고, 공유 WAN의 용량·지연과 open-loop 요청률을 조작해야 한다. 동시에 Git의 공유 연산 대기열 가정과 현 Pod별 병렬 실행의 차이를 명시해야 한다.

조건을 강하게 만드는 목적은 제안 방법이 이기는 표를 만드는 것이 아니다. 서버가 유리한 구간, 원본 전송 때문에 엣지 처리가 유리해지는 구간, 같은 정보를 준 단순 정책과 차이가 없는 구간을 찾아야 한다. 원래 입력·연산 의미를 유지한 부하와 byte padding/가짜 지연을 구분한다.

## 1. 다시 확인한 두 Git 실험

origin fetch 후 main=`0e8ebfa`, 준비 브랜치=`e110d8b`. 로컬 실제 실행 branch=`46fd850`. 아래 분석은 이 세 source와 보존 원자료를 기준으로 한다.

| 코드 | 실제로 비교한 것 | 네트워크·부하 가정 | 실측에 그대로 가져오면 안 되는 부분 |
|---|---|---|---|
| main `simulation-20261019` | original/links/queue/both 예측기 보정, 같은 Thompson 선택·학습 | 기본300Mbps/RTT40ms·2rps·90초·30초마다10/75% 배경 queue 부하 교대, VQA시간×50; burst는10초마다0.25/0.25/2.5배 | 직렬 image→VQA 구조이며 ASR branch가 아님. 합성 VQA×50과 node-wide FCFS를 실제 독립 Pod에 자동 구현할 수 없음 |
| 준비 브랜치 `topology-profile-20261019` | DAG의 resource_proxy/topology_only/profile_only/full | raw 데이터 프로파일, WAN2/10/100Mbps, 편도20ms,0.1/1rps; edge_fast/edge1_slow/cloud_hub | 25% 자원 reservation은 실행 중 CPU 배경 부하가 아님. 노드당 단일 lane과 실제 multicore Pod 병렬성은 다름 |
| 실측 `real-topology-live` | 엣지 분기 고정과 실제 default-scheduler의 첫 연결 확인 | JPEG/FLAC·native-direct·한 workflow 완료 후 다음 발행 | 공유 WAN·요청 queue·raw 프로파일과 다름. 실제 엣지 제약을 검증한 정책 비교로 쓰지 않음 |

`scenarios.py`의 burst는 평균 요청률을 키우는 대신 같은 기대 총량을 시간상 집중시킨다. `Resource.submit()`은 노드/링크별 `free` 시각으로 전체 job을 FIFO 직렬 처리한다. 반면 실측의 `model_lock`은 OperatorServer 하나에만 적용되므로 C의 vision/speech/fusion은 다른 Pod·CPU quota로 실행한다. CPU/메모리 요청값만 같게 맞춰도 이 대기열 의미가 일치하지 않는다.

코드 근거: [run_suite.py](../../experiments/simulation-20261019/run_suite.py), [scenarios.py](../../experiments/simulation-20261019/scenarios.py), [components.py](../../experiments/simulation-20261019/components.py), [Resource](../../experiments/simulation-20261019/legacy.py), [DAG 고정 설계](https://github.com/dsa04156/edge-workflow-placement/blob/e110d8b431c7c7d7c185897fe3429cda20c63257/experiments/topology-profile-20261019/DESIGN.md).

### main의 큰 이득은 어디서 왔는가

원 summary CSV의 성분 제거 결과를 다시 대조했다. 표는 기존 시뮬레이션의 seed별 상대 개선율 평균이며 새 실측값이 아니다.

| 조건 | links만 % | queue만 % | both % |
|---|---:|---:|---:|
| 기본 혼잡 | 0.00 | 33.46 | 33.46 |
| 일정 배경 부하 | 0.00 | 0.02 | 0.02 |
| 요청 burst | 0.00 | 21.75 | 21.75 |
| 일정 부하+burst | 0.00 | 1.11 | 1.11 |
| WAN25Mbps | 0.00 | 0.00 | 0.00 |
| 클라우드 별도 대기열 | 0.00 | 0.00 | 0.00 |

기본 이득은 주로 cloud feature와 VQA가 같은 FCFS queue에 재진입할 때 대기 예측을 보완한 효과다. 별도 queue 조건은 자원도 추가되는 합성 음성 대조이며 현실의 인과 기전을 입증한 결과는 아니다. 링크 calendar는 자체 보낸 데이터만 반영하고 외부 경쟁 트래픽은 없다. **main의16.92% 평균을 ‘네트워크 부하를 주면 얻을 이득’으로 사용할 수 없다.**

주 DAG 주제는 새 브랜치의 정적 topology/profile 비교로 유지한다. main의 queue 재진입 가설은 별도 serving-pool 구성에서 후속 검증한다. 강한 기존 예측기 보완과 비교하기 전에 새 알고리즘 필요성을 주장하지 않는다.

## 2. 가장 큰 누락: 압축 파일과 decoded task 데이터

원 [compute_message_size.py](../../experiments/simulation-20261019/data/jellybean/VQA/python/models_profiler/compute_message_size.py)는 JPEG/FLAC 파일 크기를 읽지 않는다. `cv2.imread`의 uint8 이미지 본체와 librosa16kHz float32 waveform 원소 수를 사용한다. 공개 표는 이미지 약813,092B, 음성 약99,401B다. 반면 smoke는 압축 파일을 보내고 모델 adapter 안에서 decode했다.

동일한 실제 평가40입력의 크기를 manifest로 재계산했다.

| 데이터 | smoke 파일 본체 평균 | decoded 본체 평균 | decoded/file |
|---|---:|---:|---:|
| 이미지 | JPEG181,636B | uint8 pixels821,505B | 4.52배 |
| 음성 | FLAC33,306B | float32 PCM108,210B | 3.25배 |

smoke가 전송한 JSON/base64 본체는 두 파일 합계의 대략4/3배다. 그 overhead까지 감안해도 약2.293Mbit/request이며, decoded binary 본체는 약7.438Mbit/request다. 두 값 모두 HTTP/header/ACK를 제외한 수요 근사이며 실제 NIC bytes가 아니다.

따라서 같은1rps·10Mbps에서도 입력 직렬화만의 nominal offered load가 압축/base64 약23%와 raw/binary 약74%로 달라진다. 2Mbps에서는 raw 입력만으로1rps 약372%여서 서버 전송 정책은 지속 가능한 정상상태가 아닐 수 있다.

주 비교는 **원 프로파일에 맞춘 decoded image/PCM 경계**로 설계한다. source 엣지에서 재생 fixture를 미리 decode한 버퍼를 준비하고 raw 입력이 준비된 시점을 요청 시작으로 정의하거나, per-request decode를 넣는 경우 그 source 비용을 전 정책에 동일하게 포함한다. 두 비용 경계를 섞지 않는다. adapter는 같은 이미지·샘플 값을 받아 같은 feature/transcript/answer를 내야 하며 프로토콜 변경 후 재검증·재보정한다.

이는 원 경계 복원이지 bytes padding이 아니다. 압축 전송도 실제 camera/audio 배포에서 가능한 방식이므로 JPEG/FLAC 조건은 별도 codec 배포 대조군으로 남긴다. raw만 ‘진짜 엣지’라고 부르지 않는다.

## 3. native 결과의 비용 경계

관측 데이터에는 statistical-analysis의 기술 통계 원칙을 적용했다. 두 배포는 같은 날짜 순차 실행의 각1환경·5입력×2반복이므로 새 p값·CI·인과 효과를 계산하지 않았다.

| 평가10요청 평균 | 엣지 분기 고정 | default-scheduler 서버 배치 |
|---|---:|---:|
| vision compute | 586.15ms | 137.23ms |
| speech compute | 1191.39ms | 680.94ms |
| fusion compute | 25.01ms | 25.09ms |
| E2E | 1237.69ms | 723.43ms |
| E2E−max(branch queue+compute)−fusion queue/compute | 21.28ms | 17.40ms |

마지막 행은 source·제어·전달·분기 중첩 등을 포함하는 잔여값이며 **순수 네트워크 시간은 아니다**. 그래도 이 smoke에서 backend queue가 평균0.002~0.004ms 수준인 점과 함께, 전송/부하 경합을 충분히 만든 조건이 아니었다는 판단을 지지한다. native 물리 링크의 실효 대역폭을 직접 측정한 증거로 사용하지 않는다.

### cap을 왜20/5Mbps에서 재검토해야 하는가

단일 task의 queue/지연을 무시한 근사는 다음과 같다.

`B_break_even ≈ 8 × (input wire bytes − output wire bytes) / (edge compute − server compute)`.

smoke5입력의 순차 관측 차이를 대입한 **초기 탐색용** 결과:

| task | compressed+base64 기준 | decoded+binary 기준 |
|---|---:|---:|
| vision | 약3.64Mbps | 약13.17Mbps |
| speech | 약0.65Mbps | 약1.55Mbps |

이 수치는 DAG 최적 배치나 확인된 전환점이 아니다. raw 경계로 바꾸면 decode 비용의 위치가 바뀌며, branch max·공유 WAN·queue·RTT·소스 비용과 입력별 차이를 새로 측정해야 한다. 다만 과거20/5Mbps 제안은 압축 파일의 전송 수요에서 특히 약한 제약이었음을 보여준다. Git의100/10/2Mbps를 먼저 대조하고, 필요하면0.5Mbps를 사전 정의한 추가 임계 진단으로 둔다. 결과가 잘 나온 cap만 골라 보고하지 않는다.

## 4. ‘부하를 더 준다’의 구체적 의미

### A. 하나의 공유 WAN과 지연

E0/E1→실험 소유 gateway→C 경로를 만들고 방향별 aggregate queue를 하나씩 둔다. 두 source에 각각10Mbps를 주는 것은 하나의10Mbps WAN이 아니다. edge 안과 C 안 통신은 해당 zone 내부 경로로 유지한다. zone을 넘는 원본·feature·전사·결과·관련 RPC의 양방향 경로와 우회 차단을 검증한다.

첫 탐색은 `B=100/10/2Mbps`, `added RTT=40ms`(편도20ms씩)다. 추가 지연 없는 같은 gateway 대조를10Mbps에서 두어 bandwidth와 delay 효과를 분리하고, native-direct와 gateway-no-cap/no-delay를 두어 gateway 자체 overhead를 구분한다.100Mbps가 underlying path에서 달성되지 않으면 high-cap 조건으로 보고하며 실제100Mbps라고 단정하지 않는다. RTT160ms·loss/jitter는 초기 결과 이후 별도 요인으로 추가한다.

기존 `pod_link_control.py`는 cap20/50, delay0만 허용하므로 이 조건을 구현하지 못한다. 공용 host NIC/CNI/EdgeCore를 바꾸지 않고 소유 Pod/router netns에만 적용한다. relay를 쓰면 TCP 분리/CPU buffering이 추가되는 별도 구현임을 밝힌다. 설치·qdisc·queue 크기·burst·GSO/TSO·goodput·동시 두 source 합계 cap·직접 우회 실패까지 확인해야 실행 가능하다고 표시한다. [공식 도구 근거와 상세 설계](../research/edge-wan-load-emulation.md).

### B. 완료와 독립적인 open-loop 도착

initial0.1/0.5/1rps를 공통 trace로 비교한다.0.1/1rps는 새 Git DAG 설계와 연결하며0.5rps는 중간 진단이다. source-local monotonic clock의 예정시각에 request를 release하고, 이전 request의 완료를 기다리지 않는다. scheduled/issued/completed·발행 지연·in-flight·실패·drain을 모두 기록한다.

평가40입력 평균의 raw 입력 수요로 계산한 all-cloud 입력만의 offered load:

| arrival | 100Mbps | 10Mbps | 2Mbps |
|---|---:|---:|---:|
| 0.1rps | 0.7% | 7.4% | 37.2% |
| 0.5rps | 3.7% | 37.2% | 185.9% |
| 1rps | 7.4% | 74.4% | 371.9% |

헤더·출력·배경 트래픽 제외값이며 구체 정책에 따라 WAN bytes가 바뀐다.100% 초과는 정상상태 비교로 해석하지 않고, 고정 도착 구간 이후 stop/drain한 유한 구간의 실패·SLO·backlog를 보고한다. 실측 compute 평균만으로도 E1 speech1rps는 직렬 capacity를 넘을 가능성이 있다. 모든 실패를 ‘망 문제’로 묶으면 안 된다.

burst는 main과 같은10초0.25/0.25/2.5배 패턴을 별도 조건으로 둔다. 단순 arrival 증가와 burst를 처음부터 동시에 적용하지 않는다. 배경 cross-traffic 없음/동일 WAN의 지정 offered rate, CPU/service 경합 없음/있음도 단독 조건 후 결합한다.

### C. 공유 연산 대기열은 별도 검증

현재24core 서버의 여유 core에 CPU hog Pod 하나를 올리는 것만으로 시뮬레이터의 node-wide 단일 FIFO가 생기지 않는다. 특정 서비스의 CPU quota·worker concurrency를 제한하거나 **여러 모델 요청이 같은 bounded serving pool을 공유하는 조건**을 명시적으로 만들어야 한다. 후자는 서비스 실행 구조 변경이며 전 정책에 같은 구조를 제공한다.

main의30초10/75% 교대 부하는 동일 pool에 실제 CPU/모델 작업을 넣어 service time/queue를 계측하는 방식으로 검증할 수 있다. `sleep`이나 VQA 시간×50을 실측 AI 처리시간으로 대신하지 않는다. 현 fusion 평균25ms라1rps에서 service demand는 약2.5%이고 ASR가 먼저 병목일 수 있다. 무조건 요청만 늘려 main의 VQA 재진입 가설을 재현했다고 부를 수 없다.

## 5. 부하를 주기 전에 고쳐야 하는 실행기

| 현재 구조 | 고부하에서 생길 왜곡 | 필수 보완 |
|---|---|---|
| 완료 entry도 TTL120초까지64개 capacity에 포함 | 연산이 모두 끝나도 다음 admission이429 | pending과 completed retention 분리, result ACK/cleanup·최대 미완료 수 계약 |
| fusion global lock을 잡은 채 model invoke | join 대기뿐 아니라 open/result 제어까지 직렬 차단 | request별 상태 잠금과 명시 inference queue 분리·queue 입장/시작/끝 기록 |
| HTTP 기본timeout60초, joinTTL120초 | 느린 링크의 지연/timeout이 임의 구현 제한에 섞임 | absolute workflow deadline·RPC timeout·join lifetime·stop/drain 정책 분리 |
| 한 workflow 끝난 뒤 다음 release | 처리량 감소가 arrival까지 낮춰 queue가 숨겨짐 | 미리 동결한 open-loop trace, 충분한 driver capacity와 발행 지연 계측 |
| FIFO 명세 없이 Python lock만 사용 | simulator의 FIFO와 실제 순서가 다를 수 있음 | bounded FIFO queue 또는 다른 discipline을 명시하고 관측 |

64개 완료 후65번째 admission이429인 현상은 실제 로컬 HTTP로 재현했다. [재현 결과](../../artifacts/git-load-reanalysis/audit-v1/join-capacity-reproducer.json). 합성 adapter를 쓴 protocol 진단이며 AI/클러스터 성능 표본으로 세지 않는다. 이 제한을 고치지 않고 부하를 높인 결과는 부하 정책 비교의 근거로 쓰지 않는다.

## 6. 수정된 첫 실행 순서

1. **입력 경계 복원:** decoded uint8/PCM binary codec, 입력 본체/실제 wire bytes, CPU reference와 동일성 확인. 기존 JPEG/FLAC smoke는 동결한다.
2. **실행기 부하 계약:**64완료 초과·중복/누락·timeout·drain·부하 발행 회귀를 통과한다. 예외를 느린 요청 누락으로 처리하지 않는다.
3. **WAN preflight:** native-direct/gateway-unlimited의 비용,2source 합계 cap, 양방향 RTT, 차단 negative probe와 복구 확인. qdisc queue 시간 한계도 동결한다.
4. **배치 전환 진단:** 낮은0.1rps에서100/10/2Mbps·RTT40ms. 서버 전체/엣지 분기/혼합 배치의 실제 연산·전달 비용을 측정해 어떤 부품이 병목인지 확인한다. 이 배치는 알고리즘 성능 평가가 아닌 비용·경계 진단이다.
5. **동일 정보 정책 비교:** 새 calibration 입력으로 비용표·품질 후보·계획을 동결한 후 resource/topology_only/profile_only/full/Task-fastest/data-greedy를 별도 평가한다. 복수 task 후보를 확보하고 같은 배치는 execution group을 공유한다. 현물프로파일이 없는 조합을 synthetic 표로 채우지 않는다.
6. **요청률·부하 확대:**0.5/1rps 및 burst, cross-traffic, shared-serving-pool 경합을 차례로 확장한다. 조건 순서는 seed로 randomize하고 같은 block 내 같은 trace를 재생한다. queue가 남은 상태에서 다음 조건을 시작하지 않는다.

주 지표는 source-to-answer E2E와 전체 요청의 완료/timeout/quality·SLO다. network의 방향별 bytes/goodput/backlog/drop, task별 queue/compute/throttling, offered/achieved RPS, gateway/driver CPU를 함께 기록한다. 기존 연구가 고려하지 않는다는 주장 대신 동일 정보의 DAG 재계산/단순 보완으로도 손실이 남는지 확인한다. 이 설계 수치들이 특정 상용 엣지 망의 대표 분포라는 주장은 하지 않는다.

## 산출물·검증

- [수치 대조 원자료](../../artifacts/git-load-reanalysis/audit-v1/audit.json): input/source SHA256, payload 크기, stage별 기술 통계, 단순 손익분기·offered load 근사, main 성분 제거 요약.
- [읽기 전용 분석 스크립트](../../artifacts/git-load-reanalysis/audit_load_gap.py). 새 성능 측정이나 시뮬레이터 재실행은 하지 않는다.
- [공유 WAN 구현의 공식 문서 검토](../research/edge-wan-load-emulation.md).

실제 실행한 분석 명령:

```bash
rtk proxy python3 artifacts/git-load-reanalysis/audit_load_gap.py --repo /home/jinuk/codex-work/scheduler --live /home/jinuk/codex-work/scheduler-topology-profile-live --out artifacts/git-load-reanalysis/audit-v1
```

기존 관측으로 차이가 유의한지 검정하거나 효과 크기를 일반화하지 않았다. 독립 block 수·본 실험 표본 수는 아직 확정하지 않으며 pilot 변동성/실질적 효과와 다중비교를 기준으로 별도 statistical-power 절차에서 정한다.
