# 품질·네트워크 VQA — 이종 CPU/GPU/NPU 능력 감사 (2026-10-23)

상태: **읽기 전용 인벤토리·준비도 감사.** 구현 변경, 클러스터 쓰기, 호스트 수리, 엣지 kubelet stream(exec/logs 등), secret 조회는 하지 않았다. 새 추론이나 성능 측정도 없다. 작성 주체는 Claude Code `claude-opus-5-5`(claude.ai firstParty 구독, 이 세션 transcript에서 확인)이고, 모든 shell은 `rtk`/`rtk proxy`로 실행했다.

API 관측 시각은 **2026-10-23T07:40:01.99Z – 07:40:02.79Z**이며 한 시점의 관측이다. 원시 결과(header에 명령·시각·returncode)는 `.scratch/quality-network-heterogeneous/raw/`에 있고, 투영 결과는 같은 폴더의 `node-inventory.json`, `projected-pods.json`, `projected-daemonsets.json`이다(생성 스크립트 `inventory.py`). 전체 pod/daemonset dump는 env literal이 들어 있을 수 있어 투영한 뒤 삭제했다. 삭제한 파일의 sha256은 `raw/removed-full-dumps.sha256`에 남겼다.

## 0. 범위 재확인 (원 사용자 범위)

- AGENTS.md와 `problem-definition.md`의 연구 대상은 **이종 엣지 노드**다. 따라서 CPU·GPU·NPU는 모두 후보 집합에 남는다. 다만 **해당 모델·설정의 실제 실행과 품질 검증을 통과해야** 실행 후보가 된다.
- 다른 모델(SqueezeNet 등) smoke의 성공은 VQA 단계 호환성의 근거가 아니다. 반대로 미검증이라는 사실이 "미지원"을 뜻하지도 않는다.
- 직전 VQA 파일럿(`docs/experiments/quality-network-vqa-pilot.md`)이 CPU 3노드(e0/e1/c)로 제한된 것은 첫 파일럿의 운영상 축소였다. 연구 범위를 재정의한 것이 아니다. 이 감사는 그 축소를 되돌려 **관측된 모든 노드와 가속기 계열**을 목록에 유지한다.

## 1. control plane — "스케줄 불가"와 "모델 비호환"의 구분

- 07:40:02Z 기준 `readyz`·`livez`는 통과했다. `lease/kube-scheduler`와 `kube-controller-manager`는 07:40:01Z에 holder `etri-ser0001-CG0MSB_*`로 갱신되었다.
- 그러나 restart 누적이 크다. kube-scheduler 373회(마지막 약 07:30Z), kube-controller-manager 367회다(`raw/kube-system-pods.log`). `raw/events-kube-system.log`에는 apiserver/etcd probe 실패와 scheduler/controller-manager liveness 재시작이 반복 기록되어 있다.
- 직전 live 시도 `qnv-20261023t073121`의 실패는 **스케줄 단계**에서 일어났다. Pod가 Pending 상태로 node=null, conditions가 없었다. 모델·runtime이 비호환이라는 근거가 아니다. 장애 시간대의 관측과 일치할 뿐, 인과는 증명되지 않았다.
- 결론: control plane이 반복적으로 불안정하므로 이후 qualification은 배포 실패와 모델 실패를 별도 상태로 기록해야 한다(§6).

## 2. 노드 × 장치 매트릭스 (07:40Z, API allocatable 기준)

"점유"는 Running/Pending Pod의 장치 request 합계다. "이전 실행 증거"는 이 저장소에 보존된 실제 실행 artifact만 적었다.

| 노드 | Ready | arch / 역할 | CPU / 메모리 alloc | CPU req(m) | 등록 가속기 resource | 점유(Pod) | device plugin | 이 저장소의 이전 실행 증거 |
|---|---|---|---|---|---|---|---|---|
| etri-dev0001-jetorn | True(since 07:27:47Z) | arm64 edge, tegra 5.15.148, Ubuntu 22.04 | 6 / 7.3 GiB | 725 | `nvidia.com/gpu.shared`=2 | 0 | nvidia-jetson-device-plugin-nano | 없음(GPU 실행 없음) |
| etri-dev0002-raspi5 (e1) | True | arm64 edge Pi5 | 4 / 15.7 GiB | 970 | `hailo.ai/h8`=1 | 0 | hailo-device-plugin | CPU: VQA 3단계 raw qualification(이전 파일럿). Hailo 실행 없음 |
| etri-dev0003-raspi5 (e0) | True | arm64 edge Pi5 | 4 / 7.8 GiB | 785 | 없음 | — | — | CPU: VQA 3단계 qualification, SqueezeNet CPU fixture |
| etri-dev0004-tedger | **Unknown**(unreachable taint) | arm64 edge, kernel 4.4.194, Debian 10 | 6 / 3.7 GiB | 955 | `rockchip.com/rk3399pro`=1 | 0 | ra-rockchip-device-plugin(desired 0) | 없음. 현재 스케줄 불가 |
| etri-dev0006-raspi5 | True | arm64 edge Pi5 | 4 / 3.9 GiB | 1720 | `hailo.ai/h8`=1 | **1/1** edgeai-scenario-npu | hailo-device-plugin | 이 저장소에는 없음. 타 프로젝트 `ra-pool-hailo` Job Succeeded 다수(모델 불명) |
| etri-dev0007-jetagx | True(since 06:23:32Z) | arm64 edge, tegra 6.8.12, Ubuntu 24.04 | 12 / 61.3 GiB | 725 | `nvidia.com/gpu.shared`=2 | 0 | nvidia-jetson-device-plugin | 없음 |
| etri-dev0008-raspi5 | True | arm64 edge Pi5 | 4 / 3.9 GiB | 715 | 없음 | — | — | CPU 다중 엣지 센서 실험 |
| etri-dev0009-jetorn | True | arm64 edge, tegra 6.8.12, Ubuntu 24.04 | 6 / 7.2 GiB | 725 | `nvidia.com/gpu.shared`=2 | 0 | nvidia-jetson-device-plugin-orin9 | CPU만(heavy 실험의 YOLO11m/mpnet ONNX CPU EP). GPU 미사용 |
| etri-ser0001-cg0msb | True | amd64 server(control plane) | 24 / 125 GiB | 7555 | `nvidia.com/gpu`=1 | **1/1** edgeai-hawkeye-safety(runtimeClass `ra-nvidia-rtx5060`) | nvidia-device-plugin v0.19.0 | 없음 |
| etri-ser0002-cgnmsb (c) | True | amd64 server | 24 / 30.5 GiB | 2460 | `nvidia.com/gpu`=1, `npu.intel.com/accel`=1 | GPU **1/1** edgeai-scenario-gpu; NPU 0/1 | nvidia-device-plugin, ra-intel-npu-plugin | CPU: VQA 3단계 qualification. **Intel NPU: SqueezeNet1.1 OpenVINO 2026.3.1 직접 NPU compile 통과**(VQA 아님) |
| etri-ser0003-cg0ms0 | True | arm64 server, kernel 6.17 nvidia | 20 / 121.6 GiB | 2935 | `nvidia.com/gpu`=1 | **1/1** edgeai-gpu-inference(runtimeClass `nvidia-spark`) | nvidia-spark-device-plugin | **NVIDIA GB10: SqueezeNet opset13 → ONNX2Torch → torch 2.9.1+cu130 CUDA 통과**(VQA 아님; capability 12.1 vs build 최대 12.0 경고) |
| etri-ser0004-cgnms0 | True | amd64 server | 16 / 31 GiB | 1910 | `mobilint.com/npu`=1 | 0 | mobilint-device-plugin v1.1.1 | 없음 |

- 이전 인벤토리(`docs/experiments/e0-kubeedge.md`, 2026-10-16)의 `etri-dev0005-jetagx`는 현재 노드 목록에 없다. `etri-dev0007-jetagx`(4일), `etri-dev0008-raspi5`, `etri-dev0009-jetorn`은 새로 생겼다.
- dev0001의 Ready는 07:27:47Z부터다. 07:31Z 무렵 `kubectl get nodes`에서는 NotReady였다. 엣지 노드 Ready는 짧은 시간 안에 바뀐다.
- GPU 제품명은 API label에 없다(GPU Feature Discovery label 없음). ser0003=GB10은 e0 실행 로그의 근거다. ser0001의 `rtx5060`은 runtimeClass **이름**일 뿐 제품 증거가 아니다. ser0002 GPU 제품은 미확인이다.
- 엣지 노드(KubeEdge)는 `status.images`를 보고하지 않는다(image_count 0). 따라서 엣지 image 존재 여부는 API로 확인할 수 없다. 서버는 node당 최대 50개만 보고되므로, 목록에 없다고 해서 부재가 증명되지는 않는다.
- DRA(`deviceclasses`/`resourceslices`)는 API에 없다(`raw/dra.log` returncode 1). RuntimeClass는 4개(handler 모두 `nvidia`)다: `ai-scientist-nvidia`, `hairp-lab-nvidia`, `nvidia-spark`, `ra-nvidia-rtx5060`.

## 3. 가속기 계열별 증거와 VQA 단계 호환성

VQA 단계(동결 adapter `experiments/real-topology-live/model_adapter.py`, sha `dad90308…`, JellyBean source commit `65a1d678…`, 현재 전부 CPU FP32·torch 2.8.0+cpu·threads 1):

- **vision**: ResNet18 ImageNet1k v1 trunk(마지막 2층 제거), 448 Resize/CenterCrop, 공간 평균 → 512-d. weight `resnet18-f37072fd.pth` sha `f37072fd…`.
- **speech**: wav2vec2-base-960h CTC, greedy decode, 가변 길이 16 kHz. `model.safetensors` sha `8aa76ab2…`.
- **fusion**: MutanNoAtt-resnet18. skip-thought embedding + BayesianGRU 2400 + MUTAN Tucker fusion, top-5. checkpoint sha `2674d468…`, vocab `de86a359…`. 토큰화는 Python.

로컬 자산(`artifacts/topology-profile-live/load-bundles.json`, `cache-bundles/`)에는 `torch-2.8.0+cpu`·`torchvision-0.23.0`(aarch64·x86_64) wheel과 `.pth`/safetensors 원본만 있다. **VQA 단계의 ONNX·OpenVINO IR·TensorRT engine·HEF·MXQ·RKNN 파생 산출물, CUDA torch wheel, NPU SDK/컴파일러는 로컬에 없다**(`rg --files`로 `*.onnx|*.hef|*.rknn|*.mxq|*.engine|*.plan|*.xml`을 검색한 결과 SqueezeNet ONNX 4개만 있음).

| 계열 / 노드 | resource 등록 | 현재 가용 | 이 저장소의 사실 증거 | VQA vision | VQA speech | VQA fusion | 없는 전제조건 |
|---|---|---|---|---|---|---|---|
| CPU ARM Pi5 (e0 dev0003, e1 dev0002, dev0006, dev0008) | — | 예 | e0/e1: VQA 3단계 60쌍 raw CPU-reference qualification(이전 실행). dev0006/0008: VQA 미실행 | e0/e1 검증, 나머지 미검증 | 동일 | 동일 | 품질 Qmin 미달(§5)은 장치와 무관 |
| CPU x86 서버 (ser0002 c; ser0001/0004 미실행) | — | 예 | c: VQA 3단계 qualification | c 검증 | c 검증 | c 검증 | ser0001/0004 미검증 |
| CPU ARM 서버 ser0003, Jetson CPU | — | 예 | VQA 미실행(dev0009 CPU는 다른 모델) | 미검증 | 미검증 | 미검증 | aarch64 cpu wheel은 있음. 실행·품질 검증 필요 |
| NVIDIA 서버 GPU ser0001(amd64), ser0002(amd64), ser0003(arm64 GB10) | `nvidia.com/gpu`=1 ×3 | **0/3** (모두 edgeai workload가 점유) | ser0003 GB10: SqueezeNet torch cu130 CUDA. VQA 없음 | 미검증. torch CUDA FP32로 같은 graph 실행 가능성 높음(추정) | 미검증 | 미검증 | GPU 자원 확보(타 workload 조정 또는 해제 대기), CUDA torch wheel(x86 cu12x/13x, aarch64 cu130), GB10 capability 경고 확인, runtimeClass 선택 |
| NVIDIA Jetson 엣지 dev0001(Orin, 22.04/tegra5.15), dev0007(AGX, 24.04/tegra6.8), dev0009(Orin, 24.04/tegra6.8) | `nvidia.com/gpu.shared`=2 ×3 (time-slicing replica로 추정, 물리 GPU 2개 아님) | 2/2 ×3 | GPU 실행 증거 없음 | 미검증 | 미검증 | 미검증 | JetPack/L4T와 맞는 torch(또는 TensorRT) wheel·image, 엣지 image pull 경로, **unpatched EdgeCore에서 exec/logs 금지**이므로 HTTP 전용 harness와 receipt 기반 device 증명 |
| Intel NPU ser0002 | `npu.intel.com/accel`=1 | 1/1 | SqueezeNet OpenVINO 2026.3.1 NPU 직접 compile, `EXECUTION_DEVICES=['NPU']`, max abs err 0.0255 vs CPU. image `ra-npu-intel@sha256:725d604c…`(노드에 존재) | 미검증. ResNet18 trunk static 1×3×448×448 ONNX/IR 필요 | 미검증. 가변 길이 → 고정 길이 padding/bucket 필요, NPU는 static shape | 미검증. GRU/embedding/topk는 NPU compile 가능 여부 불명 | VQA 단계 ONNX export + IR, static shape 정책, FP16 내부 연산의 품질 영향 측정 |
| Hailo-8 dev0002(e1), dev0006 | `hailo.ai/h8`=1 ×2 | dev0002 1/1, dev0006 0/1 | 이 저장소에는 없음. 타 프로젝트 `ra-pool-hailo@sha256:7f70d7b9…`·`inference-hailo` 실행 Pod 존재(모델·HEF 불명, 사용 안 함) | 미검증. Dataflow Compiler로 HEF 컴파일·INT8 양자화·calibration 필요 | 미검증. transformer·가변 길이로 컴파일 가능성 낮다고 추정(미확인) | 미검증(GRU) | Hailo DFC·HailoRT 버전 고정, calibration 자료(calibration split만), HEF hash, 엣지 HTTP 전용 harness |
| Rockchip RK3399Pro dev0004 | `rockchip.com/rk3399pro`=1 | **노드 Unknown, 스케줄 불가** | 없음 | 미검증 | 미검증 | 미검증 | 노드 복구(이 작업 범위 밖), RKNN-Toolkit(구형 RK3399Pro용) 변환 |
| Mobilint NPU ser0004 | `mobilint.com/npu`=1 | 1/1 | 없음. plugin `mobilint-device-plugin:v1.1.1`만 확인 | 미검증. qbcompiler로 MXQ 컴파일·양자화 필요 | 미검증 | 미검증 | Mobilint SDK/runtime image, 컴파일러, calibration, MXQ hash |

"가능성 높음/낮음"은 연산자 구성에 근거한 **제안 경로 판단**일 뿐 사실 증거가 아니다. 실행 전까지 모두 미검증이다.

## 4. 자원 공유·정밀도·fallback 제약

- **GPU 자원 단위**: 서버 GPU는 노드당 `nvidia.com/gpu`=1이고 현재 3개 모두 점유 중이다. VQA를 vision·speech·fusion 별도 GPU worker로 나누면 같은 노드에 resource가 3개 필요하다. 가능한 경로는 (a) 한 Pod에 여러 단계를 두고 GPU 1개를 공유, (b) 노드별로 단계를 분산, (c) time-slicing/MPS 설정이다. (c)는 서버에서 관측되지 않았고 운영 설정 변경이 필요하므로 이 작업 범위 밖이다. Jetson의 `gpu.shared`=2는 노드당 2개 worker까지 허용하지만 같은 GPU를 시분할하므로 간섭이 측정 대상에 포함된다.
- **NPU 단일 장치**: Intel NPU·Mobilint·Hailo는 노드당 1개다. 한 단계만 상주시키거나 여러 모델을 한 Pod에서 공유해야 한다. 모델 상주와 전환 비용은 별도로 기록한다.
- **payload**: raw RGB 이미지 0.64–1.23 MB, PCM, vision feature 2,176 B(npy), transcript ≤ 수십 B. 가속기가 vision을 맡으면 raw 이미지 전송 경로는 그대로 남는다.
- **정밀도·품질**: 현재 기준은 CPU FP32다. GPU FP32는 같은 정밀도로 비교할 수 있다. Intel NPU는 내부 FP16일 수 있고, Hailo/Mobilint/RKNN은 INT8 양자화가 필요하다. 각각 **공식 VQA 채점(calibration 20 / evaluation 40)으로 최종 품질을 재검증**해야 하며, CPU-reference 수치 오차와 정답 품질은 분리해 보고한다. 양자화 calibration에는 calibration split만 사용한다.
- **CPU fallback 거부**: 가속기 후보는 실행 장치 증거가 있어야 한다. 예: torch tensor/parameter device `cuda:*`, OpenVINO `EXECUTION_DEVICES=['NPU']`, HailoRT/Mobilint runtime의 device handle. OpenVINO AUTO/HETERO, ORT CPU EP, torch CPU 경로로 성공한 결과는 해당 가속기 실패로 기록한다. 부분 연산자가 CPU에서 실행되는 그래프는 "혼합"으로 따로 분류한다.

## 5. 해결되지 않은 품질 문제

frozen reference-v1 출력을 오프라인으로 공식 VQA 채점하면 calibration 0.515, evaluation **0.355 < Qmin 0.40**이다. 가속기 이전으로 해결되는 문제가 아니다(같은 모델·가중치). 문턱·자료 변경 없이 미해결로 남긴다. 가속기 qualification의 품질 판정은 "CPU FP32 대비 악화 여부"와 "Qmin 충족 여부"를 함께 보고해야 한다.

## 6. 최소 실행 가능한 다음 qualification wave (제안 — 증거 아님)

목표는 CPU+GPU+NPU를 각각 최소 1개 계열 이상 실제 VQA 단계로 검증하되, §2–3의 전체 후보 목록은 그대로 유지하는 것이다. 각 항목의 판정은 "배포 불가 / 스케줄 불가 / 변환 실패 / 실행 실패 / CPU-ref 불일치 / 품질 미달 / 통과"로 분리해 기록한다.

1. **공통 파생 산출물(오프라인, 클러스터 없음)**: vision ResNet18-448 trunk와 wav2vec2(고정 길이 bucket)를 ONNX로 export하고 hash를 원본 weight와 연결한다. 60쌍 reference-v1로 CPU ORT parity를 확인한다. fusion은 torch 유지가 기본이다.
2. **GPU**: (a) 엣지 Jetson 1대(가용 `gpu.shared`, EdgeCore 제약 → HTTP 전용 harness)와 (b) 서버 NVIDIA 1대(점유 해소 필요 → coordinator/소유자 조정)에서 torch CUDA FP32로 vision·speech·fusion을 실행한다. device 증거와 60쌍 parity, 공식 VQA 품질을 확인한다.
3. **NPU**: Intel NPU(ser0002, 가용)에서 OpenVINO로 vision static 448을 먼저, 이어 speech 고정 길이를 직접 NPU compile한다. Mobilint(ser0004, 가용)·Hailo(dev0002, 가용)는 toolchain 확보 여부를 먼저 확인한 뒤 vision trunk INT8 컴파일을 시도한다. calibration은 calibration split만 사용한다.
4. **CPU**: 기존 e0/e1/c 통과 결과를 유지하고, 추가 후보 CPU(ser0003, Jetson CPU 등)는 같은 harness로 선택 검증한다.
5. **비대상(현재 상태 기록)**: dev0004 Rockchip은 노드 Unknown이므로 대기한다. 서버 GPU 3개는 점유 중이라 조정이 필요하다.

필요한 결정·전제조건:
- 서버 GPU 점유 workload(edgeai 3개)와의 자원 조정
- Jetson용 L4T torch/image와 엣지 registry pull
- Hailo DFC·Mobilint qbcompiler 획득·라이선스
- static shape/bucket 정책
- 양자화 품질의 수용 기준(현재 Qmin 0.40 유지)
- control plane 불안정 시 배포 실패를 분리 기록하는 규칙

## 7. 증거 링크

- 원시 API: `.scratch/quality-network-heterogeneous/raw/{nodes,leases-kube-system,readyz,livez,kube-system-pods,runtimeclasses,dra,events-kube-system}.log`, 삭제 hash `raw/removed-full-dumps.sha256`
- 투영: `.scratch/quality-network-heterogeneous/{node-inventory,projected-pods,projected-daemonsets}.json`, 스크립트 `inventory.py`, 보고 `audit-report.md`
- 이전 가속기 실행: `docs/experiments/e0-accelerators.md`, `artifacts/e0-accelerators/{results.json,pods-final.json,inventory.json}`
- 이전 노드 표: `docs/experiments/e0-kubeedge.md`. VQA adapter·reference: `experiments/real-topology-live/model_adapter.py`, `artifacts/topology-profile-live/reference-v1/metadata.json`, `load-bundles.json`
- VQA 파일럿과 live 실패: `docs/experiments/quality-network-vqa-pilot.md` §11, `artifacts/quality-network-vqa/qnv-20261023t073121/`

## 8. 사실 보정 (2026-10-23, 구현 작업 중 추가 확인; 관측 inventory는 변경하지 않음)

- §3 표에 "plugin만 확인"이라고 적은 Mobilint, "모델·HEF 불명"이라고 적은 Hailo에 대해 registry `10.254.192.47:5000`을 읽기 전용으로 조회했다. 다른 프로젝트의 runtime image가 있다: `hailort-validation:4.23.0-*`(arm64), `resource-advisor/hailo`, `mobilint-core-demo:*`(amd64, `qb140` tag), `ra-npu-mobilint`, `ra-npu-rockchip`, `ra-npu-intel`, cu130 arm64 `edgeai/gpu-inference`(`.scratch/quality-network-heterogeneous/raw/registry-10.254.192.47.json`). 이것은 vendor runtime이 존재한다는 증거일 뿐, 이 VQA 모델의 컴파일 산출물이나 실행 증거가 아니다. `192.168.0.56:5000`은 감사 host에서 닿지 않았다.
- compiler 공개 여부를 vendor README로 확인했다(`raw/toolchain-readme-evidence.json`, `raw/toolchain-public-availability.json`).
  - Hailo-8: Dataflow Compiler v3.x가 필요하며 customer access로만 받을 수 있다.
  - Mobilint: qbcompiler는 Download Center 계정을 만들어야 받을 수 있다.
  - RK3399Pro: RKNN-Toolkit 1.7.5가 GitHub에 공개되어 있다.
- §3의 "가능성 높음/낮음" 판단은 이후 로컬 export로 일부 검증되었다(`docs/experiments/quality-network-heterogeneous.md` §3).
  - vision static 448 ONNX와 speech dynamic ONNX는 CPU ORT parity를 통과했다.
  - fusion traced export는 실패했다.
  - 모두 장치 실행 증거는 아니다.
- 추가 보정(같은 날, 구현 작업 중):
  - §3의 "로컬에는 ... 없다"는 `rg --files`(symlink 미추적)로 `artifacts`와 `experiments`만 검색한 결과다. symlink 추적 재검색, 다른 프로젝트 resource-advisor의 기존 vendor 산출물, 권한 때문에 검색하지 못한 부분은 `docs/experiments/quality-network-heterogeneous.md` §7에 정리했다.
  - Mobilint compiler는 "계정 필요"가 아니었다. 공식 tutorial이 공개 Docker Hub image(`mobilint/qbcompiler:1.4-cpu-ubuntu22.04`)를 안내하므로, Mobilint dependency 상태를 compile probe 대상으로 바꾼다(최신 wheel만 계정 필요).
  - "Hailo/Mobilint/RKNN은 INT8 양자화 필요"는 버전별로 다르게 확인된다. Mobilint `mxq_compile`은 공식 문서상 양자화 흐름이다. RKNN 1.7.5 공식 예제는 `do_quantization=True`를 쓰지만 비양자화는 미확인이다. Hailo DFC 문서는 접근할 수 없어 미확인이다.
  - Intel NPU static shape 제약은 OpenVINO 2026.3 문서 L412로 확인했다.
  - speech의 Hailo 컴파일 가능성에 대한 이전 추정("낮다")은 근거가 없으므로 철회하고 미검증으로 둔다.
