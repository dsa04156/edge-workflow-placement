# 이종 CPU/GPU/NPU VQA qualification·실행 시스템 — 구현과 실장비 검증 (2026-10-23)

현재 상태: **CPU·Jetson GPU·Intel NPU 혼합 요청 60/60건 실장비 실행 검증 완료**(`qnh-20261023t100710`, 아래 14절). CUDA 영상·음성·결합도 각 60/60건 성공했다. 최종 평가 품질은 0.355 < 0.40으로 본 goodput 비교는 아직 미완료이며, NPU 음성 단독 probe는 27/60건 뒤 종료됐다. 전체 12개 노드·69개 후보 행을 유지한다. 테스트 88개 통과, coordinator 변경 부분 42개 재검증과 실제 출력 hash·요청·장치·Pod UID 검증을 완료했다. 소유 자원 잔여 0개를 재확인했다.

아래 초기 구현·리뷰·각 실행 기록은 시점별 이력이다. 최초 작성 시점에는 live 미실행이었으며 로컬 CPU 검증만 있었다. 구현·테스트 주체는 Claude Code `claude-opus-5-5`(claude.ai firstParty 구독)다. 셸의 `rtk`/`rtk proxy` 사용 원칙에서 벗어난 일부 읽기·모니터 명령은 각 수정/실행 보고서에 기록했다. 명세는 `.scratch/quality-network-heterogeneous/implementation-spec.md`, 관측 inventory는 [능력 감사](../research/quality-network-heterogeneous-capability-audit.md)를 따른다.

## 1. 범위

관측된 12개 노드와 모든 장치 계열(CPU arm64/amd64, NVIDIA 서버 GPU 3, Jetson GPU 3, Intel NPU, Hailo-8 2, Mobilint, Rockchip)을 catalog에 유지한다. CPU-only 축소나 장치 대체는 없다. 각 row는 다음 다섯 항목을 독립적으로 기록한다.

- availability
- dependency
- execution: `not_attempted | export_failed | compile_failed | deployment_failed | runtime_failed | executed`
- parity: 동결 reference-v1 대비, 60쌍 전부
- quality: 공식 VQA, Qmin 0.40, calibration과 evaluation **모두**

실행 성공, reference parity, 서비스 품질 gate는 서로 다른 판정이다. [설계 문서](../research/quality-network-heterogeneous-design.md)에 따라 이번 qualification은 legacy 경계(분기 안에 전처리 포함)를 의미 보존용으로 유지한다. 그 결과를 전처리·추론 독립 배치의 최종 DAG 실험으로 부르지 않는다. 기존 evaluation 0.355 < 0.40이므로 working accelerator도 quality_failed가 되며 main 실험 profile에 들어가지 않는다.

## 2. 구성 (`experiments/quality-network-heterogeneous/`)

| 파일 | 역할 |
|---|---|
| `stages.py` | 동결 `real-topology-live/model_adapter.py`(sha `33e4f879…`, commit 62a3ccaf)를 provenance 기록과 함께 작게 추출했다. 이 adapter는 reference-v1 생성 adapter `dad90308…`에 raw `.npy` 입력 decode만 더한 것이다. backend는 `cpu-torch` / `cuda-torch` / `openvino-npu`다.<br>CUDA 경로: FP32, TF32 off(matmul, cuDNN), cudnn.benchmark off. parameter와 출력 tensor의 device를 assert하고, synchronize 후 host로 복사한다. 설정은 receipt에 들어간다.<br>NPU 경로: `compile_model(...,'NPU')`이고 `EXECUTION_DEVICES == ['NPU']`가 아니면 오류다. AUTO/HETERO/MULTI/BATCH는 거부한다. 장치가 없으면 `BackendUnavailable`이며 다른 장치로 대체하지 않는다.<br>NPU speech는 per-length exact reshape compile **probe**로, serving이 아니다. NPU fusion은 export가 실패해 시도하지 않는다. |
| `export_onnx.py` | 로컬 export와 ORT CPU parity를 수행하고 결과를 `artifacts/quality-network-heterogeneous/export-<UTC>/`에 쓴다. |
| `worker.py` | HTTP-only worker다. 할당 장치 1단위당 한 프로세스가 여러 단계를 맡고, 단계는 lock으로 순차 실행한다. `/ready`, `/health`, `/run`을 제공하며, receipt에는 backend, 실제 device, node, pod UID, 원본·파생 hash, shape/dtype/정밀도, runtime 버전, 단계별 시간이 들어간다. |
| `bootstrap.py` | Pod 안에서 즉시 HTTP status를 제공한다. 읽기 전용 discovery(ctypes libcuda, device-tree, /dev)를 하고, cache Pod에서 sha가 검증된 asset을 받아 wheel을 offline 설치한 뒤 worker를 load한다. Jetson은 `auto-jetson` recipe를 쓰며, CUDA driver API ≥ 13.0일 때만 cu130을 선택하고 그 외에는 dependency_unavailable로 기록한다. |
| `prepare_deps.py` | 공식 public wheel을 격리된 wheelhouse로 받고 hash를 기록한다. |
| `catalog.py` | 전체 노드×계열×단계 catalog를 JSON/CSV로 만든다. 모르는 resource key가 있으면 거부하고, receipt 없는 executed는 오류로 처리한다. |
| `mobilint_compile.py` | 공개 `docker.io/mobilint/qbcompiler:1.4-cpu-ubuntu22.04`(digest `62b5bc71…`) 안에서 CPU-only Pod로 실행하는 vision MXQ compile probe다. 공식 tutorial API(`make_calib_man`, `mxq_compile`)를 쓰고 calibration split 20장만 사용한다. HTTP로 status와 artifact를 제공한다. 이 host에는 Docker 권한이 없어 **로컬 미실행**이다. |
| `qualify.py` | `plan`은 kubectl 없이 wave, asset manifest, catalog를 쓴다. `execute`는 review release 파일이 없으면 kubectl 호출 전에 rc 2로 거부한다. `analyze`는 offline 분석이다.<br>`execute`의 소유권 처리: create-only UID 기록, `multi-edge-sensors/collect.cleanup` UID-precondition 삭제, 보호 Pod snapshot 전후 비교. stream seam은 엣지 Pod 대상 exec/logs/attach/port-forward/cp를 거부하거나 skip한다. 서버 stream은 fresh GET으로 생성 UID와 예상 노드를 확인한 뒤에만 허용한다. |

## 3. 로컬 결과 (CPU host etri-ser0001, x86_64; 실제 실행)

격리 env는 `.scratch/quality-network-heterogeneous/venv-ref`다. Python 3.11.15에 frozen wheelhouse의 torch 2.8.0+cpu, torchvision 0.23.0+cpu, transformers 4.57.1, numpy 1.26.4를 넣고, PyPI에서 onnx 1.17.0, onnxruntime 1.23.2, openvino 2026.3.1을 추가했다(전체 목록 `venv-ref-freeze.txt`). host GPU는 다른 workload가 할당 중이라 사용하지 않았다.

- **추출 동등성**: `stages` CPU 출력이 legacy adapter와 **bitwise 동일**하다(vision·speech·fusion, calibration 2쌍씩).
- **torchvision-free 전처리**(NPU image용 PIL+numpy): 60개 이미지 전부에서 torchvision과 max abs diff **0.0**이다.
- **export** `export-20261023T075427Z/manifest.json`, opset 17, TorchScript exporter:

| 산출물 | sha256 | I/O | ORT CPU parity (60쌍, 필터 없음) |
|---|---|---|---|
| `vision-static-448.onnx` | `9be0337c…` | image [1,3,448,448] → feature [1,512] | torch 대비·reference-v1 대비 max abs 3.2e-6, 60/60 reference rule 통과. OpenVINO 2026.3.1 `read_model` ok(compile 아님, NPU 증거 아님) |
| `speech-dynamic.onnx` | `d19e05cd…` | input_values [1,samples] → logits [1,frames,32] | transcript 60/60 동일(41개 서로 다른 길이), max abs logit 0.0066 |
| `fusion-dynamic.onnx` | `b22f7035…` | feature [1,512], ids [1,tokens] | **실패**: trace가 skip-thought GRU 길이를 상수화해 다른 token 길이에서 ORT ScatterND index 오류가 난다. fusion serving은 torch(strict load)로 유지한다 |

- **in-Pod 경로 로컬 smoke** `.scratch/quality-network-heterogeneous/logs/local-bootstrap-smoke-1.log`: `bootstrap.py`가 로컬 HTTP cache에서 manifest를 받아 sha를 검증하고, offline pip 설치(13.5 s) 후 worker를 load했다. `/run`으로 vision·speech·fusion을 각 2쌍씩 실행해 6/6 parity 통과했다. vision 출력 sha는 reference feature sha와 동일하다(`9bc71340…`). CPU recipe만 사용했으며 클러스터·가속기 증거가 아니다.
- **테스트** `.scratch/quality-network-heterogeneous/logs/test-all-final-3.log`: `python -m unittest -v test_qualify test_stages`, **returncode 0, 32 OK**(Mobilint compile probe manifest 테스트 추가; 이전 `test-all-final-2.log` 31 OK).
  - test_stages 9개는 실제 모델·파일을 사용한다.
  - test_qualify 22개 중 receipt/stage mock은 plumbing 증거다. 검증 항목: catalog 12노드·전 계열 유지, 관측 차단 사유, 미분류 resource 거부, receipt 없는 executed 거부, resource 이름·단위를 관측 allocatable과 일치, 서버 GPU 미사용, privileged/host namespace/hostPath 없음, review release 없이 kubectl 0회, 엣지 stream 거부, 미소유 서버 Pod stream 거부, CPU receipt의 가속기 판정 금지, UID/노드 불일치, 요청 오류 보존, NPU `EXECUTION_DEVICES` 혼합 거부, reference 답의 Qmin 미달(0.515/0.355), main plan 제외.
  - 한 프로세스에서 두 suite를 함께 돌릴 때 `real-topology-profile/profile.py`가 stdlib `profile`을 가리는 문제가 있었다(`test-all-final.log`, 4 errors). `qualify.py`가 stdlib를 먼저 import하도록 고쳤다. 이 문제는 live 수집기에도 영향을 주었을 것이다.

## 4. 준비된 의존성 (공식 출처, hash 기록, 설치·호환성 검증은 live Pod에서)

| recipe | 위치 | 내용 |
|---|---|---|
| `cu130-aarch64-cp311` | `deps-20261023T080028Z` (48 files, 2.90 GB, complete) | download.pytorch.org cu130 `torch-2.9.1+cu130`(sha `fd6c7d29…`, e0 GB10에서 쓴 것과 동일)·`torchvision-0.24.1`, CUDA 13 runtime wheel, PyPI 의존성. 첫 시도 2회는 pip `--platform` tag 정확 일치 때문에 incomplete였고 보존한다(`deps-20261023T075807Z`, `deps-20261023T075919Z/cu130-*`) |
| `jp61-aarch64-cp310` | `deps-20261023T075919Z` (29 files, 0.87 GB, complete) | NVIDIA redist `torch-2.5.0a0+872d972e41.nv24.08`(JetPack 6.1, sha `6f75fd2d…`), `torchvision-0.20.0`(--no-deps), cp310 의존성. **L4T base image가 별도로 필요해 현재 wave에는 포함하지 않았다** |
| `npu-x86_64-cp312` | `deps-20261023T075919Z` | pillow 11.3.0 cp312(sha `676b2815…`). Intel NPU image(OpenVINO 2026.3.1, numpy 1.26.4)에 추가 |
| `cpu-*-cp311` | 기존 frozen `artifacts/topology-profile-live/wheelhouse/{x86_64,aarch64}` | torch 2.8.0+cpu reference runtime |

## 5. live wave (독립 리뷰와 coordinator release 후에만)

> **2026-10-23 fix-r2 이후 현행 plan은 §9의 `plan-20261023T090037Z`이다(§8의 `084059Z`는 대체됨).** 아래 표와 명령은 이전 plan의 기록이며, 이전 plan들은 대체되었지만 보존한다.

plan `artifacts/quality-network-heterogeneous/plan-20261023T081715Z/`의 asset manifest sha는 `a9bde765f3a927827bf191abe2bfff269f6821f6bd4e7e97fe322cdf4704ab0b`(4.31 GB, calibration 이미지 20장 group 포함)이다. 이전 plan `plan-20261023T080527Z`(`fe95d652…`)는 대체되었으며 보존한다. 생성 Pod는 cache(ser0002 CPU)와 다음 9개다.

| key | 노드 | backend / recipe | 요청 resource | 단계 |
|---|---|---|---|---|
| cpu-c | ser0002 | cpu-torch / cpu-x86_64-cp311 | — | vision, speech, fusion |
| npu-c | ser0002 | openvino-npu / npu-x86_64-cp312, image `ra-npu-intel@725d604c` | `npu.intel.com/accel: 1` | vision(serving 후보), speech(exact-length compile probe) |
| cpu-e0 | dev0003 | cpu-torch / cpu-aarch64-cp311 | — | 3단계 |
| jgpu-dev0001 / dev0007 / dev0009 | 각 Jetson | cuda-torch / auto-jetson | `nvidia.com/gpu.shared: 1` | 3단계를 한 프로세스에서 |
| mblt-compile | ser0004 | qbcompiler 1.4 CPU compile probe(docker.io 이미지 8.7 GB pull 필요) | — (장치 없음) | vision MXQ compile만 함(실행 아님) |
| hailo-dev0002, mblt-ser0004 | 각 노드 | allocation probe | `hailo.ai/h8: 1`, `mobilint.com/npu: 1` | 장치 할당과 device node(`/dev/aries*` 등) 확인만 함(모델 실행 아님) |

생성하지 않는 대상(catalog에 유지):
- 서버 GPU 3개: edgeai workload가 점유 중이며, 다른 workload의 장치를 빼앗지 않는다.
- dev0006 Hailo: 점유 중.
- dev0004 Rockchip: 노드 Unknown.

end-to-end 조합:
- `cpu-c/cpu-c/cpu-c`, `cpu-e0/cpu-e0/cpu-e0`, `npu-c/cpu-c/cpu-c`
- 각 `jgpu-*` 단독 조합
- 수집기가 단계 출력을 relay하며, qualification용이고 latency 실험이 아니다.

실행 명령(저장소 루트, 수집기는 transformers 때문에 venv-ref 사용):

```bash
# release 파일에는 'RELEASE'와 위 assets_manifest_sha256이 함께 있어야 한다. 없으면 kubectl 호출 전에 rc 2로 거부한다.
rtk proxy .scratch/quality-network-heterogeneous/venv-ref/bin/python experiments/quality-network-heterogeneous/qualify.py execute \
  --plan artifacts/quality-network-heterogeneous/plan-20261023T081715Z --run-id qnh-<UTC yyyymmddthhmmss> --review-release <coordinator release file>
rtk proxy .scratch/quality-network-heterogeneous/venv-ref/bin/python experiments/quality-network-heterogeneous/qualify.py analyze \
  --run artifacts/quality-network-heterogeneous/qnh-<UTC>
```

재현 명령(이미 실행함):
```bash
MODEL_ROOT=$PWD/artifacts/topology-profile-live/model-assets JELLYBEAN_SOURCE=$PWD/.scratch/quality-network-heterogeneous/work/jellybean \
  rtk proxy .scratch/quality-network-heterogeneous/venv-ref/bin/python experiments/quality-network-heterogeneous/export_onnx.py
rtk proxy python3 experiments/quality-network-heterogeneous/prepare_deps.py [--recipes cu130-aarch64-cp311]
rtk proxy .scratch/quality-network-heterogeneous/venv-ref/bin/python experiments/quality-network-heterogeneous/qualify.py plan \
  --export artifacts/quality-network-heterogeneous/export-20261023T075427Z \
  --deps artifacts/quality-network-heterogeneous/deps-20261023T075919Z --deps artifacts/quality-network-heterogeneous/deps-20261023T080028Z
cd experiments/quality-network-heterogeneous && rtk proxy ../../.scratch/quality-network-heterogeneous/venv-ref/bin/python -m unittest -v test_qualify test_stages
```
(`.scratch/quality-network-heterogeneous/work/jellybean`은 동결 `cache-bundles/fusion.tar`(sha `fe30e79d…`)에서 추출한 것이다.)

## 6. 계열별 차단 사유와 미확인 사항 (완료 아님)

- **Jetson 3대**: L4T/driver를 노드 이름으로 추정하지 않는다. bootstrap discovery가 libcuda driver 버전, device-tree, /dev를 HTTP로 보고한다.
  - cu130 recipe는 driver ≥ 13.0에서만 시도한다.
  - JetPack 6 계열(dev0001 kernel 5.15-tegra로 짐작되지만 미확인)은 L4T base image(예: NVIDIA `l4t-jetpack` r36)가 필요하다. 이미지 선택과 pull은 coordinator 결정 대상이다.
  - 엣지 containerd가 libcuda를 container에 주입하는지(nvidia runtime 기본 설정 여부)도 미확인이다. 주입하지 않으면 dependency_unavailable로 기록하며 CPU로 대체하지 않는다.
- **서버 NVIDIA GPU 3대**: 모두 점유 중이며 pending 상태다. ser0003(GB10)은 cu130 recipe로 해제를 기다린다. x86 서버 GPU용 cu130 x86_64 wheel은 아직 내려받지 않았다.
- **Intel NPU**: image는 노드에 cache되어 있지만 192.168.0.56 registry는 이 host에서 닿지 않는다(evict되면 deployment_failed가 될 수 있다).
  - vision은 static 448을 NPU에 직접 compile한다.
  - speech는 41개 길이마다 compile하는 probe다(비용 기록).
  - fusion은 export_failed다.
- **Hailo-8**: v2.x model zoo가 Dataflow Compiler 3.33.0을 요구하며, 이는 Hailo customer access 또는 문의로만 받을 수 있다. 계정을 만들거나 라이선스에 동의하지 않았으므로 dependency_unavailable이다.
  - 다른 프로젝트의 HailoRT 4.23.0 image에는 `hailo_sdk_client`가 없다(prior probe).
  - 이번 wave는 allocation probe만 한다.
- **Mobilint**: 공식 tutorial이 Docker Hub의 공개 compiler image를 안내한다. 1.4 image가 이미 게시되어 있다(`raw/mobilint-qbcompiler-dockerhub*.json`).
  - vision compile probe Pod를 wave에 포함했다. 로컬 미실행이며, server의 docker.io pull 가능 여부와 qbcompiler 실제 포함 여부는 미확인이다.
  - target(`aries-rb` 기본값)은 allocation probe의 device node로 확인해야 한다.
  - MXQ가 나오면 qbruntime 1.4.0 image로 실행하는 단계는 다음 wave다(runtime API 미구현).
- **Rockchip RK3399Pro**: RKNN-Toolkit 1.7.5는 공개되어 있지만 노드가 Unknown이다. offline 변환도 시도하지 않았다.
- **품질**: 동결 reference 답의 공식 점수는 calibration 0.515, evaluation 0.355로 Qmin 0.40 미달이며 해결되지 않았다. 문턱과 자료는 바꾸지 않았다.
- **수치 정밀도**: CUDA 경로는 torch 2.9.1이고 CPU reference는 2.8.0이다. parity 판정은 동결 규칙(feature rtol 1e-4/atol 1e-5, transcript exact, answer_ids exact, score atol 1e-4)을 따른다.

## 7. 1차 출처 조사 결과 (2026-10-23, URL과 sha256은 `.scratch/quality-network-heterogeneous/raw/primary-sources-*.json`, `mobilint-qbcompiler-dockerhub*.json`)

| 주제 | 1차 출처 | 확인 내용 | 이 시스템에 반영한 것 |
|---|---|---|---|
| PyTorch cu130 aarch64 아키텍처 | https://github.com/pytorch/pytorch/blob/v2.9.1/.ci/aarch64_linux/aarch64_ci_build.sh | CUDA 13.0 aarch64 wheel의 `TORCH_CUDA_ARCH_LIST="8.0;9.0;10.0;11.0;12.0+PTX"`. x86 `.ci/manywheel/build_cuda.sh`는 `7.5;8.0;8.6;9.0;10.0;12.0+PTX` | Orin(8.7)은 8.0 cubin과, Thor(11.0)는 11.0과 같은 major의 상위 minor 규칙으로 호환될 수 있다. 다만 **CUDA 13 driver가 필요하다**. bootstrap은 driver ≥ 13.0일 때만 cu130을 쓴다. 실제 동작은 live discovery와 실행으로 판정한다 |
| JetPack 6 torch | https://developer.download.nvidia.com/compute/redist/jp/v61/pytorch/ | `torch-2.5.0a0+872d972e41.nv24.08` cp310 aarch64. index에는 v60/v61까지만 있음 | jp61 recipe를 준비했다. L4T base image 결정이 남아 있다 |
| JetPack 버전 목록 | https://developer.nvidia.com/embedded/jetpack-archive | archive에 JetPack 6.2.1까지 나열됨 | kernel→JetPack 대응은 1차 출처로 확정하지 못했다. 노드 이름이나 kernel로 추정하지 않는다 |
| OpenVINO NPU (2026.3) | https://github.com/openvinotoolkit/openvino/blob/releases/2026/3/docs/articles_en/openvino-workflow/running-inference/inference-devices-and-modes/npu-device.rst | L412 "only models with static shapes are supported on NPU". L113–116: 내부 primitive 정밀도, "Computation precision for the HW is FP16", 양자화 모델은 U8/INT8 혼합 선택 가능. L159/293–308: `compiler_type` 조회 가능(driver/plugin compiler 선택이며 CPU fallback이 아님). L172: `execution_devices` | vision static 448, speech는 길이별 exact reshape compile probe. receipt에 `NPU_COMPILER_TYPE`과 `EXECUTION_DEVICES`를 기록하고, 정밀도는 "HW FP16"으로 표기한다(FP32 장치 비교라고 하지 않는다) |
| Mobilint qbcompiler | https://github.com/mobilint/mblt-sdk-tutorial/blob/master/compilation/README.md (blob 9734db29), `compilation/image_classification/model_compile.py`, `_guides/01_about_quantization_config.md` | Docker Hub에 `mobilint/qbcompiler:{1.2,1.3,1.4}-{cpu,cuda12.8.1}-ubuntu22.04` 게시. image 안의 torch 2.7.1을 업그레이드하지 말 것. 최신 wheel은 Download Center 계정이 필요. compile에 NPU device 불필요. `mxq_compile`은 calibration 기반 양자화 패키지 | 1.4-cpu image를 digest(`62b5bc71…`, 8.7 GB)로 고정한 compile probe를 넣었다. 양자화는 calibration split만 사용한다 |
| Hailo-8 | https://github.com/hailo-ai/hailo_model_zoo/blob/v2.17/README.rst, https://github.com/hailo-ai/hailort | Hailo-8은 model zoo v2.x와 DFC 3.x(README badge 3.33.0) 조합으로 지원되고, DFC는 customer access 또는 문의로 받는다. HailoRT runtime(libhailort 등)은 MIT | dependency_unavailable(compiler). runtime image는 있지만 이 모델의 HEF가 없다 |
| RK3399Pro | https://github.com/rockchip-linux/rknn-toolkit (v1.7.5 README, `examples/onnx/resnet50v2/test.py`) | RKNN-Toolkit 1.7.5가 RK3399Pro를 지원하고 release에서 공개 배포된다. 공식 예제는 `rknn.build(do_quantization=True, dataset=...)` | 비양자화 build는 이 세션에서 확인하지 않았다. 노드가 Unknown이라 대기 |
| 엣지 container의 CUDA 주입 | https://github.com/NVIDIA/nvidia-container-toolkit (`cmd/nvidia-ctk/cdi/generate`에 CSV/tegra 경로 존재) | Jetson CSV mount 경로가 존재한다. 실제 엣지 containerd 설정은 확인하지 않았다 | bootstrap의 ctypes libcuda discovery로 주입 여부를 관측하고, 실패하면 dependency_unavailable로 기록한다 |

**부재 주장 보정**:
- 이전 "로컬 산출물 없음" 검색(`rg --files`)은 symlink를 따라가지 않았다. `rg --files -L`로 `artifacts/`를 다시 검색한 결과, 우리와 e0의 것 외에 vendor 산출물은 없었다.
- 다만 `/mnt/data3tb/user-storage/jinuk/artifacts/mlops/resource-advisor/`(다른 프로젝트)에 다음이 있다. 모두 읽기만 했고 수정하지 않았다.
  - `rknn_toolkit_lite-1.7.1` wheel, `resnet_18.rknn`(분류 모델이며 이 448 pooled feature graph가 아님)
  - `hailo-probe-tools.tar`, Hailo/Mobilint qualification 로그
  - 이전 probe 기록: Hailo image는 hailort 4.23.0만 있고 `hailo_sdk_client`가 없다. Mobilint image는 qbruntime 1.4.0만 있고 qbcompiler가 없으며, MXQ는 style transfer 모델뿐이다.
- 일부 경로는 권한 문제로 검색하지 못했으므로 전역 부재를 증명한 것은 아니다.

## 8. R1 수정(fix-r1) 후 현행 상태 (2026-10-23, live 미실행)

R1 결함 7건과 준비도 보정을 반영했다(`.scratch/quality-network-heterogeneous/fix-r1-spec.md`, 보고 `fix-r1-report.md`).

- **분석 견고성과 실패 분류**: `analyze`는 Pod 전부 실패, receipt 없음, 중단된 run에서도 `analysis.json`과 전체 catalog를 쓴다. collector, health, run-status 근거를 합쳐 다음 범주를 따로 기록한다: `dependency_unavailable`, `deployment_failed`(ready/install/bootstrap), `compile_failed`, `compiled_not_executed`(Mobilint), `runtime_failed`, `partial`, `executed`, `not_attempted`(중단 원인 포함).
- **evidence binding**: pinned 60쌍(고유, 20/40 split)과 각 receipt의 stage, node, Pod UID, backend/device, request ID, input/output hash를 대조한다. 중복·미지정 ID·split 오류·입력 교체·출력 변조·부분 coverage는 사유별로 집계하며, `executed`와 cost table, main plan에서 제외한다.
- **품질 coverage**: split별로 expected/answered/missing/coverage, answered-only 평균, zero-imputed 평균, 평가기 원출력을 남긴다. Qmin 0.40은 그대로다.
- **유한 subprocess**: `proc.py`를 추가했다. 별도 process group, 유한 deadline, 증분 stdout/stderr 파일, `/health` live tail, 소유 그룹만 종료하는 동작이며 pip install, deps 다운로드, Mobilint compile child에 적용했다.
- **HTTP admission**: 본문을 읽기 전에 slot을 잡고(초과 시 즉시 503), 소켓 read deadline과 413 크기 제한을 두며, 모든 경로에서 slot을 해제한다.
- **NPU speech 그래프**: 컴파일된 그래프는 최대 1개만 유지하고, 새 길이 전에 이전 참조를 해제한다. eviction, 재compile 비용, process RSS를 관측값으로 기록한다(장치 메모리 해제 주장 아님).
- **JP6.1 경로**: Jetson마다 driver-gated worker 2개를 둔다.
  - `cu130-aarch64-cp311`: `node-inventory-python`, driver ≥ 13000.
  - `jp61-aarch64-cp310`: `nvcr.io/nvidia/l4t-jetpack@sha256:34ccf0f3…` (L4T r36.4, cuDNN 9), 12000 ≤ driver < 13000.
  - 이미지에 pip가 없으면 wheel을 직접 unpack한다.
  - **가정**: l4t-jetpack의 python3 존재 여부와 edge에서의 nvcr.io pull 가능 여부는 미검증이다. 장치 호환성은 live 증거로만 판정한다.
- **조합**: 모든 조합이 쌍마다 vision ‖ speech를 **새로** 병렬 호출한 뒤 fusion을 호출한다. 각 단계의 receipt를 바인딩한다. 대조군은 `cpu-c/cpu-c/cpu-c`, `cpu-e0/…`, `npu-c/cpu-c/cpu-c`, 각 Jetson 전체 GPU 조합이다. **혼합 CPU+GPU+NPU DAG**는 각 Jetson worker마다 `npu-c` vision + Jetson CUDA speech + `cpu-c` fusion이다. 구성 요소가 실패하면 미완료로 남기며 CPU로 대체하지 않는다. 이것은 qualification trace이며 goodput 측정이 아니다.
- **source binding**: plan이 ConfigMap으로 배포될 source hash(`shipped_sources`)를 기록한다. execute는 hash가 다르면 kubectl 호출 전에 rc 2로 거부한다.

**현행 plan** `artifacts/quality-network-heterogeneous/plan-20261023T084059Z/`:
- assets manifest `eb78d6dda4b32ad2cc5f6456d31aba147a4a1a4b6f9b855cd522621ef0a0d74d`, 5.19 GB, jp61 wheelhouse 포함
- worker 12개 + cache: cpu-c, npu-c, cpu-e0, jgpu-dev0001/0007/0009 × {cu130, jp61}, mblt-compile, hailo-dev0002/mblt-ser0004 allocation probe
- 조합 15개

대체된 plan `plan-20261023T080527Z`(`fe95d652…`)와 `plan-20261023T081715Z`(`a9bde765…`)는 보존한다.

```bash
rtk proxy .scratch/quality-network-heterogeneous/venv-ref/bin/python experiments/quality-network-heterogeneous/qualify.py execute \
  --plan artifacts/quality-network-heterogeneous/plan-20261023T084059Z --run-id qnh-<UTC> --review-release <release naming eb78d6dd…>
rtk proxy .scratch/quality-network-heterogeneous/venv-ref/bin/python experiments/quality-network-heterogeneous/qualify.py analyze \
  --run artifacts/quality-network-heterogeneous/qnh-<UTC>
```

**증거 종류**:
- **mock(plumbing)**: `test_fix_r1`, `test_qualify`의 fake receipt, core, worker.
- **localhost 실제 CPU**: `test_stages`의 모델 비교, export, `logs/local-bootstrap-smoke-4-final.log`(bootstrap, finite pip, worker, bound receipt 6/6).
- **live 클러스터 증거**: 없음.

테스트 `logs/fix-r1-final-suite.log`: rc 0, 55 OK, tested hash가 최종 파일과 동일하다.

## 9. R2 수정(fix-r2) 후 현행 상태 (2026-10-23, live 미실행)

- **정확한 evidence gate**: stage와 composition 모두 60개 유효 row이면서 invalid, 중복, 미지정 extra가 **없어야** 통과한다. 비어 있지 않은 생성 UID를 필수로 한다(None == None은 identity 증거가 아니다).
- **중단된 로그**: JSONL의 잘못된 줄이나 마지막 줄 잘림은 줄 번호, 제한된 원문 조각, 사유를 붙여 invalid 증거로 보존한다. 분석과 catalog는 항상 쓰고, 원본 파일은 수정하지 않는다.
- **NPU speech compile 단계**: 요청 시점 compile 실패는 worker HTTP 오류 본문의 `phase`(prepare/compile/inference)를 거쳐 collector의 `error_phase`, 분석의 `compile_failed`까지 전달된다.
- **live lifecycle (`collect_live`)**:
  - 순차 `kubectl wait`를 공유 readiness window(**2100 s**)와 API GET 상태 polling으로 바꿨다. ImagePullBackOff, CrashLoopBackOff, CreateContainerError, Failed/terminated는 조기 종료한다. ErrImagePull은 3회 연속 관측 시 종료한다.
  - phase가 바뀔 때마다 `/health` snapshot을 저장한다.
  - 수집 순서: GPU/NPU serving stage → CPU stage → 1회 late-readiness 재확인 → composition(혼합 CPU+GPU+NPU 우선) → NPU speech probe.
  - Mobilint compile은 /ready를 server 기동 시점으로 두고 /health만 polling하며, 다른 수집을 막지 않는다. 마감 시 아직 실행 중이면 `compile_incomplete`(running_at_deadline)로 기록하고 terminal로 간주하지 않는다.
  - 요청 timeout은 min(600 s, 남은 예산)이다.
- **예산**:
  - collection 5400 s(worker 생성 기준), cleanup 예비 900 s
  - worker Pod lifetime 7200 s, cache Pod 9300 s
  - install 1200 s, 파일당 fetch 600 s, compile 4200 s, poll 10 s
- **HTTP**: 요청줄, header, body 전체에 절대 read deadline(기본 2 × inactivity)을 두고 timer로 socket을 닫는다. trickle client도 slot을 반환한다.
- **subprocess 로그**: 계속 drain하면서 stream당 head 1 MiB를 디스크에 쓰고 tail 256 KiB를 메모리에 둔다. 끝나면 잘림 표시와 tail을 붙이고 total/truncated byte를 기록한다.

**현행 plan** `artifacts/quality-network-heterogeneous/plan-20261023T090037Z/`:
- manifest `a3aba26e108cffaa700a887e9d2d93fbf6ba3099ccf5a6b7926be3a06ce900a2`, 5.19 GB
- worker 12개 + cache, 조합 15개
- shipped/planner source hash 검증 완료

이전 plan 3개(`080527Z`, `081715Z`, `084059Z`)는 보존한다.

```bash
rtk proxy .scratch/quality-network-heterogeneous/venv-ref/bin/python experiments/quality-network-heterogeneous/qualify.py execute \
  --plan artifacts/quality-network-heterogeneous/plan-20261023T090037Z --run-id qnh-<UTC> --review-release <release naming a3aba26e…>
```

테스트 `.scratch/quality-network-heterogeneous/logs/fix-r2-final-suite.log`: rc 0, 69 OK, tested hash가 최종 파일과 동일하다. 같은 신규 테스트를 동결 R2 코드에 돌린 red는 `fix-r2-red-frozen-r2.log`(13/13 실패)이다. 실제 localhost CPU smoke는 `local-bootstrap-smoke-5-fix-r2.log`(rc 0)이다. mock과 fake clock 테스트는 plumbing 증거일 뿐이고, live 증거는 없다.

## 10. 첫 live 시도 `qnh-20261023t090355` (2026-10-23 09:04–09:22Z, coordinator 중단, 추론 receipt 0건)

- 명령: `qualify.py execute --plan artifacts/quality-network-heterogeneous/plan-20261023T090037Z --run-id qnh-20261023t090355 --review-release .scratch/quality-network-heterogeneous/coordinator-live-release.md`. coordinator 지시로 collector PID에만 SIGINT를 보냈고 rc -2로 끝났다. 이후 `qualify.py analyze`는 rc 0.
- 단계: cache 65 s(5.19 GB 업로드), worker 12개 생성, readiness window 진행 중 900 s에 중단. 설계상 readiness window가 끝난 뒤 stage를 수집하므로 CPU를 포함한 **모든 worker에서 추론 receipt가 0건**이다.
- **실측 사실**:
  - dev0001(L4T R36.4.7, driver 12060):
    - cu130 worker는 driver gate에서 거부됐다(설계대로).
    - jp61 worker는 `nvcr.io` l4t-jetpack 이미지를 엣지에서 pull했고 python3도 존재했다. wheel 설치까지 됐지만 `libopenblas.so.0`이 없어 torch import에 실패했다.
  - dev0007(L4T R39.2.1, libcuda driver 13020, "Orin", `/dev/nvidia0`):
    - cu130 gate를 통과하고 설치 중 pip가 rc 1로 끝났다.
    - 이어 두 Pod 모두 **ephemeral-storage eviction**이 발생했다.
  - dev0009: cu130 gate를 통과하고 설치 중 **노드가 Unknown**이 됐다(09:15:09Z 이후 kubelet 상태 보고 중단, 인과는 주장하지 않음).
  - Intel NPU: OpenVINO가 `EXECUTION_DEVICES`를 문자열 `'NPU'`로 반환해 우리 adapter의 `list()`에서 `['N','P','U']`가 되었다. **adapter 버그이며 장치·모델 비호환이 아니다.**
  - Mobilint: 공개 `qbcompiler:1.4-cpu` 이미지에 `qbcompiler`가 설치되어 있지 않다(dependency_unavailable).
  - Hailo/Mobilint allocation probe는 완료됐다.
- **정리**: 13개 중 12개를 UID로 삭제했다. `jgpu-dev0009-jp61`(uid `325dc900…`)은 dev0009에서 Terminating 상태이며 force-delete는 하지 않았다. 보호 workload 중 edgeai 2개가 다른 소유자에 의해 교체됐다(인과 주장 없음). `audit=partial`.
- **다음 단계(별도 dispatch)**:
  - NPU 정규화 수정(scratch에서 5/5 통과)
  - JP6 OpenBLAS `/work` unpack(jammy arm64 패키지 hash 확보)
  - 노드별 driver에 맞춘 단일 recipe와 storage 예산
  - proc stderr 보존 버그 수정
  - window 안에서 준비된 worker부터 수집
  - interrupt 시 pending 범주 추가

보고 `.scratch/quality-network-heterogeneous/live-report-qnh-20261023t090355.md`.

## 11. 첫 live 결함 수정(npu-property-fix) 후 현행 상태 (2026-10-23, live 미실행)

- 정리 재확인: §10 이후 dev0009가 Ready로 돌아오자 `jgpu-dev0009-jp61`이 스스로 finalize됐다. 이후 GET에서 `run-id=qnh-20261023t090355` 소유 객체는 **0개**였다(`.scratch/quality-network-heterogeneous/live-extra/post-cleanup-verify.json`). 원래 `cleanup.json`(remaining=1)은 그대로 둔다.
- 수정 범위는 첫 실행의 구체적 결함뿐이다.
  - **NPU `EXECUTION_DEVICES` 정규화**
    - 문자열 → 원소 1개 list, sequence → list. 직접 NPU 단독만 허용하고 CPU/GPU/혼합/빈 값은 거부한다.
    - raw type/value를 describe와 receipt에 기록한다.
    - speech 요청 시점의 장치 검증 실패는 `device_verification` 단계로 분리한다.
  - **JP6.1 의존성 closure** (`prepare_jp61_sysroot.py`, 결과 `artifacts/quality-network-heterogeneous/toolchains-jp61-sysroot-20261023T093145Z/`)
    - 출처(sha256 검증):
      - jammy arm64 `libopenblas0-pthread 0.3.20+ds-1`, `libgfortran5 12.3.0-1ubuntu1~22.04.3` (ports.ubuntu.com, Packages index hash)
      - cuSPARSELt `0.6.2.3` linux-aarch64 (NVIDIA redist JSON, `b081a82a…`)
    - 고정 이미지 layer 목록을 이름만 스트리밍해 inventory를 만들었다. torch/torchvision wheel ELF의 모든 NEEDED soname이 image + sysroot + host 제공(`libcuda.so.1`)으로 해석되며 **unresolved 0**이다(`.scratch/quality-network-heterogeneous/jp61-closure-resolution.json`).
    - CUDA 12.6 라이브러리는 이미지 Env `LD_LIBRARY_PATH=/usr/local/cuda/lib64`(→ `cuda-12.6/targets/aarch64-linux/lib` symlink)로 찾는다(`jp61-image-config-env.json`).
    - Pod는 `libgfortran.so.5 → libopenblas.so.0 → libcusparseLt.so.0`을 절대경로 + `RTLD_GLOBAL`로 preload한 뒤 torch를 import한다(실패 시 `preload_failed`).
    - host apt, Docker 권한 변경, 라이선스 계정 생성은 없다.
  - **recipe와 storage**
    - 관측 driver 근거로 recipe 하나만 고른다. dev0001 → jp61(driver 12060, L4T R36.4.7, run 1 health 근거).
    - dev0007/dev0009는 Pod를 만들지 않는다. catalog에 `deployment_blocked`(storage eviction / 노드 Unknown 근거)로 남기고 storage·readiness 재검증 전까지 재시도하지 않는다.
    - Mobilint compile probe는 반복하지 않는다(catalog `dependency_unavailable`, run 1 근거).
    - wave asset은 사용하는 recipe만 담는다(cu130 wheel 없음; artifact는 보존).
    - Pod ephemeral request = limit = work emptyDir를 recipe별로 계산한다(fetch + jellybean 해제 + 설치 + pip staging + 1 GiB). 이미지 용량은 노드 수준에서 따로 기록한다.
    - pip `TMPDIR=/work/tmp`로 2 GiB `/tmp` 설계를 폐기했다.
  - **proc**
    - drain을 join한 뒤 tail을 읽는다.
    - 메모리 tail을 디스크 기록보다 먼저 남긴다.
    - 디스크 기록 실패(ENOSPC 등)는 `write_error`로 남기고 drain은 계속한다. run 1의 누락 원인을 ENOSPC로 주장하지 않는다.
  - **수집**
    - readiness window 안에서 준비된 worker의 serving stage를 수집하고, 구성 요소 세 개가 준비되면 composition도 수집한다. ledger로 중복을 막는다.
    - interrupt 시 `interrupted_pending`(snapshot 참조)과 "ready; interrupted before collection"을 명시한다.
- **run 1 재분석**: 별도 artifact `artifacts/quality-network-heterogeneous/reanalysis-qnh-20261023t090355-20261023T094531Z/`. 원본 run 디렉터리는 hash 불변이다.
  - NPU vision: `compile_failed/load` → `runtime_failed/device_verification`(adapter 오류, raw 근거 유지)
  - dev0009: `interrupted_pending`
  - 준비 완료 CPU worker: "ready; interrupted before collection"
- **테스트**: `logs/npu-property-fix-final-suite.log` rc 0, **83 OK**(test_qualify, test_stages, test_fix_r1, test_fix_r2, 신규 test_npu_property_fix 14). 로컬 CPU bootstrap smoke `logs/local-bootstrap-smoke-6-npu-property-fix.log` rc 0. mock·fake-clock은 plumbing 근거일 뿐이다.
- **새 고정 plan**: `artifacts/quality-network-heterogeneous/plan-20261023T094437Z/`, assets manifest `07d854ce718fe76f54810f02d92799cece8285ac7067b0ae587cbb7bced18411`, wave.json `59bbf74c…`, 2.33 GB.
  - worker 6개: cpu-c, npu-c, cpu-e0, jgpu-dev0001-jp61, hailo/mblt allocation probe
  - 이전 plan과 run 1은 보존한다.
- **미완료**:
  - JP6.1 torch가 dev0001 GPU에서 실제로 import·추론되는지는 다음 live에서만 증명된다.
  - dev0001 이미지 저장소 여유는 노드 수준에서 별도 확인해야 한다.
  - Qmin 0.40 미달(0.515/0.355)은 그대로다.
  - NPU FP16 parity 임계값은 바꾸지 않았으며 장치 실행·parity·VQA 품질을 분리해 보고한다.

보고 `.scratch/quality-network-heterogeneous/npu-property-fix-report.md`.

## 12. 수정 후 live 실행 `qnh-20261023t095013` (2026-10-23 09:50Z, plan-20261023T094437Z, 완료·정리 검증)

- **실행 요약**
  - 상태 `collected`, rc 0, 수집 경과 340 s.
  - 정리: UID 기준 삭제, 소유 객체 0, 보호 workload 변화 없음(`audit=complete`).
  - preflight: kubectl GET 전용. dev0001 root-fs 여유 365.91 GiB(기존 node-exporter 결과, 이미지 fs 위치는 미증명).
- **Intel NPU vision**
  - 실제 하드웨어 추론 **60/60**. receipt마다 `device=NPU`, `execution_devices=['NPU']`(raw str `'NPU'`), Intel AI Boost, device infer p50 10.5 ms.
  - 기존 임계값(rtol 1e-4) parity는 **failed**(0/60): FP16 차이로 max|Δ| p50 0.0026, 최대 0.0054, cosine ≥ 0.9999992.
  - `npu-c/cpu-c/cpu-c` 조합의 최종 답은 CPU와 60/60 동일하고 품질도 0.515/0.355로 같다. 임계값은 바꾸지 않았고 수치 동등을 주장하지 않는다.
- **NPU speech 정확-길이 compile probe**
  - 14건은 NPU에서 실행됐다(compile 5–6 s, infer 42–107 ms, RSS ≤ 1.89 GB).
  - 이후 컨테이너가 exit 137(reason "Error")로 종료돼 46건이 transport_error → `partial`. OOM은 확인되지 않았다.
- **CPU control**: ser0002·dev0003의 3개 stage와 self 조합 모두 60/60, parity passed, 품질 0.515/0.355 < Qmin.
- **Jetson dev0001 JP6.1**
  - sysroot preload가 성공하고 torch import도 됐다. run 1의 libopenblas 문제는 해결됐다.
  - 이어 PyPI `torchvision 0.20.0`의 C++ op가 nv24.08 torch에 등록되지 않아(`operator torchvision::nms does not exist`) vision/fusion load가 실패했다.
  - speech는 `Wav2Vec2ForCTC` import 실패로 load에 실패했다(연쇄로 추정, 미증명).
  - 따라서 **목표 혼합 조합 NPU vision + CUDA speech + CPU fusion은 실행되지 않았다**(not_attempted, fallback 없음).
- **유지된 제약**: catalog는 12개 노드 69행을 유지한다. dev0007/dev0009는 `deployment_blocked`, Mobilint compiler probe는 반복하지 않았다(dependency_unavailable).
- **다음 작업(별도 task)**:
  - NVIDIA torch ABI에 맞는 torchvision 확보
  - NPU speech 종료 원인 진단
  - dev0007/dev0009 재검증
  - Qmin 미달 문제

보고 `.scratch/quality-network-heterogeneous/live-report-qnh-20261023t095013.md`.

## 13. dev0009 CUDA13 wave 준비 (2026-10-23, live 미실행)

- **dev0009 선택**
  - 재검증된 dev0009에 기존 cu130 recipe(torch 2.9.1+cu130 / torchvision 0.24.1, `Requires-Dist torch==2.9.1`) 하나만 배정했다.
  - 근거:
    - run 1: driver 13020, L4T R39.2.1
    - 09:54Z 재검증: Ready, pressure 없음, root fs 68.69 GiB 여유
    - storage budget 13Gi
- **dev0001 JP6.1 보류**
  - 같은 recipe로 재시도하지 않고 catalog에 `runtime_failed/load`(run 2 `torchvision::nms` 등록 실패, 수리 대기)로 남겼다. 하드웨어 미지원으로 분류하지 않는다.
- **dev0007**: `deployment_blocked` 유지.
- **package 근거와 한계**
  - package metadata는 import·CUDA 실행 증명이 아니다(`.scratch/quality-network-heterogeneous/cu130-pair-metadata.json`).
  - torchvision `_C.so`는 CUDA 라이브러리에 링크되지 않는다(CPU op 빌드로 추정, live 미확인).
- **테스트**: `logs/dev0009-wave-final-suite.log` rc 0, 88 OK.
- **새 고정 plan**: `artifacts/quality-network-heterogeneous/plan-20261023T100449Z/`, assets manifest `f06c64ff2a40007365c2d16a3c75db14657448ff78d55d05f9b8e9367dc5ab5d`, 121 파일, 4.31 GB.
  - 혼합 조합 `npu-c/jgpu-dev0009-cu130/cpu-c`를 우선 실행한다.
  - coordinator release 후에만 실행한다.

보고 `.scratch/quality-network-heterogeneous/dev0009-wave-report.md`.

## 14. live 실행 `qnh-20261023t100710` (2026-10-23 10:07Z, plan-20261023T100449Z, 완료·정리 검증)

- **실행 요약**
  - rc 0, 수집 719 s(deadline 미도달).
  - UID 정리: 소유 객체 0.
  - 보호 workload: 다른 소유자의 완료된 gc Pod 1개가 사라졌다(인과 주장 없음).
- **첫 실제 CPU + Jetson GPU + Intel NPU 혼합 DAG**: `npu-c/jgpu-dev0009-cu130/cpu-c`가 새 요청 60/60으로 identity·coverage gate를 통과했다.
- **dev0009 CUDA13** (driver 13020, torch 2.9.1+cu130)
  - vision/speech/fusion 모두 60/60 `cuda:0`, CPU-reference parity passed.
  - device p50: vision 31.8 ms, speech 45.2 ms, fusion 16.4 ms.
- **Intel NPU vision**
  - 60/60 실행, device p50 10.6 ms.
  - parity는 기존 규칙에서 failed(FP16).
- **NPU speech probe**
  - 27/60 partial.
  - 컨테이너가 exit 137(reason "Error")로 종료됐다. OOM event 0, 표본 working set 최대 1.63 GB / 한도 3Gi. 원인 미상이다.
  - run 2의 index 14와 이번 index 27로 종료 위치가 다르다. 같은 입력에서의 반복 실패는 관측되지 않았으며, 이것만으로 입력 의존성을 배제할 수는 없다.
- **CPU control**: ser0002·dev0003 모두 60/60, parity passed.
- **품질**: 모든 조합 cal 0.515 / eval 0.355로 Qmin 0.40 미달이다. main 후보가 없고 goodput·품질 우위를 주장하지 않는다. 재사용 fixture이며 holdout이 아니다.
- **catalog**: 12개 노드 69행 유지.
  - dev0001 JP6.1: runtime_failed(보류)
  - dev0007: deployment_blocked
  - Hailo/Mobilint: dependency_unavailable
  - dev0004: node_unavailable

보고 `.scratch/quality-network-heterogeneous/live-report-qnh-20261023t100710.md`.
