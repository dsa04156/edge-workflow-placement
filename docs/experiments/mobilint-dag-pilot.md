# Mobilint 실추론 DAG 파일럿 (2026-10-26)

선형 3단계 DAG에서 **Mobilint NPU(ARIES)로 실제 모델을 실행**했다. 단계는 다음과 같다.
- 엣지 전처리: ARM Raspberry Pi 5 CPU, JPEG → tensor
- 추론: ser0004의 Mobilint NPU, 또는 같은 노드의 CPU reference
- 엣지 후처리: 같은 Pi, top-5

CPU reference는 같은 원본 모델의 FP32 ONNX다. 두 경로는 같은 입력과 같은 실행기로 짝지어 비교했다.

- 이것은 기존 멀티모달 5단계 VQA DAG의 배치 비교가 아니다. 노드 간 배치 정책 비교도 아니다.
- 결과 수준: 코드 존재 + 관련 테스트 통과 + **실장비 실행(1회 run, closed loop)**.
- 운영 배포나 공식 수용이 아니다.
- 용량, goodput, 품질 우월성, 일반 가속기 성능은 주장하지 않는다.

## 1. 결론

- **Mobilint 실행 확인:** 공식 Mobilint ResNet50 MXQ를 ser0004의 ARIES(`aries-rb`, `/dev/aries0`, Cluster0/Core0 single core)에서 qbruntime 1.4.0으로 실행했다.
  - 동결 schedule 530행이 **530/530 ok**였다(warmup 10, 평가 400 = 200쌍, COCO parity 120 = 60쌍).
  - 실패 0, identity 불일치 0. 모든 응답 receipt가 이 run의 생성 Pod UID, node, model sha, runtime, frozen sha에 결속되어 있다.
- **지연(평가 200요청/arm, closed loop, 동시성 1):** "추론"은 서버 child process 안의 backend 호출 시간이며 입력 정규화를 포함한다.
  - Mobilint: 추론 p50 **4.59 ms**(p95 6.77), 엣지 기준 e2e p50 **19.9 ms**(p95 28.2, p99 50.3).
  - CPU FP32(ORT, 1 thread): 추론 p50 112.3 ms, e2e p50 128.8 ms(p95 141.2).
  - CPU arm은 thread 1개 설정이다. CPU 성능 상한이나 일반화로 읽지 않는다.
- **ImageNetV2 정답 품질(200 표본, 실패 포함 분모 200):**

  | arm | top-1 | top-5 |
  |---|---|---|
  | Mobilint | 0.600 (95% Wilson [0.531, 0.665]) | 0.845 [0.788, 0.889] |
  | CPU FP32 | 0.595 [0.526, 0.661] | 0.835 [0.777, 0.880] |

  - top-1 정오 불일치 쌍은 Mobilint만 정답 3, CPU만 정답 2다. 두 arm의 차이는 이 표본 크기로 구분되지 않는다.
  - 이 값은 위 200장 표본에서 측정한 결과일 뿐이다. vendor의 ImageNet 50k 값을 재현했다고 주장하지 않는다.
- **CPU reference parity(정답 품질과 별개, 같은 입력 tensor 200쌍):**
  - top-1 일치 188/200(0.94), top-5 집합 일치 0.78, top-5 순서 일치 0.59.
  - logit 최대 절대오차의 중앙값 0.475, 최대 4.89. cosine 최솟값 0.989.
  - COCO 60장(라벨 없음): top-1 일치 56/60.
  - 양자화 MXQ와 FP32는 수치적으로 같은 모델이 아니다.

## 2. 모델·런타임·장치 provenance

| 항목 | 값 |
|---|---|
| MXQ | HF `mobilint/ResNet50.tv1_in1k` rev `8adb91ca7578fae9395cb4e8e2311b2afc7041c0`, `aries-rb/resnet50_IMAGENET1K_V1.mxq`, sha256 `48b1ca4e669ac9d293148a748c06c352a7f542c5999f5f8eb2d1f5a31cf21e2d`, 26,018,637 B, MXQv7. vendor metadata: qbcompiler 1.4.0, single 모드, vendor 평가 qbruntime 1.5.0, ImageNet 50k top-1 0.7602(vendor 주장, 미재현) |
| 원본 | 같은 repo의 `resnet50_IMAGENET1K_V1.onnx`, sha256 `3a742b2337fc640cae0ebff4c00fee264379a03275a5b192e707cb3a3e62d58a`. base_model `timm/resnet50.tv_in1k`(torchvision IMAGENET1K_V1). 입력 `input` [N,3,224,224] float, 첫 연산 Conv(정규화 미포함). 출력 logits 1000 |
| Mobilint runtime | 기존 registry image `10.254.192.47:5000/ra-npu-mobilint@sha256:3e1760cd…`(qbruntime 1.4.0, numpy 1.26.4, python 3.11.16; 10/08·10/12 다른 프로젝트 style_candy 실행 이미지). 새 runtime·driver 설치 없음 |
| 장치 증거(health) | `device_name=aries-rb`, `dev_nodes=[aries0]`, `target_cores=[Cluster0/Core0]`, `core_mode=Single`, input 224×224×3 **Float32**, output [1,1,1000] Float32, MXQ load 315 ms(full run). device plugin `mobilint.com/npu: 1` 할당 |
| CPU arm | `node-inventory-python@sha256:370a8339…` + onnxruntime 1.23.2 cp311 wheel(sha `da44b992…`), CPUExecutionProvider만 사용, intra/inter thread 1 |
| fallback 차단 | Mobilint 프로세스 트리에 ORT가 없다. backend는 별도 child process로 실행하며, load 120 s와 호출 30 s 기한을 넘기면 종료하고 재사용하지 않는다. 실패는 실패로 기록한다 |

- 입력 계약 정정([erratum](../../artifacts/mobilint-dag-pilot/input-contract-erratum.md))
  - 처음에는 SDK tutorial을 근거로 "MXQ에 정규화가 fuse되어 uint8을 입력한다"고 가정했다. 이 가정은 이 model-zoo MXQ에 맞지 않았다.
  - 이 문장은 run-bound 코드인 `experiments/mobilint-dag-pilot/protocol.py`의 `MEAN, STD` 주석에만 있다. 그 주석은 고치지 않았고, erratum이 대체한다. `frozen-protocol.json`에는 mean/std 값만 있다.
  - 공식 `mblt-vision-python` 0.0.6(wheel sha `60562a5c…`, BSD-3; URL·파일별 hash는 [provenance.json](../../artifacts/mobilint-dag-pilot/provenance/provenance.json))의 `models/ResNet50.yaml` IMAGENET1K_V1 설정을 확인했다: PIL reader → Resize 256 bilinear → CenterCrop 224 → HWC → Normalize torch.
  - 이에 따라 Mobilint arm에는 **float32 정규화 HWC**를 입력한다. 정규화 수식은 vendor `normalize.py`와 같다.
  - 엣지는 두 arm에 같은 uint8 tensor를 보내고, 각 backend가 자기 공식 정규화를 적용한다. 정정은 평가 출력을 관측하기 전에 했고, 표본과 순서는 바뀌지 않았다.

## 3. 실험 설계(동결)

- **DAG**
  - origin(엣지 Pod, JPEG을 메모리에 보유) → preprocess(같은 엣지 Pod, Pillow: RGB, 짧은 변 256 PIL bilinear, `round()` center crop 224, uint8 HWC 150,528 B)
  - → HTTP binary framing → infer(ser0004 Pod, arm별)
  - → logits(JSON base64, 응답 약 5.98 KB) → postprocess(엣지, softmax top-5) → origin.
  - edge→server는 Pod IP로 직결했다. 데이터는 collector를 거치지 않는다.
- **노드:** 엣지 `etri-dev0003-raspi5`(후보 dev0003/dev0002/dev0008 중 첫 Ready·untainted·자원 여유 노드), 서버 `etri-ser0004-cgnms0`.
- **표본:**
  - ImageNetV2 matched-frequency(HF `vaishaal/ImageNetV2` rev `d626240b…`, sha `f0c37fdf…`, MIT, 10,000장/1000 class)에서 seed 20261026으로 평가 200장과 warmup 10장(서로소)을 뽑았다.
  - COCO val2014 60장(기존 VQA corpus)은 parity 진단용이다.
- **순서:** warmup은 arm을 교대로 5장씩 돌렸다. 평가는 표본 순서를 seed로 섞고, 각 표본을 두 arm에서 연속 실행했다(쌍마다 arm 순서도 seed로 정함; Mobilint 먼저 99/200쌍). parity도 같은 방식이다.
  - 동결 파일은 [`frozen-protocol.json`](../../artifacts/mobilint-dag-pilot/frozen-protocol.json)(로컬 원본 `.scratch/mobilint-dag-pilot/frozen-protocol.json`, sha `ecb4f920c01cc399611a49e501db90ba289e6a963a1d372f2d0a57b5880ce27b`)이며, 모든 모델 출력보다 먼저 만들었다.
- **경계:** e2e는 엣지 단조 시계로 잰다(JPEG이 메모리에 있는 시점부터 top-5까지).
  - `rpc_ms`는 서버 시간을 포함한다. `rpc_ms - server_ms`는 전송·HTTP 잔여이며 순수 네트워크 시간이 아니다.
  - `infer_ms`는 child process 안의 backend 호출 시간이다. 순수 장치 연산 시간이 아니다.
    - Mobilint: vendor float32 정규화 + qbruntime `infer`
    - CPU: NCHW 정규화 + ORT `run`
  - `server_ms`는 framing 검증, IPC, 정규화를 포함한다.
- **자원(추가분):** ser0004 requests cpu 2.25 / mem 2.25Gi + `mobilint.com/npu` 1(cache 0.25, mobilint 1, cpu-fp32 1). 엣지는 cpu 0.75 / 384Mi. 선점 없음.
  - preflight와 trace 후 모두 NPU 보유 Pod가 **이 run의 Mobilint UID 하나뿐**임을 확인했다.

## 4. 결과 상세 (run `mdp-20261026t092212`)

평가 200요청/arm, ok 행 기준(ms):

| arm | 단계 | mean | p50 | p90 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| mobilint | e2e | 21.26 | 19.94 | 25.84 | 28.23 | 50.32 | 159.17 |
| | preprocess(엣지) | 8.99 | 7.48 | 12.14 | 13.86 | 25.38 | 148.41 |
| | rpc | 11.48 | 11.02 | 13.64 | 14.93 | 24.32 | 28.86 |
| | infer(backend 호출, 정규화 포함) | 4.63 | 4.59 | 6.31 | 6.77 | 7.06 | 7.09 |
| | server | 5.47 | 5.34 | 7.32 | 7.84 | 8.15 | 8.31 |
| | 잔여(rpc−server) | 6.01 | 5.52 | 7.59 | 8.95 | 17.14 | 21.83 |
| | postprocess(엣지) | 0.75 | 0.54 | 1.03 | 2.61 | 3.91 | 4.92 |
| cpu-fp32 | e2e | 130.24 | 128.80 | 137.68 | 141.17 | 158.04 | 214.53 |
| | preprocess | 8.82 | 7.81 | 12.35 | 13.81 | 27.96 | 96.23 |
| | rpc | 120.73 | 119.76 | 126.65 | 129.14 | 137.25 | 165.69 |
| | infer(backend 호출, 정규화 포함) | 113.35 | 112.31 | 118.17 | 121.10 | 127.43 | 155.44 |
| | server | 114.33 | 113.42 | 119.12 | 122.27 | 128.44 | 156.27 |
| | 잔여 | 6.40 | 5.81 | 8.43 | 9.45 | 12.98 | 19.50 |
| | postprocess | 0.67 | 0.54 | 0.88 | 1.72 | 2.93 | 3.34 |

- **bytes(평균):** JPEG 126,288 B(평가), 요청 body 150,741 B(tensor 150,528 + framing 헤더), 응답 body Mobilint 5,981 / CPU 5,985 B.
- **warmup(분석 제외, 각 5):** Mobilint e2e p50 29.1 ms, CPU e2e p50 136.5 ms.
- **COCO parity(각 60):** Mobilint e2e p50 25.6 ms, CPU 133.3 ms.
- **phase 시간:** preflight 0.3 s, cache(Pod 생성 + 212 MB bundle 업로드) 7.7 s, workers 7.8 s, probe 1.1 s, trace 45.5 s, cleanup 5.1 s.

## 5. 모든 시도(실패 포함)

| run | stage | 결과 | 원인/조치 |
|---|---|---|---|
| `mdp-20261026t091406` | probe | MXQ **load 성공**(397 ms, aries-rb). Mobilint 추론 HTTP 500 `ValueError('array is not writeable')`. CPU 정상 | 코드 결함이었다: child process가 읽기 전용 `np.frombuffer` view를 그대로 넘겼다. `.copy()` 1줄로 수정하고 회귀 테스트를 추가했다 |
| `mdp-20261026t091641` | probe | Mobilint `QbRuntimeError Model_DtypeMismatched`. CPU 정상 | uint8 입력이 거부되었다(MXQ 입력은 Float32 선언). vendor 공식 전처리 계약을 확인해 정규화 float32 HWC로 정정했다(§2) |
| `mdp-20261026t092138` | probe | **통과**: warmup 표본(label 186)에서 두 arm의 top-5가 같다 [192,186,185,193,203]. Mobilint infer 5.29 ms | — |
| `mdp-20261026t092212` | full | 530/530 ok | — |

- 네 run 모두 생성 UID 5개(ConfigMap 1, Pod 4)를 UID precondition으로 삭제했고, 삭제 후 부재를 재확인했다(`cleanup.json.verified=true`).
- 보호 workload 감사는 두 가지이며 서로 따로 읽는다.
  - **full run collector projection:** run 비소유 Pod 259개의 전후 UID와 phase가 같았다([요약](../../artifacts/mobilint-dag-pilot/protected-workload-summary.json)). 재시작 횟수와 readiness는 담지 않는다.
  - **coordinator 독립 감사** ([`coordinator-live-verification.json`](../../artifacts/mobilint-dag-pilot/coordinator-live-verification.json)): 실험 전체 기간에 걸친 broader 범위다.
    - 기존 Pod 258개는 제거된 것 없이 readiness와 restart가 변하지 않았다.
    - 이 실험과 관계없는 `ai-scientist` GC Pod 1개가 추가되었다(258→259).
    - 같은 검증에서 full run 530행의 schedule 일치, logits sha·top-5 재계산 일치, foreign/identity 거부 0, run-bound source 불변, 소유 객체 전부 부재를 확인했다.
- 프로브 표본은 warmup[0]이며 평가 200장과 겹치지 않는다. 실패 후에도 표본과 순서는 바꾸지 않았다.

## 6. 해석 범위와 한계

- 단일 run의 closed-loop 순차 실행이다. 처리량, 동시성 간섭, 큐잉, goodput, SLO는 측정하지 않았다.
- CPU arm(1 thread)과 비교한 지연 차이는 이 설정에서의 관측일 뿐이다. 가속기 일반 성능, 전력, 비용 비교가 아니다.
- 정답 품질은 ImageNetV2 200장 표본에서 나온 값이다(신뢰구간 폭 약 ±0.07). 서비스 품질 문턱은 정하지 않았다. quantized MXQ와 FP32 사이에 품질 우열이나 동등성을 주장하지 않는다.
- runtime 1.4.0 호환성: 이 MXQ의 load와 1,000-class 추론이 반복 성공한 것만 확인했다. vendor가 평가에 쓴 1.5.0과의 수치 차이나 전체 ImageNet 정확도는 확인하지 않았다.
- 엣지 JPEG 재생은 엣지 Pod 안에서 만든 입력이다. 실제 카메라나 센서 스트림 수집을 검증한 것이 아니다.
- ser0004 CPU arm과 Mobilint arm은 같은 노드에 있다. 노드 간 배치 비교나 이종 배치 정책 비교는 이번 범위가 아니다.

## 7. 코드·재현 명령

코드: `experiments/mobilint-dag-pilot/`

| 파일 | 역할 |
|---|---|
| `protocol.py` | 동결 상수, 표본 선택, schedule |
| `prepare.py` | 오프라인 hash 검증, 동결, bundle 생성 |
| `worker.py` | Pod 프로그램: edge / mobilint / cpu-fp32 |
| `live.py` | collector. `multi-edge-sensors/collect.Session`을 재사용한다: create-only, UID 기록, UID precondition 삭제 |
| `analyze.py` | 실패 포함 분석, identity 재검증 |
| `test_pilot.py` | 단위·회귀 테스트 |

공유 HTTP 유틸 `quality-network-baselines/bounded_http.py`와 `real-topology-live/http_runtime.py`는 변경 없이 Pod에 동봉한다.

모든 명령은 저장소 루트에서 실행한다. `prepare.py`는 기존 동결 파일을 덮어쓰지 않으므로, 재현할 때는 새 `--out`/`--frozen` 경로를 준다.

```bash
# 1) 오프라인 준비 (자산은 .scratch 아래, 공개 금지)
rtk proxy python3 experiments/mobilint-dag-pilot/prepare.py \
  --downloads .scratch/mobilint-dag-pilot/assets/downloads \
  --wheelhouse artifacts/topology-profile-live/wheelhouse \
  --coco artifacts/topology-profile-assets/20261019T091725Z/selected-data \
  --out .scratch/mobilint-dag-pilot/assets/prepared --frozen .scratch/mobilint-dag-pilot/frozen-protocol.json

# 2) 테스트 (42 OK)
rtk proxy env PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=experiments/mobilint-dag-pilot:experiments/quality-network-baselines:experiments/real-topology-live \
  uv run --python python3.11 --no-build --with numpy==1.26.4 --with Pillow==11.3.0 --with onnxruntime==1.23.2 \
  python -m unittest -v test_pilot

# 3) release 초안(실행 의존 모듈 19개 + 동결 sha) -> 검토 후 approved_by/stages 기입
rtk proxy python3 experiments/mobilint-dag-pilot/live.py --prepared .scratch/mobilint-dag-pilot/assets/prepared \
  --frozen .scratch/mobilint-dag-pilot/frozen-protocol.json --print-release-draft

# 4) 실행 (probe 후 full; 모두 종료 시 salvage -> UID cleanup -> 부재 검증)
rtk proxy python3 experiments/mobilint-dag-pilot/live.py --prepared .scratch/mobilint-dag-pilot/assets/prepared \
  --frozen .scratch/mobilint-dag-pilot/frozen-protocol.json --runs .scratch/mobilint-dag-pilot/runs \
  --release .scratch/mobilint-dag-pilot/release-normalized.json --stage probe   # 이후 --stage full

# 5) 분석 (analysis.json이 이미 있으면 거부하므로 새 --out 지정)
rtk proxy python3 experiments/mobilint-dag-pilot/analyze.py --run .scratch/mobilint-dag-pilot/runs/mdp-20261026t092212 \
  --frozen .scratch/mobilint-dag-pilot/frozen-protocol.json --out .scratch/mobilint-dag-pilot/analysis-recheck.json
```

- 실제 다운로드:
  - MXQ 26 MB, ONNX 102 MB, ORT wheel 17 MB, ImageNetV2 tar 1.26 GB(합계 약 1.41 GB, 승인 예산 1.5 GB 이내)
  - 공식 전처리 확인용 `mblt-vision-python` wheel 0.4 MB
  - cluster에는 선택된 270장만 올렸다(bundle 212 MB, cache emptyDir 한도 2Gi).
- 검토:
  - 독립 리뷰 2회(Astra Spec/Standards R1 → 수정 → R2 → salvage 2건 소폭 수정)
  - 이후 coordinator 확인 아래 실행 중 결함 수정 2건(writeable copy, vendor 정규화)
  - release 파일: `release-final.json` → `release-writeable.json` → `release-normalized.json`

## 8. 증거 위치

공개 증거는 [`artifacts/mobilint-dag-pilot/`](../../artifacts/mobilint-dag-pilot/)에 있다. 파일 목록과 sha256은 [`publication-manifest.json`](../../artifacts/mobilint-dag-pilot/publication-manifest.json), 포함·제외 기준은 [`README.md`](../../artifacts/mobilint-dag-pilot/README.md)에 있다.

- 문서:
  - [`feasibility.md`](../../artifacts/mobilint-dag-pilot/feasibility.md), [`input-contract-erratum.md`](../../artifacts/mobilint-dag-pilot/input-contract-erratum.md), [`developer-fix-r1.md`](../../artifacts/mobilint-dag-pilot/developer-fix-r1.md)
  - [`fix-writeable.diff`](../../artifacts/mobilint-dag-pilot/fix-writeable.diff), [`fix-vendor-normalize.diff`](../../artifacts/mobilint-dag-pilot/fix-vendor-normalize.diff)
  - release 파일 3개, [`test-fix-normalize.log`](../../artifacts/mobilint-dag-pilot/test-fix-normalize.log)
- provenance:
  - [`provenance/provenance.json`](../../artifacts/mobilint-dag-pilot/provenance/provenance.json): vendor wheel URL과 파일 hash. 제3자 원문 YAML/py는 공개본에서 제외했다.
  - [`provenance/hf-resnet50-aries-rb-metadata.json`](../../artifacts/mobilint-dag-pilot/provenance/hf-resnet50-aries-rb-metadata.json)
  - [`bundle-manifest.json`](../../artifacts/mobilint-dag-pilot/bundle-manifest.json): cluster bundle hash 목록. 대상 파일은 공개하지 않는다.
- 검증:
  - [`coordinator-live-verification.json`](../../artifacts/mobilint-dag-pilot/coordinator-live-verification.json)
  - [`protected-workload-summary.json`](../../artifacts/mobilint-dag-pilot/protected-workload-summary.json)
- run 4개 [`runs/mdp-*/`](../../artifacts/mobilint-dag-pilot/runs/):
  - 공통: phase/event, `preflight.json`, `health-*.json`, `identities.json`, `probe-result.json`, `npu-occupancy-*.json`, `salvage*.json`, `cleanup.json`
  - full run만: 정본 [`ledger-main.json`](../../artifacts/mobilint-dag-pilot/runs/mdp-20261026t092212/ledger-main.json)(요청별 receipt와 logits, sha256 `21729bf8…`), [`analysis.json`](../../artifacts/mobilint-dag-pilot/runs/mdp-20261026t092212/analysis.json)
  - 같은 byte인 `salvage-ledger-main.json`은 중복이므로 공개본에서 뺐다.
- 로컬 전용(`.scratch/mobilint-dag-pilot/`, 공개 안 함):
  - `assets/`: 모델 MXQ/ONNX, ImageNetV2 tar, 이미지, wheel
  - `provenance/mblt-vision-python-0.0.6/`: vendor wheel과 원문
  - run 디렉터리의 kubectl 원시 stdout/stderr와 cluster 전체 Pod projection
  - `ledger-main.jsonl`(증분 checkpoint), review 원본(`review-*`), transcript
