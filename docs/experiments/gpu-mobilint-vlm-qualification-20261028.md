# GPU·Mobilint Qwen3-VL 실행 검증 — 2026-10-28

## 상태와 목적

후속 상태: SD 영구 캐시 준비 및 GPU CUDA 로딩을 완료했고 별도 `gmv-20261028t-bench1`에서 qualification8/8 및 본 측정80/80을 완료했다. 이 문서의 아래 결과는 이전 시도 기록이다. 최신 결과는 [실장치 반복 실측·슬라이드 자료](../presentations/vlm-runtime-20261028/README.md)를 따른다.

준비·입력 검증과 두 차례 코드 리뷰 완료. 첫 실제 배포에서 NPU 모델 로드에 성공했으나 GPU bootstrap 오류로 추론 전 중단했다. 이후 모빌인트 단독 검증에서 **4/4 실제 추론에 성공**했다. GPU 재시도는 파일 수신 중 노드 상태 보고가 끊겨 중단했으며, GPU 추론은 미검증이다. 사용자 요구는 GPU·NPU 실행이며, 이전 Intel CPU vision + NPU language smoke는 진단 기록으로 보존한다. 그 구성을 최종 선택으로 삼았던 결정은 철회했다.

Mobilint를 제외할 근거가 없었다. 기존 ARIES ResNet50 실측 성공과 공식 사전 컴파일 VLM 제공을 모델 선정에 반영하지 못한 것이 원인이다. qbcompiler 설치 유무는 공개 MXQ의 실행 가능성을 대신하지 않는다.

## 고정 후보

| 역할 | 장치 | 모델/설정 | 고정 revision |
|---|---|---|---|
| NPU | ser0004 Mobilint ARIES `aries-rb` | mobilint/Qwen3-VL-2B-Instruct, vision/text W4V8 MXQ, qbruntime1.4.0 | f4be56382079d7553d1d175d9063e689cf0d8a0c |
| GPU | dev0009 Jetson Orin CUDA | Qwen/Qwen3-VL-2B-Instruct BF16, torch2.9.1+cu130 | 89644892e4d85e24eaac8bacfd4f463576704203 |

같은 base 모델 계열이지만 양자화·정밀도가 다르다. 이 검증을 동일 정밀도 하드웨어 성능 비교나 배치 정책 우월성으로 해석하지 않는다. 실제 실행 후보 확인이 목적이며 정답 품질 평가·정책 goodput 실험은 포함하지 않는다.

NPU 경로의 호스트 가중치는 token embedding과 vision position embedding 두 테이블이다. 공식 소스에서 주요 vision encoder/projector, decoder, LM head는 MXQ 경로에 있다. 전처리, 토큰화, 임베딩 조회, 위치 보간, RoPE, sampling 및 장치 제어는 CPU에 남고 별도 기록한다. CPU 사용 0이라는 주장을 하지 않는다. Mobilint의 실제 가속기 실행 근거는 아래 live 결과에서 확인했다. CUDA는 준비 단계의 안정성 문제로 추론 미검증이다.

공식 출처: [Mobilint 모델](https://huggingface.co/mobilint/Qwen3-VL-2B-Instruct), [Mobilint SDK](https://github.com/mobilint/transformers-mblt), [ARIES VLM 예제](https://github.com/mobilint/aries-vision-vlm-demo). 상세 확인 수준은 [조사 노트](../research/mobilint-gpu-npu-vlm-feasibility-20261028.md).

## 입력과 검증 근거

기존 smoke의 입력 1713820, 3733440, 4556100, 3355890과 질문을 그대로 사용한다. 각 장치에서 smoke1 + 추가3, greedy 최대64토큰. image size shortest_edge65536 / longest_edge200704를 명시했다.

- 네 입력 모두 양쪽 processor의 chat text, input_ids, pixel_values, image_grid_thw가 byte-identical.
- 실제 grid는 748 또는784 pre-merge patch이며, 확인한 vision MXQ 제한4096 이내.
- contract SHA256: `91e1650f0a38ea43eb3d0dc56555119ac74773abfcdd6128a14cec9a98281891`.
- 최초 준비: `.scratch/gpu-mobilint-vlm-20261028/developer-frozen-r1/`; parity는 `developer-frozen-r1-parity.json`.
- 원본·파생 가중치와 SDK/wheel의 hash는 manifest에 기록했다. 전체 배포 파일9,955,125,218bytes, cache12Gi.
- 로컬 최초 테스트16/16 통과. 독립 R1 리뷰에서 여섯 고유 결함을 재현하여 수정 담당에게 전달했다. 이 테스트 성공은 실제 장치 추론 증거가 아니다.

R1 결함: 공개 실패 로그 누락, 스트림 직전 실제 Pod UID/node 재검증 누락, 추론 오류 후 자식 재사용, 출력 receipt identity 검증 부족, HTTP 절대시간/크기 제한 누락, 실제 측정되지 않는 NPU counter의 표시. 리뷰 기록은 `.scratch/gpu-mobilint-vlm-20261028/review-r1-summary.md`.

## 실행 결과

첫 실행 `artifacts/gpu-mobilint-vlm/gmv-20261028t0652`: NPU qbruntime1.4.0에서 vision/text MXQ 로드 성공. GPU는 모델·런타임 파일 수신 후 `python -m pip --version` 확인이60초를 넘겨 bootstrap_failed. 양쪽 inference 요청0회. 생성한4개 객체 모두 삭제, unresolved0, 새 API확인잔여0. GPU 준비 오류를 별도 진단하고 독립 장치 검증을 진행한다. 이번 호출의 입력 발신자는 수집기이며, 주 실험의 edge-source DAG 성능 결과가 아니다. 기존 ARIES530회 ResNet 성공이나 Intel4회 smoke를 이 모델의 실행 성공으로 합산하지 않는다.

### Mobilint 단독 실제 추론

`artifacts/gpu-mobilint-vlm/gmv-20261028t-npu-arm1`: 기존 입력·질문·최대64토큰 계약을 유지한 독립 장치 검증. 명시적 `--arm mobilint` 선택이며 전체 pair 성공으로 해석하지 않는다.

| 입력 | 출력 | 생성 토큰 수(EOS 포함) | 요청 내부 total_ms | TTFT_ms |
|---|---|---:|---:|---:|
| smoke-1713820 | skateboard |4|473.70|259.46|
| cal-3733440 | no |2|339.58|258.50|
| cal-4556100 | cone |2|328.81|261.78|
| cal-3355890 | pink |2|367.29|280.99|

정답 채점은 하지 않았다. total_ms는 자식 모델 프로세스의 전처리·생성·출력 디코딩 벽시계 시간이며 RPC·배포 시간을 포함하지 않는다. 각 응답의 UID/node/model/contract/input/output hash와 ARIES vision/text backend, 두 호스트 embedding table을 확인했다. CPU learned-matrix fallback은 없다.

호스트 CPU 사용을 숨기지 않는다. 네 요청의 전처리+생성 CPU시간 합2.283s, 요청 내부 벽시계 합1.509s로, 평균 **1.51 CPU core 상당**이다(단일 코어 사용률 환산151%; 전체 머신 사용률 아님). 이것만으로 전처리·조회·runtime 제어·busy waiting의 세부 기여를 분리할 수 없다. NPU aggregate counter는 SDK 경로상 측정 불가로 기록했다.

프로세스 exit0, 생성3개 객체 UID삭제/부재 확인, unresolved0, 새 API 잔여0. 동시에 dev0009에서 별도 GPU 진단이 진행됐으며 보호 snapshot에서 해당 노드의 시스템 Pod4개 ready False→True를 관측했다. 이를 NPU 작업의 영향이나 무영향으로 단정하지 않는다. 상세 검증: `.scratch/gpu-mobilint-vlm-20261028/npu-arm1-verification.json`.


### GPU 준비 지연 진단과 수정

첫 GPU 실패는 7.17GB 파일 수신 후 pip 기동 검사 60초 초과다. dev0009의 eMMC 쓰기 포화와 약1GB dirty writeback을 관측했다. 같은 장치·이미지·자원에서 진단한 결과, 7.17GB 쓰기 뒤 pip 기동은29.98초였고 backlog 배출 후2.78초였다. 실제60초 초과를 그대로 재현한 것은 아니므로 IO 병목 가설을 지지하는 부분 재현으로 기록한다.

CUDA 전용 `vlm_bootstrap.py`에 파일별 flush/fsync를 추가했다. 공유 bootstrap, 모델, timeout, 메모리 제한, 장치 선택은 유지한다. 수정된 부분의 로컬 경계 테스트11개를 coordinator가 직접 실행하여 통과했다. 테스트 HTTP 서버의 unclosed socket ResourceWarning 1건이 있었으나 테스트 실패는 없었다. 실제 장치 재시도는 `gmv-20261028t-gpu-arm1`이며 아래와 같이 중단됐다.

진단 r1과 dev0009의 일시적 readiness 이상이 겹쳤으며, dev0007의 별도 NodeStatusUnknown도 관측했다. 원인을 단정하지 않는다. 추가 쓰기 진단 r2는 중단하여 부분 기록을 보존했고, 생성 객체는 UID 기준으로 모두 삭제했다. fsync 조건의 pip 기동시간은 r2에서 측정되지 않았다. 더 이상의 합성 디스크 부하 반복은 수행하지 않는다. 상세: `.scratch/gpu-mobilint-vlm-20261028/developer-runtime-report.md`.


### 이전 CUDA 재시도 중단 상태

`gmv-20261028t-gpu-arm1`은 CUDA만 선택했다. 준비 서버로 7.17GB 업로드에415.67초가 걸렸고, GPU worker는07:42:32Z부터 파일을 받았다. 07:43:27Z에 dev0009 Ready/MemoryPressure/DiskPressure/PIDPressure가 Unknown으로 바뀌었다. HTTP health는 fetching으로 응답했지만 노드 상태 보고가 끊겼으므로07:45:05Z에 collector에 SIGINT를 보내 정상 finally 정리를 수행했다. **설치·모델 로드·추론에 도달하지 않았고 CUDA 추론0회다.** 파일별 fsync가 pip 지연을 해결했는지는 여전히 측정되지 않았다.

생성3개 객체 모두 UID 기준 삭제, cleanup verified=true, unresolved0. 별도 API 재확인에서도 Pod/ConfigMap 잔여0. dev0009는07:46:19Z에 Ready=True 및 Pressure=False로 회복했다. 보호 snapshot의 변경7건은 별도 dev0007 시스템 Pod ready True→False이며, 새로 나타나거나 사라진 외부 객체는 없었다. 이 snapshot만으로 실행 중 dev0009 상태 이상이 없었다고 해석하면 안 된다. 다른 노드의 상태 변화 원인은 확인하지 않았다.

해당 시도 종료 시점에 검증된 후보는 Mobilint ARIES의 Qwen3-VL-2B 4/4 실행이었다. CUDA 구성은 CPU로 대체하지 않았고 성공 후보로 등록하지 않는다. 다음 GPU 시도에는 매번 7GB 이상 쓰는 현재 bootstrap 경로를 줄이는 사전 준비 이미지·캐시 구성 또는 다른 검증 가능한 GPU 자원이 필요하다. 이는 다음 설계 과제이며 이번에 실행된 것으로 기록하지 않는다.

근거: `.scratch/gpu-mobilint-vlm-20261028/cuda-arm1-verification.json`, `monitor-cuda-arm1-*.json`, `cuda-cancel-node.json`; 원본 run의 phase/cleanup 기록. 이번 작업의 반복 진단과 두 GPU 시도는 모델 준비의 운영 문제를 보여주며 배치 정책·서비스 goodput 실험 결과가 아니다.
