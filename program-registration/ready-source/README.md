# EdgeFlow 실행 패키지

소스 제출본과 별도 런타임 자료를 함께 사용한다. 배치 계획, 실제 이미지·음성·결합 추론, 클러스터 비교 실험, 결과 검증·보고서 생성을 연결했다. 이전 소스 묶음을 대체한다.

## 빠른 실행

소스 ZIP을 최종 실행 위치에 풀고 해당 폴더에서 실행한다. 실행 환경은 리눅스이며 런타임 휠은 Python3.11·x86_64/ARM64용이다. 컨트롤러는 tarfile의 data 필터를 지원하는 Python3.11.8이상 또는3.12를 사용한다. 모델 실행용 Python3.11이 별도로 필요하다.

```bash
python3 edgeflow.py import-assets /path/to/EdgeFlow_runtime_assets.tar
python3 edgeflow.py setup-local --python python3.11
python3 edgeflow.py local-smoke --out output-local.json
python3 edgeflow.py plan --out output-plan
```

첫 명령은 제공 런타임 자료의 전체·개별 파일·모델 묶음·60쌍 입력 hash를 검사해 가져온다. 두 번째 명령은 새 가상환경에 동결 휠을 오프라인 설치한다. 세 번째는 실제 ResNet18/Wav2Vec2/MUTAN을 로컬 HTTP DAG로 연결하고2입력·6receipt·CPU reference 품질을 검사한다. 이는 단일 호스트 기능 검증이며 클러스터 성능 실험이 아니다. 네 번째는 제공된 과거 실측 프로파일로 배치 계획을 재생한다.

같은 출력 이름은 덮어쓰지 않는다. 설치 후 패키지 폴더를 이동하면 가상환경 경로가 달라지므로 최종 위치에 먼저 압축을 푼다. Linux시스템에 Python3.11,venv,표준 런타임 라이브러리가 필요하다. 실패한 설치는 local-runtime/setup-failed.txt와 터미널 오류를 확인한 후 별도 새 위치에서 재시도한다.

## 실제 장비 실행

cluster.json의 context,namespace,nodes(e0/e1/c),image를 해당 환경에 맞춘다. 기본값은 기존 검증 클러스터다. 이미지 source는e0, 음성 source는e1에 고정된다. 준비된 namespace와 kubeconfig, kubectl 및 실행 권한이 필요하다. 실행 호스트에서 Pod IP로 직접 접속할 수 있어야 하며, 네트워크 제어 대상 노드의 Pod CIDR은 10.0.0.0/8 범위여야 한다. 기본 이미지는 사설 레지스트리에 있으므로 각 노드가 접근할 수 있는 Python 3.11 기반 amd64/arm64 이미지를 지정한다. 자격 증명은 패키지에 포함하지 않는다.

```bash
python3 edgeflow.py doctor
python3 edgeflow.py run
python3 edgeflow.py verify --run artifacts/topology-profile-live/생성된실행폴더
python3 edgeflow.py report --run artifacts/topology-profile-live/생성된실행폴더 --out output-report.md
```

`doctor`는 읽기 전용으로 장비Ready·context·namespace·주요 권한을 검사한다. 이미지 접근성·실제 자원 admission·모델 품질은 실제 실행에서 검증한다. `run`은 새 소유 Pod/ConfigMap을 만들고 보정→계획 동결→평가→정리를 수행한다. 소유 Pod 네트워크 공간의 대역폭/지연을 제어하므로 NET_ADMIN capability를 허용하는 실험 환경이 필요하다. 호스트 인터페이스는 변경하지 않는다. 실효대역폭 검사와 우회차단 검사를 통과해야 평가한다.

`--config 다른설정.json`을 사용했다면 doctor/run/verify/report 모두 같은 옵션을 사용한다. `run --budget`은 후속 슬롯 제한 비교이며 보고서도 `report --budget`을 사용한다. 일반 run은 기존에 완주한 비교 설계다. 과거 KubeEdge exec EOF의 근본 원인은 아직 확인하지 못했다. 장애나 중단 시 부분 stdout/stderr와 정리 결과를 보존한다. 이번 패키징에서는 새 클러스터 전체 비교를 실행하지 않았다.

## 컨테이너 이미지

cluster.json에는 기존 실험의 Python 3.11 이미지 digest가 있다. 다른 환경에서는 Dockerfile.runtime으로 대상 amd64/arm64 이미지를 만들고 image 값을 바꾼다. 공식 python:3.11-slim-bookworm 기반의 대체 빌드 파일이며, 이 이미지의 클러스터 검증은 별도로 필요하다. 모델과 휠은 실험 Pod의 emptyDir에 설치한다.

공식 이미지 소스: https://github.com/docker-library/python/blob/master/3.11/slim-bookworm/Dockerfile

## 자료와 소스의 구분

- 등록 제출: `EdgeFlow_ready_source.zip`. 자체 소스와 실행에 필요한 설정, 예제, 자료 해시 목록, 실행 설계 및 컨테이너 빌드 파일을 포함한다.
- 별도 실행 자료: `EdgeFlow_runtime_assets.tar` (약 1.05 GB). 모델, 입력, 기준 출력, 아키텍처별 휠과 외부 모델 코드를 포함한다. 등록 화면의 190 MB 제한 소스 첨부란에 올리지 않는다. 공개 Git에도 추가하지 않는다.
- 클러스터 자격 증명과 컨테이너 이미지는 포함하지 않는다. 외부 자료의 출처와 이용 조건은 원 실험의 기록을 따른다.

## 코드와 검증

```bash
python3 edgeflow.py test
```

기존 전체 소스에 환경 준비와 통합 실행 진입점을 추가했다. 절대 경로, Git checkout 및 rtk 의존을 제거하고 장비 설정을 cluster.json으로 분리했다. 모든 클러스터 명령에 설정된 context를 지정한다. HTTP 서버 종료 시 모델 작업 스레드의 종료를 기다린다. 런타임 소스가 달라졌으므로 새 장비 비교는 새 보정으로 시작한다.

로컬 검증은 실제 모델을 사용한 입력 두 건과 실행 기록 여섯 건을 확인한다. 전체 데이터의 품질 동등성이나 클러스터 성능 검증을 뜻하지 않는다. 과거 27.83% 개선은 기존 단일 실험 구간의 관측 결과다.
