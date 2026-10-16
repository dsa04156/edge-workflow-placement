# KubeEdge E0: GPU/NPU 모델 실행 계약 메모

확인일: 2026-10-16. Context7과 GitHub MCP의 공식 자료를 확인했다. 본 조사는 장비 실행·다운로드·클러스터 변경을 하지 않았다. CPU E0의 SqueezeNet1.1-7·ORT1.23.2·동일 RGB float32 NCHW224 입력을 기준으로 하되 backend의 실제 지원은 별도 검증한다.

## NVIDIA: CUDA EP를 실제 사용했다는 최소 증거

- ORT **v1.23.2**는 `session.disable_cpu_ep_fallback=1`을 제공한다. non-CPU EP가 graph를 전부 지원하지 못하면 session 생성 실패이며, 이 옵션과 CPU EP의 명시적 추가는 함께 사용할 수 없다. [해당 버전 계약](https://github.com/microsoft/onnxruntime/blob/v1.23.2/include/onnxruntime/core/session/onnxruntime_session_options_config_keys.h)
- 최소 설정 예: `so=ort.SessionOptions(); so.add_session_config_entry('session.disable_cpu_ep_fallback','1'); so.enable_profiling=True; sess=ort.InferenceSession(model,sess_options=so,providers=['CUDAExecutionProvider'])`. 실제 inference 뒤 `sess.end_profiling()`의 파일을 수집한다. [공식 profiling API](https://onnxruntime.ai/docs/api/python/api_summary.html)
- `get_available_providers()`는 설치된 EP, `sess.get_providers()`는 session EP 목록이지 모든 연산이 GPU에서 실행됐다는 증거가 아니다. profile의 실제 Node 실행 이벤트에서 `args.provider`별 개수·연산 종류를 집계하고 CUDA 연산이 실제 존재하는지 확인한다. strict session 실패와 CPU 일부 허용 실행을 서로 다른 결과로 기록한다. [EP 선택/배정 원리](https://onnxruntime.ai/docs/execution-providers/)
- 모델·입력 hash, ORT/GPU driver/CUDA/cuDNN 버전, session providers, profile, 결과를 함께 저장한다. `onnxruntime` CPU package만 설치된 성공이나 `nvidia-smi` 장치 인식만을 CUDA 모델 실행 성공으로 집계하지 않는다. GPU package와 CUDA/cuDNN 호환성은 실제 선택한 릴리스/이미지에 맞춰 확인한다. [공식 CUDA EP 의존성](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)
- strict는 일부 shape/보조 노드의 CPU 배정까지 거부할 수 있는 **엄격한 기준**이다. 실패가 GPU 전체 부적합이라는 뜻은 아니며, permissive profile에서 어떤 연산이 CPU에 남는지 별도 설명한다.

## Intel: OpenVINO NPU를 명시적으로 컴파일·실행

```python
import openvino as ov
core = ov.Core()
model = core.read_model("squeezenet1.1-7.onnx")
supported = core.query_model(model, "NPU")
compiled = core.compile_model(model, "NPU")
devices = compiled.get_property("EXECUTION_DEVICES")
request = compiled.create_infer_request()
outputs = request.infer({compiled.input(0): tensor})
```

- `read_model`은 ONNX를 직접 읽을 수 있고, NPU 공식 예는 `compile_model(model,'NPU')`를 사용한다. 진단에는 `AUTO`/`HETERO:NPU,CPU` 대신 직접 `NPU`를 지정한다. `available_devices`·query 지원은 사전 검사이고, compile+infer 성공 및 실행 장치가 실제 증거다. [ONNX 읽기](https://docs.openvino.ai/2025/openvino-workflow/model-preparation.html), [NPU 공식 계약](https://docs.openvino.ai/2025/openvino-workflow/running-inference/inference-devices-and-modes/npu-device.html)
- 위 Python API 예는 설치 버전에서 확인할 제안이다. 지원 property를 먼저 열람하고 `EXECUTION_DEVICES`·장치 full name·driver/compiler version을 가능한 범위에서 저장한다. 지원하지 않는 property 조회 실패와 모델 compile/infer 실패는 구분한다. [OpenVINO 2025.3 NPU 원문](https://github.com/openvinotoolkit/openvino/blob/2025.3.0/docs/articles_en/openvino-workflow/running-inference/inference-devices-and-modes/npu-device.rst)
- 같은 ONNX·입력 의미를 사용할 수 있어도 모든 연산·shape가 해당 NPU compiler에서 지원된다고 가정하면 안 된다. OpenVINO 2025 문서는 입력 primitive F32/F16 등을 지원하되 HW compute precision은 FP16이라고 설명한다. **같은 float32 입력이 bitwise 동일 내부 연산을 뜻하지 않으므로** class258와 수치 허용오차를 분리해 비교한다. [NPU precision·driver 요구](https://docs.openvino.ai/2025/openvino-workflow/running-inference/inference-devices-and-modes/npu-device.html)
- compile 시간·첫 추론·warm 추론을 분리하고 cache 상태를 기록한다. 문서는 FEIL(compile 포함)과 FIL(precompiled model 초기화/첫 실행)을 구분한다. [공식 cache 설명](https://docs.openvino.ai/2025/openvino-workflow/running-inference/inference-devices-and-modes/npu-device.html)

## Hailo: HEF의 존재와 동일 Task 실행은 다른 계약

- 공식 HailoRT `HEF`는 하나 이상의 **compiled models**를 담은 Hailo Executable Format이다. `HEF(path)`·`get_networks_names()`·input/output vstream metadata로 어떤 산출물인지 확인한다. 모델별 ONNX/TF→최적화/컴파일→HEF 절차가 존재한다. [HailoRT API](https://github.com/hailo-ai/hailort/blob/master/hailort/libhailort/bindings/python/platform/hailo_platform/pyhailort/pyhailort.py), [공식 Model Zoo](https://github.com/hailo-ai/hailo_model_zoo)
- ResNet HEF 실행은 Hailo 장치/runtime의 **다른 workload 실행성** 근거다. 같은 SqueezeNet source weights·변환 경로·입출력·정규화·양자화 계약을 입증하지 못하면 CPU E0와 같은 Task의 backend 검증으로 합치지 않는다. 같은 분류 목적이나 class258만 같아도 모델 동일성이 생기지 않는다.
- HEF checksum·network name·대상 Hailo chip·HailoRT/driver/DFC 버전·vstream dtype/layout/quantization·전후처리를 기록한다. 공식 최신 Model Zoo는 Hailo-8/8L에 v2.x+DFC3.x 계열을 안내하므로 장치와 다른 master branch의 HEF/예제를 무조건 적용하지 않는다. [공식 compatibility 안내](https://github.com/hailo-ai/hailo_model_zoo/blob/master/README.rst)

통과 판정은 resource 등록→runtime 장치 인식→해당 모델 compile/load→실제 가속기 실행→동일 입력의 의미/수치 검사로 분리한다. 이 문서는 API 계약 메모이며, 실제 실행 여부와 검증 한계는 [가속기 실험 결과](../experiments/e0-accelerators.md)를 따른다. Hailo의 같은 모델 실행은 미검증이다.

## 후속: legacy opset을 Torch CUDA용 파생 graph로 변환

- onnx2torch **v1.5.15**의 MaxPool 등록 버전은 8/10/11/12이며 1은 없다. registry는 모델 opset 이하 최신 ONNX schema의 `since_version`을 조회한다. 따라서 opset7의 MaxPool1 오류는 설명되며, opset13의 MaxPool12 선택은 이 오류를 피할 수 있는 근거다. 다른 연산까지 변환 성공한다는 보증은 아니다. [MaxPool 구현](https://github.com/ENOT-AutoDL/onnx2torch/blob/v1.5.15/onnx2torch/node_converters/max_pool.py), [registry](https://github.com/ENOT-AutoDL/onnx2torch/blob/v1.5.15/onnx2torch/node_converters/registry.py), [ONNX schema 이력](https://onnx.ai/onnx/operators/onnx__MaxPool.html)
- `derived = onnx.version_converter.convert_version(original, 13)`은 공식 default-domain opset 변환 API다. 필요한 adapter가 없으면 실패할 수 있으므로 import 버전 숫자만 수동 변경하지 않는다. 원본은 보존하고 파생 파일·변환 도구 버전·hash를 별도로 기록한다. [공식 API와 예외](https://onnx.ai/onnx/api/version_converter.html)
- **검증 절차:** 재학습이나 다른 weights 교체 없이 graph를 파생하되, 원래 initializer별 이름·dtype·shape·값 보존을 확인한다. `onnx.checker.check_model` 후 동일 입력으로 원본 ORT CPU→파생 ORT CPU→Torch CUDA의 전체 출력 오차와 top5를 비교한다. class258만 같다는 이유로 수치 동등성을 확정하지 않는다. 새 상수 initializer가 추가되는 경우 원래 학습 weights와 구분한다. 이번 단일 fixture 결과와 GPU 경고·trace 제한은 [실험 기록](../experiments/e0-accelerators.md)에 분리했다.
