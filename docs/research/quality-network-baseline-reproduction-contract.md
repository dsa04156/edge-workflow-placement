# JellyBean·OctopInf 기준선 재현 및 real5 연결 계약

확인일: 2026-10-26. 연구·소스 감사 보고서이며 새 정책 구현이나 클러스터 성능 결과가 아니다. [기존 방법 우선 계획](quality-network-baseline-first-plan.md)과 [직접 선행 대조](quality-network-orchestration-related-work.md)를 구체화한다. 아래 **원문/코드 사실**, **이번 확인 결과**, **연결 제안**을 구분한다.

## 먼저 실행할 기준선

**품질/워크플로 직접 비교는 JellyBean의 공개 Python optimizer부터 시작한다.** 고정 revision의 `optimizer/main.py`와 공개 예제 입력을 그대로 사용하면 추론 서버 없이 정책 결정 자체를 검증할 수 있다. 이 entry는 모델 선택과 워커 배정을 모두 포함한다. 기존 VQA 모델을 실행했다는 사실만으로 JellyBean 정책 재현이 성립하지 않는다. 네트워크/serving 직접 비교는 **OctopInf의 논문 대응 `PPP` 구현**으로 정하고, 배치·복제·Coral의 보존 범위를 따로 적는다. 일반 통신 greedy를 이 이름으로 바꾸지 않는다. [J-readme][J-main][O-readme][O-build]

| 우선순위 | 실행/비교 대상 | 허용할 이름과 완료 조건 |
|---|---|---|
| 1 | JellyBean 원 optimizer + 공개 VQA 예제 | `JellyBean original optimizer replay`; 원 소스·입력·의존성·출력 고정. 우리 장비 성능/품질 증거가 아님 |
| 2 | 같은 optimizer + 우리 사전 프로파일·검증된 전체 구성 품질·정의된 비용 | `JellyBean policy on common executor`; 입력 의미와 worker 용량을 보존해야 함. 정책 코드/목적/제약을 변경하면 아래 adaptation label 추가 |
| 3 | OctopInf `PPP`의 Cwd/Coral 원 결정 경로 | `OctopInf PPP original decision replay` 후 CPU/GPU/NPU adapter; GPU stream 제약을 CPU/NPU에도 그대로 지원한다고 가정하지 않음 |
| 별도 즉시 대조 | 저장소의 Task-fastest, 통신 greedy, 정적 DAG 열거, 관측 재최적화/hysteresis | 일반 기준선. 소규모 비용 모델 검산 및 원인 분리용; JellyBean/OctopInf 재현이나 goodput oracle이 아님 |

이 우선순위는 구현 노력에 대한 **권고**다. 원 JellyBean entry가 가능하다면 먼저 그 실행 결과를 확보하고, 편의를 이유로 임의 정책으로 교체하지 않는다. 현재 소유 장비의 가격이 정의되지 않았으므로 공개 예제 가격을 우리 장비 가격으로 전용하거나 가격을 만들어 넣지 않는다. [J-main][J-inputs]

## 1. 출처와 고정 버전

| 대상 | 고정점 | 확인 근거 |
|---|---|---|
| JellyBean 논문 | arXiv `2205.04713v2`, 2022-08-03 | [원문][J-paper]; 공식 artifact는 `libertyeagle/JellyBean` |
| JellyBean 코드 | `65a1d67817946a4ae67b068eafb49d649bc061bc` | [공식 commit][J-commit], 2022-09-28 `init`; root README의 optimizer 예제와 실제 코드 대조 |
| OctopInf 논문 | arXiv `2502.01277v1`, 2025-02-03 | [원문][O-paper]이 `tungngreen/PipelineScheduler`를 직접 연결 |
| OctopInf 코드 | `ab9bc150f07f8225ba2fd7fd6933dfa4a32eb50a` | [공식 commit][O-commit], 2025-02-02 `Complete README before OctopInf camera-ready`; `OctopInf-PERCOM2025`와 `v1.0.0`이 이 SHA와 일치 |
| 현재 master와의 구분 | 감사 시 master `2ca4d58456780f8a0d1403591a01823b2f53bde6` | GitHub MCP branches/tags/commit 확인 및 [보존 API 응답](../../.scratch/quality-network-baseline-first/research/octopinf-branches.json). 후속 `OctoCross-ICSOC2025`, `CHEIS-SEC2026` 브랜치도 존재하므로 master 전체를 PerCom 논문 코드로 취급하지 않음 |

버전 선택은 branch 이름만의 추정이 아니다. 태그·커밋 시점/메시지·해당 README의 OctopInf 인용과 `SYSTEM_NAME=PPP` 빌드 분기를 함께 확인했다. 다만 해당 공개 artifact가 논문의 모든 실험 산출물을 byte-identical하게 재현한다는 검증은 아니다. [O-commit][O-readme][O-build]

원시 근거는 `.scratch/quality-network-baseline-first/research/`에 보존했다. 논문 PDF/HTML, GitHub tree·branches·tags·commit, 선택한 원 소스와 URL/수집 시각/SHA256 manifest가 있다. **다운로드한 코드·입력 78/78개는 GitHub tree의 Git blob SHA1과 일치**했다. 기존 `.scratch/quality-network-heterogeneous/work/jellybean`은 독립 Git checkout이 아니라 VQA Python 하위 소스이며, 대조 가능한 **154/154개 파일**이 고정 commit의 `VQA/python/` blob과 일치했다. 이 폴더에서 `git rev-parse`하면 scheduler 부모 저장소가 나오므로 그것을 JellyBean HEAD로 읽으면 안 된다. 추가 로컬 파일까지 원본이라고 주장하지 않는다. [검증 원시 결과](../../.scratch/quality-network-baseline-first/research/verification.json)

## 2. JellyBean: 실제 정책과 입력 계약

### 원문과 원 코드의 결정 경로

논문은 workflow 정확도와 입력 처리율을 만족시키면서 연산·통신 **serving cost**를 최소화한다. 모델 조합을 고른 뒤 워커 집합을 배정한다. DAG, upstream 품질의 영향, 계층형 자원과 통신비용을 이미 다루므로 “품질+네트워크+DAG” 자체를 신규 기여로 삼을 수 없다. [J-paper] §3–4[J-main]

| 책임 | 실제 함수/모듈 | 보존할 동작 |
|---|---|---|
| 전체 실행 | `optimizer/main.py::main` | 입력 읽기 → model beam → 각 모델 조합의 worker assignment → compute+communication 최저 비용 선택; 비용이 같으면 기록된 종단 정확도 비교 |
| 품질/모델 선택 | `beam_search.py::MLDataflowGraph.beam_search`, `sample_acc_profile`, `eval_cost` | sink에서 역위상 순서로 품질 요구를 전파; `OperatorAccuracyProfile`의 upstream/output 품질 sample과 variant cost/weight 사용 |
| 전체 구성 품질 | `EndToEndAccuracyProfile`, `lookup_end_to_end_accuracy` | 모델 variant tuple→종단 품질 lookup. 단계 정확도 단순 곱이 아님 |
| 모델별 비용표 선택 | `utils.py::assign_model_variants` | `(node,worker,variant)` latency와 `(u,v,u_variant,v_variant)` payload/정규화 선택. 비-ML 노드는 `default` |
| 워커 배정 | `worker_assignment.py::compute_worker_assignment` → `compute_worker_assignment_multiple_tiers` | 공개 예제의 `multiple-tiers`/`greedy`; 여러 위상 순서·tier pool·beam을 탐색하며 필요한 입력 처리율까지 워커를 할당 |
| 통신 점수 | `communication_cost.py::compute_communication_cost` | traffic rate × message size × link unit cost를 시간 기준으로 환산. `main`은 backward traffic 불허 |
| tier 입력/생성 | `utils.read_inputs`, `tier_partition.partition_workers_into_tiers` | 제공된 partition을 우선 사용; 없으면 선택한 partition 전략 적용 |

표의 근거: [J-main][J-beam][J-utils][J-worker][J-comm][J-tier]. `config.py` 기본값은 `same-tier`/`dp`지만 **공개 예제 config는 `multiple-tiers`/`greedy`, model beam 10, assignment beam 10, topological orders 10, communication scaling 0.1**이다. 기본값 실행을 논문 예제 실행으로 혼동하지 않는다. [J-config][J-example-config]

### 최소 full-entry 입력

아래 파일 schema는 `utils.read_inputs` 및 실제 공개 입력을 기준으로 한다. [J-utils][J-inputs]

| 파일/데이터 | 실제 의미 | real5에 채울 값 |
|---|---|---|
| `logical_graph.csv` | directed edges | 고정 source sentinel과 5개의 계산 Task를 구분한 실제 dependency |
| `workers_specs.csv` | worker name, worker cost | 명시적으로 확보한 비용 계약. 가상 가격 금지 |
| `workers_numbers.csv` (선택) | worker type별 개수 | 물리 용량을 중복 계산하지 않는 독립 worker/slot 수 |
| `workers_partition.json` | tier별 worker 목록 | 실제 topology에 대응한 명시적 tier 정의 |
| `workers_workers_link.csv` | pair별 unit communication **cost** | 화폐/정규화 비용 단위 명시. 전송 ms를 같은 열에 숨겨 넣지 않음 |
| `sources_workers_link.csv` | source별 worker ingress unit cost | 엣지 source 고정. preprocess 위치와 source 위치 구분 |
| `execution_profile.csv` | node,worker,variant, latency **ms** | 실제 실행 가능한 조합의 새 프로파일. 미지원 조합은 유효한 latency처럼 넣지 않음 |
| `message_sizes.csv` | u,v,upstream/downstream variants,size | 실제 wire payload 크기. 원 코드 단위 환산과 일관성 확인 |
| `input_injection_rates.csv` | source별 유입률 | frozen offered trace와 동일한 정해진 입력률 |
| `throughput_normalization_ratios.csv` | edge별 출력/입력 rate 관계 | VQA 1:1 관계와 fan-in 정렬을 명시; detector object fan-out과 혼동하지 않음 |
| `accuracy_requirement.json` | sink,threshold | 현재 서비스 품질 척도와 일치. 공개 예제 `56.0`은 우리 진단 `0.40`과 scale이 다름 |
| `nodes_accuracy_profiles.pkl` | operator sample, cost/weight, 전체 구성 정확도 | 새 calibration 데이터에서 얻은 profile. 공개 VQA 품질을 변경된 ASR/전처리/backend의 품질로 전용하지 않음 |

공개 pickle은 `beam_search`의 dataclass를 참조한다. 실행 시 이 모듈이 동일한 import 이름으로 보여야 한다. 공개 입력의 top-level은 `end_to_end_profile`과 `per_operator_profiles`이고, 전체 구성은 `ExtractImageFeature`·`SpeechRecognition` variant tuple을 사용한다. 이는 pickle opcode inspection 및 [J-beam]의 schema에서 확인했다. 고정 모델 pilot에서는 모델 탐색이 퇴화할 수 있고, 그 경우 quality awareness 이득을 주장하지 않는다.

### 자원·네트워크·동적 상태의 경계

- **하나의 worker는 하나의 operator에만 할당된다.** 반대로 operator 하나는 여러 worker에서 처리율을 나누어 감당할 수 있다. `workers_is_assigned`로 중복 할당을 막고 `1000/latency_ms`로 capacity를 만든다. 출력 내부에는 `(worker, assigned throughput)`이 있지만 `main`의 표시용 JSON은 worker 이름만 남긴다. executor 연결에서는 이 내부 배정률을 보존하거나, 동일 worker→routing rule을 별도로 동결해야 한다. [J-worker][J-main]
- **CPU/GPU/NPU 물리 노드 세 개를 worker 세 개로 간주하면 5계산 Task의 원 제약과 충돌할 수 있다.** 반대로 stage마다 같은 GPU/NPU를 가짜 독립 worker로 복제하면 용량이 늘어난다. 실제 예약 가능한 CPU core/장치 lane별 용량 및 동시성 실측이 있어야 slot 변환이 정당하다. 공유 queue를 허용하는 변경은 resource-model adaptation으로 표시한다. 이 항목은 위 소스 제약에서 도출한 연결 요구다. [J-worker]
- 통신 점수는 같은 tier에서 0, 상위 tier 이동에는 단위 traffic 비용을 계산하며 `main`은 하위 tier로 돌아가는 내부 edge에 무한 비용을 준다. request queue 길이, 링크 대기, deadline 또는 현재 ready 상태를 직접 받는 필드는 이 entry에 없다. 이것은 감사한 함수 signature와 계산 경로의 범위에 한정한 사실이다. [J-main][J-comm][J-worker]
- 원래 가격 목적을 latency/goodput으로 바꾸거나, tier 순서 제약을 없애거나, 실제 같은-tier 전송·queue를 추가하면 **`JellyBean-derived <변경 내용>`**으로 표시한다. “동일 정보”는 같은 snapshot을 제공한다는 뜻이지 모든 원 정책이 모든 필드를 사용해야 한다는 뜻이 아니다.
- `beam_search`는 `nodes_to_assign = self.assign_order` 뒤 `pop(0)`으로 순서를 소모한다. 반복 재최적화는 매번 새 `MLDataflowGraph`를 만들어야 한다. `None` infeasible 결과를 유효 계획으로 바꾸거나 조용히 다른 정책으로 fallback하지 않는다. 전체 종단 품질 lookup과 요구 threshold를 호출 경계에서도 검증한다. [J-beam][J-main]
- 출력 파일 옵션은 `.yaml`에서 JSON dump, `.json`에서 YAML dump를 호출한다. 원 소스를 보존한 smoke는 **stdout JSON**을 저장하고 이 suffix 문제를 회피한다. [J-main]

### 실행 의존성과 라이선스

최소 optimizer는 Python **3.9 이상**의 `graphlib`와 NumPy, SciPy, pandas, NetworkX, scikit-learn, YACS, PyYAML이 필요하다. scikit-learn은 partition을 제공해도 top-level import 때문에 필요하다. 이 목록은 직접 import를 감사한 것으로, upstream이 정확한 버전 lock을 제공했다고 주장하지 않는다. [J-beam][J-utils][J-worker][J-tier][J-config]

원 optimizer의 공식 명령은 repository root에서 다음과 같다. 이는 full original policy entry이며 VQA 가중치나 CUDA를 요구하는 inference 실행 명령이 아니다. [J-readme]

```sh
rtk proxy python optimizer/main.py \
  --inputs VQA/examples/optimizer_inputs/ \
  --config VQA/examples/optimizer_inputs/config.yaml
```

전체 시스템 재현은 별도다. Rust query processor, PyO3/NumPy bindings, Python 모델 라이브러리와 실제 가중치/데이터가 추가로 필요하다. 소스의 실제 경로는 `exeuctor/MLdataflow`이며 README의 `executor` 표기와 다르다. 기존 Torch/Transformers/OpenVINO 모델 실행은 이 Timely 기반 processor 재현을 대신하지 않는다. [J-cargo][J-readme]

JellyBean root는 **MIT License**, copyright 2022 libertyeagle이며 복제/수정본에 copyright와 permission notice를 유지해야 한다. 이것만으로 별도 데이터셋·모델 가중치까지 같은 라이선스라고 확장하지 않는다. [J-license]

## 3. OctopInf: 논문 대응 PPP와 필요한 적응

### 원문과 실제 모듈

논문은 pipeline batch size, device/GPU, instance 수, 실행 시점을 제어한다. 계획 목적의 latency 기반 throughput 식과 실측 **SLO 내 sink 도착 objects/s**를 구분해야 한다. VQA 요청 하나의 최종 답변/s로 바꾸는 것은 workload/metric adaptation이다. [O-paper] §II–IV

| 기능 | 고정 revision의 callable/위치 | 입력과 결과 |
|---|---|---|
| 알고리즘 선택 | `libs/controller/CMakeLists.txt`, `SYSTEM_NAME=PPP` | `controller.cpp`와 `scheduling-ppp.cpp`를 빌드 |
| 전체 round | `Controller::Scheduling()` | KB profile 수집 → Cwd → source-edge shift → timing/merge → `ApplyScheduling` |
| 통계 수집 | `queryingProfiles(TaskHandle*)` | arrival rate/coefficient of variation, device별 batch profile, source/device-pair network profile |
| Cwd | `crossDeviceWorkloadDistributor(TaskHandle*, uint64_t slo)` | 최소 batch/replica 초기화, 가능하면 server 배치, replica 조정, batch 배증과 SLO/cost 검사 |
| edge 이동 | `shiftModelToEdge(...)` | source device에서 downstream 순회, possibleDevices와 I/O 압축비 검사; 소스의 기준은 `inputSize * 0.6 > outputSize` |
| 지연/queue 모델 | `estimateModelLatency`, `estimateModelNetworkLatency`, `calculateQueuingLatency`, `estimatePipelineLatency` | p95 preprocess/infer/postprocess·transfer, 도착률/처리율, upstream 완료를 이용한 예측 |
| Coral | `colocationTemporalScheduling` → `modelColocationTemporalScheduling` → `containerColocationTemporalScheduling` | GPU lane/시간 portion에 instance를 배치; `findFreePortionForInsertion`, `insertUsedGPUPortion` |
| 실행 적용/복제 | `Controller::ApplyScheduling`, `Rescaling`, `ScaleUp`, `ScaleDown`, `incNumReplicas`, `decNumReplicas` | container/routing/시간 설정 갱신과 복제 개수 조절 |

표의 근거: [O-ppp][O-controller][O-header][O-build]. 실제 entry는 독립 Python placement 함수가 아니다. Controller와 pipeline/container/device 상태 및 profile 자료구조가 연결된 C++ 코드이므로, 몇 줄의 greedy 함수를 작성하고 원 코드 재현이라고 부를 수 없다.

### 감사에서 드러난 검증 gate

다음은 **실행으로 입증한 논문 실패가 아니라 코드 이식 전 확인 사항**이다.

1. `queryingProfiles`는 `upstreams.front()`로 가능한 네트워크 쌍을 만들고 `estimateModelNetworkLatency`도 `upstreams[0]`를 참조한다. 반면 `estimatePipelineLatency`는 모든 upstream의 최대 완료를 계산한다. 두 가지 의미가 혼재하므로 실제 image/audio fan-in의 양쪽 payload 전달·동일 request join을 시험해야 한다. 단일 upstream selector를 양쪽 edge에 확장하면 패치를 명시한다. [O-ppp] L67–179, L1312–1357
2. 네트워크 profile 삽입 키는 `pair.first,pair.second`이고 조회에는 `current device, upstream device`가 사용된다. directed link의 송수신 순서가 맞는지 비대칭 네트워크 fixture로 확인해야 한다. 여기서는 이것을 측정된 성능 결함으로 단정하지 않는다. [O-ppp] (`queryingProfiles`/`estimateModelNetworkLatency`)
3. 논문 Cwd 설명은 burstiness 내림차순 탐색이다. 감사한 `crossDeviceWorkloadDistributor`는 제공된 model 목록 순서로 순회하고 이 함수 내부에는 그 sort가 없다. `coeffVar`를 수집하는 것과 탐색에 사용하는 것을 구분한다. **논문 Algorithm 1 이식**과 **공개 PPP 코드 replay**를 별개 결과로 기록하며, 불일치를 조용히 “수정된 원본”으로 숨기지 않는다. [O-paper] §III-B[O-ppp] L1184–1268
4. 원 `Scheduling`은 Cwd에 `task->tk_slo / 2`, edge 이동에는 전체 SLO를 전달한다. 같은 entry를 옮길 때 임의로 이 비율을 제거하지 않는다. 현재 task가 실제 server에 놓일 수 있는지, 하나의 source edge를 따라 이동하는 구조가 CPU/GPU/NPU 후보 전체를 탐색하는지 별도 확인한다. [O-ppp] L208–247
5. `calculateQueuingLatency` 주석은 M/D/1이지만 코드가 계산하는 항과 경계(`rho > 1` sentinel, `rho=1`, arrival=0)를 그대로 검산해야 한다. 특정 분포의 정확한 queue model이라고 주석만으로 보증하지 않는다. measured queue를 이 식과 교체하면 별도 adapted mode다. [O-ppp] L1518–1534
6. 품질 dataset/최종 정답 하한은 감사한 PPP의 명시적 입력 제약에서 확인하지 못했다. 그래서 **외부 동일 품질 적격 후보 집합**을 모든 정책에 제공한다. 이것을 원 OctopInf가 품질 최적화를 수행한다고 해석하지 않는다. [O-header] (`PipelineModel`)[O-ppp]

### backend·빌드·라이선스 범위

공개 README의 현재 inference 구현은 NVIDIA GPU/Jetson 중심이며 TensorRT Inferencer를 다른 backend로 바꾸는 확장 지점을 설명한다. 논문도 OpenVINO/ONNX 연동 가능성을 언급한다. 이것은 **Intel NPU의 해당 VQA 실행을 검증한 증거가 아니며**, 반대로 NPU 지원이 원천적으로 불가능하다는 근거도 아니다. [O-readme][O-paper]

빌드에는 CMake, C++17, CUDA/CUDAToolkit, OpenCV, gRPC/Protobuf, Boost, spdlog, PostgreSQL/libpqxx 등이 연결되고 inference 경로는 TensorRT를 요구한다. 공개 amd64 Dockerfile은 CUDA 11.4.3/cuDNN8/Ubuntu20.04 base, TensorRT 8.4.3.1 archive, gRPC 1.62.0 설치 경로를 갖는다. Jetson Dockerfile은 별도 registry 주소를 base로 사용한다. 단순 Python 함수 smoke와 달리 이 자료만으로 환경 설치/빌드가 완료되었다고 할 수 없다. **이번 감사에서는 빌드·실행하지 않았다.** [O-cmake][O-docker][O-jetson]

CPU/NPU adapter에서는 `torch CPU`, `torch CUDA`, 직접 OpenVINO `NPU` 실행을 서로 다른 후보로 유지한다. NPU에 GPU lane 개수/메모리·CUDA stream 제어를 가정하지 않는다. batch/replica/time scheduling을 실제 지원하는 범위만 노출하고, 나머지는 고정한 restricted control space를 모든 비교군에 동일하게 적용한다. **`OctopInf-Cwd-derived, batch=1, replicas=1, no-Coral`은 full OctopInf가 아니다.** 품질·CPU fallback·artifact hash는 기존 장치 검증 계약을 재사용하되 runtime 변경 후 다시 검증한다. OpenVINO 공식 문서에는 직접 `compile_model(..., "NPU")`와 AUTO/HETERO 모드가 별도로 설명되어 있다. [OpenVINO NPU][OV-npu]; [기존 stage source](../../experiments/quality-network-heterogeneous/stages.py).

고정 commit의 전체 Git tree에서 root 또는 repository-wide `LICENSE` 파일은 확인되지 않았다. 확인된 license-named 파일은 vendored RetinaFace의 `LICENSE.MIT`뿐이다. 이것을 전체 PipelineScheduler의 MIT 라이선스로 확대하지 않는다. 외부 재배포 시 필요한 권리 확인은 별도 항목으로 남긴다. 이는 **해당 revision 파일 목록 감사**의 결과이며 비공개/별도 허가가 없다는 주장이 아니다. [원시 tree](../../.scratch/quality-network-baseline-first/research/octopinf-tree.json)

## 4. 기존 로컬 정책과 real5 연결 계약

### 이미 있는 단순 DAG 선택기

| 파일/함수 | 확인한 의미 | 재사용 경계 |
|---|---|---|
| `experiments/e0/fresh_placement.py::data_greedy`, `dag_enumeration`, `plan_cost` | warm 3-task chain에서 ingress+task+두 edge 합산 및 전수 열거 | 새로운 5-task fan-in에 그대로 적용 불가; 스스로 published reproduction 아님을 명시 |
| `experiments/e0/measured_policy_bridge.py::task_fastest` | 공통 후보 중 task 실행시간 최소 | task-only 대조로 유지. 모델 reference parity를 서비스 정답 품질로 바꾸어 읽지 않음 |
| `experiments/e0/measured_policy_bridge.py::E2Policy` | `fixed`, `warm_dag`, `ready_dag`, `ready_greedy`, `credit_hysteresis`; 과거 dog/H4 계약 | 과거 프로파일/상태 계약. 새 real5의 동적 정책 실험 완료 근거 아님 |
| `experiments/real-topology-live/placement_policies.py::sequential` | ready task별 earliest finish; `ranked=True`는 upward rank를 쓰는 명시적 HEFT-serving adaptation | 원 HEFT 구현 재현 아님; node-wide processor serialization을 하지 않는다는 주석 유지 |
| `experiments/real-topology-profile/prepare.py::cost`, `plan` | fan-in max completion+return 비용, CPU/memory 용량 제약, Cartesian enumeration | no-queue/no-contention critical path proxy. label `ours`가 있어도 이번 작업에서는 기존 일반 DAG 열거로 보고 |

근거: [3-task source](../../experiments/e0/fresh_placement.py), [bridge](../../experiments/e0/measured_policy_bridge.py), [static selectors](../../experiments/real-topology-live/placement_policies.py), [DAG cost/plan](../../experiments/real-topology-profile/prepare.py). 감사 시 hash는 [local-source-hashes.json](../../.scratch/quality-network-baseline-first/research/local-source-hashes.json)에 보존했다. 다른 구현 워커가 수정 중인 파일의 이후 상태를 이 읽기 결과로 인증하지 않는다.

### 공통 입력/출력 경계 — 구현 제안

다음은 위 source를 real5에 연결하기 위한 **계약 제안**이며 새로운 최적화 알고리즘이 아니다.

1. **DAG/입력:** `edge_image -> image_preprocess -> vision -> fusion`, `edge_audio -> audio_preprocess -> speech -> fusion`. 두 source sentinel은 엣지 입력 위치이고 계산 Task가 아니다. `fusion`은 같은 request의 두 완료에 의존한다. 전처리의 실행시간·serialized tensor bytes·전송을 0으로 만들거나 모델 단계에도 중복 포함하지 않는다. [프로젝트 계획](quality-network-baseline-first-plan.md)
2. **후보:** `(task, model/config hash, backend, node, reserved capacity, readiness, derived artifact hash, dataset-quality-evidence)`를 식별자로 한다. preprocessing은 CPU, vision은 실제 통과한 CPU/GPU/Intel NPU, speech/fusion은 실제 검증된 backend만 후보로 넣는다. 특정 GPU/NPU를 반드시 선택하게 강제하지 않는다. 미검증 장비 계열은 전체 catalog에서 사유와 함께 남긴다. [기존 capability audit](quality-network-heterogeneous-capability-audit.md), [프로젝트 계획](quality-network-baseline-first-plan.md)
3. **사전 품질:** full configuration별 실제 ASR 경로 최종 VQA 점수, dataset/split hash, coverage, missing 처리, 평가 수를 기록한다. Qmin과 SLO는 서비스 계약 값이며 정책별로 바꾸지 않는다. backend별 단일 정확도 상수나 단계 정확도 곱을 사용하지 않는다. JellyBean operator profile을 만드는 경우에도 최종 선택 구성은 실제 전체 pipeline quality로 확인한다. [J-beam]; [프로젝트 계획](quality-network-baseline-first-plan.md)
4. **공통 snapshot:** `snapshot_id, observed_at, valid_until, edge source, offered arrival statistics, compatible candidates, execution/batch profiles, payload bytes, directed transfer profiles, queue/active counts, resource budget, readiness/setup costs, quality evidence, cost-unit contract`. 새 상태로 재계획하는 모든 정책에 동일한 관측 시점/지연/갱신 비용을 준다. 원 정책이 쓰지 않는 필드는 unused로 기록한다. 미래 regime·평가 정답은 제공하지 않는다.
5. **계획 출력:** `policy_id, upstream_revision, adapter_hash, snapshot_id, model assignment, task-to-worker/replica assignment, routing fractions, batch sizes, scheduling portions(if any), predicted original objective, feasibility, reason`. 단일 placement 실행기라면 multi-worker 정책을 조용히 첫 worker로 축약하지 않는다. 지원하지 않는 plan은 `unsupported-control-space`로 남기거나 비교 계약을 사전 제한한다. [J-main][J-worker][O-header]
6. **적용:** request마다 plan version을 고정하고 common executor가 같은 admission/queue/deadline/retry 규칙으로 처리한다. 정적, 최신 profile 재최적화, 동일한 hysteresis 보완을 별도 label로 기록한다. 원 JellyBean의 반복 호출이나 OctopInf의 적용 cadence를 바꾸면 관측/전환 비용도 계측한다.
7. **동일성:** 정책별 resource/eligible configuration/trace hashes가 같아야 한다. 원 시스템이 배제한 tier-backward·동일-tier link를 처리하려고 후보를 더 제한한 비교는 `common restricted feasibility`로 구분한다. 우리 전체 후보 범위와 그 제한 범위를 함께 보고하며 원 정책의 scope를 몰래 넓히지 않는다.

## 5. 목적함수·공정성·검증 수용 기준

| 구분 | 원 계약 | 우리 공통 비교에서 유지/변경 |
|---|---|---|
| JellyBean objective | 입력 처리율·정확도 제약 아래 compute+communication serving cost 최소 | 이 목적을 보존한 결정을 request deadline goodput으로 **외부 평가**. 가격 미정이면 real-device original-cost 비교 미완료; 정규화 비용은 별도 adaptation |
| OctopInf metric | SLO 내 object 결과 처리율; batch/replica/temporal control | VQA 최종 답변 1개/요청의 deadline goodput. 분기·재시도는 별도 request로 세지 않음 |
| 품질 | JellyBean의 profile 기반 선택; 감사한 OctopInf에 dataset quality gate 없음 | 동일한 사전 full-config quality gate 제공, 품질 미달 성능은 진단 |
| 계측 | 예측 목적/지연식 | 실제 edge scheduled arrival→최종 응답. 큐/전송/선택/적용 비용 포함, 중첩 RPC 합산 금지 |
| 자원 | 원 worker exclusivity 또는 GPU stream/memory constraints | 같은 실제 quota/장치 cap과 예약. replication 허용량/메모리/준비비용도 공통 제한 |
| 입력 정보 | 원 profile/KB 입력 | 같은 snapshot/갱신 주기/시점, 미래 정보 제외. 공개 예제 profile은 oracle 아님 |

첫 real5 비교를 원 논문 정책 이식 결과로 보고하기 전에 다음 근거가 필요하다.

- 원 entry와 adapter의 **동일 fixture 결정/목적값 대조**. 소스 코드에서 확인한 순서/단위/제약을 보존한다. 실패·infeasible·unsupported는 독립 상태로 남긴다.
- JellyBean: worker capacity 중복 없음, throughput 분배 합계, tier 위반, price/communication 단위, quality scale, 반복 `beam_search` fresh instance, full-config quality gate를 검증한다. 공개 예제 가격은 공개 예제 smoke에만 사용한다.
- OctopInf: 두 upstream fan-in, 비대칭 network pair 방향, 0/과부하 queue 경계, batch/replica/duty cycle/Coral 적용을 검증한다. 원 코드 replay와 논문 알고리즘 해석의 차이를 기록한다.
- CPU/GPU/NPU 실행 장치·fallback 부재·원/파생 artifact hash·전처리 경계·실제 전체 서비스 품질을 검증한다. OpenVINO 문서 확인이나 단일 입력 smoke만으로 이 gate를 통과시키지 않는다.
- 같은 source/입력/절대 offered trace/resource/quality 집단에서 비교한다. 주 지표는 frozen 유입 cohort 중 deadline 내 최종 완료 수/유입구간 길이. 마지막 deadline까지 관측하고 전체 유입 기준 failed/dropped/timed-out/incomplete/quality/coverage를 함께 보고한다. 동일 선택 배치의 결정 replay를 독립 실행 표본으로 세지 않는다. [프로젝트 계획](quality-network-baseline-first-plan.md)

## 6. 실행 근거와 명시적 한계

**원 JellyBean optimizer의 실행 가능성을 확인했다.** 공개 입력 그대로는 `execution_profile.csv` 5행의 빈 다섯 번째 field 때문에 pandas parsing에 실패했다. 별도 입력 복사본에서 마지막 쉼표 하나만 제거하고 숫자·정책 코드를 유지한 실행은 exit 0이었고, 출력 JSON 전체가 공식 README 예제와 일치했다: compute cost `6.242`, communication cost `1.2667783071018048`, profile accuracy `56.59`, ResNet18 + wav2vec2-large-960h-lv60-self 및 같은 worker sets. 이는 **입력 문법 보정 후 원 optimizer replay**이며 새 inference나 정확도 측정이 아니다. 실행 후 원시 파일 78/78 blob hash도 다시 일치했다. [검증 JSON](../../.scratch/quality-network-baseline-first/research/verification.json), [입력 보정 diff](../../.scratch/quality-network-baseline-first/research/csv-normalization.json), [J-readme]

이번 소스 수집·검증 명령, 원 optimizer smoke의 stdout/stderr/종료 상태, 의존성 lock 및 결과는 [연구 실행 보고서](../../.scratch/quality-network-baseline-first/research-report.md)에 기록한다. OctopInf C++ build, 장치 추론, 새 품질 평가, 정책 부하 실험, 성능 우위 검증은 수행하지 않았다. 독립 모델/장비 품질이 아직 부족한 조합은 이 문서로 적격해지지 않는다.

도구: GitHub MCP의 branch/tag/commit/file 조회로 공식 revision을 확인했고, 공개 GitHub raw/API·arXiv로 소스를 보존했다. Context7의 OpenVINO 조회는 `Monthly quota exceeded`를 반환하여 OpenVINO 공식 문서로 fallback했다. 버전별 문서 기능은 실제 설치된 NPU runtime의 실행 증거와 구분한다.

모델 provenance: Orca `worker-show --dispatch ctx_8e412189a4e3`의 live projection은 **provider `codex`, model `gpt-6-astra`**, 해당 terminal preview는 **GPT-6-Astra high**였다. source audit 담당 모델을 교체하지 않았다. 이는 runtime이 보고한 모델 식별이며 모델 가중치 자체의 독립 attestation은 아니다. [보존 receipt](../../.scratch/quality-network-baseline-first/research/orca-worker-provenance.json)

## 고정 1차 출처

[J-paper]: https://arxiv.org/pdf/2205.04713v2
[J-commit]: https://github.com/libertyeagle/JellyBean/commit/65a1d67817946a4ae67b068eafb49d649bc061bc
[J-readme]: https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/README.md
[J-main]: https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/optimizer/main.py
[J-beam]: https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/optimizer/beam_search.py
[J-utils]: https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/optimizer/utils.py
[J-worker]: https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/optimizer/worker_assignment.py
[J-comm]: https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/optimizer/communication_cost.py
[J-tier]: https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/optimizer/tier_partition.py
[J-config]: https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/optimizer/config.py
[J-example-config]: https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/VQA/examples/optimizer_inputs/config.yaml
[J-inputs]: https://github.com/libertyeagle/JellyBean/tree/65a1d67817946a4ae67b068eafb49d649bc061bc/VQA/examples/optimizer_inputs
[J-cargo]: https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/VQA/Cargo.toml
[J-license]: https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/LICENSE
[O-paper]: https://arxiv.org/html/2502.01277v1
[O-commit]: https://github.com/tungngreen/PipelineScheduler/commit/ab9bc150f07f8225ba2fd7fd6933dfa4a32eb50a
[O-readme]: https://github.com/tungngreen/PipelineScheduler/blob/ab9bc150f07f8225ba2fd7fd6933dfa4a32eb50a/README.md
[O-build]: https://github.com/tungngreen/PipelineScheduler/blob/ab9bc150f07f8225ba2fd7fd6933dfa4a32eb50a/libs/controller/CMakeLists.txt
[O-ppp]: https://github.com/tungngreen/PipelineScheduler/blob/ab9bc150f07f8225ba2fd7fd6933dfa4a32eb50a/libs/controller/scheduling-ppp.cpp
[O-controller]: https://github.com/tungngreen/PipelineScheduler/blob/ab9bc150f07f8225ba2fd7fd6933dfa4a32eb50a/libs/controller/controller.cpp
[O-header]: https://github.com/tungngreen/PipelineScheduler/blob/ab9bc150f07f8225ba2fd7fd6933dfa4a32eb50a/libs/controller/controller.h
[O-cmake]: https://github.com/tungngreen/PipelineScheduler/blob/ab9bc150f07f8225ba2fd7fd6933dfa4a32eb50a/CMakeLists.txt
[O-docker]: https://github.com/tungngreen/PipelineScheduler/blob/ab9bc150f07f8225ba2fd7fd6933dfa4a32eb50a/dockerfiles/DOCKERFILE-amd64
[O-jetson]: https://github.com/tungngreen/PipelineScheduler/blob/ab9bc150f07f8225ba2fd7fd6933dfa4a32eb50a/dockerfiles/DOCKERFILE-jetson
[OV-npu]: https://docs.openvino.ai/2025/openvino-workflow/running-inference/inference-devices-and-modes/npu-device.html
