# 이기종 자원 오케스트레이션: 기존 방안과 배포·전환 관점의 검증 과제

조사일: 2026-10-16. 목적: 실제 이기종 클라우드에서 기존 방법의 적용 조건을 확인하고, 재현할 문제와 비교 대상을 좁히는 것. 문헌·공식 문서 조사이며 이번 작업에서 새 성능 실험이나 독립 재현은 수행하지 않았다.

## 현재 주제와 이 문서의 역할

현재 연구는 **이종 엣지 AI 환경에서 실행 호환성과 데이터 이동 비용을 고려한 동적 워크플로우 배치 최적화**다. [문제 정의](problem-definition.md)와 [첫 비교 실험 계획](workflow-placement-experiment-plan.md)이 현재 범위·우선순위의 기준이다. 아래 넓은 조사는 배경 근거로 보존한다. 학습·상태 있는 LLM 등을 고르는 이전 제안은 첫 실험의 필수 범위가 아니다.

가까운 직접 선행에는 워크플로의 모델 선택·배치를 다루는 **JellyBean·Atlas**를 추가한다. Task별 최단 실행 노드 선택과 DAG 공동 배치의 차이를 먼저 측정하고, 동적 환경에서는 기존 DAG 재최적화·운영 스케줄러·DRL·단순 보완을 비교한다. [JellyBean 논문 정보](https://arxiv.org/abs/2205.04713), [Atlas 검토본 §III–V](https://arxiv.org/html/2609.04513v1)

2026-10-16 사용자 제시 **AnchorDL**도 근접 선행으로 추가했다. 공식 초록은 데이터 전송·모델 상주/로딩 비용의 공동 결정과 look-ahead, chain/fan-in/fan-out·greedy joint-cost 비교를 명시한다. 본문·코드는 미확보이며 구체 NPU/runtime/동적 전환 범위는 미확인이다. [공식 초록](https://pure.korea.ac.kr/en/publications/anchordl-dual-locality-based-scheduling-for-serverless-inference-/), [검토 기록](ieee-11662529-review.md)

## 1. 조사 결과

**자원·네트워크·QoS를 공동 최적화하거나 전환 비용을 추가하는 것만으로는 연구 차별성을 주장하기 어렵다.** Pollux는 재배정 지연을 목적함수의 penalty에 반영한다. INFaaS는 CPU/GPU/Inferentia의 모델 변형, 로딩 상태와 로딩 비용을 사용한다. ServerlessLLM은 체크포인트 위치·로딩 시간·실행 중 요청의 이전 비용을 함께 고려한다. 따라서 비교 대상은 정적 배치만이 아니라 이런 배포·전환 인지 방법이어야 한다. [Pollux §4.2](https://www.usenix.org/system/files/osdi21-qiao.pdf), [INFaaS §3–4](https://www.usenix.org/system/files/atc21-romero.pdf), [ServerlessLLM §5–6](https://www.usenix.org/system/files/osdi24-fu.pdf)

**유망한 조사 방향은 기존 추상화가 실제 실행 가능성과 어긋나는 구체적인 경계다.** 예를 들어 ONNX Runtime의 연산자별 실행 가능성 확인과 fallback, TensorRT의 엔진 호환성 모드, Universal Checkpointing의 병렬화별 학습 상태 변환은 이미 존재한다. 이 기능들을 적용하고도 모델 버전·런타임·상태·재컴파일 조건 때문에 예측한 순이익이 실현되지 않는지 확인해야 한다. 이 결합에서 새로운 문제가 남는다는 것은 현재 **연구 가설**이며, 아래 자료만으로 미해결성을 확정할 수 없다. [ONNX Runtime 아키텍처](https://onnxruntime.ai/docs/reference/high-level-design.html), [TensorRT 엔진 호환성](https://docs.nvidia.com/deeplearning/tensorrt/latest/inference-library/engine-compatibility.html), [Universal Checkpointing §5](https://www.usenix.org/system/files/atc25-lian.pdf)

**가까운 최신 선행인 Trident도 우선 대조해야 한다.** 2026년 preprint는 CPU/가속기 파이프라인의 배치·병렬도·설정 전환을 MILP로 함께 결정하고 cold start·rolling update·migration penalty를 반영한다. 실제 실험의 가속기는 Ascend 910B로 제시되므로, 이것만으로 다중 벤더 runtime 간 상태 이동이 검증됐다고 볼 수 없다. [Trident §6–8.1](https://arxiv.org/html/2603.02075v1)

## 2. 범위와 근거 읽는 법

조사 범위는 Kubernetes 자원 노출·할당·입장 제어, HPC 태스크 런타임, DNN 분할, 학습 클러스터 스케줄링, GNN/RL 스케줄링, 병렬화 계획, 추론 서빙, 체크포인트·이전, 모델 업데이트 운영이다. 기존 연구의 대표 축과 배포·전환을 이미 다루는 반례를 우선 선택했다. 체계적 문헌고찰이나 전수 목록은 아니며 통신망 NFV, 스토리지 오케스트레이션, FPGA 재구성, 모든 상용 스케줄러를 포괄하지 않는다.

이 보고서에서는 **이종성의 수준을 분리**한다. 요청/모델 구조의 다양성, 같은 벤더의 GPU 세대 차이, CPU+GPU/NPU 조합, 서로 다른 벤더/runtime, 여러 클라우드는 같은 조건이 아니다. Alpa의 다양한 모델 구조나 Llumnix의 요청 다양성을 cross-vendor 실행 이전의 근거로 바꾸어 읽지 않는다. [Alpa §8](https://www.usenix.org/system/files/osdi22-zheng-lianmin.pdf), [Llumnix §2–4](https://www.usenix.org/system/files/osdi24-sun-biao.pdf)

검색은 `heterogeneity-aware scheduling`, `goodput`, `DNN partition`, `graph scheduling`, `model loading`, `live migration`, `checkpoint reconfiguration`, `runtime/engine compatibility`와 시스템명을 조합했다. USENIX·저자 원문·공식 프로젝트 문서·벤더 문서를 직접 확인했으며, 아래 링크는 주장을 뒷받침하는 1차 자료다. 논문별 성능 배수는 실험 조건이 다르고 이번에 재현하지 않았으므로 비교 수치로 인용하지 않았다.

근거 수준은 다음처럼 구분한다.

- **본문 확인**: 관련 설계·구현·제약 절을 확인했다. 전체 코드 감사나 모든 부록 검증을 의미하지 않는다.
- **공식 문서 확인**: 문서에 명시된 기능·제약을 확인했다. 운영 환경에서 재현한 것은 아니다.
- **초록 확인**: 목적·개요만 요약하며 기능 부재를 추론하지 않는다.
- **해석/가설**: 조사자의 비교 또는 향후 검증 제안이다. `미확인`은 `미지원`을 뜻하지 않는다.

웹 공식 문서는 변경된다. Kubernetes·Kueue·TensorRT는 실험 전에 릴리스, 드라이버, 옵션과 문서 버전을 고정해야 한다. 특히 열람한 Kueue Concurrent Admission 문서는 v0.18부터 alpha라고 명시한다. 이 기능의 존재와 실제 사용할 배포판의 지원은 별도 확인 대상이다. [Kueue Concurrent Admission](https://kueue.sigs.k8s.io/docs/concepts/concurrent_admission/)

## 3. 대표 방안 분류

### 3.1 인프라·실행 런타임

| 방안 | 목적·대상 | 핵심 방법 | 배포·전환에서 확인한 범위 | 근거 수준·출처 |
|---|---|---|---|---|
| Kubernetes Device Plugins | GPU/NIC/FPGA 등의 장치를 Pod에 노출 | 벤더 플러그인이 kubelet에 장치 등록·할당 제공 | 장치 접근 설정의 확장점. 이 문서만으로 모델 실행·상태 이전까지 보장한다고 볼 수 없음 | 공식 문서 확인: [Device Plugins](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/) |
| Kubernetes DRA | 장치 속성·구성에 맞는 할당 | DeviceClass, ResourceSlice, ResourceClaim, CEL 필터, CDI 연결 | 장치 선택과 준비·정리를 연결. 모델 연산자/엔진 호환성 판정과 같은 계약은 별도 검증 필요 | 공식 문서 확인: [DRA](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/), [할당 흐름](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/how-dra-works/) |
| Kueue | 배치 작업 입장 제어·할당량·공정 공유 | ResourceFlavor별 quota, borrowing, preemption 및 flavor 선택 | 최신 문서는 중단 허용 작업의 선호 flavor로의 migration도 제공. 상태 보존형 live migration으로 확대 해석하면 안 됨 | 공식 문서 확인: [ClusterQueue](https://kueue.sigs.k8s.io/docs/concepts/cluster_queue/), [Concurrent Admission](https://kueue.sigs.k8s.io/docs/concepts/concurrent_admission/) |
| Volcano | Kubernetes의 고성능·배치 작업 스케줄링 | gang scheduling, queue, 우선순위, topology 기반 배치 | 작업 집합의 동시 자원 확보와 배치 정책을 제공. 모델별 runtime 변환·상태 이전은 별도 계층의 계약으로 확인해야 함 | 공식 문서 확인: [v1.10 Introduction](https://volcano.sh/docs/v1.10.0/home/introduction/) |
| HAMi | 여러 벤더 가속기의 할당·공유 | 장치별 plugin과 GPU 메모리·연산 자원 공유, Volcano 연계 | 벤더별 분할·다중 장치 지원 조건이 다름. 여러 장치를 관리하는 것과 동일 모델의 실행 산출물·상태를 서로 변환하는 것은 구분 | 공식 문서 확인: [v2.6.0 FAQ](https://project-hami.io/docs/v2.6.0/faq). 해당 버전의 범위이며 최신 지원표로 일반화하지 않음 |
| Karmada | 여러 클러스터의 workload 배치·장애 전환 | application health와 의존성 전파, graceful eviction, 상태 메타데이터 보존 | 문서 v1.19에는 JSONPath로 jobID 등을 추출하고 공유 checkpoint로 복원하는 예시가 있음. 상태 주입 기능은 alpha·기본 off이며 VRAM/kernel live migration을 뜻하지 않음 | 공식 문서 확인: [Application-level failover 및 State Preservation](https://karmada.io/docs/userguide/failover/application-failover/) |
| StarPU | CPU·GPU 혼합 환경의 의존성 태스크 실행 | codelet별 구현, 성능모델·데이터 지역성 기반 스케줄링, 데이터 전송·prefetch | CUDA/OpenCL 등 복수 구현을 준비한 태스크 실행과 데이터 일관성 관리. 임의의 단일 GPU 바이너리 변환을 뜻하지 않음 | 공식 문서 확인: [Features: codelets, transfers, scheduling](https://starpu.gitlabpages.inria.fr/features.html) |
| ONNX Runtime | 모델 그래프를 지원 backend에 실행 | EP의 GetCapability로 subgraph 배정, 기본 EP fallback | 연산자 수준 실행 가능성을 이미 질의. CPU fallback이 생긴 배치의 지연·전송 비용과 실행 중 상태 이동은 따로 확인할 문제 | 공식 문서 확인: [Architecture](https://onnxruntime.ai/docs/reference/high-level-design.html) |

### 3.2 분할·학습·그래프 기반 계획

현재 워크플로 배치 주제에 추가한 직접 선행은 다음과 같다. 기존 25개 방안의 조사 이력과 구분한다.

| 추가 선행 | 이미 다루는 것 | 현재 비교에서 확인할 경계 | 근거 |
|---|---|---|---|
| JellyBean | 이종 인프라의 ML 워크플로 모델 선택·worker 배정 | 동적 변화·재계획·실제 재배포 계약은 코드/실험 절 추가 대조. 기능 부재를 단정하지 않음 | [저자 초록 확인](https://arxiv.org/abs/2205.04713) |
| AnchorDL, CLOUD2026 | 데이터 전송·모델 상주/로딩 joint cost와 인접 의존성 look-ahead; chain/fan-in/fan-out | 공식 초록만 확인. NPU/runtime/compile/전환 범위는 미확인. 같은 정보와 look-ahead를 제공한 비교 필요 | [공식 초록](https://pure.korea.ac.kr/en/publications/anchordl-dual-locality-based-scheduling-for-serverless-inference-/), [대조 상태](ieee-11662529-review.md) |
| Atlas, arXiv:2609.04513v1 | 모델 선택·하드웨어 배치 공동 최적화 | §III-A의 단일 사이트 offline·작은 통신 비용 가정. 동적 엣지는 확장 적용으로 평가 | [관련 본문 확인](https://arxiv.org/html/2609.04513v1) |

다음은 이전 조사에서 정리한 관련 계획·학습 계열이다.

| 방안 | 목적·대상 | 핵심 방법 | 배포·전환에서 확인한 범위 | 근거 수준·출처 |
|---|---|---|---|---|
| Neurosurgeon, ASPLOS 2017 | 모바일–클라우드 DNN 계산 분담 | 계층 단위 분할로 지연·에너지·데이터센터 처리량 개선 | 본 조사에서는 출판사 초록의 문제·목적만 확인. 런타임 호환성·업데이트·이전 기능 부재를 판단하지 않음 | 초록 확인: [ACM 원출판물](https://doi.org/10.1145/3037697.3037698) |
| JointDNN, 2018/2020 | 모바일–클라우드 추론·온라인 학습 | 계층 묶음의 계산·송수신 비용을 그래프에 넣어 최단경로/제약 최적화 | 온라인 학습에서 cloud가 갱신한 weight의 다운로드 비용도 모델링. 이것은 운영 모델 버전 교체와 구분해야 함 | 본문 확인: [§II-B 및 온라인 학습 모델](https://arxiv.org/html/1801.08618) |
| Gandiva, OSDI 2018 | DL 학습 자원 이용률·빠른 탐색 피드백 | 미니배치 반복성에 기반한 time slicing, packing, migration, grow/shrink | 실제 학습 상태 checkpoint/migration, 목적지 warm-up과 overhead 절감 구현. 상태 이전을 무시한 정적 스케줄러로 분류할 수 없음 | 본문 확인: [§4.1, §5.2, §6.1](https://www.usenix.org/system/files/osdi18-xiao.pdf) |
| Gavel, OSDI 2020 | 서로 다른 가속기 성능에서 공정성·JCT·비용 등 정책 구현 | effective throughput으로 최적화하고 라운드별 실제 자원 배정 | checkpoint API와 lease renewal로 재시작 절감, 실행 불가 조합은 throughput 입력에서 제외 가능. 구현 절에서는 PyTorch 지원·TensorFlow 향후 작업으로 명시 | 본문 확인: [§3.1, §6, §7.2](https://www.usenix.org/system/files/osdi20-narayanan_deepak.pdf) |
| Pollux, OSDI 2021 | 학습 진행량·자원 효율과 공정성 | GPU 할당과 배치 크기 등을 goodput 기준으로 공동 적응 | checkpoint-restart와 재배정 지연 추정치를 fitness penalty에 반영. 범용 이종 가속기 런타임 변환이 입증됐다는 뜻은 아님 | 본문 확인: [§4.2, §5.2–5.3](https://www.usenix.org/system/files/osdi21-qiao.pdf) |
| Decima, SIGCOMM 2019 | Spark 등의 DAG 작업 완료시간 | 의존성 그래프 임베딩/GNN과 RL로 실행 stage·병렬도 선택 | 실행 중 task를 선점하지 않고 stage 완료 후 executor 회수한다고 명시. DL 가속기 런타임 호환성을 검증한 시스템으로 간주하면 안 됨 | 본문 확인: [§4–5, §8 Preemptive scheduling](https://web.mit.edu/decima/content/sigcomm-2019.pdf) |
| Alpa, OSDI 2022 | 대규모 DL의 자동 병렬화 | intra-op ILP, inter-op 계획, device mesh와 cross-mesh resharding | 실행 계획 생성과 통신 runtime을 구현. 논문의 heterogeneous model architecture는 다양한 모델 구조를 뜻하며 다중 벤더 런타임 지원과 구분 | 본문 확인: [§4–7 및 §8 실험 환경](https://www.usenix.org/system/files/osdi22-zheng-lianmin.pdf) |
| Trident, 2026 preprint | CPU·가속기가 섞인 멀티모달 데이터 준비 파이프라인 | GP throughput 추정·메모리 제약 tuning·MILP의 폐루프 | configuration transition과 cold-start discount, bandwidth, migration penalty를 통합. 독립 재현·동료심사 상태는 별도 확인 | 본문 확인: [§4–8](https://arxiv.org/html/2603.02075v1) |

### 3.3 추론 배치·서빙·이전·업데이트

| 방안 | 목적·대상 | 핵심 방법 | 배포·전환에서 확인한 범위 | 근거 수준·출처 |
|---|---|---|---|---|
| Clockwork, OSDI 2020 | DNN 요청의 지연 예측성과 SLO | 중앙 제어기가 LOAD/UNLOAD/INFER와 GPU 상주 상태 관리 | 모델 로딩시간과 현재 상태를 스케줄에 직접 반영. 단순한 정상 실행 throughput 최적화로 요약하면 틀림 | 본문 확인: [§4–5](https://www.usenix.org/system/files/osdi20-gujarati.pdf) |
| INFaaS, ATC 2021 | 정확도·지연 요구를 만족하는 비용 효율적 추론 | CPU/GPU/Inferentia의 모델 변형 선택과 모델/VM autoscaling | 로딩·간섭·포화 상태, 로딩시간 포함 비용, 이미 등록된 모델의 변형 생성. 모델 버전 교체의 의미적 안전성은 별도 판단 | 본문 확인: [§3–5](https://www.usenix.org/system/files/atc21-romero.pdf) |
| REEF, OSDI 2022 | GPU에서 실시간·best-effort 추론 공존 | reset-based kernel preemption과 dynamic kernel padding | 선점·복구 비용과 GPU runtime/driver에 들어가는 기법. AMD 구현과 제한적 NVIDIA 구현의 차이 존재; 장치 간 이식과는 다른 문제 | 본문 확인: [§4–6 및 구현 범위](https://www.usenix.org/system/files/osdi22-han.pdf) |
| SHEPHERD, NSDI 2023 | 불확실한 요청에서 goodput·확장성 | 그룹 단위 planner와 선점·모델별 batching online scheduler | 계획·로딩·선점 overhead 평가. GPU 모델 상주는 계획 주기 시작에 갱신하며 online 동적 model swapping은 향후 작업이라고 명시 | 본문 확인: [§5–8](https://www.usenix.org/system/files/nsdi23-zhang-hong.pdf) |
| ServerlessLLM, OSDI 2024 | LLM cold start와 모델 시작 지연 | 다계층 checkpoint loading, token 기반 live migration, startup-time-aware scheduling | checkpoint 지역성·로딩·migration cost estimator를 결합. 토큰을 옮기고 목적지에서 KV cache를 재계산하는 접근 | 본문 확인: [§4–6](https://www.usenix.org/system/files/osdi24-fu.pdf) |
| Llumnix, OSDI 2024 | 실행 중 LLM 요청의 동적 부하 균형·우선순위 | 요청별 KV cache의 다단계 복사와 분산 재스케줄링 | 실행 중 요청 상태 이전·예약·중단/실패 처리 handshake까지 설계. 서로 다른 모델 버전 사이의 KV 호환성 증명으로 일반화할 수 없음 | 본문 확인: [§4.2–5, §6.2](https://www.usenix.org/system/files/osdi24-sun-biao.pdf) |
| Universal Checkpointing, ATC 2025 | 분산 학습 checkpoint의 병렬화 재구성 | tensor/optimizer state의 atomic checkpoint와 pattern 기반 변환 | source/target 병렬화·하드웨어 구성과 학습 상태를 분리. 같은 학습 모델의 상태 재배치를 새 아키텍처 버전의 의미 호환성과 혼동하면 안 됨 | 본문 확인: [§5](https://www.usenix.org/system/files/atc25-lian.pdf) |
| GCR, FAST 2026 | GPU 프로그램의 투명한 checkpoint/restore | control/data 경로 분리, GPU dirty tracking과 incremental C/R | CUDA 중심 system primitive로 switching·복원을 지원. UVM 및 mid-kernel preemption은 논문 discussion의 후속 과제 | 본문 확인: [§4–5, §8](https://www.usenix.org/system/files/fast26-zeng.pdf) |
| SageMaker deployment guardrails | 운영 중 모델 버전 갱신 | blue/green·canary·rolling, 관찰 구간·alarm·rollback | 업데이트 트래픽 전환과 복구가 이미 운영 기능. GPU 내부 실행 상태의 버전 간 변환까지 제공한다는 주장은 이 문서로 뒷받침되지 않음 | 공식 문서 확인: [Deployment guardrails](https://docs.aws.amazon.com/sagemaker/latest/dg/deployment-guardrails.html) |

## 4. 핵심 네 축의 evidence matrix

다음은 종합 점수표가 아니라 **확인된 계약의 경계**를 비교한 표다. `미확인`은 원문에서 지원하지 않는다고 선언하는 표현이 아니다. 모델 업데이트는 학습의 weight update, 배포된 모델 변형 선택, 새 모델 버전으로 운영 교체를 구분했다.

| 근거 | 런타임/산출물 호환성 | 모델 업데이트 | 실행 상태 | 전환 비용 |
|---|---|---|---|---|
| [StarPU 공식 기능](https://starpu.gitlabpages.inria.fr/features.html) | 준비된 codelet 구현을 장치에 배정; 공식 지원표는 CUDA+HIP 혼용 예외 명시 | 운영 모델 교체는 미확인 | 데이터 의존성과 일관성 | 전송·지역성·prefetch 고려 |
| [ONNX Runtime 아키텍처](https://onnxruntime.ai/docs/reference/high-level-design.html) | EP별 subgraph 실행 가능성 및 fallback | 새 버전 전환 정책은 미확인 | 그래프 실행 경로 설명; live state 이전 미확인 | 이 문서에서 cluster 전환의 총비용 최적화 미확인 |
| [TensorRT 엔진 호환성](https://docs.nvidia.com/deeplearning/tensorrt/latest/inference-library/engine-compatibility.html) | 기본 버전/장치 검사와 version/hardware compatibility 모드 존재 | runtime 갱신 시 엔진 호환성 조건 명시 | 다른 runtime으로 상태 이전은 미확인 | 호환성 모드는 tactic 선택·성능에 영향을 줄 수 있음 |
| [Gavel §3.1, §6–7](https://www.usenix.org/system/files/osdi20-narayanan_deepak.pdf) | 실행 불가 조합 제외 가능; 구체적 변환은 앱 구현에 의존 | 새 모델 버전 교체는 미확인 | 앱 checkpoint API | lease renewal·checkpoint overhead 평가 |
| [Pollux §4.2](https://www.usenix.org/system/files/osdi21-qiao.pdf) | 이종 runtime 변환은 미확인 | 학습 진행과 배치 적응 | checkpoint-restart | 재배정 지연 penalty 명시 |
| [Trident §4.4, §6–8.1](https://arxiv.org/html/2603.02075v1) | CPU+Ascend NPU 실험; cross-vendor 변환 미확인 | model refresh는 throughput 추정기 재학습; DNN weight 버전 교체와 다름 | 현재·후보 설정과 rolling 전환 상태 | cold-start discount·startup/shutdown penalty |
| [JointDNN §II-B](https://arxiv.org/html/1801.08618) | 연산 실행 backend 계약은 별도 확인 필요 | 온라인 학습의 갱신 weight 다운로드 | 중간 텐서/학습 그래프 비용 | 계산·통신·weight 다운로드 |
| [INFaaS §3–5](https://www.usenix.org/system/files/atc21-romero.pdf) | 여러 backend용 variant 생성·프로파일 | 등록 모델 변형의 선택·교체 | loaded/inactive/interfered/overloaded | 로딩+하드웨어 비용을 scaling 판단에 반영 |
| [Clockwork §4–5](https://www.usenix.org/system/files/osdi20-gujarati.pdf) | 지정 runtime의 모델 실행 | 운영 버전 rollout은 미확인 | 모델 상주·요청·메모리 상태 | LOAD/INFER 시간에 기반한 일정 |
| [ServerlessLLM §5–6](https://www.usenix.org/system/files/osdi24-fu.pdf) | 이종 벤더간 변환은 미확인 | 모델 loading 대상 선택; semantic version 교체 미확인 | token 이전·KV 재계산 | loading/migration 시간 모델 |
| [Llumnix §4.2](https://www.usenix.org/system/files/osdi24-sun-biao.pdf) | 다른 runtime/버전 간 KV 호환성은 미확인 | 다른 가중치 버전으로 진행 중 요청 변환은 미확인 | KV cache와 요청 live migration | downtime·복사 overhead 평가 |
| [Universal Checkpointing §5](https://www.usenix.org/system/files/atc25-lian.pdf) | 병렬화·하드웨어 구성과 checkpoint 분리 | 학습 weight/optimizer 상태; 운영 버전 변경과 다름 | atomic model/optimizer checkpoint | 병렬 변환·중복 loading 회피 |
| [SageMaker guardrails](https://docs.aws.amazon.com/sagemaker/latest/dg/deployment-guardrails.html) | 제공되는 endpoint 배포 조건 확인 필요 | 배포 버전 traffic shift·rollback | GPU 내부 상태 변환 미확인 | 안전한 전환 메커니즘; 범용 총비용 최적화로 단정 불가 |

## 5. 이미 다뤄진 부분과 주장하면 안 되는 신규성

1. **서로 다른 장치의 성능을 보고 배치한다**: Gavel, StarPU 등과 겹친다. 장치 종류를 더 늘렸다는 사실만으로 기여를 세우기 어렵다. [Gavel](https://www.usenix.org/system/files/osdi20-narayanan_deepak.pdf), [StarPU](https://starpu.gitlabpages.inria.fr/features.html)
2. **그래프/GNN/RL로 스케줄링한다**: Decima, Alpa 등의 목적·행동 단위와 무엇이 다른지 필요하다. Decima는 일반 이종 가속기 이식 시스템의 직접 대체재는 아니지만 학습 기반 정책 자체의 신규성을 배제하는 선행 근거다. [Decima](https://web.mit.edu/decima/content/sigcomm-2019.pdf), [Alpa](https://www.usenix.org/system/files/osdi22-zheng-lianmin.pdf)
3. **전환 비용과 상태를 처음 고려한다**: Gandiva, Pollux, INFaaS, ServerlessLLM, Llumnix가 반례다. 비용의 종류·시간 척도·추정 오차·호환성 계약의 차이로 논점을 좁혀야 한다. [Gandiva](https://www.usenix.org/system/files/osdi18-xiao.pdf), [Pollux](https://www.usenix.org/system/files/osdi21-qiao.pdf), [INFaaS](https://www.usenix.org/system/files/atc21-romero.pdf), [ServerlessLLM](https://www.usenix.org/system/files/osdi24-fu.pdf), [Llumnix](https://www.usenix.org/system/files/osdi24-sun-biao.pdf)
4. **모델 포맷/체크포인트를 통일하면 된다**: EP capability, compiled engine 제약, 병렬화별 checkpoint 변환이 이미 별도 문제로 다뤄진다. 새 방법은 이 해결책을 적용한 뒤의 실패까지 보여야 한다. [ONNX Runtime](https://onnxruntime.ai/docs/reference/high-level-design.html), [TensorRT](https://docs.nvidia.com/deeplearning/tensorrt/latest/inference-library/engine-compatibility.html), [Universal Checkpointing](https://www.usenix.org/system/files/atc25-lian.pdf)
5. **모델 업데이트 운영이 없다**: 상용 serving의 canary·rolling·rollback이 반례다. 운영 트래픽 전환과 장치 내부 진행 상태의 재사용은 다른 계약이다. [SageMaker guardrails](https://docs.aws.amazon.com/sagemaker/latest/dg/deployment-guardrails.html)

## 6. 이전 범위의 검증 질문과 재현 시나리오

현재 실험은 [워크플로 배치 계획](workflow-placement-experiment-plan.md)의 E0–E4를 우선한다. 아래 Q1–Q5는 필요할 때 가져올 배경 질문이며 모두 구현할 실험 목록이 아니다.

아래는 **실험 제안**이며, 성능 저하가 관측됐거나 기존 연구가 해결하지 못한다고 확정한 결과가 아니다.

| 검증 질문 | 통제할 변화 | 기록할 관측·실패 | 먼저 적용할 기존 대응 |
|---|---|---|---|
| Q1. 실행 가능하다고 판단한 배치가 실제로도 실행·SLO를 만족하는가? | 장치/runtime/driver, operator·dtype·shape, engine 옵션 | compile/load 성공률, fallback subgraph·복사, 지연 오차 | ORT capability/fallback, TensorRT 호환성 모드와 장치별 사전 빌드. [근거](https://onnxruntime.ai/docs/reference/high-level-design.html), [엔진 조건](https://docs.nvidia.com/deeplearning/tensorrt/latest/inference-library/engine-compatibility.html) |
| Q2. 모델 업데이트가 가용 배치·측정 profile을 얼마나 바꾸는가? | 같은 모델의 weight-only, shape/연산자 변경, adapter 변경을 분리 | profile 재사용 오차, 재컴파일·검증 시간, rollout 중 SLO·rollback | 버전 고정·사전 프로파일, blue/green·canary. [운영 근거](https://docs.aws.amazon.com/sagemaker/latest/dg/deployment-guardrails.html) |
| Q3. 상태를 직접 이동할지 재계산할지 선택해야 하는 경계는 어디인가? | 학습 optimizer state와 추론 KV를 별도 workload로 설정; 상태 크기·네트워크·배치 변화 | downtime, transfer/recompute 시간, 완료율·수치 일관성 | checkpoint-restart/UCP, Llumnix식 복사, ServerlessLLM식 token 재계산. [UCP](https://www.usenix.org/system/files/atc25-lian.pdf), [Llumnix](https://www.usenix.org/system/files/osdi24-sun-biao.pdf), [ServerlessLLM](https://www.usenix.org/system/files/osdi24-fu.pdf) |
| Q4. 정상 실행 이익이 전체 관측 구간에서도 남는가? | 부하 변화 지속시간, warm/cold 목적지, 관측 horizon | 배포부터 종료까지 비용·goodput·SLO 손실과 전환 횟수 | Pollux식 재배정 penalty, INFaaS식 loading cost, 단순 hysteresis. [Pollux](https://www.usenix.org/system/files/osdi21-qiao.pdf), [INFaaS](https://www.usenix.org/system/files/atc21-romero.pdf) |
| Q5. 복잡한 정책보다 작은 운영 수정으로 해결되는가? | 자원 동일 조건에서 profile 갱신·warm pool·최소 체류시간을 각각 추가 | 추가 정책의 한계 이익과 controller overhead | 기존 정책의 설정·후처리 개선을 먼저 비교; Gavel lease renewal 같은 단순 장치도 포함. [근거](https://www.usenix.org/system/files/osdi20-narayanan_deepak.pdf) |

호환성 조건이 성능 profile을 무효화하고, 그 결과 잘못된 전환으로 QoS 또는 전체 비용이 악화되는 **최소 사례**를 우선 찾는 것이 본 조사에서 제안하는 다음 단계다. 문제의 원인이 연산자 지원, artifact cache, 상태 형식, 전환시간 추정 중 무엇인지 한 가지씩 분리한다. 여러 제약을 동시에 투입해 기존 방법을 실패시키는 것은 충분한 기여 근거가 아니다.

## 7. 이전 범위의 비교 기준선과 평가 계약

현재 필수 비교군은 kube-scheduler·Koordinator·기존 DAG 최적화·DRL이며 Task별 fastest-feasible과 기존 DAG 재최적화·단순 보완을 대조군으로 둔다. 아래 workload별 제안이 현재 첫 실험의 범위를 확대하지 않는다.

모든 논문을 한 실험에 재현할 필요는 없다. 먼저 학습·일반 DNN 추론·상태 있는 LLM 추론 중 하나를 고르고 해당 계열을 비교한다. 아래 기준선 선택은 조사자의 **실험 설계 권고**다.

| 계층 | 권장 기준선 | 비교에서 확인할 것 |
|---|---|---|
| 공통 운영 | 전환하지 않는 고정 배치; fastest-feasible 배치; threshold+hysteresis; measured-cost greedy | 새로운 정책이 없어도 해소되는 문제인지 |
| 자원 할당 | Kubernetes device/DRA+Kueue를 배포 기반으로 사용 | 입장 제어·장치 선택과 응용 실행 정책의 책임을 분리. [DRA](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/), [Kueue](https://kueue.sigs.k8s.io/docs/concepts/cluster_queue/) |
| 학습 | Gavel류 성능 인지 배정 + Pollux류 재배정 penalty; 필요 시 UCP/checkpoint-restart | 자원 선택 이점과 상태 변환/재시작 이점을 분리. [Gavel](https://www.usenix.org/system/files/osdi20-narayanan_deepak.pdf), [Pollux](https://www.usenix.org/system/files/osdi21-qiao.pdf), [UCP](https://www.usenix.org/system/files/atc25-lian.pdf) |
| 멀티모달 파이프라인 | Trident류 joint placement/configuration-transition 정책 | 고정 자원·네트워크·cold start를 함께 고려한 최근 비교 대상. [Trident](https://arxiv.org/html/2603.02075v1) |
| 일반 DNN 추론 | INFaaS류 variant/loading-aware 정책, Clockwork류 residence-aware serving | 가속기 선택·상주 모델·cold start를 이미 아는 정책을 넘는지. [INFaaS](https://www.usenix.org/system/files/atc21-romero.pdf), [Clockwork](https://www.usenix.org/system/files/osdi20-gujarati.pdf) |
| 상태 있는 LLM | ServerlessLLM류 startup/migration-aware 정책, Llumnix류 request migration | 이미 상태·이전 비용을 고려한 비교에서 추가 이득이 남는지. [ServerlessLLM](https://www.usenix.org/system/files/osdi24-fu.pdf), [Llumnix](https://www.usenix.org/system/files/osdi24-sun-biao.pdf) |
| 능력 상한·분해 | 미래를 아는 offline oracle; 완전한 호환성 oracle; 실측 전환시간 oracle | 실행 불가능한 상한임을 명시하고 예측·최적화·runtime의 손실을 분리 |

논문 코드 그대로 실행한 재현과 아이디어를 동일 testbed에 옮긴 구현을 구분한다. 바뀐 workload와 장치에서 원 논문과 동일 성능을 기대하거나, 미지원 기능을 임의로 제거한 약한 baseline을 원 시스템 이름으로 제시하면 안 된다.

실험 전에 장치·메모리·네트워크·runtime/driver·compiler·model hash·operator/dtype/shape·상태 형식·cache/warm 상태·전환 trigger와 전체 관측 구간을 고정한다. 배포·compile·load·state transfer/recompute·downtime·검증·controller 계산 시간을 항목별로 측정하고, 정상 실행 지표와 전체 기간 goodput/SLO 위반/완료시간/비용을 나란히 보고한다. 반복 수·불확실성 보고 방법과 실질적 개선 기준은 실제 실험 설계 때 정한다.

## 8. 남은 탐색과 한계

- 다중 벤더 지원이 곧 runtime/state portability라는 뜻은 아니다. StarPU조차 지원 조합 예외를 명시한다. CPU fallback 또는 checkpoint 변환이 가능한 것과 진행 중 kernel/KV를 임의 backend로 옮기는 것은 구분해야 한다. [StarPU 지원표](https://starpu.gitlabpages.inria.fr/features.html), [ORT 아키텍처](https://onnxruntime.ai/docs/reference/high-level-design.html)
- 최신 cross-vendor preprint도 추적해야 한다. **AIvailable**은 CUDA/ROCm 혼합 장치의 Ollama·HAProxy 배포 prototype을 설명하지만 정량 benchmark 미제공을 명시한다. **Joint Training on AMD and NVIDIA GPUs**는 vendor별 통신 adapter와 P2P를 제안하되 실험의 이종성은 pipeline parallelism에 한정하고 DP/TP 그룹은 동종으로 유지한다. **HetGPU**는 portable IR와 cross-GPU 상태 이전을 제안하며, 공식 저장소는 개발 중이고 사용 지침이 작동하지 않을 수 있다고 명시한다. 이 자료들은 범용 호환성의 확정 근거나 본 조사의 성능 비교값으로 사용하지 않았다. 동료심사 상태와 실제 artifact 재현은 추가 확인 대상이다. [AIvailable §4·6](https://arxiv.org/html/2511.11621v1), [Joint Training §2·4.1](https://arxiv.org/html/2602.18007v1), [HetGPU §4·8](https://arxiv.org/html/2506.15993v1), [HetGPU 저장소](https://github.com/Multi-V-VM/hetGPU)
- 런타임 호환성, 새 모델 버전, 진행 상태, 전환 비용을 한 번에 최적화하는 문제가 선행 연구 전체에서 비어 있다는 주장은 아직 성립하지 않는다. 가까운 방법의 후속 논문·artifact·운영 설정을 더 확인하고, 동일 조건에서 재현한 실패로 범위를 좁혀야 한다.

이번에 확정한 것은 문헌 분류와 비교 방향이다. 특정 알고리즘·가속기·runtime 채택, 성능 우위, 미해결 문제의 최종 확정은 하지 않았다.
