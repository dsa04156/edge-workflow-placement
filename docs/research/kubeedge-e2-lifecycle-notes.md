# E2: Intel NPU 서비스 객체 수명과 재준비

확인일: 2026-10-16. 공식 OpenVINO 2025.3.0 소스·2025 Python API를 확인했다. Context7은 월간 quota 초과로 조회하지 못했으며 GitHub MCP와 공식 문서를 사용했다. 장비 실행·드라이버·클러스터 변경은 하지 않았다.

## 구현에 사용할 API 계약

- `core=ov.Core(); core.set_property({'CACHE_DIR':''}); model=core.read_model(path); compiled=core.compile_model(model,'NPU'); request=compiled.create_infer_request()`를 재준비 경로로 삼는다. `read_model`과 `compile_model`은 별도 단계이며, `NPU`를 직접 지정한다. [Python Core API](https://docs.openvino.ai/2025/api/ie_python_api/_autosummary/openvino.Core.html), [공식 NPU 사용법](https://github.com/openvinotoolkit/openvino/blob/2025.3.0/docs/articles_en/openvino-workflow/running-inference/inference-devices-and-modes/npu-device.rst)
- `CACHE_DIR`는 생략하거나 빈 문자열이면 OpenVINO 모델 캐싱이 비활성화되는 공식 property다. 이 설정은 기존 파일 삭제나 OS/드라이버 캐시 flush 명령이 아니다. 새 Core와 compile 설정에 다른 cache directory가 덮어씌워지지 않는지 함께 기록한다. [2025.3 property 계약](https://github.com/openvinotoolkit/openvino/blob/2025.3.0/src/inference/include/openvino/runtime/properties.hpp)
- 재사용 요청은 `request.infer(...)`의 동기 완료 뒤에만 해제한다. async를 썼다면 `wait()` 완료가 먼저다. E2에서는 inference와 release/prepare가 겹치지 않도록 서비스 잠금으로 직렬화한다. [InferRequest API](https://docs.openvino.ai/2025/api/ie_python_api/_autosummary/openvino.InferRequest.html)
- **제안 순서:** 결과를 독립 CPU 배열로 복사→외부 output tensor/view·callback·request·compiled model·필요시 읽은 model의 서비스 참조 제거→`gc.collect()`→필요할 때 위 재준비 경로. `None` 대입은 해당 참조만 제거하므로 지역변수·대기 요청·출력 wrapper에도 참조가 남지 않게 한다. [Python 객체 수명](https://docs.python.org/3.11/reference/datamodel.html#objects-values-and-types), [gc 계약](https://docs.python.org/3.11/library/gc.html#gc.collect)

## 남아 있을 수 있는 참조와 API의 한계

- 공식 Python wrapper는 `CompiledModel.__call__()`에서 생성한 `_infer_request`를 내부 보관해 다음 호출에 재사용한다. `create_infer_request()`는 별도 요청과 입출력 tensor를 만든다. C++ 동기 요청도 `shared_ptr<const ICompiledModel>`을 보관하므로 compiled 변수 하나만 지웠다고 모델 해제를 입증할 수 없다. [Python wrapper](https://github.com/openvinotoolkit/openvino/blob/2025.3.0/src/bindings/python/src/openvino/_ov_api.py), [요청의 shared ownership](https://github.com/openvinotoolkit/openvino/blob/2025.3.0/src/inference/dev_api/openvino/runtime/isync_infer_request.hpp)
- `CompiledModel.release_memory()`는 가능한 경우 중간 구조·cache·tensor·임시 버퍼를 해제하는 API다. compiled graph 전체 폐기, driver global cache flush 또는 device reset으로 정의되지 않는다. E2의 객체 참조 해제와 동일한 조작으로 섞지 않는다. [API 범위](https://docs.openvino.ai/2025/api/ie_python_api/_autosummary/openvino.CompiledModel.html#openvino.CompiledModel.release_memory)
- `Core.unload_plugin('NPU')`는 로드된 plugin instance와 자원 제거용 API이며, 미로드 상태면 예외를 낸다. 이것도 full device reset 보증이 아니다. 이번 E2는 같은 Core/plugin을 유지하는 **서비스 객체 재준비**로 한정하며 unload나 운영 드라이버 reset을 추가하지 않는다. [공식 unload 계약](https://docs.openvino.ai/2025/api/ie_python_api/_autosummary/openvino.Core.html#openvino.Core.unload_plugin)

## OpenVINO 파일 캐시와 NPU 드라이버 캐시의 구분

- 공식 NPU 문서는 OpenVINO 모델 cache와 기본 활성 UMD model cache를 구분한다. UMD bypass는 `NPU_BYPASS_UMD_CACHE`라는 별도 compile property이며, OpenVINO cache 사용 시 plugin이 UMD cache를 우회한다고 설명한다. 해당 절의 DirectX Shader cache 설명은 Windows 문맥이므로 현재 Linux 드라이버의 cache 구현·hit 여부까지 입증한 것은 아니다. [공식 NPU caching 절](https://github.com/openvinotoolkit/openvino/blob/2025.3.0/docs/articles_en/openvino-workflow/running-inference/inference-devices-and-modes/npu-device.rst#model-caching)
- **해석:** `CACHE_DIR=''`와 Python 참조 해제·GC를 수행해도 NPU global cache나 OS page cache가 초기화된다는 근거는 없다. 따라서 E2 결과는 “OpenVINO 파일 cache 비활성, 서비스 객체 재준비, driver/OS cache 유지”로 기록하고 “완전 cold compile”이나 “장치 reset”으로 부르지 않는다. GC 반환값도 장치 메모리 반환량이 아니다. [property 계약](https://github.com/openvinotoolkit/openvino/blob/2025.3.0/src/inference/include/openvino/runtime/properties.hpp), [gc 반환값](https://docs.python.org/3.11/library/gc.html#gc.collect)

## E2 기록 제안

- ARM CPU+Intel NPU 동일 3Task와 모델/입력 hash를 유지한다. source client가 edge/server로 번갈아 위치하는 조건과 NPU prepared/released 조건을 구분한다. release+GC, read_model, compile_model, request 생성, 첫 추론, warm 추론, 전체 workflow 시간을 별도로 남긴다.
- 서비스 generation·prepare 횟수·OpenVINO/plugin/driver 버전·`CACHE_DIR` 설정·실행 장치·오류를 기록한다. 재준비 후 기존 CPU reference와 top5/출력 오차를 다시 확인한다. 빠른 재compile 자체는 객체 해제 실패나 driver cache hit의 단독 증거가 아니다.
