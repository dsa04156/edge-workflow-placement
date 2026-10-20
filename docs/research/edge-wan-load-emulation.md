# 공유 WAN과 요청 부하를 포함하는 실제 엣지 실험 보완

2026-10-19 조사. **구현·실행 전 설계 제안**이며 이번 조사에서 클러스터·호스트 네트워크를 변경하지 않았다. 주 분석은 [실제 실험 계획](../experiments/topology-profile-cluster-plan.md)과 함께 읽는다.

## 현재 결과가 답하는 범위

이전 실행은 실제 AI 모델의 native 연결 smoke다. `scheduler-topology-profile-live/docs/experiments/topology-profile-smoke.md`와 `experiments/real-topology-live/{http_runtime.py,edge_client.py,run_smoke.py}`를 확인했다. 입력 위치 E0/E1 고정, 엣지 파일 재생, 실제 분기·합류를 검증했지만 공유 WAN 병목·고정 요청률·부하 경합은 만들지 않았다. 따라서 서버 배치의 native 지연 우위를 네트워크 제약이 있는 엣지 일반 결과로 확장할 수 없다.

Git의 `experiments/topology-profile-20261019/DESIGN.md`는 WAN 2/10/100Mbps, 0.1/1rps, 방향별 FIFO 링크, node-wide 단일 실행 lane을 가정한다. 실제 실행은 각 OperatorServer별 model lock이므로 같은 노드의 다른 task Pod는 병렬 실행할 수 있다. 같은 자원 requests가 시뮬레이터의 node-wide FIFO를 구현하지 않는다. 이 차이는 네트워크 제약과 별도로 보정해야 한다.

부모 작업의 공개 source audit에 따르면 `compute_message_size.py`의 원본 크기는 `cv2.imread`의 decoded uint8 image(`8+img.size*itemsize`)와 librosa mono16k float32 waveform(`12+audio.size*itemsize`) 기준이다. smoke는 JPEG/FLAC 파일 bytes를 전송했다. **source boundary가 다르므로 Git의 네트워크 수요를 그대로 실행한 것이 아니다.** 보완 주 비교는 source 엣지에서 decode한 실제 image/PCM을 padding 없이 binary wire로 전달하고, 압축 JPEG/FLAC replay는 별도 배포 조건으로 유지한다. 카메라·마이크도 codec을 사용할 수 있으므로 raw만 현실적이라고 규정하지 않는다. 압축 입력에서는 20/5Mbps가 배치 선택의 손익분기점까지 가지 않을 수 있다. 실제 크기·CPU 차이와 손익분기 계산은 부모 분석 문서에 기록한다.

기존 [pod_link_control.py](../../experiments/e0/pod_link_control.py)는 명시된 owned Pod netns의 목적지별 **송신** TBF만 설치한다. 현재 허용값은 None/20/50Mbps와 delay 0뿐이다. 5Mbps·2Mbps·양방향 지연을 그대로 실행할 수 없다. 각 Pod에 10Mbps 제한을 주면 동시 두 Pod가 합계 20Mbps까지 보낼 수 있어, 하나의 공유 10Mbps WAN과 다르다. 과거 [edge-network-placement](../experiments/edge-network-placement.md)도 이 한계를 명시한다.

## 서로 다른 조작을 구분한다

| 조작 | 확인하는 현상 | 별도 기록 |
|---|---|---|
| 방향별 rate cap | 큰 원본 전송의 직렬화 비용 | 설정 rate와 실제 장기 처리율 |
| 고정 delay / jitter | 왕복 제어·짧은 메시지 비용 | 편도 설정값과 실제 RTT |
| 공유 병목 | 두 source/분기의 aggregate 경쟁 | 방향별 합계 bytes·backlog·drop |
| 같은 병목의 background cross-traffic | 외부 트래픽이 차지하는 용량 | offered rate와 achieved rate |
| workflow arrival 증가 | 네트워크·연산 queue 누적 | offered/completed RPS·미완료 수 |
| 별도 CPU 경합 | task 서비스 시간·throttling 변화 | CPU 사용·throttled time·배경 Pod |

rate를 낮추는 것만으로 이동통신 전체를 재현했다고 부르지 않는다. 지연·손실·외부 트래픽을 동시에 키우면 원인을 분리하기 어려워 처음에는 loss 0, jitter 0으로 시작한다. `tc`의 shaping은 egress에서 작동하므로 한쪽 송신 설정이 양방향 제약을 뜻하지 않는다. [iproute2 tc 매뉴얼](https://man7.org/linux/man-pages/man8/tc.8.html)

## 권장 구조: 고정 source와 하나의 공유 duplex gateway

논리 경로를 `E0/E1 → 실험 소유 gateway → C`로 만든다. **모든 정책에 같은 경로 계층을 적용**하고 placement가 zone 경계를 넘는 모든 원본·중간 출력·반환 응답·해당 제어 RPC를 gateway로 보낸다. edge 안에서 끝나는 통신은 정한 edge LAN 경로를 사용한다. cloud-hub topology를 시험할 때만 E0↔E1도 WAN을 두 번 지나는 별도 topology로 정의한다. 경로를 바꾸는 실험과 물리 링크 자체를 바꾸는 실험은 구분한다.

권장 구현은 실험 소유 Pod netns 안의 kernel forwarding/tunnel gateway다. 방향별 aggregate shaper를 **각각 하나씩** 두고 E→C의 두 source와 모든 task 흐름을 동일 queue에 합친다. C→E도 별도 하나의 queue를 공유한다. sidecar tunnel/명시 route는 실험 Pod 내부에만 만들고 host NIC, kubelet/CNI, EdgeCore/CloudCore를 수정하지 않는다. netns는 네트워크 장치·route·프로토콜 stack 등의 격리 경계다. [Linux network namespaces 매뉴얼](https://man7.org/linux/man-pages/man7/network_namespaces.7.html)

전제는 다음과 같다.

1. zone·실제 Pod UID/IP·gateway UID·방향별 interface/class·destination map을 manifest로 동결한다. URL을 정하는 함수가 `/execute`뿐 아니라 `fusion_url`, `/open`, `/join`, `/result`, E1 `/branch`와 반환 경로까지 적용해야 한다. 지금 client는 환경 proxy를 비활성화하므로 HTTP_PROXY 환경변수만 설정해도 우회가 해소되지 않는다.
2. routing과 실험 Pod 내 firewall 또는 지원되는 NetworkPolicy로 직접 edge↔C 연결을 막는다. CNI의 NetworkPolicy 지원·실효성을 확인하고 API apply 성공만으로 차단됐다고 가정하지 않는다. 정책 적용은 비동기적이므로 positive probe와 **직접 경로 실패 negative probe**를 통과한 뒤 평가한다. [Kubernetes NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
3. 양쪽 source가 동시에 송신할 때 합계 처리율이 설정 WAN 용량을 넘지 않는지, 반대 방향도 독립 설정대로 동작하는지 확인한다. edge-local, C-local, E→C, C→E, E→C→E 경로를 각각 hash 검증한다. native-direct와 **동일 gateway 무제약** 결과를 둘 다 남겨 gateway 자체 비용을 분리한다.
4. 모델·wheel·입력 bundle 준비는 평가 전에 끝내며 준비 전송은 별도 기록한다. evaluation 중 model 다운로드·외부 registry 경로를 WAN task bytes에 섞지 않는다.

명시 TCP/HTTP relay도 대안이지만 TCP를 분리하고 사용자 공간 buffering·CPU 비용을 추가한다. 그 구현은 실험 전용 relay 조건으로 명시하며 end-to-end TCP WAN과 같다고 부르지 않는다. relay 자체 CPU가 병목이 아닌지 무제약 동시 전송으로 먼저 검증한다. 클러스터 CNI 안에서 신규 route가 가능한지는 아직 확인하지 않았다.

## qdisc를 단순 복사하면 생기는 문제

TBF는 평균 rate와 초기 burst, queue를 함께 결정한다. 기존 burst 16,000B는 작은 feature/전사 요청을 즉시 통과시킬 수 있다. queue 2,000,000B를 단순 재사용하면 queue가 꽉 찼을 때 2Mbps에서 약 8초, 10Mbps에서 약 1.6초에 해당하는 bytes가 대기한다(단순 `8Q/C`, 실제 TCP·하위 queue 제외). cap만 바꾸면 queue 시간 의미도 바뀐다. burst/queue를 본 평가 전에 동결하고 measured RTT·장기 goodput·drop·backlog로 검증한다. [TBF 매뉴얼](https://man7.org/linux/man-pages/man8/tc-tbf.8.html)

TBF 아래 netem을 붙일 수 있지만 child를 바꾸면 기존 TBF limit/latency가 더 이상 유효하지 않으므로 leaf queue 한계를 별도로 지정해야 한다. 이는 기존 TBF queue 수치를 그대로 신뢰할 수 없다는 뜻이다. [TBF child qdisc 설명](https://man7.org/linux/man-pages/man8/tc-tbf.8.html)

netem에는 timer 정밀도와 qdisc 조합 한계가 있으며 TCP 시험은 TSQ 영향을 고려해 receiver ingress 쪽 배치를 권고한다. **sender Pod egress에만 netem을 붙이는 방식은 우선 피하고**, router forwarding 후 수신 측 경계에서 지연을 주는 구조를 사전 검증한다. 한 방향 20ms 설정이면 reverse도 20ms일 때 idle RTT가 대략 native+40ms이며, 20ms RTT라고 기록하지 않는다. [netem 매뉴얼](https://man7.org/linux/man-pages/man8/tc-netem.8.html)

GSO/TSO/GRO는 큰 buffer와 실제 packet 사이의 관계를 바꾸므로 해당 offload 설정, MTU, 실제 packet 크기와 queue counters를 함께 남긴다. 변경이 필요하면 실험 소유 가상 interface에 한정한다. [Linux segmentation offload 문서](https://docs.kernel.org/networking/segmentation-offloads.html)

## 부하 실험에 앞서 실행기를 바꿀 부분

- 현재 `request()` timeout은 기본 60초이고 fusion join 상태 TTL은 120초, capacity는 64다. 느린 공유 WAN·queue 누적에서는 정상적인 장시간 요청을 구현 한계로 실패시킬 수 있다. timeout/TTL/capacity를 CLI·manifest로 고정하고 요청 deadline, admission 제한, stop-arrivals, drain-deadline을 명시한다. Python socket timeout을 전체 workflow wall deadline과 동일시하지 않는다.
- `/open`은 완료한 entry도 TTL까지 보유한다. 따라서 64 제한은 미완료 수 제한만이 아니다. 결과 확인/ack 뒤 정리하는 규칙과 미완료 요청 retention을 분리해야 한다. 실패·만료도 terminal JSONL 행으로 보존하며 평가 뒤 성공한 요청만 골라 latency를 요약하지 않는다.
- fusion은 전역 `self.lock`을 잡은 채 추론하므로 다른 요청의 `/open`, `/join`, `/result`까지 막힌다. request별 join 상태 잠금과 inference queue를 분리하고 수락 시각·실행 시작/끝·반환 시각을 기록해야 한다. 현재 model lock은 FIFO 순서를 보장하지 않으므로 단일 task queue라고 표현할 수는 있어도 엄밀한 FIFO로 가정하지 않는다.
- 두 branch만 병렬인 현재 `run_once()`를 반복 호출하는 smoke는 closed-loop다. source-local monotonic clock에 예정 arrival trace를 미리 올리고 이전 요청 완료와 독립적으로 발생시키는 open-loop driver가 필요하다. 예정/실제 발행 시각·발행 지연·in-flight·완료/실패·drain 잔여 수를 저장한다. driver worker 상한에 막혀 요청을 늦추면 누락 없이 기록하고 offered RPS가 유지됐다고 주장하지 않는다.
- wire는 JSON/base64이므로 task 원본 bytes와 base64/JSON/HTTP bytes를 구분한다. 학습 표의 raw tensor 크기를 그대로 wire 수요로 쓰지 않는다. network·branch·compute wall time은 중첩되므로 단순 합산해 E2E를 만들지 않는다.

## 첫 실행 순서와 음성 대조군

제안 격자는 효과를 보기 위한 사후 선택이 아닌 사전 탐색 예산이다. 1단계는 source·DAG·모델·CPU limits·thread 수를 고정하고, native-direct / gateway-no-cap / 100·10·2Mbps를 비교한다. delay는 별도 0/20ms-per-direction 대조로 시작한다. 용량×지연을 동시에 바꾼 조건만 비교하지 않는다. 기존 원격 netem rate 실패는 원인이 미확정이므로 새 delay/shaper 조합의 preflight를 먼저 통과해야 한다.

2단계는 각 route의 실측 service demand를 사용해 사전 고정한 open-loop 0.1→0.25→0.5→1rps를 단계적으로 시험한다. 한 단계의 도착 구간을 고정하고 신규 도착을 중지한 뒤 drain한다. backlog·실패·수집 메모리의 중단 기준과 최대 drain 시간을 실행 전에 정한다. 불안정한 부하는 불안정으로 보고하며 영구 queue를 만든 상태에서 다음 조건을 이어 시작하지 않는다. 이는 독립 초기상태의 finite replay이며 steady-state 성능을 뜻하지 않는다.

3단계는 네트워크와 CPU를 2요인으로 분리한다. 네트워크는 동일 공유 WAN의 cross-traffic 없음/사전 지정 offered load, CPU는 배경 경합 없음/지정 CPU 부하로 둔다. 각 요인의 단독 조건과 결합 조건을 모두 포함한다. cross-traffic generator는 inference Pod 밖에서 같은 WAN queue를 통과하게 하며, background CPU는 공유 gateway나 source driver를 포화시키지 않게 배치한다. 높은 부하가 현실의 특정 엣지 deployment 분포를 대표한다는 주장은 하지 않는다.

필수 negative control은 (a) 큰 원본의 서버 전송과 작은 feature 전송의 차이가 거의 없는 고속 링크, (b) 통신 병목 없이 CPU만 경합하는 조건, (c) CPU 경합 없이 WAN만 병목인 조건, (d) 두 source 동시 전송 합계 cap 확인, (e) gateway 우회 요청의 거부, (f) 같은 계획을 고른 정책이 관측을 공유하는 조건이다. topology/full 방법이 native에서 지거나 모든 정책이 같은 배치를 고르는 결과도 보존한다. 실제 관측 수와 정책별 참조 수를 구분한다.

지표는 source-to-answer E2E, completion ratio, deadline miss/SLO, drain 포함 완료시간, 방향별 WAN bytes/backlog/drop, offered/achieved RPS, task별 queue/compute, CPU throttling과 gateway CPU다. 조건 내 요청 수를 독립 환경 반복 수로 세지 않고 무작위 paired block의 변동을 평가한다. 위 수치는 모두 **계획값**이며 이번 조사에서 성능을 측정한 결과가 아니다.
