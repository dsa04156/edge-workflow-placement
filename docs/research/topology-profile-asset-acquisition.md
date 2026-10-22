# topology-profile 실제 모델·입력 자산 확보

확인일: 2026-10-19. 자산 기준 디렉터리: `artifacts/topology-profile-assets/20261019T091725Z/`. JellyBean source commit은 `65a1d67817946a4ae67b068eafb49d649bc061bc`다. 자산 확보·형식 검증 결과이며 클러스터 성능 결과가 아니다.

## 공식 모델과 데이터 다운로드

[JellyBean 공식 README](https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/README.md)의 두 Box 링크는 익명 HTTP GET으로 정상 열렸다. 이전 웹 파서의 `Internal Error`는 다운로드 불가의 근거가 아니었다. 공개 페이지에 `canDownload=true`와 파일 ID가 있었고 공식 shared/static URL의 Range 요청은 ZIP magic과 HTTP206을 반환했다. 접근 제어 우회나 인증정보 입력은 없었다. 보존 HTML의 익명 request token은 제거했고 HTML·대형 ZIP·가중치·입력 미디어는 로컬 `.gitignore`로 제외한다.

| 자산 | 공식 공개 URL | 크기 | SHA256 |
|---|---|---:|---|
| 모델 ZIP | [공식 Box](https://duke.box.com/s/c1pfwbxq6qc51jx9sw91mnp8zstpo3zn), [다운로드](https://duke.app.box.com/shared/static/c1pfwbxq6qc51jx9sw91mnp8zstpo3zn.zip) | 3,726,377,770 B | `92eba1ad04811fff95be4ff475e11cb3aaa65b12a28a198ae04b9f41349baf6e` |
| 입력 ZIP | [공식 Box](https://duke.box.com/s/wu7o721wrqu0ei75ob8yojcw1ntg3tfj), [다운로드](https://duke.app.box.com/shared/static/wu7o721wrqu0ei75ob8yojcw1ntg3tfj.zip) | 10,470,990,374 B | `7bc8e89ee21ce1539cec87bf581629dd7b150662eb4990d6edddb1d54046fa7f` |
| VQA1 train questions | [VQA 공식 S3](https://s3.amazonaws.com/cvmlp/vqa/mscoco/vqa/Questions_Train_mscoco.zip) | 22,006,431 B | `e9b08177345dffc3b5e3f442ba8f4a5bd60261e24979a6ed7cc129f8fe722647` |
| VQA1 train annotations | [VQA 공식 S3](https://s3.amazonaws.com/cvmlp/vqa/mscoco/vqa/Annotations_Train_mscoco.zip) | 12,177,326 B | `3fa91827c57afe99c01220c5e6129d34253c8989fc1da795df0f615e3504cafa` |
| VQA1 val annotations | [VQA 공식 S3](https://s3.amazonaws.com/cvmlp/vqa/mscoco/vqa/Annotations_Val_mscoco.zip) | 6,036,155 B | `12243b1adb1375492586110714b482ba39899a7a68972f566bd1648147fe6ac4` |

모델 ZIP의52개 항목과 입력 ZIP의162,023개 항목은 각각 `models-zip-index.json`, `data-zip-index.json`에 중앙 디렉터리로 기록했다. 항목의 uncompressed/compressed bytes·ZIP CRC·header offset을 보존한다.30MB 전체 입력 index는 로컬에 보존하고 Git에서는 제외하며,121개 선택 항목은 `selected-data-zip-index.json`으로 남긴다. 선택 파일 추출은 ZIP CRC와 길이를 검사했다. 전체 입력 ZIP 다운로드가 끝나기 전에도 공개 Range로 필요한 파일을 확보했으므로 선택 입력의 준비 상태와 전체 archive 상태를 구분한다. HTTP metadata는 `*-http.json`, `*-download-probes.json`, `*-download.json`에 있다. 대형 파일 재취득은 예를 들어 다음 명령으로 가능하다.

```bash
rtk proxy curl --fail --location --output models.zip 'https://duke.app.box.com/shared/static/c1pfwbxq6qc51jx9sw91mnp8zstpo3zn.zip?download=1'
rtk proxy curl --fail --location --output data.zip 'https://duke.app.box.com/shared/static/wu7o721wrqu0ei75ob8yojcw1ntg3tfj.zip?download=1'
```

## 정확한 ResNet18용 MUTAN checkpoint

모델 ZIP의 `vqa-workload/python/trained_models/variants/mutan_noatt_resnet18/`에서 `best_model.pth.tar`, YAML, best_info, logger, args와 마지막 checkpoint를 `selected-model/`로 추출했다. `best_model.pth.tar`는135,343,293B, SHA256은 `2674d468b6feceda739b9c347d4cc5858a134883304b9106a6a32560ac55c8fc`다. 대응 YAML의 SHA256은 `96982eac7c3a9fc3509115b5a95a0d5cc593b89fe4d7cebb491892068d796b28`다. 아키텍처는 `MutanNoAtt`, vision512차원, question2400차원, R10, hidden360, classifier2000이다. [공식 config](https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/VQA/python/configs/variants/mutan_noatt_resnet18.yaml)

`/tmp/tp-reference-env/bin/python`의 torch2.8.0 CPU에서 `torch.load(...,map_location='cpu',weights_only=True)`로 checkpoint를 읽었다. 모든 항목은 tensor state_dict이며 `seq2vec.embedding.weight`는 `[12435,620]`, BayesianGRU의 입력/상태 가중치와 classifier `[2000,360]`가 모두 있다. 목록은 `best_model.pth.tar.inspection.json`이다. `best_info`의 weights_only 읽기는 `vqa.lib.logger.Experiment` 비허용으로 실패했으며, 이를 회피해 임의 pickle을 실행하지 않았다. inference 가중치 자체의 로드는 성공했다.

두 Box ZIP에 skipthought 초기 학습 자산(dictionary.txt,utable.npy,uni_skip.npz)은 없다. 공식 source의 생성자는 이 파일로 임베딩과 GRU를 초기화하지만, 확보 checkpoint에 해당 학습된 tensor가 전부 포함돼 있다. 따라서 동일 module shape를 생성한 뒤 **전체 state_dict strict load 성공을 확인하면** 초기 사전학습 파일을 다운로드할 필요가 없다. 이를 tensor 누락을 허용한 초기화나 랜덤 head 사용과 혼동하지 않는다. 이 변경의 실제 strict load·forward 확인은 실행 담당 결과에 기록한다. [공식 skipthought 구현](https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/VQA/python/vqa/external/skip-thoughts.torch/pytorch/skipthoughts.py)

## vocabulary/answer mapping 재구성

두 Box ZIP에는 train vocabulary·answer mapping이 없고 checkpoint에도 문자열 mapping은 없다. 공식 VQA1 train JSON을 받아 pinned `vqa_processed.py`의 원 함수를 그대로 사용해 재구성했다. `derive_vocab.py`는 관련 다섯 함수를 AST로 추출하며 토큰화·정렬·필터링 함수 본문을 변경하지 않는다. train questions/annotations248,349쌍의 question_id가 순서대로 일치함을 확인했다. top2000 답변 필터 후225,202개 질문,12434개 단어(UNK포함)를 얻었다. embedding행12435와 classifier행2000에 대응한다. [공식 interim](https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/VQA/python/vqa/datasets/vqa_interim.py), [공식 preprocessing](https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/VQA/python/vqa/datasets/vqa_processed.py)

`derived-vocab/vocab.json` SHA256은 `de86a3596f0bfdce3e733b03a810acae0f8527e49b6fa052001ed86d5cfbedda`다. 원 dataset factory용 네 pickle도 생성했다. JSON과 pickle 해석상의 숫자 key 차이에 주의한다. `wid_to_word` JSON key는 문자열이다. 재생성 명령은 다음과 같다.

```bash
rtk proxy python artifacts/topology-profile-assets/20261019T091725Z/derive_vocab.py --source-root PATH_TO_PINNED_JELLYBEAN
```

**잔여 검증:** 배포된 원 vocabulary의 독립 hash가 없어, 훈련 당시 word index 순서와의 byte 동일성까지 입증한 것은 아니다. 현재 결과는 공식 train 입력·전처리로 재구성했고 checkpoint 차원과 일치한다. dict의 최초 관측 순서를 사용하므로 원 훈련의 Python/데이터 순서가 달랐을 가능성은 남는다. 실제 validation 품질과 CPU/분산 동일성을 확인하고, 정확도 결과를 원 JellyBean의 보고 정확도와 동일하다고 주장하지 않는다.

## 실제 이미지·음성 입력60쌍

`selected-data/pairs.json`은 추론 결과를 보기 전에 생성했다. 전체 val question에서 image_id를 정렬한 다음 seed20261019로 섞고 각 image의 가장 작은 question_id 하나를 고른다. 첫20개는 calibration, 다음40개는 evaluation이며 image가 겹치지 않는다. Box ZIP의 JPEG·동일 question_id FLAC를 추출했고 길이·CRC·SHA256를 검증했다.60개 JPEG는 Pillow의 verify를 통과했으며60개 FLAC는 모두 mono16kHz다. manifest에 해상도·음성 길이·파일 절대경로·각 파일 hash·질문·공식 val annotation의 정답과10개 human answers를 보존한다. 최종 manifest SHA256은 `d680aab23eac79a804800d372e6a49abe38aeeb7cca2ceca7caa81bf67930c97`다.

이는 실제 데이터셋 파일을 엣지에서 재생할 입력이며 실카메라·마이크 수집을 검증하지 않았다. 입력을 실제 모델에 통과시키는 것과 정답 품질을 통과하는 것은 별도 gate다. 결과를 보고 불일치 입력을 제외하지 않는다. calibration/evaluation은 배치 보정의 구분이며 원 모델의 train/val split을 다시 학습하는 것이 아니다.

## 사용 조건과 다음 검증

JellyBean README는 VQA와 Speech-based VQA를 출처로 명시한다. Box 페이지의 공개 다운로드 허용은 재배포 라이선스의 대체 근거가 아니다. 본 작업은 공개 자산의 로컬 연구 실행을 준비했으며 미디어·가중치를 Git에 재배포하지 않는다. 현재 checkpoint와 mapping 및60개 입력은 준비됐고, ResNet18/wav2vec2 가중치·동일 module의 strict load·실제 inference·아키텍처별 실행 검증은 통합 실행 담당에서 이어진다.
