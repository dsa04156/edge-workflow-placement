# VQA 품질 미달 진단과 독립 자료 계획 (2026-10-26)

| 수준 | 상태 |
|---|---|
| 아이디어/설계 | 이 문서, image-disjoint 분할 제안(아래 4절) |
| 코드 존재 | `.scratch/quality-network-baseline-first/quality/`의 진단 스크립트 4개(진단 전용) |
| 관련 테스트 통과 | 해당 없음. 진단 실행 결과만 있다(아래 명령과 원자료) |
| 실제 클러스터 실행/실장비 성능 | **없음**. CPU 단일 스레드 로컬 reference 진단이다. 클러스터 GET·변경 0회, GPU/NPU 미사용 |
| 공식 수용 | 없음. 품질 gate 통과를 주장하지 않는다 |

기존 60쌍(calibration 20 / evaluation 40)은 이미 관찰한 진단 자료로 유지한다. Qmin 0.40과 2초 SLO는 바꾸지 않는다. evaluation 40건은 이번 작업에서 추론·채점·조정에 쓰지 않았다(아래 3절의 장치 간 출력 보존 확인은 정답을 읽지 않는 transcript 문자열 비교다).

## 1. 결론 요약

1. **구현 불일치는 찾지 못했다.** 점검한 항목은 checkpoint 설정, vocab/answer id 순서, tokenizer, padding, 영상 가중치, ASR 경로, 장치 간 출력 보존이다(2절). 모두 원 학습·추론 계약과 일치하거나 출력에 영향이 없었다.
2. **ASR 기여(calibration 20건 진단):** 동결 ASR transcript 대신 정답 질문 텍스트를 넣으면 VQA 점수가 0.515에서 0.615로 오른다(+0.100). top-1이 바뀐 것은 6건이다. 그중 3건은 0점에서 양수로, 1건은 양수에서 0점으로 바뀌었고 2건은 바뀌기 전후 모두 0점이다. **정답 텍스트를 넣어도 7/20건은 0점**이다. 이는 ASR을 정답 텍스트로 대체해도 현재 영상 특징·결합 구성에서 오류가 남는다는 뜻이다. 이 대조만으로 남은 오류의 구체적 원인을 결합 모델 하나에 귀속할 수는 없다. 이 값은 원인 분리용 대체 실험이며 서비스 품질이나 성능이 아니다.
3. **이 구성의 공식 기대 품질:** JellyBean이 배포한 정확도 프로필에서 ResNet18 + wav2vec2-base-960h의 VQA1 val 전체(121,512 질문) end-to-end 정확도는 **54.02%**다. 기존 calibration 0.515는 이 값과 가깝다. evaluation 0.355(40건)와 60건 전체 0.408은 이 값보다 낮다. 질문당 표준편차를 약 0.48로 잡은 기술적 계산으로는 각각 약 −2.4 SE, −2.1 SE다. 다만 기존 fixture는 영상당 question_id가 가장 낮은 질문 하나를 고른 비무작위 선택이다. 그래서 이 차이를 표본 변동과 선택 규칙 중 어느 쪽으로도 확정하지 않는다.
4. **다음 단계:** 모델을 바꾸기 전에 아래 4절의 새 image-disjoint dev 400건으로 **현행 구성의 품질을 신뢰구간과 함께 다시 측정**한다. dev에서 Qmin 여유가 부족할 때만 JellyBean이 지원하는 최소 변경을 검토한다. 그 변경은 ASR을 `wav2vec2-large-960h-lv60-self`로 바꾸는 것이고, 공식 프로필 기준 54.02%에서 56.59%다. 이 경우 새 모델 버전으로 GPU/NPU 실행 가능성과 품질을 다시 보정한다.

## 2. 구현·설정 대조 (1차 출처)

| 항목 | 근거 | 결과 |
|---|---|---|
| checkpoint 학습 자료 | 공식 models.zip `mutan_noatt_resnet18/{args.yaml, mutan_noatt_resnet18.yaml, logger.json}`. 2022-01-27 학습 | `vqa.trainsplit=train`(VQA1 train만), nans 2000, minwcount 0, nlp mcb, pad right, maxlength 26, samplingans. val은 학습에 쓰지 않았다 |
| checkpoint 선택 | logger val acc1 최대 50.63(epoch 87), 마지막 epoch 88은 45.06. 배포 asset `mutan-resnet18.pth.tar` sha `2674d468…` = `best_model.pth.tar`(≠ ckpt `2f34415b…`) | **VQA1 val 전체로 epoch를 선택했다.** val은 학습 자료는 아니지만 모델 선택에 노출됐다 |
| vocab/answer id | `derive_vocab.py`가 상류 `vqa_processed.py`(sha `99565a5a…`)의 함수를 MultipleChoice train 질문과 annotations에 같은 순서로 실행한다. 상류 `vqa_interim`과 같은 파일·순서다 | embedding 12,435 = 12,434 + 1, classifier 2,000이고 strict load에서 모든 tensor가 같다. **id 순서 검사:** 학습된 embedding의 최근접 이웃(red → scarlet/reddish/pink, two → three/four/five)과 classifier 행의 최근접 이웃(red → blue/yellow/black, left → right/to left)이 의미상 일관된다. 의미적 일관성은 보조 근거이며, 최근접 이웃만으로 전체 id 순서의 정확성을 증명하지는 못한다 |
| tokenizer/padding | 상류 `tokenize_mcb`를 AST로 그대로 실행한다. 학습 때는 26 길이로 오른쪽 zero padding을 했다 | padding을 하든 안 하든 20/20 top-5가 같고 score 차이는 0.0이다(BayesianUniSkip이 길이 위치의 출력을 쓴다) |
| 영상 | 상류 `convnets.factory` = torchvision `resnet18(pretrained=True)`, Scale(448) + CenterCrop(448) + ImageNet 정규화, `sum(3).sum(2)/regions` | 동결 `resnet18-f37072fd.pth`와 공식 구 URL의 `resnet18-5c106cde.pth`(sha `5c106cde…`, 이번에 46.8MB를 받음)는 **102개 tensor가 모두 같다**(max abs diff 0). 학습 시점의 torchvision 버전과 상관없이 같은 가중치다 |
| ASR | 상류 `speech_recognition.py`: wav2vec2-base-960h, `padding=longest`, greedy argmax, attention mask 없음, LM 없음, `librosa.load(sr=16000)` | 동결 adapter의 경로와 같다. fixture FLAC은 16 kHz mono다. 오류(`GRAS`, `TRAFIC`, `TURAF PING`)는 LM 없는 CTC의 철자 오류이고 상류 구성에서도 같다. 20건에서 ASR UNK token 15개, 정답 텍스트 0개 |
| 장치 간 보존 | `qnh-20261023t100710` receipts | 3개 worker(cpu-c, cpu-e0, jgpu-dev0009-cu130)의 speech transcript 60/60이 reference와 같다. 5개 composition(NPU 영상 포함) 모두 품질이 0.515/0.355로 같다. 따라서 이 60건의 성공한 구성에서는 장치 변경에 따른 최종 출력 품질 차이가 관측되지 않았다. 이를 모든 입력·장치의 동등성으로 일반화하지 않는다. NPU vision parity 실패와 NPU speech 요청 오류 33건은 별도 기록으로 남아 있다(새 장비 주장 없음) |
| reproduction | 복사한 adapter(sha `33e4f879…`)로 동결 transcript를 fusion에 넣음 | 20/20 answer_ids가 같고 score 차이는 0.0이다 |

JellyBean의 ProfileTranscriptions.pkl(4,096개 문자열 list, question_id 없음)은 질문과 짝지을 수 없어서 대조하지 못했다.

## 3. calibration 20건 진단의 원자료

- 결과 `.scratch/quality-network-baseline-first/quality/text-vs-asr-cal20.json`(sha `ae57cba7…`)에 행별 tokens, UNK, input ids, top-5, score, VQA 점수, fusion ms, 환경(Python 3.11, torch 2.8.0+cpu, threads 1)을 기록했다.
- 교차표(ASR 점수 / 텍스트 점수): 둘 다 양수 10건, ASR만 양수 1건(3852040), 텍스트만 양수 3건(3355890, 5020240, 5504310), 둘 다 0점 6건.
- 텍스트로도 0점인 question_id: 3733440, 4599120, 5615230, 4185630, 3852040, 5343730, 644090.
- 표본이 20건이므로 +0.10은 효과 크기의 추정치일 뿐이다. 공식 프로필에서 base에서 large로 바꿀 때 val 전체 +2.57%p와 비교하면 20건에서의 변동이 크다.

## 4. 독립 자료와 분할 제안

- **가용 pool(로컬, 추가 다운로드 없음):** 기존 data.zip(10.47 GB, sha `7bc8e89e…`)에 VQA1 val2014 영상 40,504장과 음성 질문 121,512개가 모두 있고 질문과 짝이 맞는다. 60쌍의 영상과 그 영상의 질문 180개를 빼면 **40,444장 / 121,332 질문**이 남는다. VQA1 test/test-dev는 정답이 공개되지 않아 쓸 수 없다. VQA2 val은 같은 COCO val 영상이고 음성이 없으므로 이 서비스 계약 밖이다.
- **학습 중복 위험:** MUTAN 학습 자료는 train2014뿐이다(COCO val2014 영상과 겹치지 않음). ResNet18은 ImageNet-1k, wav2vec2는 LibriSpeech 960h로 학습됐다. 다만 **checkpoint epoch를 val 전체 acc1로 골랐고**(88개 epoch 중 1개), JellyBean 프로필도 val 전체로 측정했다. 따라서 새 holdout은 "학습에는 쓰이지 않았지만 checkpoint 선택에는 노출된" 자료로 표기하고 완전 독립이라고 주장하지 않는다. 음성의 화자·세션 metadata는 로컬 파일에 없다. 그래서 세션 분리는 영상·질문 단위 분리로 대신하며 이 한계를 기록한다.
- **분할(모델 출력을 보기 전에 고정):** `propose_split.py`, seed **20261026**. 정렬한 val image_id에서 관찰한 60장을 빼고 `random.Random(seed).shuffle`한 뒤 앞에서부터 dev 400 / calibration 400 / heldout 800장으로 나눈다. 영상마다 같은 rng로 질문 하나를 고른다(기존의 최저 question_id 규칙을 쓰지 않음). 모든 영상·음성 member가 zip index에 있음을 확인했다. 결과는 `split-proposal-seed20261026.json`(sha `e00fc7ee…`)이고 split hash는 dev `b92464b9…`, calibration `6f936fe0…`, heldout `66a40bbc…`다. annotation은 읽지 않았다. **heldout은 봉인한다.** 정답 결합과 채점은 정책·모델·Qmin을 동결한 뒤 한 번만 한다.
- 규모 근거: 질문당 표준편차 약 0.48 기준으로 SE는 n=400에서 약 0.024, n=800에서 약 0.017이다. reference-v1의 stage_ms 중앙값은 vision 125 ms, speech 190 ms, fusion 20 ms이고(이번 진단의 fusion 중앙값 21 ms), 3-stage를 합치면 약 0.34 s/건이다. dev와 cal 800건은 단일 스레드로 수 분에서 십여 분 걸린다. 대량 추론은 이번에 하지 않았다.

## 5. 권장 다음 단계 (변경 범위 최소)

1. dev 400건(영상·음성 추출은 기존 data.zip에서 로컬로)에 **현행 구성 그대로** CPU reference를 실행하고 VQA 점수와 95% 구간을 기록한다. 같은 dev에서 정답 텍스트 대체 진단을 반복해 ASR 손실을 다시 추정한다. calibration 400은 Qmin gate와 profile 보정에만 쓰고 heldout 800은 봉인을 유지한다.
2. dev 구간의 하한이 Qmin 0.40보다 충분히 높으면 모델 변경 없이 새 calibration/heldout으로 품질 계약을 다시 동결한다. 기존 60건은 진단 기록으로만 남긴다.
3. 여유가 부족하면 최소 변경으로 ASR만 바꾼다. 후보는 `wav2vec2-large-960h-lv60-self`(JellyBean 상류 지원, 315M)다. 이것은 새 모델 버전이며, CPU/GPU/NPU 각각 실행 가능성·parity·지연을 다시 profile한다(NPU 동적 길이 변환은 미검증). 영상 쪽 resnet152 + large(58.33%)는 영상 차원이 2048로 바뀌고 계산량이 늘어 2순위로 둔다. ASR 철자 교정 같은 비상류 후처리와 문턱·표본 조정은 제안하지 않는다.

## 6. 실행 명령 (저장소 루트, 모두 진단 전용)

```bash
Q=.scratch/quality-network-baseline-first/quality
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 timeout 900 rtk proxy \
  .scratch/quality-network-heterogeneous/venv-ref/bin/python -I $Q/diag_text_vs_asr.py --out $Q/text-vs-asr-cal20.json
rtk proxy curl -sSfL --max-time 180 -o $Q/external/resnet18-5c106cde.pth https://download.pytorch.org/models/resnet18-5c106cde.pth
OMP_NUM_THREADS=1 timeout 300 rtk proxy .scratch/quality-network-heterogeneous/venv-ref/bin/python -I \
  $Q/compare_resnet18_weights.py $Q/external/resnet18-5c106cde.pth artifacts/topology-profile-live/model-assets/resnet18-f37072fd.pth $Q/resnet18-weight-equality.json
rtk proxy python3 -I -B $Q/read_jellybean_accuracy_profile.py \
  .scratch/quality-network-baseline-first/research/jellybean/VQA/examples/optimizer_inputs/nodes_accuracy_profiles.pkl $Q/jellybean-accuracy-profile.json
timeout 300 rtk proxy python3 -I $Q/propose_split.py --out $Q/split-proposal-seed20261026.json
```

- 동결 adapter와 vqa_eval은 hash를 검사한 복사본(`$Q/frozen-copy/`, `33e4f879…`, `32ee2b76…`)을 쓴다. 다른 worker가 편집하는 `quality-network-baselines`와 `quality-network-heterogeneous` 코드는 import하지 않았다. JellyBean source는 `artifacts/topology-profile-live/jellybean-source/…65a1d678…/VQA/python`이다. `.scratch/quality-network-heterogeneous/work/jellybean`과 비교하면 `.py` 소스는 같고 `.pyc`만 다르다.
- 구 URL `resnet18-5c106cde.pth`는 legacy tar 형식이어서 `weights_only=False`로 읽었다. download.pytorch.org 공식 파일이고 sha 접두가 파일명과 같다.
- **절차 이탈 기록:** 작업 초반에 읽기 전용 명령 여러 개를 rtk 없이 실행했다(cat/grep/find, `find /`로 `vqa_processed.py` 위치 검색 1회, python3 -c 판독). 첫 진단 실행이 `-B` 없이 돌아서 gitignore된 `artifacts/topology-profile-live/jellybean-source`에 cpython-311 `.pyc` 12개가 새로 생겼다. 이 12개는 지웠고 이후 artifacts 아래에 새 파일은 없다. 이후 명령은 모두 rtk proxy로 실행했다.

## Coordinator scope corrections

2026-10-26: 정답 텍스트 대조의 잔여 오류 원인, embedding 최근접 이웃의 증명 범위, 성공한60건의 장치 동등성 표현을 위와 같이 제한했다. 보고서의 SE 근사는 탐색 설명이며 본 반복 수/MDE의 통계 설계를 대신하지 않는다.
