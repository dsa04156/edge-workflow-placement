# Mobilint ARIES + GPU VLM 실행 후보: 2026-10-28

## 결론과 확인 범위

**Mobilint를 제외하면 안 된다. 우선 후보는 `Qwen/Qwen3-VL-2B-Instruct`의 GPU 실행과 공식 `mobilint/Qwen3-VL-2B-Instruct`의 ARIES 실행이다.** 최신 공식 패키지는 qbruntime 1.4.0 이상을 요구하며, 공식 Qwen3-VL 문서는 8B의 현재 런타임 오류를 명시하면서 2B/4B 사용을 안내한다. 따라서 기존 ser0004의 qbruntime 1.4는 사전 배제 사유가 아니다. 다만 이전 ResNet50 성공은 이 VLM의 실제 실행·메모리·성능 검증이 아니다.

이 문서는 공식 문서·소스·모델 메타데이터를 읽은 조사다. 모델 가중치 다운로드, 설치, 구현, 배포, live 추론을 하지 않았다. ser0004 ARIES/qbruntime 1.4/ResNet50 성공은 프로젝트 제공 전제다. CPU에서 대형 vision encoder 또는 decoder를 실행하는 경로를 GPU/NPU 완료로 인정하지 않는다.

이전 `modern-vlm-workload-candidates-20261028.md`의 Intel NPU/Qwen2.5-VL 우선안은 이번 GPU+Mobilint 선정의 확정안이 아니라 이전 진단 기록으로 읽는다. 해당 문서는 보존한다.

## 실제 공개 산출물

| 후보 | 정확한 공개 산출물과 근거 | 판단 |
|---|---|---|
| **Qwen3-VL-2B-Instruct** | Mobilint Hub revision `f4be56382079d7553d1d175d9063e689cf0d8a0c`. `Qwen3-VL-2B-Instruct_vision-W4V8.mxq`, `Qwen3-VL-2B-Instruct_text-W4V8.mxq`. 양쪽 `target_device=aries-rb`, `core_mode=global8`; `dynamic_vision=true`, `max_batch_size=1`. 공개·비게이트, 카드 Apache-2.0. | **주 후보.** 현대 VLM의 두 큰 추론 구성요소가 ARIES 대상으로 제공된다. 파일명 W4V8을 임의의 전체 정밀도 설명으로 확대하지 않는다. |
| Qwen2-VL-2B-Instruct | revision `d807d0b32a1b285cb7a1970a94508dab65fd8b14`. `Qwen2-VL-2B-Instruct_vision_transformer.mxq`, `Qwen2-VL-2B-Instruct_text_model.mxq`. 공개·비게이트, 카드 Apache-2.0. 공식 ARIES 데모의 기본 VLM. | **호환성 대체 후보.** Qwen3 실행 검증 실패 시 모델을 바꾸는 별도 조건으로 기록. |
| Qwen3-VL-4B-Instruct | 공식 Mobilint 모델 카드와 별도 vision/text MXQ config 확인. | 추가 용량 후보이며 첫 검증 범위를 늘릴 필요 없음. |

출처: [Qwen3 2B 고정 config](https://huggingface.co/mobilint/Qwen3-VL-2B-Instruct/blob/f4be56382079d7553d1d175d9063e689cf0d8a0c/config.json), [Qwen3 2B Hub API](https://huggingface.co/api/models/mobilint/Qwen3-VL-2B-Instruct), [Qwen2 고정 config](https://huggingface.co/mobilint/Qwen2-VL-2B-Instruct/blob/d807d0b32a1b285cb7a1970a94508dab65fd8b14/config.json), [Qwen2 카드](https://huggingface.co/mobilint/Qwen2-VL-2B-Instruct), [Qwen3 4B 카드](https://huggingface.co/mobilint/Qwen3-VL-4B-Instruct). 2B metadata/config는 root가 보존한 `.scratch/gpu-mobilint-vlm-20261028/` 자료도 읽어 대조했다. Safetensors에 표시된 작은 파라미터 수는 MXQ를 포함한 전체 VLM 크기가 아니다.

## 현재 패키지와 qbruntime 1.4의 관계

- `mblt-model-zoo`는 deprecated compatibility facade이며, 언어/VLM 경로는 `transformers-mblt`, 공통 NPU backend는 `mblt-npu-python`으로 이동했다. **사전 컴파일 MXQ 실행에 qbcompiler 설치가 필수라는 근거는 없다.** 모델 zoo는 실제 컴파일 호출 시에만 compiler를 요구하도록 분리했다. [공식 model-zoo](https://github.com/mobilint/mblt-model-zoo)
- 확인한 `transformers-mblt` revision: `a8b94431ef5961b942ee73f577747ff8a794a211`. 의존성은 Python `>=3.10,<3.13`, `mobilint-qb-runtime>=1.4.0`, `mblt-npu-python>=0.1.0`, Transformers `>=4.54.0,<5.18.0`, Torch `>=2.4.1`. 이는 패키지 허용 범위이며 모든 모델×버전 조합의 성공 증명은 아니다. [고정 pyproject](https://github.com/mobilint/transformers-mblt/blob/a8b94431ef5961b942ee73f577747ff8a794a211/pyproject.toml)
- `mblt-npu-python` revision `fd4b350e7a82ce55310dd021872730f68386d0a8` 역시 runtime `>=1.4.0`을 요구한다. [고정 의존성](https://github.com/mobilint/mblt-npu-python/blob/fd4b350e7a82ce55310dd021872730f68386d0a8/pyproject.toml)
- 공식 runtime 1.4 문서의 표는 runtime 1.0 이상에 MXQ v1–v7 지원을 명시한다. 모델별 MXQ 형식·target과 driver/firmware/OS 조합은 별도 확인해야 한다. 문서 제시 확인 수단은 `mobilint-cli mxqtool show <file>`이며 여기서는 실행하지 않았다. [runtime 1.4 compatibility](https://docs.mobilint.com/runtime/v1.4/en/compatibility.html)

## CPU에 무엇이 남는가

Qwen3-VL 공식 구현을 읽으면 vision은 MXQ `infer`를 호출하고 text MXQ는 logits를 반환한다. `lm_head`는 `nn.Identity`다. 반면 **text token embedding과 dynamic vision position embedding은 PyTorch `nn.Embedding`으로 남는다.** vision MXQ 출력 계약은 merger와 세 DeepStack feature를 포함한다. 그러므로 별도 대형 host LM head가 필수라는 근거는 없지만 “모든 학습 파라미터·모든 연산이 NPU”라고 부를 수도 없다. [고정 구현: L233, L774–795, L1024, L1845–1847, L1978–1981](https://github.com/mobilint/transformers-mblt/blob/a8b94431ef5961b942ee73f577747ff8a794a211/transformers_mblt/models/qwen3_vl/modeling_qwen3_vl.py)

공식 API 문서에서 `device=cpu`는 host-side layer 배치이며 MXQ 실행 장치를 CPU로 바꾸지 않는다. embedding·전처리·RoPE·sampling·host↔NPU 복사는 측정 항목으로 남겨야 한다. GPU로 host layer를 옮기면 오히려 추가 복사가 생길 수 있다. 이 문서의 Qwen3-VL release contract는 8B가 runtime 1.4.0에서 오류를 내므로 2B/4B를 쓰라고 명시한다. vision/text MXQ·processor·config는 함께 컴파일/보정된 한 release이며 임의 혼합은 불가하다. [고정 API 문서: Qwen3-VL release contract / Original Keyword Parameters](https://github.com/mobilint/transformers-mblt/blob/a8b94431ef5961b942ee73f577747ff8a794a211/transformers_mblt/README.md)

## GPU와 NPU를 쓰는 구체적인 범위

**첫 검증은 완전한 VLM 서비스의 GPU 후보와 ARIES 후보를 모두 확보하는 것**이다. ARIES 공식 데모도 Qwen2-VL의 vision/text를 NPU core에 배치하고 NPU가 없을 때 CUDA를 사용한다. 이는 CPU-only serving을 지원하지 않는 실제 vendor 실행 경로다. 데모의 임의 자동 fallback은 본 실험에서 그대로 허용하지 말고 배치와 실장치를 기록해야 한다. [공식 데모 고정 README](https://github.com/mobilint/aries-vision-vlm-demo/blob/ada61024d7b5e200d728062318d948585d0f10c5/README.md), 주요 파일 `backend_vlm/src/ImageTextToTextPipeline.py`, `core_allocation.yaml`.

서비스 DAG 후보는 엣지 입력→GPU/NPU 영상 탐지·ROI 선택→GPU/NPU VLM 설명 생성→응답 조립이다. 동일 입력 서비스에서 실제 탐지 산출물이 VLM 입력을 결정해야 하며 ResNet 분류만으로 대체하지 않는다. 모델 내부 vision/prefill/decode는 우선 진행 신호의 구분이며 자동으로 독립 배치 Task가 되는 것은 아니다.

GPU vision→ARIES text, 또는 ARIES vision→GPU text는 **후속 공학 가설**이다. Qwen3-VL은 merger뿐 아니라 DeepStack feature와 위치/RoPE 계약이 있어 embedding 하나만 보내는 연결이 아니다. MXQ 출력 순서·shape·dtype·보정 release·전송 bytes·host staging을 확인하기 전에는 지원 완료로 선언할 수 없다. 지금은 공식 두 MXQ 경계가 존재한다는 사실까지만 확인했다.

## 검증이 남은 구체 조건

1. 현재 ser0004 driver/firmware와 runtime 1.4 정확 patch, Python ABI, NPU 보드 메모리 및 점유 상태. 서버 RAM 32GB를 NPU 메모리로 간주하지 않는다.
2. 고정한 2B artifact와 wheel 버전 조합에서 vision/text MXQ 모두 로드·실행되는지, host learned component와 복사 시간이 얼마인지. GPU 실행도 GPU 메모리·커널 실행으로 별도 확인한다.
3. 입력 해상도·영상 토큰·문맥 길이·출력 토큰을 고정하고 queue, vision encode, prefill, TTFT, decode/ITL, host time, 최종 성공을 분리한다. 부분 성공을 전체 VLM 성공으로 집계하지 않는다.
4. GPU 원본과 Mobilint 양자화 산출물은 같은 원본 모델 계열이지만 동일 정밀도는 아니다. 변환/정밀도 효과와 배치 효과를 분리하고 기존 고정모델 계약에 파생 산출물 hash를 연결한다.

ARIES와 REGULUS는 동일 후보가 아니다. 패키지가 두 계열을 지원하더라도 각 MXQ는 특정 target용이다. 이 조사에서 선택한 것은 **ARIES-RB artifact**이며 target 문자열만 REGULUS로 바꾸는 것은 이식 검증이 아니다. Intel NPU와 Hailo 역시 별도 조합으로 남겨야 하며 Mobilint 성공을 다른 NPU 지원으로 확대하지 않는다.
