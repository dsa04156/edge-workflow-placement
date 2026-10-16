# KubeEdge E0: 3-Task 추론 실행 근거 메모

확인일: 2026-10-16. Context7의 ONNX Runtime/KubeEdge 문서와 GitHub MCP의 ONNX Model Zoo·공식 Python 이미지 자료를 확인했다. 이 메모 작성 중 장비 접속·클러스터 변경·모델 다운로드·실행은 수행하지 않았다.

## E0에서 구분할 통과 조건

- `Ready`와 GPU/NPU resource 등록은 시작 근거다. device plugin의 등록·Allocate는 장치 노출/준비 계약이며 특정 모델의 연산자·자료형·산출물 실행 성공을 보장하지 않는다. backend별 실제 inference 출력이 별도 증거다. [Kubernetes Device Plugins](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/), [ORT capability/partition](https://onnxruntime.ai/docs/reference/high-level-design.html)
- 배치 대상은 기존 노드 label·`kubernetes.io/arch`·taint를 읽어 선택하고 `nodeSelector`/required affinity로 고정한다. `spec.nodeName`은 scheduler를 우회하므로 정상 배치 검증과 구분한다. [Pod 노드 배정](https://kubernetes.io/docs/tasks/configure-pod-container/assign-pods-nodes/)
- edge Pod의 상태 조회와 `logs/exec`는 별도 경로다. 후자는 CloudStream/EdgeStream tunnel·배포 방식의 영향을 받는다. 실패 시 추론 실패로 분류하기 전에 관측 경로를 확인한다. 기존 설정을 조사하며 문서의 iptables 초기화/재시작 명령을 그대로 실행하지 않는다. [KubeEdge logs/exec](https://kubeedge.io/docs/advanced/debug/)

## 작은 CPU 기준선 후보

| 항목 | 근거·권고 |
|---|---|
| 모델 | ONNX Model Zoo의 FP32 `squeezenet1.0-12.onnx`, opset 12, 약 4.95 MB, Apache-2.0. 우선 CPU 실행성 기준선으로 사용하고 NPU 호환성은 별도 검증한다. [공식 README](https://github.com/onnx/models/blob/main/validated/vision/classification/squeezenet/README.md), [공식 배포](https://huggingface.co/onnxmodelzoo/squeezenet1.0-12) |
| 모델 고정 | 검토한 HF revision `0e6cddd4291d78516c22543bc5a347d5916154a0`, SHA256 `dec81a8684617770b3cf13fadc1d92565d1d453d23935fc6388b792d99c992bd`. 실행 담당자가 실제 받은 파일과 대조한다. [artifact metadata](https://huggingface.co/onnxmodelzoo/squeezenet1.0-12/blob/0e6cddd4291d78516c22543bc5a347d5916154a0/squeezenet1.0-12.onnx) |
| 파일 직접 경로 | [revision 고정 ONNX 파일](https://huggingface.co/onnxmodelzoo/squeezenet1.0-12/resolve/0e6cddd4291d78516c22543bc5a347d5916154a0/squeezenet1.0-12.onnx). 이 조사에서는 내려받지 않았다. |
| 다운로드 경로 | GitHub Model Zoo는 역사 보존용이며 2025-07-01부터 LFS 다운로드 종료를 명시한다. 오래된 raw GitHub 명령보다 공식 안내의 HF 모델 파일을 사용한다. 전체 저장소 clone 불필요. [공식 종료 안내](https://github.com/onnx/models/blob/main/README.md) |
| CPU runtime | 공식 Python CPU package `onnxruntime`는 Linux x64/ARM64를 지원하며 `InferenceSession(..., providers=['CPUExecutionProvider'])`를 명시한다. 이것은 NPU 사용 증거가 아니다. [설치](https://onnxruntime.ai/docs/get-started/with-python.html), [공식 실행 예](https://onnxruntime.ai/docs/api/python/sources/tutorial.rst.txt) |
| 이미지 | `python:3.11-slim-bookworm` 공식 base는 amd64/arm64v8 manifest가 있다. 동일 소스에서 architecture별 이미지를 만들되 base digest·ORT/NumPy/Pillow 버전을 고정하고 각 architecture의 wheel 설치 가능 여부를 먼저 확인한다. 완성된 추론 이미지의 존재·작동은 이 메모에서 확인하지 않았다. [공식 이미지 manifest](https://github.com/docker-library/official-images/blob/master/library/python) |

## 추천 3-Task 계약

1. **decode/preprocess**: 사용권이 확인된 고정 JPEG/PNG를 decode, RGB·resize/crop·정규화하고 `float32 NCHW [1,3,224,224]` 텐서와 input hash를 출력한다. README는 `[0,1]`, mean `[0.485,0.456,0.406]`, std `[0.229,0.224,0.225]`를 제시한다. 정확한 전처리는 artifact별 reference output으로 검증한다. [모델 입력 계약](https://github.com/onnx/models/blob/main/validated/vision/classification/squeezenet/README.md)
2. **classify**: 세션의 실제 input/output metadata를 읽고 CPU EP로 실행한다. 모델 hash·ORT 버전·provider·노드/arch·load/infer 시간과 출력 텐서를 남긴다. output shape·finite 여부를 검사하며 graph에 softmax가 있으면 중복 적용하지 않는다. [ORT Python API](https://onnxruntime.ai/docs/api/python/api_summary.html)
3. **postprocess**: 실제 출력 shape를 flatten해 1000-class top-k의 class index/score를 JSON으로 기록한다. label 파일은 같은 모델 출처/버전에 맞춘다. 한 이미지의 성공을 정확도 평가나 모든 NPU 지원으로 확대하지 않는다. [모델 출력·labels](https://github.com/onnx/models/blob/main/validated/vision/classification/squeezenet/README.md)

전처리 기준 구현은 shorter-side 256 bilinear resize(비율 유지·round), center 224 crop 순서다. [공식 코드](https://github.com/onnx/models/blob/main/validated/vision/classification/imagenet_preprocess.py). 알려진 smoke fixture 후보는 [PyTorch Hub dog.jpg 고정 revision](https://raw.githubusercontent.com/pytorch/hub/c3beaae7d32fca2a23fec30aa7938ef5c9b6e5d5/images/dog.jpg)(GitHub metadata 661378 bytes)다. 이미지 개별 라이선스는 이번 확인에서 미확인이며 모델의 Apache-2.0과 동일하다고 단정하지 않는다. README의 output 예시는 여러 SqueezeNet 변형 공용이므로 실제 ONNX output 이름/shape를 사용한다.

## 실행 담당자가 남길 최소 증거

- 기존 대상 노드 하나에서 image pull→Pod 시작→실제 모델 load→3단계 완료와 결과 수집을 우선 확인한다. amd64/arm64 비교는 동일 입력·모델 hash와 고정 스레드 조건으로 수행한다.
- Task를 서로 다른 Pod/노드에 둘 경우 tensor/result 전달 경로도 별도 통과 조건이다. 노드 로컬 `hostPath`가 다른 노드에도 공유된다고 가정하지 않는다. 현 클러스터의 기존 전달 수단을 먼저 확인한다.
- CPU 통과 뒤에만 해당 backend의 지원 연산자·dtype·컴파일 artifact·driver/runtime 및 실제 선택된 EP/SDK를 확인한다. CPU fallback 성공을 GPU/NPU 성공으로 집계하지 않는다.
- 위 항목은 실행 계획이다. 현재 10노드의 CPU inference, edge logs/exec, cross-node artifact 전달, Hailo/Rockchip/Intel/Mobilint 모델 실행은 **이 조사자가 검증하지 않았다**.

## 후속 발견: 실행 성공과 의미적 smoke 통과의 분리

- 실행 담당자는 위 고정 SHA의 `squeezenet1.0-12`가 x86 ORT CPU에서 실행되지만 dog 이미지의 top-k에 예상 Samoyed가 없다고 전달했다. 따라서 초기 후보의 **모델 실행 성공만 확인됐고 품질/의미적 smoke는 미통과**다. 이 조사자가 재실행한 결과는 아니다.
- 공식 저장소의 [#659](https://github.com/onnx/models/issues/659)는 1.0 예측 문제의 직접 사용자 보고이며, 원인 확정·수정은 아니다. [공용 README](https://github.com/onnx/models/blob/main/validated/vision/classification/squeezenet/README.md)는 1.0을 Caffe2 변환으로 구분하지만 공통 RGB 정규화를 제시한다. **1.0-12가 BGR/raw255/별도 mean을 요구한다는 artifact별 공식 근거는 이번 조사에서 확보하지 못했다.**
- 대체 검증 후보는 Apache-2.0 `squeezenet1.1-7`의 [고정 파일](https://huggingface.co/onnxmodelzoo/squeezenet1.1-7/resolve/61e525224ad479521059f4586bcacf50ad3627ca/squeezenet1.1-7.onnx), SHA256 `1eeff551a67ae8d565ca33b572fc4b66e3ef357b0eb2863bb9ff47a918cc4088`, 4,956,208 bytes다. [배포 metadata](https://huggingface.co/onnxmodelzoo/squeezenet1.1-7/commit/61e525224ad479521059f4586bcacf50ad3627ca). 열람한 공식 모델표에서 확인한 1.1 배포명은 `1.1-7`이며 `1.1-12`를 가정하지 않는다.
- 1.1의 공식 [ONNX inference notebook](https://github.com/onnx/models/blob/main/validated/vision/classification/imagenet_inference.ipynb)은 RGB·Resize256·CenterCrop224·ToTensor·mean/std 경로를 사용한다. [Microsoft ORT 예제](https://github.com/microsoft/onnxruntime-inference-examples/blob/main/c_cxx/OpenVINO_EP/Linux/squeezenet_classification/squeezenet_cpp_app.cpp)도 `1.1-7`과 RGB/255 정규화를 사용한다. input/output 이름·shape는 실제 세션 metadata로 확인한다.
- [PyTorch 공식 SqueezeNet 예](https://github.com/pytorch/hub/blob/master/pytorch_vision_squeezenet.md)는 같은 dog 파일과 RGB 정규화를 사용하고, [공식 labels](https://github.com/pytorch/hub/blob/master/imagenet_classes.txt)의 0-based 258은 Samoyed다. 그러나 해당 **ONNX artifact+dog의 공식 top1=258 golden output은 확보하지 못했다**. 258은 이미지 의미와 label mapping에 기반한 smoke 예상이며 reference benchmark로 표기하지 않는다. 대체 후보도 실제 출력 확인 전 성공으로 기록하지 않는다.
