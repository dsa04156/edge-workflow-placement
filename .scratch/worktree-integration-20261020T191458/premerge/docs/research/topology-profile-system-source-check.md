# 실제 topology-profile 실험의 모델·스케줄러 근거 확인

확인일: 2026-10-19. 대상 준비 코드: `e110d8b`. 상태: **소스 검토 완료, 모델 다운로드·실행 미검증**. 본 문서는 실험 결과가 아니다.

## 1. 가장 먼저 닫아야 하는 모델 계약

JellyBean 공식 저장소를 GitHub MCP로 확인했다. 확인 commit은 `65a1d67817946a4ae67b068eafb49d649bc061bc`이다. 단순히 ResNet과 임의 VQA 모델을 연결하는 것으로 원 워크플로가 되지 않는다. 다음 조합을 먼저 단일 CPU 프로세스에서 검증하는 것이 모델 구조 변경을 최소화한다.

| Stage | 소스에서 확인한 계약 | 이번 실험 제안 |
|---|---|---|
| vision | `ImageFeatureExtractor`는 BGR 입력을 RGB로 바꾸고 resize/center crop/정규화한다. ResNet layer4 출력을 공간 평균하면 no-att feature가 된다. | ResNet18, 입력 크기448, `use_att_feat=false`, 단일 입력/batch1을 고정한다. |
| speech | `SpeechRecognitionEngine`은 `facebook/wav2vec2-base-960h` 또는 `facebook/wav2vec2-large-960h-lv60-self`를 사용해 CTC argmax를 텍스트로 decode한다. processor의 sampling rate와 입력 rate가 일치해야 한다. | base-960h, mono16kHz 입력과 processor revision을 고정한다. |
| fusion | `VQAInferenceEngine`은 이미지 feature와 질문 문자열을 받아 MUTAN 계열로 답변한다. ResNet18 no-att config는 `dim_v=512`, 최대 질문길이26, 답변 vocabulary2000을 사용한다. | **해당 ResNet18용 MutanNoAtt checkpoint**와 훈련 시 vocabulary/answer mapping을 함께 고정한다. |

근거: [feature_extractor.py](https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/VQA/python/feature_extractor.py), [convnets.py](https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/VQA/python/vqa/models/convnets.py), [speech_recognition.py](https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/VQA/python/speech_recognition.py), [vqa_inference.py](https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/VQA/python/vqa_inference.py), [ResNet18 config](https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/VQA/python/configs/variants/mutan_noatt_resnet18.yaml).

위 제안은 소스에 존재하는 모델 경로에 따른 선택이며 현재 Pi/서버에서 실행된다는 증거는 아니다. 512개 FP32 원소라면 tensor 본체는2048bytes지만, **실제 전달 bytes는 직렬화 후 측정**해야 한다. 주 실험에서는 feature를 임의 padding하거나 attention feature로 바꿔 네트워크 효과를 부풀리지 않는다. Attention 모델로 확장한다면 checkpoint·품질·중간 데이터가 함께 바뀌는 별도 워크플로로 명명한다.

## 2. 체크포인트·데이터와 의존성의 미해결점

공식 README는 가공한 VQA/Speech-based VQA 데이터와 variant별 pretrained checkpoint의 Box 링크를 제공한다. 이번 브라우저 열기는 두 링크 모두 `Internal Error`였으므로 **현재 다운로드 가능·불가능 어느 쪽도 확정하지 않았다**. 실제 파일 목록·hash·라이선스 및 재배포 조건을 확인하는 것이 첫 gate다. [공식 README](https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/README.md), [데이터 링크](https://duke.box.com/s/wu7o721wrqu0ei75ob8yojcw1ntg3tfj), [모델 링크](https://duke.box.com/s/c1pfwbxq6qc51jx9sw91mnp8zstpo3zn).

VQA 엔진은 시작 시 `datasets.factory_VQA`로 trainset vocabulary와 answer mapping을 만들고, `models.factory`와 checkpoint를 로드한다. YAML은 skip-thoughts 경로도 요구한다. 따라서 `.pth` 한 개만 확보한 상태를 준비 완료로 세지 않는다. 또한 YAML이 `logs.dir_logs`를 지정하고 엔진이 config와 YAML을 병합하므로 실제 checkpoint 경로 해석을 smoke에서 검증한다. 위 `vqa_inference.py`와 config가 직접 근거다.

`requirements.txt`는 버전이 고정되지 않은 numpy/torch 등의 목록이며 `speech_recognition.py`가 import하는 transformers/librosa와 feature extractor의 torchvision을 모두 명시하지 않는다. `transforms.Scale`, `pretrained=True` 등 기존 API를 사용하므로 최신 패키지를 단순 설치한 것으로 재현을 주장할 수 없다. 의존성 고정과 필요한 호환 패치는 따로 기록하고, CPU reference의 feature·전사·답변을 검증한다. [requirements.txt](https://github.com/libertyeagle/JellyBean/blob/65a1d67817946a4ae67b068eafb49d649bc061bc/VQA/python/requirements.txt), 위 stage 소스.

공식 엔진의 device 분기는 CPU 또는 CUDA다. NPU 직접 지원을 확인한 것이 아니며 이번 CPU 우선 계획과 NPU 변환 실험은 분리한다. VQA 엔진은 입력 feature에 무조건 batch 차원을 추가하므로 첫 adapter는 문서의 batch 일반성에 기대지 않고 batch1로 한정해 shape를 검사한다. 소스에서 발견한 제약을 수정할 경우 patch hash를 남긴다.

체크포인트를 확보하지 못하면 임의 head 또는 다른 VLM을 같은 이름의 VQA로 대체하지 않는다. 모델 준비는 blocked로 두고 synthetic RPC로 fork/join의 무결성만 검증할 수 있다. 대체 VQA가 원본 이미지를 다시 받거나 자체 encoder를 실행하면 간선·연산 의미가 달라지므로 별도 DAG로 명시해야 한다.

## 3. Kubernetes 비교의 공정성

Context7 `resolve_library_id(Kubernetes)`를 호출했으나 월간 quota 초과로 문서를 받지 못했다. 아래는 공식 Kubernetes 웹 문서를 직접 확인한 근거다. 실제 클러스터 kube-scheduler는 부모 조사에서 `v1.31.14`로 확인됐으며, 최신 문서의 새 기능이나 특정 default plugin 목록을 이 클러스터 설정으로 추정하지 않는다.

- `nodeName`은 scheduler를 우회한다. 제안 배치는 required node affinity로 선택 노드를 제한하고, 기본 비교군은 동일한 실행 가능 후보 집합만 제한한다. 두 경우 모두 실제 scheduler 결정을 캡처한다. [Assigning Pods to Nodes](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/)
- CPU/메모리 scheduling은 requests를 기준으로 한다. snapshot의 가용량은 Allocatable과 이미 예약된 requests를 대조하고, 순간 사용률을 예약 가능한 양으로 대체하지 않는다. init container·Pod overhead 등 유효 요청의 차이는 API dry-run과 scheduler 이벤트로 확인한다. [Resource Management](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/)
- GPU device plugin은 extended resource를 노출한다. GPU limits를 명시하며 requests도 명시하면 값이 같아야 한다. 모든 정책에 같은 extended resource와 후보를 적용해야 한다. [Schedule GPUs](https://kubernetes.io/docs/tasks/manage-gpus/scheduling-gpus/)

계획상의 결론: 첫 비교는 CPU-only stage로 동일한 후보 계약을 만들고 Pi와 서버의 CPU 이종성을 측정한다. GPU 요청이 붙은 동일 Pod는 CPU-only 노드로 갈 수 없으므로 CPU/GPU를 한 후보표에 섞는 확장은 node→device 선택 및 후보별 Pod 자원 명세를 명시적으로 설계한 뒤 별도 비교로 수행한다. GPU 등록만으로 모델 실행 가능성을 판정하지 않는다.

## 4. 실행 전 통과 기준

1. **자산 확보:** 가중치·config·vocabulary·answer mapping·skip-thoughts 자산·입력 pair의 hash와 provenance를 manifest로 동결한다. 다운로드 실패는 기록하고 성능 측정으로 넘어가지 않는다.
2. **단일 프로세스 reference:** paired image/audio로 feature shape/dtype, ASR 문자열, 최종 답변을 확인한다. stage별 출력 수치와 최종 답변 정확도를 구분한다. 품질 기준과 보정/평가 입력 분할은 성능 확인 전에 정한다.
3. **아키텍처별 실행:** amd64/arm64 이미지 digest, 실제 node/device, RSS, 모델 준비시간, 반복 실행 성공·품질을 확인한다. 통과한 task×node만 후보에 넣는다. 어떤 stage의 후보가 하나뿐이면 해당 배치 자유도의 제한을 보고한다.
4. **분산 동등성:** source의 동일 request_id에 대해 실제 vision/speech Pod가 fusion으로 직접 전송하고, 중복·지연·누락 branch에서도 정확히 한 번 결과를 만든다. reference와 결과 및 전달 hash를 대조한다.
5. **동일 조건 배치:** default-scheduler와 affinity 계획의 이미지·requests/limits·입력·품질·후보가 같은지 확인한다. Pod UID/실제 배치/스케줄러 이벤트를 보존한 뒤 노드별 연산과 실제 payload 경로를 보정한다.

이번 통합은 **JellyBean 모델 경로 기반의 Kubernetes 정적 배치 실험**이다. 원 optimizer·timely-dataflow executor·모델 선택·처리량 목적까지 재현한 JellyBean 전체 시스템 재현으로 부르지 않는다.
