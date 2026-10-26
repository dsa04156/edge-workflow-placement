# Mobilint 실추론 DAG 파일럿 — Phase A 실행 가능성 조사 (2026-10-26, 읽기 전용)

작성: Claude Opus 5.5 (`claude-opus-5-5`), claude.ai firstParty 구독 로그인 확인(`claude auth status`: authMethod=claude.ai, apiProvider=firstParty, subscriptionType=max). API 종량제나 다른 모델로 전환하지 않음.
이 단계에서는 클러스터·registry·파일을 변경하지 않았다. 수행한 것은 API GET, registry HTTP GET(manifest/config, 3 KB짜리 app layer 1개), HF 공개 메타데이터 GET뿐이다.

## 1. 결론 요약

- **"Mobilint 사용 불가"가 아니다.** 10/23의 실패는 공개 `qbcompiler:1.4-cpu` 이미지에 compiler가 없다는 의미다(compile 경로). runtime 경로에는 실행 근거가 있다.
  - 10/08·10/12 다른 프로젝트(resource-advisor)가 ser0004 ARIES2에서 qbruntime 1.4.0으로 `style_candy.mxq`를 **실제로 추론**했다. 수행 단위는 Job 6개 × 30회이고, target core 확인과 output hash 반복 일치가 남아 있다.
- 장치 현황(2026-10-26 GET): ser0004 `mobilint.com/npu` allocatable 1, **점유 0**(이 노드의 Pod 16개 모두 npu 요청 없음). 노드 Ready, taint 없음, CPU 16 / 메모리 31 GiB.
- 후보는 둘이다. **권장은 A**다.

| | A. ResNet50 (Mobilint 공식 공개 MXQ) — 권장 | B. style_candy (기존 runtime image 내장) |
|---|---|---|
| 작업 | ImageNet-1k 분류 | Fast neural style transfer (candy) |
| MXQ | HF `mobilint/ResNet50.tv1_in1k`, rev `8adb91ca7578fae9395cb4e8e2311b2afc7041c0`, `aries-rb/resnet50_IMAGENET1K_V1.mxq`, sha256 `48b1ca4e669ac9d293148a748c06c352a7f542c5999f5f8eb2d1f5a31cf21e2d`, 26,018,637 B, MXQv7, single-core 전용 | `/models/style_candy.mxq`, sha256 `aa158a35735a876cc9f9220f9967b711b8e49ce5de49126f0597e994c6b543da`, 6,267,184 B |
| 원본 모델 | 같은 repo의 `resnet50_IMAGENET1K_V1.onnx`, LFS sha256 `3a742b2337fc640cae0ebff4c00fee264379a03275a5b192e707cb3a3e62d58a`, 102,146,392 B. base_model `timm/resnet50.tv_in1k`(torchvision IMAGENET1K_V1) | ONNX zoo `candy-9.onnx` sha `9d11a352…`(로컬에 있음). **MXQ와의 대응 관계는 미확립**(resource-advisor protocol에 `model_equivalence: not-established`로 기록) |
| 원본↔MXQ provenance | vendor metadata: qbcompiler 1.4.0, image `1.4-cuda12.8.1` digest `7910f166…`, variant `c0923-asym-gptq`, 공식 arxiv folder 링크. vendor가 ImageNet 50k에서 top1 0.7602 / top5 0.92856을 보고했다(retention 99.83%). **vendor 주장이며 재현값이 아님** | 없음. 10/12 비교에서 같은 seed 입력에 대해 출력 상관 0.37이었고 출력 범위도 달랐다(이번 세션에서 capture-v3.json을 재계산). 전처리 규약은 미상 |
| runtime 호환성 | **미검증 위험**: vendor 평가는 qbruntime **1.5.0**으로 했고, 우리 image에는 **1.4.0**만 있다. target `aries-rb` ↔ 관측 장치 `ARIES2`의 대응도 미확인이다. 첫 live 단계는 load probe여야 한다 | qbruntime 1.4.0 + ARIES2에서 실행된 것이 확인됨 |
| 입력/출력 | 입력: uint8 HWC 224×224×3. 정규화가 MXQ에 fuse된 것은 공식 tutorial 기준이다(resize 256 bilinear → center crop 224). 출력: logits 1000개 → softmax/top-5 | float32 HWC 224×344×3 → 같은 shape의 이미지. 입력 scale 미상 |
| 품질 검증 | 같은 원본 ONNX FP32 CPU reference 대비 top-1 일치와 top-5 집합 일치, logit 오차. 정답 품질에는 ImageNet 라벨 이미지가 필요하다(§5 선택지) | CPU reference(candy-9) 일치 여부 자체가 불확실. 정답 없음 → 반복성 진단만 가능 |
| CPU/GPU 비교 | 같은 원본 ONNX를 ORT CPU에서 실행해 비교할 수 있다(정밀도는 FP32 vs MXQ 양자화이므로 "같은 원본의 파생 변형"으로 명시). 서버 GPU 3/3 점유, ser0004에는 GPU 없음 → GPU arm은 blocked로 기록 | 진단 전용 |

## 2. 근거 위치

- 이전 실행: `/mnt/data3tb/user-storage/jinuk/artifacts/mlops/resource-advisor/all-accelerators-20261008/mobilint-stack-report.json`, `mobilint-qual2-report.json`, `.../mobilint-5080-20261012-v1/{protocol.json,capture-v3.json,probe-v2.py,npu-template.json,jobs/}`. 다른 프로젝트의 자료이며 읽기만 했다.
- runtime image: `10.254.192.47:5000/ra-npu-mobilint@sha256:3e1760cdef820206d262488dc60a5047acd551b95e81f914e1f516fda1a496da`(tag `binding-20261008-v1`, amd64, 압축 약 51 MB).
  - 이전 Job이 쓴 `192.168.0.56:5000/...`와 digest가 같다. 192.168.0.56은 이 host에서 timeout.
  - config history: python 3.11.16, `libqbruntime.so.1.4.0`, wheel `mobilint_qb_runtime-1.4.0-cp311`, `numpy==1.26.4`, `/models/{style_candy,style_mosaic}.mxq`, `/app/app.py`(seed noise demo; 전처리 근거 아님).
  - **onnxruntime/Pillow 없음.** ser0004 노드의 다른 Pod는 같은 registry에서 pull한다.
- 이전 Job manifest 패턴(재사용 가능): `requests/limits mobilint.com/npu: 1`, cpu 1, mem 1Gi/4Gi, `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, seccomp RuntimeDefault, host mount 없음, privileged 아님, `automountServiceAccountToken: false`. 장치는 device plugin이 주입한다(`/dev/aries*`).
- vendor 출처: GitHub `mobilint/mblt-sdk-tutorial` `runtime/python/image_classification/{inference_mxq.py,README.md}`(qbruntime API: `Accelerator(0)`, `ModelConfig.set_single_core_mode`, `Model(path,cfg).launch(acc)`, `infer(uint8 HWC)`). HF `mobilint/ResNet50.tv1_in1k` README·`aries-rb/metadata.json`(ungated, BSD-3, lastModified 2026-10-17). `mobilint/mblt-model-zoo`는 deprecated이며 HF를 통해 다운로드한다.
- 도구: Context7 MCP **없음**. 별도 GitHub MCP **없음**. GitHub 파일 읽기는 alphaXiv의 `read_files_from_github_repository`로 했다. research skill은 목록에 없어(`research`라는 이름의 skill 미노출) 1차 출처(vendor repo/HF)를 직접 읽었다.
- 입력 corpus: `artifacts/topology-profile-assets/20261019T091725Z/selected-data/` COCO JPEG 60장(hash manifest 있음). ImageNet 라벨은 없다.
- 데이터 경로: edge Pi Pod → server Pod IP 직접 연결. `quality-network-baselines` qnb-compare-r2에서 실제로 쓰인 경로다.
  - edge 후보 Ready: dev0002/0003/0006/0008(raspi5), dev0001/0009(Jetson Orin).
  - dev0004/dev0007은 NotReady.

## 3. 제안 DAG (A안)

```
edge origin(Pi, raw JPEG, request 생성/수신·e2e 시계)
  -JPEG(수십~수백 KB)-> preprocess (Pillow: RGB, resize 256 bilinear, center crop 224, uint8 HWC = 150,528 B)
  -uint8 tensor-> infer: Mobilint ResNet50 MXQ @ ser0004 (qbruntime 1.4.0, single core Cluster0/Core0; receipt=model sha, target_cores, device name, runtime version, pod UID/node)
  -logits 1000×f32 = 4,000 B-> postprocess (softmax, top-5, label)  -> origin
```

- 비교 arm: infer 단계만 **같은 원본 ONNX FP32를 ORT CPU EP로**(ser0004 CPU, threads 고정) 바꾼 경로다. 같은 입력 tensor, 같은 실행기, 같은 pre/post를 쓴다. 가능하면 edge Pi CPU도 진단 arm으로 둔다.
- CPU fallback 금지: Mobilint 경로에는 ORT가 아예 없다. qbruntime launch 실패 = 실패로 기록하며 다른 장치로 대체하지 않는다.
- 측정: warmup은 별도 기록하고, 측정 요청은 arm당 ≥30(권장: COCO 60장 전부 1회씩)이다. 단계별 서버측 시간, e2e(origin 단조 시계), 각 hop bytes, 실패/timeout 전부를 남긴다.
- 품질:
  - (i) MXQ vs ONNX-CPU top-1 일치율, top-5 집합 일치율, logit max/mean abs 오차. 이것은 reference parity이며 정답 품질이 아니다.
  - (ii) 정답 품질은 §5 선택에 따른다.
- 요청 형태: closed-loop 순차(동시성 1)가 기본이다. NPU 단일 core 1개를 측정한다.

## 4. 정확한 추가 자원 예산 (A안, 모두 신규 owned, 생성 UID 기록 후 삭제)

| Pod | 노드 | requests / limits | 특수 자원 | 비고 |
|---|---|---|---|---|
| mblt-infer | ser0004 | cpu 1 / 1, mem 1Gi / 2Gi | `mobilint.com/npu: 1` | image `ra-npu-mobilint@sha256:3e1760cd…`. MXQ는 owned cache에서 HTTP로 받아 emptyDir(≤64 MiB)에 두고 sha 검증 |
| cpu-ref | ser0004 | cpu 1 / 1, mem 1Gi / 2Gi | 없음 | `node-inventory-python@sha256:370a8339…` + onnxruntime wheel offline 설치. ONNX 102 MB |
| preprocess | edge Pi(예: dev0003 또는 dev0008) | cpu 500m / 1, mem 256Mi / 512Mi | 없음 | 같은 python image + numpy/Pillow wheel(wheelhouse에 aarch64 cp311 있음) |
| origin | 같은 edge Pi | cpu 250m / 500m, mem 128Mi / 256Mi | 없음 | stdlib |
| cache | ser0004 또는 ser0002 | cpu 250m / 500m, mem 256Mi / 512Mi, emptyDir ≤ 1 GiB | 없음 | MXQ 26 MB, ONNX 102 MB, wheels, JPEG 60장 |

- 합계: ser0004 cpu 2.25 / mem 2.25Gi(requests) + NPU 1, edge Pi 1대 cpu 0.75 / mem 384Mi.
- 신규 다운로드:
  - HF MXQ 26 MB, ONNX 102 MB
  - onnxruntime cp311 wheel x86_64(+선택 aarch64) 약 15–17 MB씩. PyPI 공개, 버전은 기존 저장소 고정값 1.23.2와 맞춤
  - B안이나 정답 품질 선택에 따라 추가(§5)
- 기한(제안): 각 Pod ready 600 s, 설치 600 s, load probe 120 s, 측정 run 전체 ≤ 20 min, `activeDeadlineSeconds` 3600. 정리는 생성 UID 기준이다.
- 금지 준수: privileged/host mount/hostNetwork 없음. edge exec/logs/attach/cp/port-forward 없음(HTTP만). 기존 workload 변경 없음. NPU가 점유되면 즉시 중단하고 보고한다.

## 5. coordinator 결정 필요

1. 모델 선택:
   - **A ResNet50 공식 MXQ(권장)**
   - B style_candy(1.4.0 실행 확실하나 provenance·품질 불가)
   - A 우선 + A 로드 실패 시 B로 Mobilint 실행 진단만
2. A의 runtime 1.4.0 ↔ MXQ(1.5.0 평가) 호환성: 첫 live 단계로 **load+단일 추론 probe**(같은 Pod, 짧은 기한)를 허용하는가? 실패하면 그 오류를 증거로 남기고 B 진단으로 전환한다.
   - HF 이전 revision의 legacy `aries/` MXQ(구 compiler)는 아직 조사하지 않았다. 필요하면 추가 조사한다.
3. 정답 품질 데이터:
   - (a) 정답 없음 — CPU-reference parity만 보고(COCO 60장)
   - (b) ImageNetV2 matched-frequency(공개, 약 1.2 GB tar, ImageNet과 같은 1000 class 라벨) 일부, 예를 들어 클래스 무작위 표본 200장을 고정 seed로 뽑아 top-1/top-5 측정. 표본 크기에 따른 신뢰구간 명시
   - (c) 둘 다
   - 권장: (c). 데이터가 부담되면 (a)
4. CPU 비교 arm 범위: ser0004 CPU ORT만(권장) / edge Pi CPU arm 추가. GPU arm은 서버 GPU 3/3 점유로 blocked 기록을 권장하며, Jetson은 이번 범위 밖이다.
5. preprocess 위치: edge(origin과 같은 Pi, 권장 — 센서 근처 전처리) 고정. 서버 preprocess는 진단 arm으로 추가하지 않는다(단일 실험 범위 유지).
