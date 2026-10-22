# 다중 엣지 RTSP 영상·생성 텍스트의 최소 CPU 재현 구성

확인일: 2026-10-22. **자료 조사와 로컬 자산 읽기 결과이며 설치·추론·RTSP 송수신·클러스터 검증 결과가 아니다.** 이 문서만 작성했다. `research` 스킬의 1차 자료·단일 보고서 원칙을 적용했고, 요청에 따라 추가 worker는 만들지 않았다. Context7 `resolve_library_id(MediaMTX)`는 `Monthly quota exceeded`로 실패했다. GitHub MCP로 MediaMTX 설정/라이선스와 Ultralytics 고정 버전 설정을 읽고 공식 문서·배포 API로 보완했다.

## 1. 권장 결정

**첫 실행 후보는 각 엣지의 MediaMTX + FFmpeg 파일 재생, YOLO11n CPU FP32, 영어 이벤트 + all-MiniLM-L6-v2 CPU FP32다.** 코디네이터 후속 지시와 [파일럿 명세](../../.scratch/multi-edge-sensors/spec.md)에 따라 작은 영어 모델을 우선하며, 원 조사 대상인 다국어 MiniLM은 확장 선택지로 보존한다. Python 3.11과 기존 torch 2.8.0 CPU wheel을 재사용한다. 영상·텍스트 source는 엣지에 고정하되 추론 위치는 배치 정책이 선택하도록 분리한다. ONNX Runtime은 동일 품질을 확인한 후의 대안이다. 이는 구현 부담을 줄이려는 판단이지 성능 우위의 측정 결과가 아니다.

| 역할 | 최소 후보 | 결정 근거/조건 |
|---|---|---|
| 영상 source | 기존 COCO JPEG → 고정 변환·순서의 H.264 MP4 → 엣지 FFmpeg `-c copy` → 엣지 MediaMTX RTSP | 객체가 있는 이미지로 실제 검출기를 구동한다. 파일 준비와 반복 송출 비용을 분리한다. |
| 검출 | `yolo11n.pt`, Ultralytics `8.3.220`, `device="cpu"`, FP32 | 공식 COCO 80종 검출 모델. YOLOv8n은 명시적 대체 후보이며 노드마다 서로 다른 모델로 바꾸지 않는다. [모델 문서](https://docs.ultralytics.com/models/yolo11/) |
| 텍스트 source | 엣지에서 고정 영어 템플릿·어휘표로 문장을 생성하고 UTF-8 JSONL/HTTP로 전달 | 별도 LLM 서버가 필요 없다. synthetic text로 명시하고 같은 논리 요청을 모든 정책에 제공한다. 한국어는 다국어 확장에 포함한다. |
| 첫 문장 임베딩 | `sentence-transformers/all-MiniLM-L6-v2` | 영어, 384차원, 256 token, mean pooling 후 L2 정규화. 기존 Transformers로 실행 가능. [모델 카드](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) |
| 다국어 확장 | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 한국어 포함, 384차원, 128 token, 기본 mean pooling. 모델 크기와 정규화 계약이 영어 모델과 다르다. [모델 카드](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2) |
| 공통 runtime | 기존 CPython 3.11, torch `2.8.0+cpu`, NumPy `1.26.4`, Transformers `4.57.1` | 양 아키텍처 wheel이 로컬에 있다. 모델 실행 성공은 별도 확인해야 한다. |

시작 부하 제안은 영상 640×480, 5 fps 송출·사전 지정 1 fps 추론, 텍스트 batch=1·1 request/s다. 이는 작은 CPU에서 점검하기 위한 **미측정 시작값**이다. 입력 token 수, CPU thread 수, RSS, p95 지연·누락을 확인한 후 부하를 올린다. 발표된 서버 CPU benchmark를 Raspberry Pi/Jetson CPU 성능으로 옮기지 않는다.

## 2. 읽기 전용으로 확인한 재사용 자산

- `artifacts/topology-profile-assets/20261019T091725Z/selected-data/pairs.json`: SHA256 `d680aab23eac79a804800d372e6a49abe38aeeb7cca2ceca7caa81bf67930c97`. 기존 seed20261019로 선택한 calibration 20 / evaluation 40의 COCO 이미지 60개가 있다. 이번 조사에서 **60개 모두 실제 파일 hash가 manifest와 일치**했으며 합계 10,570,304 B다. 다운로드하지 않았다. [기존 확보 기록](topology-profile-asset-acquisition.md)
- `artifacts/topology-profile-live/model-assets/`: ResNet18 46,830,571 B, MUTAN 135,343,293 B, wav2vec2 safetensors 377,607,901 B 및 vocabulary/config가 있다. **이 디렉터리에는 YOLO/MiniLM 가중치가 없다.** 기존 VQA/ASR 모델을 YOLO 검출·다국어 임베딩의 대체 검증으로 쓰지 않는다.
- 위 두 자산 경로와 wheelhouse는 `/mnt/data3tb/user-storage/jinuk/scheduler-storage/artifacts/` 아래로 연결되는 symlink다. 원본·동결 manifest를 수정하지 않고 새 실험은 별도 입력 manifest를 만든다.
- `artifacts/topology-profile-live/wheelhouse/{x86_64,aarch64}/`에 아래 wheel이 실제 존재한다. torch 두 파일의 SHA256을 이번에 다시 계산했고 기존 requirements hash와 일치했다. CPython 3.11 및 `manylinux_2_28` 기준이므로 32-bit ARM이나 musl 기반 이미지를 대상으로 삼지 않는다.

| wheel | 로컬 크기 B | SHA256 또는 동결 근거 |
|---|---:|---|
| `torch-2.8.0+cpu-cp311-cp311-manylinux_2_28_x86_64.whl` | 184,053,363 | `cb06175284673a581dd91fb1965662ae4ecaba6e5c357aa0ea7bb8b84b6b7eeb` |
| `torch-2.8.0+cpu-cp311-cp311-manylinux_2_28_aarch64.whl` | 102,073,276 | `680129efdeeec3db5da3f88ee5d28c1b1e103b774aef40f9d638e2cce8f8d8d8` |
| torchvision x86_64 `0.23.0+cpu` / aarch64 `0.23.0` | 1,911,930 / 2,395,543 | [x86 requirements](../../artifacts/topology-profile-live/requirements-x86_64.txt), [ARM requirements](../../artifacts/topology-profile-live/requirements-aarch64.txt) |
| Transformers `4.57.1` | 11,990,925 | 같은 requirements 및 양쪽 wheelhouse |

로컬 `/usr/bin/ffmpeg -version`은 `6.1.1-3ubuntu5`, amd64, `--enable-gpl --enable-libx264`를 반환했다. 로컬 PATH에 MediaMTX는 없었다. 이는 coordinator 호스트 관찰이며 엣지의 설치 상태를 말하지 않는다. 구현 시 FFmpeg의 실제 배포 패키지 버전·빌드 옵션과 양쪽 컨테이너 digest를 고정해야 하며, 현재 호스트 버전을 양쪽 엣지에 설치 완료한 것으로 기록하면 안 된다.

## 3. 공식 자산과 고정값

크기는 파일 bytes이며 RAM 요구량이 아니다. API metadata와 실제 다운로드 hash를 구분했다. 이번에 네트워크로 읽은 모델 본문은 **YOLO11n 5.6MB 한 개뿐**이며 메모리에서 SHA256만 계산하고 저장·로드하지 않았다. 10MB 초과 모델·wheel·바이너리를 다운로드하지 않았다.

| 자산 | 고정 위치·크기 | 확인 수준 |
|---|---|---|
| YOLO11n | [Ultralytics assets v8.3.0/yolo11n.pt](https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt), 5,613,764 B | 실제 응답 SHA256 `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`. 공식 표의 파라미터 수는 2.6M이며 파일 크기와 FP32 실행 메모리는 다르다. |
| YOLOv8n 대안 | [동일 release/yolov8n.pt](https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt), 6,549,796 B | [공식 release API](https://api.github.com/repos/ultralytics/assets/releases/tags/v8.3.0)의 크기만 확인. API digest=null이므로 선택 시 실제 SHA256을 취득한다. |
| 영어 MiniLM FP32 | [all-MiniLM-L6-v2 고정 revision](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/tree/1110a243fdf4706b3f48f1d95db1a4f5529b4d41)의 `model.safetensors`, 90,868,376 B | [HF API](https://huggingface.co/api/models/sentence-transformers/all-MiniLM-L6-v2?blobs=true)의 LFS SHA256 `53aa51172d142c89d9012cce15ae4d6cc0ca6895895114379cacb4fab128d9db`. 본문 미다운로드. tokenizer.json 466,247 B, vocab.txt 231,508 B. |
| MiniLM FP32 | [고정 revision](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/tree/e8f8c211226b894fcb81acc59f3b34ba3efd5f42)의 `model.safetensors`, 470,641,600 B | [HF API](https://huggingface.co/api/models/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2?blobs=true)의 LFS SHA256 `eaa086f0ffee582aeb45b36e34cdd1fe2d6de2bef61f8a559a1bbc9bd955917b`. 본문 미다운로드. |
| MiniLM ONNX FP32 대안 | 같은 revision의 `onnx/model.onnx`, 470,301,610 B | LFS SHA256 `10f7a088420252b26caf819236ca2c9d2987afd0fc06fec7553b542a5655a05a`. 본문·실행 미검증. |
| MediaMTX | [v1.21.2 release](https://github.com/bluenviron/mediamtx/releases/tag/v1.21.2), linux amd64 28,345,997 B / arm64 30,503,607 B | [release API](https://api.github.com/repos/bluenviron/mediamtx/releases/tags/v1.21.2) digest: amd64 `121be6e00397e473a95d83c75f4fcd3819a6af3b0e1a50889cba43ab4fab6a27`; arm64 `180a235374a548d9133cdd5e20f5dbd8b01317be1c86e0d7e66df585dc42ed6f`. 다운로드하지 않았다. |
| Ultralytics wheel | [8.3.220 metadata](https://pypi.org/pypi/ultralytics/8.3.220/json), 1,078,083 B | wheel SHA256 `2c59923cc2a1ed8f5ebdf865820c5f9829c721f6a7933712bee39519bad3caec`. 소스 commit `b35e516270349ea39ee9dd18d6e593012a589c46`. |

MiniLM의 큰 파일은 다국어 어휘 임베딩을 포함한다. 고정 config는 12 layer, hidden=384, vocab=250037이다. **영어 `all-MiniLM-L6-v2`의 작은 크기를 이 다국어 모델 크기로 인용하지 않는다.** 필요한 파일은 같은 revision의 safetensors, `config.json`, `tokenizer.json`(9,081,518 B), `tokenizer_config.json`, `special_tokens_map.json`, `modules.json`, `sentence_bert_config.json`, `1_Pooling/config.json`이다. slow tokenizer를 사용할 경우 `sentencepiece.bpe.model`(5,069,051 B)도 필요하다. fast tokenizer만 쓰는 첫 구성은 `tokenizer.json`을 동결한다. 전체 repository snapshot에는 중복 PyTorch/ONNX/OpenVINO 산출물이 있으므로 필요한 형식만 확보한다. [고정 파일 트리](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/tree/e8f8c211226b894fcb81acc59f3b34ba3efd5f42)

Ultralytics `8.3.220`에는 OpenCV 등 추가 의존성이 있다. `opencv-python==4.11.0.86`은 기존 NumPy 1.26.4와 호환되는 요구 범위이고 aarch64/x86_64 Linux wheel이 모두 있다(42,230,439 / 62,986,597 B). 이 버전의 [PyPI metadata](https://pypi.org/pypi/opencv-python/4.11.0.86/json)까지 확인했으며 설치·공유 라이브러리 로딩은 하지 않았다. `pip install -U`로 기존 torch/NumPy를 바꾸지 않고 현재 constraints를 유지한 별도 환경에서 의존성 전체를 해결·동결한다. OpenCV headless 교체는 패키지 의존성 처리까지 필요한 별도 선택이므로 이 조사에서 자동 대체하지 않았다.

## 4. 의미 있는 영상과 sequence 재현

**권장 입력은 기존 60개 COCO 사진의 순차 재생에 고정 pan/zoom·밝기 변화를 적용한 synthetic replay다.** 사람·차량·동물 등 검출 대상이 있는 실제 사진을 이용한다. `testsrc`/컬러바는 RTSP 연결 점검용으로만 사용한다. 검출 개수나 scene 복잡도에 따른 후처리·출력 bytes를 평가할 때 컬러바를 대표 입력으로 삼지 않는다. FFmpeg는 `zoompan`, `crop`, `scale`, `eq` 필터를 제공한다. [필터 문서](https://ffmpeg.org/ffmpeg-filters.html#zoompan)

1. 기존 calibration/evaluation 소속을 유지한다. 현재 bounded pilot은 명세의 보정 3개·평가 3개를 이 pool에서 사전 선택할 수 있으며 60개 전체 실행을 요구하지 않는다. 같은 원본 이미지에서 만든 변환 프레임이 양 split에 걸치지 않게 한다. 최초 선택은 검출 결과를 보기 전에 고정하며 검출이 안 되는 입력도 보존한다.
2. 새 seed는 `20261022`, generator revision과 변환표를 명시한다. 노드 ID 대신 안정적인 `source_id=edge-a/edge-b`를 사용한다. `(seed, source_id, split, image_id, frame_index)`의 canonical UTF-8 직렬화·SHA256에 따른 정수 선택 규칙을 정하면 Python `hash()`나 전역 PRNG 소비 순서에 의존하지 않는다. **실제 변환 매개변수 배열도 manifest에 저장**한다. seed만으로 재현성을 주장하지 않는다.
3. 동일 split의 JPEG를 640×480 캔버스에 비율 보존 배치하고, 예를 들어 이미지당 10프레임·5 fps와 1.00–1.05배 확대/소폭 이동을 사전 고정한다. 변환은 움직임을 합성한 것이며 자연 영상의 추적·행동 정확도 benchmark가 아니다. 새 원본 영상이 필요한 경우 출처·허가·파일 hash를 먼저 고정한다.
4. 엣지의 준비 단계에서 H.264/yuv420p, 고정 fps·GOP·B-frame 설정으로 MP4를 만든 뒤 파일 hash·ffprobe 정보·순서를 동결한다. 같은 seed라도 FFmpeg/x264 빌드·CPU별 인코딩 bytes가 같다고 가정하지 않는다. 정책 비교에는 동일한 완성 MP4 bytes를 쓰거나 엣지별 생성 산출물의 동일성을 별도로 입증한다. source는 실제 송출 엣지로 기록한다.
5. 요청 식별자는 `(dataset_id, sequence_id, source_id, loop_index, frame_index)`로 두고 원본 image hash·변환·MP4 hash·PTS/time_base를 연결한다. 실행 epoch, 송출/수신/추론 시간, 누락·재연결·재시작도 기록한다. RTSP/RTP의 sequence/timestamp만으로 전역 frame ID가 보존된다고 가정하지 않는다. 디코딩 직후 공통 ingest가 PTS와 프레임 ID를 붙이고 이후 DAG가 이 envelope를 전달하는 형태가 적합하다.

초기 수동 smoke에는 공식 예제 [bus.jpg](https://raw.githubusercontent.com/ultralytics/ultralytics/v8.3.220/ultralytics/assets/bus.jpg)(137,419 B), [zidane.jpg](https://raw.githubusercontent.com/ultralytics/ultralytics/v8.3.220/ultralytics/assets/zidane.jpg)(50,427 B)도 있다. 두 장만 반복한 성공을 입력 다양성·검출 정확도 검증으로 확대하지 않는다. 기존 COCO 파일과 VQA 정답은 **검출 bbox 정답이 아니다**. AP/mAP를 보고하려면 해당 이미지의 COCO instances annotation과 변환된 box 기준이 추가로 필요하다.

## 5. RTSP/API와 실제 모델 호출 경계

다음은 **미실행 설정·호출 예시**다. source 엣지마다 MediaMTX를 두고, source publisher와 같은 Pod/network namespace라면 `127.0.0.1`로 송출한다. 원격 추론기는 해당 엣지의 실제 도달 가능한 RTSP 주소를 사용해야 한다. 중앙 서버가 원본 파일을 대신 송출하는 형태는 이 실험의 edge-local source가 아니다.

```yaml
# MediaMTX v1.21.2 설정 후보
rtsp: true
rtspAddress: :8554
rtspTransports: [tcp]
rtmp: false
hls: false
webrtc: false
srt: false
moq: false
api: true
apiAddress: 127.0.0.1:9997
paths:
  edge-a:
    source: publisher
```

```bash
# 이미 생성·동결한 H.264 파일의 반복 송출; 이 조사에서는 실행하지 않음
rtk proxy ffmpeg -re -stream_loop -1 -i source.mp4 -map 0:v:0 -an \
  -c:v copy -f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8554/edge-a
# 같은 network namespace의 상태 조회
rtk proxy curl --fail http://127.0.0.1:9997/v3/paths/list
```

FFmpeg 공식 사용법의 `-re -stream_loop -1 -c copy`를 기반으로 TCP를 명시했다. TCP는 RTP를 RTSP control connection에 interleave하므로 이 구성의 스트림 포트는 TCP 8554다. `-c copy`는 재인코딩하지 않으며 입력 H.264를 준비해 두는 전제다. MediaMTX는 영상 검출/임베딩 엔진이 아니다. [MediaMTX FFmpeg](https://mediamtx.org/docs/publish/ffmpeg), [FFmpeg RTSP](https://ffmpeg.org/ffmpeg-protocols.html#rtsp), [버전 고정 설정](https://github.com/bluenviron/mediamtx/blob/914e39535ece709919ac325aaeed98d394ed9093/mediamtx.yml)

Control API는 기본 비활성이고 localhost에서 접근한다. v1.21.2 `/v3/paths/list`의 `online`, `available`, `source`, `readers`, `tracks2`, `inboundBytes/outboundBytes`를 관찰할 수 있으며 `ready`, `tracks`, `bytesReceived/bytesSent`는 deprecated다. `online=true`만으로 frame decode·YOLO 실행을 통과한 것은 아니다. [API 사용법](https://mediamtx.org/docs/features/control-api), [고정 OpenAPI](https://github.com/bluenviron/mediamtx/blob/914e39535ece709919ac325aaeed98d394ed9093/api/openapi.yaml)

Ultralytics의 기본 `stream_buffer=False`는 오래된 입력을 버린다. `stream=True`는 결과를 generator로 반환하는 옵션이며 frame 보존 보장이 아니다. `stream_buffer=True` 역시 실시간 지연을 해결해 주지 않는다. 따라서 품질 비교용 프레임은 공통 ingest에서 사전 정한 ID로 추출하고 동일한 decoded frame을 모델에 넣는다. 실시간 부하 비교에서는 입력 전체 요청 수를 분모로 누락·timeout을 포함한다. [고정 기본 설정](https://github.com/ultralytics/ultralytics/blob/b35e516270349ea39ee9dd18d6e593012a589c46/ultralytics/cfg/default.yaml), [Predict 문서](https://docs.ultralytics.com/modes/predict/)

```python
# 이미 hash를 검증한 파일과 공통 ingest의 BGR uint8 프레임 사용; 미실행 예시
from ultralytics import YOLO
detector = YOLO("/models/yolo11n.pt")
result = detector.predict(frame_bgr, device="cpu", imgsz=640, rect=False,
                          half=False, conf=0.25, iou=0.7, max_det=300,
                          augment=False, save=False, verbose=False)[0]
# result.boxes.xyxy / .conf / .cls 를 frame_id와 함께 반환
```

## 6. 생성 텍스트와 MiniLM의 동일 품질 계약

텍스트는 작은 버전 고정 템플릿 표로 엣지에서 생성한다. 첫 파일럿은 `A person is moving in zone A.` 같은 영어 이벤트와 의미가 다른 대조 문장을 사용한다. 다국어 확장에서는 `구역 A에서 사람이 이동합니다.` 같은 번역 쌍을 추가한다. 언어·구역·객체·행동 조합, 빈도, seed/sequence, UTF-8 bytes를 사전 manifest로 고정한다. Unicode 정규화(NFC), 공백·구두점, JSON 직렬화 규칙도 고정한다. 동일 의미 쌍과 다른 의미 쌍을 미리 지정하고 표현을 바꾼 평가 템플릿을 분리한다. 이는 synthetic text이며 실제 사용자 대화 분포를 대표한다고 주장하지 않는다.

**영어 파일럿:** 고정 revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`의 `all-MiniLM-L6-v2`는 Apache-2.0이고, `max_seq_length=256`, mean pooling 다음 `2_Normalize` 모듈이 있다. Transformers 경로에서도 attention-mask mean pooling 후 `F.normalize(..., p=2, dim=1)`을 수행한다. config/tokenizer/special tokens/ST 설정을 같은 revision으로 묶고 로컬 CPU FP32로 실행한다. 한국어 지원을 검증한 모델로 소개하지 않는다. [고정 modules](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/blob/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/modules.json), [공식 모델 카드](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)

**다국어 확장:** 최소 runtime은 기존 `AutoTokenizer(use_fast=True)`와 `AutoModel`로 **다운로드 완료된 로컬 revision**을 열고 `local_files_only=True`, CPU FP32, `eval()`/inference mode를 사용한다. attention mask를 곱한 token embedding 합을 유효 token 수로 나누는 mean pooling으로 `[batch,384]`를 얻는다. 실제 ST 설정의 `max_seq_length=128`을 명시한다. tokenizer/model의 512 상한만 사용하면 ST와 다른 결과가 된다. 고정 modules에는 Normalize가 없으므로 기본 출력은 비정규화 임베딩이다. cosine 계산용 L2 정규화가 필요하면 모든 경로에 동일하게 추가하고 출력 계약에 기록한다. [모델 카드의 Transformers 경로](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2), [고정 ST 길이](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/blob/e8f8c211226b894fcb81acc59f3b34ba3efd5f42/sentence_bert_config.json), [고정 pooling](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/blob/e8f8c211226b894fcb81acc59f3b34ba3efd5f42/1_Pooling/config.json)

`[384]` FP32 자체는 문장당 1,536 B지만 JSON 직렬화·HTTP envelope 크기는 별도로 실측한다. 문자열 전송과 임베딩 전송의 비용을 혼동하지 않는다. 영상 프레임과 텍스트를 연계하는 DAG가 필요하면 `event_id`로 연결하되, 독립 텍스트 source가 실제 이미지 관측을 생성한 것처럼 취급하지 않는다.

## 7. ONNX 대안과 수용 기준

`onnxruntime==1.23.2`에는 CPython 3.11용 Linux aarch64(15,220,095 B), x86_64(17,372,080 B) wheel이 있다. SHA256은 각각 `bbfd2fca76c855317568c1b36a885ddea2272c13cb0e395002c402f2360429a6`, `da44b99206e77734c5819aa2142c69e64f3b46edc3bd314f6a45a932defc0b3e`다. wheel 존재만 확인했고 설치하지 않았다. [PyPI metadata](https://pypi.org/pypi/onnxruntime/1.23.2/json), [공식 CPU 설치](https://onnxruntime.ai/docs/install/)

- YOLO는 원본 `.pt`에서 한 번 export한 FP32 ONNX를 공통 사용한다. 후보 설정은 Ultralytics 8.3.220, `imgsz=640`, batch=1, `dynamic=False`, `opset=17`, `simplify=False`, `nms=False`다. 원본 SHA256·exporter/onnx 버전·출력 hash를 연결한다. 전처리 letterbox, RGB/BGR, `[0,1]` 스케일, xywh→xyxy와 class별 NMS를 reference와 맞춘다. YOLO11을 최신 YOLO26의 NMS-free 출력 규칙으로 해석하지 않는다. 이 export 조합은 아직 실행 검증하지 않았다. [고정 export 설정](https://github.com/ultralytics/ultralytics/blob/b35e516270349ea39ee9dd18d6e593012a589c46/ultralytics/cfg/default.yaml)
- MiniLM은 위 고정 FP32 ONNX를 `CPUExecutionProvider`로 실행하되 입력 이름/형식은 실제 graph에서 확인한다. 공식 ONNX는 Transformer 부분이며 pooling/정규화는 별도다. `model_qint8_arm64.onnx`(118,412,398 B)도 공식 저장소에 있지만 처음부터 FP32 경로와 혼용하지 않는다. ISA별 INT8 파일의 속도·품질은 다른 비교다. [Sentence Transformers ONNX 안내](https://sbert.net/docs/sentence_transformer/usage/efficiency.html)

다음은 **실행 전 확정할 수용 기준 제안**이다. 정책 비교 시작 전에 모든 실행 후보에서 같은 고정 입력을 검사하고, 불일치를 보고 나서 입력을 제외하지 않는다.

| 검증 | 통과/보고 계약 |
|---|---|
| 자산·입력 | 원본/파생 모델 hash, 모든 입력 ID·bytes hash, tokenizer와 전처리 설정 일치. RTSP decode 성공, 누락·중복·loop 경계 식별. |
| YOLO CPU parity | 모든 평가 frame에서 class/검출 수·confidence·좌표를 대조. class별 IoU 매칭으로 unmatched box와 score·좌표 오차를 보고한다. 초기 gate 후보는 동일 class/count, matched IoU ≥0.99, score 절대 오차 ≤1e-4이며 threshold 근처의 NMS 변화도 실패 사례로 남긴다. 이는 제안 허용오차이며 통과 실측값이 아니다. |
| MiniLM CPU parity | 입력 token ID/attention mask 일치, shape/finite 검사, FP32 reference 대비 `rtol=1e-4, atol=1e-5`, cosine ≥0.9999를 초기 gate로 제안. pooling 이후 raw vector와 정규화된 similarity를 나누어 보고한다. 고정 의미 쌍의 검색 순위·positive/negative 분리도 확인한다. |
| 품질·성능 분리 | parity는 정확도 증명이 아니다. YOLO는 bbox 정답이 있을 때 AP, 텍스트는 사전 label의 검색/유사도 지표를 별도 보고한다. warm 모델 시간뿐 아니라 decode·tokenize·queue·전송·재연결·모델 load·source 준비를 경계별로 기록한다. |
| 배치 비교 | source node·동일 논리 요청·모델·품질 gate·thread 수·후처리를 유지한다. decoder 위치와 전송 표현(H.264/JPEG/raw frame)을 사전 고정한다. API bytes를 Task payload bytes나 순수 링크 throughput으로 해석하지 않는다. |

## 8. 라이선스와 남은 일

- MediaMTX는 [MIT](https://github.com/bluenviron/mediamtx/blob/914e39535ece709919ac325aaeed98d394ed9093/LICENSE), MiniLM 모델 카드는 Apache-2.0을 표시한다. Ultralytics는 코드와 학습 모델에 AGPL-3.0/Enterprise 조건을 안내한다. ONNX 변환이 가중치 라이선스를 바꾸지는 않는다. [공급자 라이선스 안내](https://www.ultralytics.com/license)
- FFmpeg는 구성에 따라 LGPL/GPL이 달라지고 현재 호스트는 GPL/libx264 빌드다. 배포 이미지의 실제 빌드·고지 파일을 함께 기록한다. [FFmpeg 라이선스](https://ffmpeg.org/legal.html)
- COCO annotation의 CC BY 4.0과 사진 권리는 별개다. COCO는 사진 저작권을 소유하지 않으며 Flickr 조건을 따르도록 명시한다. 공개 URL이나 기존 로컬 보유를 재배포 허가로 취급하지 않고, 공개 배포할 montage에는 원본 사진별 허가·출처 정보를 연결한다. [COCO 공식 약관 원문](https://github.com/cocodataset/cocodataset.github.io/blob/master/dataset/termsofuse.htm)

남은 구현 작업은 (1) 양 아키텍처 dependency lock/컨테이너 digest 확정, (2) MiniLM 등 큰 자산 취득과 본문 hash 검증, (3) 영상·텍스트 sequence manifest 생성, (4) 두 아키텍처의 실제 RTSP·decode·모델 추론 및 품질 gate, (5) 엣지 source CPU/메모리·부하와 누락 포함 비용 측정이다. **현재 확정된 것은 공식 자산/호출 근거와 로컬 재사용 가능 파일이며, 완성된 재현 bundle·실시간 처리량·arm64/amd64 수치 동등성은 아직 없다.**
