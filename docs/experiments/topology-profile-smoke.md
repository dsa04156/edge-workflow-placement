# 실제 영상·음성 합류 DAG 첫 실행

2026-10-19 KST 실행 완료. **실제 모델·실제 클러스터의 실행 연결 검증**이다. 입력은 엣지의 데이터셋 파일 재생이며 카메라/마이크 실시간 수집은 아니다. 제안 배치 알고리즘의 우월성이나 네트워크 제한 실험 결과가 아니다.

## 확인한 결과

ResNet18 특징 추출과 wav2vec2-base 음성 인식을 병렬로 실행하고, 두 출력이 직접 MUTAN VQA Pod로 전달되도록 연결했다. 이미지 source는 Pi E0, 음성 source는 Pi E1에 고정했다. 두 배포에서 각각2warmup+10평가, 총24개 workflow와72개 stage receipt를 대조했다. 실행 오류·CPU reference 대비 answer ID/Top5 순서 불일치는0이었다.

| 배포 | 실제 vision / speech / fusion 위치 | 평가 수 | 평균 E2E | 중앙값 |
|---|---|---:|---:|---:|
| 엣지 분기 고정 | dev0003 / dev0002 / ser0002 | 10 | 1237.69ms | 1356.86ms |
| 실제 default-scheduler | ser0002 / ser0002 / ser0002 | 10 | 723.43ms | 888.50ms |

이는 같은 날짜에 순차 실행한 두 배치의 기술 통계다. 첫 배치는 분기를 엣지에 고정했고 둘째는 vision의 E0/C, speech의 E1/C 후보를 실제 scheduler에 제공했다. fusion은 둘 다 C고정이다. 둘째의 모든 task가 서버에 배치됐어도 **이미지는 E0, 음성은 E1에서 발생**했다.

두 배포의 평균 차이를 새 방법의 개선율로 제시하지 않는다. 무작위 block 반복·동일 프로파일 동결에 따른 정책 선택·네트워크 regime 비교는 수행하지 않았다. 실제 scheduler가 native에서 유리한 서버 배치를 선택한 결과도 후속 비교의 기준으로 유지한다.

| 항목 | 엣지 분기 고정 | default-scheduler |
|---|---:|---:|
| 실행 / 평가 요청 | 12 / 10 | 12 / 10 |
| 유효 평가 | 10 | 10 |
| 실행 / 기준 품질 실패 | 0 / 0 | 0 / 0 |
| Top5 순서 일치 | 12/12 | 12/12 |
| 최대 answer score 절대 오차 | 2.24e-7 | 0 |
| 후보 경로 품질 확인 입력 | 6 | 6 |
| CPU reference 대비 feature 최대 절대 오차 | 1.79e-6 | 0 |
| ASR 문자열 일치 | 6/6 | 6/6 |

ARM의 feature 허용오차는 rtol1e-4/atol1e-5, ASR문자열/answer ID는 exact, score 절대 오차는1e-4로 reference 추론 전에 고정했다. Top5 순서는 별도로 확인했다. 품질 probe는 해당6입력의 단독 stage 검증이며 ARM의 전체60입력 품질 동등성을 뜻하지 않는다.

## 모델·입력과 기준 출력

- JellyBean source: `65a1d67817946a4ae67b068eafb49d649bc061bc`.
- vision: torchvision ResNet18 ImageNet1K V1, RGB resize/crop448, layer4 공간 평균, FP32 512차원. NPY 직렬화2176bytes이며 tensor 본체2048bytes와 구분한다.
- speech: `facebook/wav2vec2-base-960h`, 고정 revision·local safetensors, mono16kHz FLAC 입력, CTC 전사.
- fusion: 공식 ResNet18용 MutanNoAtt. checkpoint SHA256 `2674d468b6feceda739b9c347d4cc5858a134883304b9106a6a32560ac55c8fc`.
- 모든 실행은 CPU/batch1, intra-op1thread였다. accelerator 등록/실행 결과로 세지 않는다.

공식 Box 모델/데이터 archive를 공개 다운로드했고 필요한60쌍을 추론 전에 선택했다.20calibration/40evaluation은 image ID가 겹치지 않는다. 후보 품질 probe/워밍업은 첫 calibration 입력, 평가 smoke는 first5evaluation 입력을2회씩 재생했다. 결과를 본 뒤 입력을 제외하지 않았다.

전체60쌍의 단일 프로세스 CPU reference는60/60성공했다. 첫 질문은 `WHAT IS THIS GENTLEMAN JUMPING WITH`, 답변은 `skateboard`였다. 공식 multiple-choice 정답 문자열과의 단순 일치는24/60이다. 이는 공식 VQA accuracy metric이 아니며 같은 실행 경로의 동등성 검증과 구분한다.

MUTAN의 학습된 embedding/GRU가 checkpoint에 모두 있어, 사용하지 않는 skipthought 사전학습 초기화를 생략하고 원 graph의56개 state 항목을 strict load했다. 각 tensor의 torch.equal을 확인했다. 임의 head나 빠진 weight를 허용하지 않았다. vocabulary는 공식 VQA1 train 입력과 고정 원 전처리로 재구성했으며 **훈련 당시 원 mapping의 독립 hash는 없다**. 모델 전체 재현 정확도나 원 JellyBean optimizer/실행기의 재현으로 부르지 않는다.

Transformers는 `masked_spec_embed`가 checkpoint에 없다는 경고를 냈다. 이 항목은 학습 masking 또는 명시 mask에서 사용되며 adapter는 eval/no-mask로 실행했다. 해당 설치 소스의 `_mask_hidden_states`와 버전/hash를 [진단 파일](../../artifacts/topology-profile-live/asr-eval-augmentation-check.json)에 보존했고 경고를 stderr에서 지우지 않았다.

## 데이터 경로·지표 경계

E0 source Pod가 로컬 JPEG를 vision으로 보내고, E1 source Pod가 로컬 FLAC를 speech로 보냈다. 외부 collector는 ID·endpoint·시작 제어만 전달했다. vision NPY feature와 speech UTF-8 전사는 각 stage가 fusion으로 직접 보냈다. source가 중간 출력을 모두 중계하는 구조가 아니다.

실제 wire는 JSON/base64다. receipt의 payload bytes/hash는 직렬화한 task 데이터 기준이며 HTTP header·base64 envelope 크기와 구분한다. 첫 구현의 serialization/ACK/control 경로까지 포함한 실제 E0 monotonic E2E를 측정했다. 연산·RPC·분기 시간을 단순 합산하지 않았다. 닫힌 루프 smoke이며 고정 RPS·실센서 스트림·one-way 순수 네트워크 측정이 아니다.

실험 image는 기존 다중 아키텍처 Python digest를 재사용했다. ARM은 Python3.11.16/aarch64, 서버는3.11.16/x86_64다. hash 고정 wheel을 실험 emptyDir에 오프라인 설치하고 모델/소스 bundle hash를 확인했다. CPU reference는 수집 PC의 Python3.12.3이며, 장치 간 수치 차이는 위 허용오차로 검사했다. stage별 모델 준비시간과 pip/cache 업로드는 실행 로그로 따로 보존했다. 이를 지연 표나 운영 배포 비용에 합치지 않았다.

## 실패한 준비 시도와 수정

`tp-20261019t093320`은 기본 scheduler 후보를 API 서버에 등록하는 단계에서 중단됐다. `metadata.name`의 matchFields In에 여러 값을 넣은 기존 준비 명세가 거부됐다. 모델 성능 요청은0개이며 두 성공 배포에 합치지 않았다.

후보마다 단일 값의 nodeSelectorTerm을 만들어 OR로 연결하도록 `prepare.py`와 smoke manifest를 수정했다. 로컬 회귀에서 먼저 실패를 확인한 뒤 수정했고, 새 배포 `tp-20261019t093419`가 성공했다. 성공한 명세의 API server dry-run도 통과했다. 원 실패 출력·부분 생성 리소스의 정리 증거는 유지했다.

코드 리뷰에서는 events 조회 실패가 cleanup을 건너뛰는 경로와 model/final-output hash 대조 누락도 발견했다. 후속 collector는 진단과 삭제를 분리하고, Pod/ConfigMap0개·보호 workload 전후를 확인하며 모델/hash 대조를 수행한다. 첫 실행은 원 collector를 동결하고36개 모델 receipt 및12개 최종 출력 hash를 별도 사후 audit했다. 첫 collector가 나중 수정으로 수집된 것처럼 보고하지 않는다.

## 실제 확인한 명령

작업트리: `/home/jinuk/codex-work/scheduler-topology-profile-live`, 브랜치 `experiment/topology-profile-live`, 기준 `e110d8b`. 아래 명령을 실제 실행했다. 재실행에는 로컬 asset/bundle 준비가 필요하며 다운로드 자산은 Git에 포함하지 않는다.

```bash
rtk git worktree add -b experiment/topology-profile-live ../scheduler-topology-profile-live origin/experiment/topology-profile-system-20261019
rtk proxy uv venv --python 3.12 /tmp/tp-reference-env
rtk proxy uv pip install --python /tmp/tp-reference-env/bin/python --index-url https://download.pytorch.org/whl/cpu torch==2.8.0 torchvision==0.23.0
rtk proxy uv pip install --python /tmp/tp-reference-env/bin/python transformers==4.57.1 librosa==0.11.0 soundfile==0.13.1 opencv-python-headless==4.11.0.86 PyYAML==6.0.3 nltk==3.9.2 scipy==1.15.3 h5py==3.14.0 numpy==1.26.4
rtk proxy python3 -m unittest discover -s experiments/real-topology-profile -p test_prepare.py -v
rtk proxy python3 -m unittest discover -s experiments/real-topology-live -v
rtk proxy python3 -u experiments/real-topology-live/run_smoke.py
rtk proxy python3 -u experiments/real-topology-live/run_smoke.py --placement default_scheduler
rtk proxy kubectl --request-timeout=10s get pods,configmaps -n topology-profile-lab -l experiment=topology-profile
```

reference 실행은 환경변수를 설정해 다음 CLI를 사용했다.

```bash
# 이 작업트리에서 실행하며 출력 디렉터리는 새 경로여야 한다.
MODEL_ROOT="$PWD/artifacts/topology-profile-live/model-assets" \
JELLYBEAN_SOURCE="$PWD/artifacts/topology-profile-live/jellybean-source/JellyBean-65a1d67817946a4ae67b068eafb49d649bc061bc/VQA/python" \
rtk proxy /tmp/tp-reference-env/bin/python -u experiments/real-topology-live/reference.py \
  --pairs /home/jinuk/codex-work/scheduler/artifacts/topology-profile-assets/20261019T091725Z/selected-data/pairs.json \
  --out artifacts/topology-profile-live/reference-v1
```

로컬 `reference-v1`은 이미 존재하므로 같은 경로로 실행하면 거부한다. stdout/stderr는 별도 파일로 보존했다. 실제 node용 Python3.11 wheel은 `pip download --only-binary=:all: --python-version 311 --abi cp311`로 ARM/x86 각각 수집했다. ARM torchvision은 `0.23.0`이며 `0.23.0+cpu` 배포가 없어 첫 다운로드가 실패했다. x86은 `0.23.0+cpu`, torch는 양쪽 `2.8.0+cpu`다. [아키텍처별 고정 requirements](../../artifacts/topology-profile-live/requirements-aarch64.txt)와 [x86 requirements](../../artifacts/topology-profile-live/requirements-x86_64.txt)에 전체 해시를 보존했다.

현재 collector에는 API dry-run과 보호 상태 snapshot 보완이 추가돼 있다. 측정 당시 정확한 source는 각 run의 `collector-source/`와 ConfigMap manifest를 따른다. 새 collector 수정은 과거 latency를 변경하지 않는다.

## 원자료와 복구

- [집계·한계](../../artifacts/topology-profile-live/smoke-comparison.json)
- [엣지 분기 배포](../../artifacts/topology-profile-live/tp-20261019t092957/summary.json), [사후 hash/복구 audit](../../artifacts/topology-profile-live/tp-20261019t092957/post-run-audit.json)
- [API 거부 시도](../../artifacts/topology-profile-live/tp-20261019t093320/005-workers.stderr), [정리 확인](../../artifacts/topology-profile-live/tp-20261019t093320/cleanup.json)
- [기본 scheduler 배포](../../artifacts/topology-profile-live/tp-20261019t093419/summary.json), [정리](../../artifacts/topology-profile-live/tp-20261019t093419/cleanup.json), [보호 상태](../../artifacts/topology-profile-live/tp-20261019t093419/protected-after.json)
- [CPU reference](../../artifacts/topology-profile-live/reference-v1/summary.json), [API dry-run](../../artifacts/topology-profile-live/server-dry-run.json)

각 run에 manifest, 실제 node/Pod UID/image ID, 단계별 stdout/stderr/start-end event, qualification, requests JSONL, receipt와 원 collector가 있다. 임시 namespace는 유지하고 소유 Pod/ConfigMap은 모두0개를 확인했다. 두 성공 실행 전후 보호 대상 Running194개는 상태/Ready 변화가 없었다. host NIC·EdgeCore/CloudCore·기존 서비스·가속기 할당은 변경하지 않았다.

다음은 두 task의 측정 후보를 확대하고 공통 비용표를 동결해6개 정책을 동일 후보/자원에서 비교하는 단계다. 현재 native에서 기본 scheduler가 이미 서버 배치를 선택했으므로 이를 이기기 어려운 조건도 포함해야 한다. 토폴로지·성능 정보의 추가 이득은 이후 네트워크 제약·무작위 block 비교에서 확인한다.
