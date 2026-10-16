# E0: GPU/NPU 실행 호환성 검증

관측일: 2026-10-16. 상태: 선택한 서버 GPU 1종·NPU 1종의 단일 fixture 실행 검증 통과, 임시 리소스 정리 완료. 이전 [CPU 검증](e0-kubeedge.md)·[Task 전달](e0-transport.md)과 동일한 원본 SqueezeNet1.1-7 ONNX·입력 이미지·전처리 텐서 hash를 사용했다. GPU는 아래에 기록한 파생 그래프를 거친다.

## 대상과 검증 경계

- 기존 GPU 사용자 workload는 중단하지 않았다. 비어 있는 NVIDIA GB10(ARM64 서버)과 Intel AI Boost NPU(x86 서버)를 선택했다. 이번 두 대상은 서버 노드이며 Jetson/Hailo/Rockchip/Mobilint 등 EdgeCore 엣지 장치의 모델 실행까지 검증한 것은 아니다.
- 각 Job은 해당 Kubernetes GPU/NPU resource 1개를 정상 요청한다. 호스트 device 우회 mount나 운영 workload 변경은 사용하지 않는다.
- CPU fallback 성공을 가속기 성공으로 취급하지 않는다. NPU는 OpenVINO 직접 NPU compile과 실행 device를 확인한다. GPU는 동일 ONNX를 CUDA 실행 그래프로 변환해 모델 parameter·출력 device·CUDA event와 가능하면 kernel profile을 확인한다.
- PyPI·공식 release 확인에서 ORT 1.23.2의 ARM64 GPU wheel/asset을 찾지 못했다. GB10 기존 image에도 DNN 라이브러리가 없어, 버전 고정 PyTorch CUDA 13·ONNX2Torch를 준비한다. 이것은 ORT CUDA EP의 재현이 아닌 **다른 runtime으로 같은 ONNX를 옮기는 준비 경로**다.
- Intel 기존 image는 OpenVINO 2026.3.1과 NPU를 제공한다. SDK와 맞춰 NumPy1.26.4를 유지하며 GPU는 NumPy2.2.6이다. 실제 전처리 tensor hash 일치를 강제한다.

## 실행 순서와 성공 기준

1. discovery Job으로 image의 Python/module/device를 확인한다. 장치 discovery만으로 모델 실행 성공을 선언하지 않는다.
2. 설치·모델 다운로드/읽기·변환/compile·device loading을 별도 기록한다.
3. 같은 노드의 ORT CPU reference와 비교한다. 1,000개 출력 finite·shape, top-1 및 top-5 순위, max absolute error·RMSE를 기록한다.
4. 가속기별 warmup 2회·측정 5회의 host input→host output blocking inference를 수행한다. GPU profiler가 별도 첫 실행을 추가하므로 기록한다.
5. 원시 로그·resource 할당·runtime/모델/입력 hash와 source를 보존하고 Job을 정리한다.

공통 fixture의 top-5 `[258,259,157,279,152]` 일치가 분류 smoke의 통과 조건이다. 전체 score의 bitwise 일치나 데이터셋 정확도를 의미하지 않는다. FP32 model/I/O라도 NPU 내부 연산은 FP16일 수 있다. 서로 다른 backend의 시간은 소규모 실행 경계 관측이며 우월성·SLO·정책 개선의 근거로 삼지 않는다.

## 실행 결과

두 backend 모두 출력 1,000개가 finite였고, 각각 7회 모두 같은 top-5를 반환했다. 아래 추론 시간은 warmup 2회 뒤 **같은 프로세스에서 측정한 5회**의 중앙값이다. 전처리·HTTP/RPC·원격 데이터 전달은 포함하지 않으며 host tensor 입력에서 host 출력 복사 완료까지를 잰다. 독립 반복이나 지연 p95는 아니다.

| 대상 | 검증된 실행 경로 | 모델 준비 관측 | 추론 중앙값 | CPU 기준 최대 절대 오차 |
|---|---|---|---|---|
| NVIDIA GB10 / ARM 서버 | ONNX opset13 파생 → ONNX2Torch1.5.15 → PyTorch2.9.1+cu130 CUDA | 정규화 포함 변환·device load 242.742ms | 1.546ms | 0.000015259 |
| Intel AI Boost / x86 서버 | 원본 ONNX → OpenVINO2026.3.1 직접 NPU compile | read 18.187ms, compile 149.558ms | 1.422ms | 0.025490 |

- GPU의 모델 parameter 1,235,496개가 CUDA에 있었고 출력은 `cuda:0`였다. 7회 모두 CUDA timing event가 양수였고 host 반환 전에 동기화했다. CPU reference 계산은 비교를 위한 별도 실행이며 GPU backend의 fallback 경로가 아니다.
- **GPU 제한:** PyTorch가 장치 capability12.1과 자신이 보고한 최대 지원12.0 사이의 경고를 냈다. 이번 fixture는 통과했지만 다른 연산·모델 및 이 build의 포괄적인 장치 지원은 확인하지 않았다. 별도 profiler 추론은 실행됐으나 CUDA trace event는 0개였다. 원인은 미확인이며 kernel trace를 확보했다고 주장하지 않는다. 이 측정을 위해 권한·driver·운영 설정을 변경하지 않았다.
- NPU는 `compile_model(...,"NPU",...)`와 `EXECUTION_DEVICES=['NPU']`를 확인했다. AUTO/HETERO로 CPU에 넘기는 경로를 사용하지 않았다. 원본 ORT CPU 대비 RMSE는 약 0.005480으로, top-5 smoke 성공과 수치 오차를 함께 남겼다.
- GPU는 profiler용 추론 1회를 먼저 추가 실행했으므로, 기록된 warmup0을 진짜 첫 CUDA 추론 시간으로 해석하지 않는다. NPU warmup0은 약 10.485ms였다. 서로 다른 초기 실행 절차로 첫 추론 성능을 비교하지 않는다.

[구조화 결과·검증 항목](../../artifacts/e0-accelerators/results.json), [GPU 전체 로그](../../artifacts/e0-accelerators/gpu.log), [NPU 전체 로그](../../artifacts/e0-accelerators/intel_npu.log)에 원시 수치·버전·device·Pod 할당 근거를 보존했다. 이번 결과는 **해당 모델·입력·준비 경로의 실행 가능성**을 확인하며, 본 배치 실험의 데이터셋 품질 기준이나 runtime 간 동일 정밀도 기준을 확정하지 않는다.

## 확인된 준비 경로 차이

Intel NPU는 원본 opset7 ONNX를 직접 컴파일했다. 첫 GPU 시도는 ONNX2Torch1.5.15가 원본의 `MaxPool` schema1을 지원하지 않아 변환 단계에서 실패했다. CUDA 장치 고장이나 모든 GPU 런타임의 미지원으로 해석하지 않는다. [첫 실패 로그](../../artifacts/e0-accelerators/gpu-attempt1.log)를 보존했다.

공식 ONNX1.17.0 `convert_version(...,13)`으로 파생 그래프를 만들었다. 원본 initializer 53개의 이름·dtype·shape·값을 보존했고, 같은 fixture에서 원본·파생 그래프의 ORT CPU 출력이 bitwise 일치했다. 파생 hash는 `5846b40aca708f9e817e19caa3d49e49e81b9bd9ba2fffc70a175a8558904857`다. 이는 단일 입력 검증이며 모든 입력에 대한 동등성 증명은 아니다. [변환 검사](../../artifacts/e0-accelerators/opset-check.json), [검사 코드](../../experiments/e0/check_opset.py).

이 사례의 실행 가능 후보는 단순히 `ONNX 지원`이나 `GPU 자원 있음`으로 표현하기 부족하다. Task 원본과 연결된 파생 산출물·runtime 버전·준비 절차를 함께 기록해야 한다. 이 준비 경로 차이가 배치 정책의 실제 손실이나 새 알고리즘 필요성으로 이어지는지는 아직 검증하지 않았다.

GPU image에는 실행 라이브러리가 없고 NPU image에는 OpenVINO가 이미 있다. 임시 작업 디렉터리에 수행한 pip 설치시간은 진단 환경을 만드는 시간이다. 이를 운영용 image 준비시간·보편적인 cold start·GPU 대 NPU 배포 성능으로 비교하지 않는다. 변환·compile·loading 측정은 별도이며 GPU의 `conversion_and_device_load_ms`에는 `normalization_ms`가 포함되므로 합산하지 않는다. NPU cache는 새로운 `/work/ov-cache`에서 관측했지만 driver의 전역 cache까지 초기화했다고 주장하지 않는다.

## 재현 근거

- [원시 장치·할당·image 인벤토리](../../artifacts/e0-accelerators/inventory.json)
- [discovery Job](../../artifacts/e0-accelerators/discovery-jobs.json), [실행 정보](../../artifacts/e0-accelerators/run.json)
- [가속기 추론 코드](../../experiments/e0/accelerator_inference.py), [discovery 코드](../../experiments/e0/accelerator_discovery.py)
- [동결된 image·source·요구 패키지와 추론 Job](../../artifacts/e0-accelerators/inference-jobs.json)
- [opset13 파생 그래프를 쓰는 GPU 재시도 Job](../../artifacts/e0-accelerators/gpu-opset13-job.json)
- [GPU 공식 wheel URL·SHA256](../../artifacts/e0-accelerators/gpu-package-urls.json)
- [NPU discovery](../../artifacts/e0-accelerators/discovery-intel_npu.json), [GPU discovery](../../artifacts/e0-accelerators/discovery-gpu.json)
- [NPU 실행 로그](../../artifacts/e0-accelerators/intel_npu.log)
- [실제 GPU 설치 패키지](../../artifacts/e0-accelerators/gpu-installed.txt), [NPU 설치 패키지](../../artifacts/e0-accelerators/intel_npu-installed.txt). NPU의 custom OpenVINO 버전은 discovery·실행 로그를 따른다.
- [최종 Pod 상태·image ID·할당](../../artifacts/e0-accelerators/pods-final.json), [Job 성공/실패 상태](../../artifacts/e0-accelerators/jobs-final.json)
- [공식 runtime 계약 조사](../research/kubeedge-e0-accelerator-notes.md)

실제 실행한 명령:

```bash
rtk proxy kubectl apply -f artifacts/e0-accelerators/discovery-jobs.json
rtk proxy kubectl apply --dry-run=client -f artifacts/e0-accelerators/inference-jobs.json
rtk proxy kubectl apply -f artifacts/e0-accelerators/inference-jobs.json
rtk proxy uv run --python /usr/bin/python3 --no-build --with onnx==1.17.0 --with onnxruntime==1.23.2 --with numpy==2.2.6 --with Pillow==11.3.0 python experiments/e0/check_opset.py
rtk proxy kubectl apply --dry-run=client -f artifacts/e0-accelerators/gpu-opset13-job.json
rtk proxy kubectl apply -f artifacts/e0-accelerators/gpu-opset13-job.json
```

같은 SqueezeNet compiled artifact가 없는 Hailo에서 다른 ResNet HEF를 실행해 같은 Task의 호환성으로 분류하지 않는다. 실행하지 않은 조합은 미검증으로 유지한다.

## 검증과 정리

원본/파생 ONNX의 fixture 출력 비교, GPU/NPU 14회 분류 결과, 실제 성공 Pod의 장치 resource 요청·할당·node·image digest·종료 코드와 source 구문을 검사했다. GPU 재시도는 변경된 source를 새 immutable ConfigMap에 고정했고, 성공한 NPU를 불필요하게 재실행하지 않았다. 첫 GPU 실패 source·로그도 보존했다. 이 작업 공간은 Git 저장소가 아니므로 commit diff를 전제로 하는 code-review 스킬의 두 축 리뷰는 실행할 수 없었다. source와 동결 manifest를 직접 점검했다.

증거 수집 뒤 이번 run의 정확한 이름으로 Job 5개와 ConfigMap 2개를 삭제했다. [정리 결과](../../artifacts/e0-accelerators/cleanup.json)의 잔여 Pod/Job/ConfigMap은 0개다. namespace는 유지했다. [기존 가속기 workload 3개](../../artifacts/e0-accelerators/existing-workloads-observation.json)는 관측 당시 모두 Running/Ready였고, 변경·중단하지 않았다.

후속 [공통 Task 전달 프로파일](e0-rpc-profile.md)과 [세 Task 공동 위치 선택 파일럿](e1-dag.md)을 수행했다. 후속 코드에는 네트워크로 받은 tensor 입력과 GPU 복사 구간 계측을 추가했으므로 이 페이지 당시의 source·경계는 위 동결 manifest를 기준으로 읽는다. EdgeCore 관측 오류의 원인과 엣지 가속기의 모델 실행은 별도 미해결 항목이다. 데이터셋 품질·독립 배포 반복·동적 변화·본 정책 비교는 다음 검증에 남아 있다.
