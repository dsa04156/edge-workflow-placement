# E0: 기존 KubeEdge 환경과 공통 CPU 실행 검증

관측일: 2026-10-16. 아래 인벤토리는 point-in-time 관측이다. ARM/x86 공통 CPU fixture 검증은 통과했으며, 분산 배치 실험 전체를 통과한 것은 아니다. 실제 모델의 GPU/NPU 실행 지원은 자원 등록과 별개로 검증한다.

## 환경 확인 결과

- 기존 클러스터 재사용: Ready 10개, KubeEdge 엣지 6개·서버 4개.
- 엣지 kubelet은 v1.32.10-kubeedge-v1.23.0, 서버 kubelet은 v1.31.14.
- CloudCore 1개, EdgeMesh agent 10개, Kubeflow namespace의 Argo workflow-controller v3.5.14 관측.
- 이 조사에서 이름 기준 Koordinator deployment/CRD는 발견하지 못했다. 완전한 부재 증명은 아니다.

| 노드 | arch | 분류 | CPU | 등록 가속기 |
|---|---|---|---|---|
| etri-dev0001-jetorn | arm64 | edge | 6 | nvidia.com/gpu.shared=2 |
| etri-dev0002-raspi5 | arm64 | edge | 4 | hailo.ai/h8=1 |
| etri-dev0003-raspi5 | arm64 | edge | 4 | 없음 |
| etri-dev0004-tedger | arm64 | edge | 6 | rockchip.com/rk3399pro=1 |
| etri-dev0005-jetagx | arm64 | edge | 12 | nvidia.com/gpu.shared=2 |
| etri-dev0006-raspi5 | arm64 | edge | 4 | hailo.ai/h8=1 |
| etri-ser0001-cg0msb | amd64 | server | 24 | nvidia.com/gpu=1 |
| etri-ser0002-cgnmsb | amd64 | server | 24 | npu.intel.com/accel=1, nvidia.com/gpu=1 |
| etri-ser0003-cg0ms0 | arm64 | server | 20 | nvidia.com/gpu=1 |
| etri-ser0004-cgnms0 | amd64 | server | 16 | mobilint.com/npu=1 |

`nvidia.com/gpu.shared=2`는 resource 단위이며 물리 GPU 두 장으로 해석하지 않는다. 이 표는 Kubernetes allocatable 등록값이며 특정 모델의 성공이나 장치 성능을 의미하지 않는다.

## 첫 실행의 범위

ARM 엣지 etri-dev0003-raspi5와 amd64 서버 etri-ser0004-cgnms0에서 CPU EP만 사용하는 동일한 3단계 전처리→분류→후처리를 검증한다. 각 Pod 내부에서 순차 실행하며 cross-node DAG·전송·배치 성능은 측정하지 않는다. GPU/NPU resource를 요청하거나 기존 배포를 변경하지 않는다.

실험 namespace는 `scheduler-research`, 리소스는 run label로 격리했다. Job은 backoffLimit=0, activeDeadlineSeconds=480, CPU 최대 1개·메모리 768Mi로 제한했다.

## 관측된 첫 실행 문제

1. 공식 외부 Python 이미지가 ARM 엣지에서 ErrImagePull(connection reset)로 시작되지 못했다. 내부 클러스터에서 이미 사용하는 digest 고정 Python 이미지로 변경했다. 서명된 CDN URL은 기록에서 제외했다.
2. SqueezeNet1.0-12는 x86에서 inference를 수행했지만 공통 README 전처리와 dog fixture 조합에서 예상과 다른 top1=669를 출력했다. 모델 실행 성공을 품질 통과로 인정하지 않았다. 원인은 아직 확정하지 않았으며, 1.1-7과 class 258 smoke 검증으로 변경했다.
3. 첫 시도의 manifest·로그·상태를 보존하고 해당 두 진단 Job만 삭제했다. 수정한 시도는 별도 run ID를 사용한다.

## 근거와 실행 파일

- [원시 인벤토리](../../artifacts/e0/cluster-inventory.json)
- [현재 실행 manifest](../../artifacts/e0/manifests.json), [run 정보](../../artifacts/e0/run.json)
- [첫 시도 상태](../../artifacts/e0/attempt1-status.json), [첫 시도 manifest](../../artifacts/e0/attempt1-manifests.json)
- [E0 실행 코드](../../experiments/e0/probe.py), [주요 의존성](../../experiments/e0/requirements.txt)
- [공식 runtime·모델 근거 메모](../research/kubeedge-e0-runtime-notes.md)

## 수정 후 검증 결과

- SqueezeNet1.1-7 고정 artifact와 같은 이미지의 SHA256을 두 노드에서 확인했다.
- ONNX Runtime 1.23.2·NumPy 2.2.6·Pillow 11.3.0, Python 3.11.16, CPUExecutionProvider·intra/inter-op 각 1 thread로 실행했다. 설치된 전이 의존성 버전도 두 노드에서 같았다.
- 두 Job 모두 exit code 0. 전처리 텐서 SHA256과 top-5 순위가 같고 top-1은 class 258(Samoyed)다. 비교한 top-5 score 최대 절대 차이는 약 5.72e-6이다. 전체 출력의 수치 동등성을 검증한 것은 아니다.
- Samoyed는 고정 이미지에 대한 의미 기반 smoke 예상값이며, 이 ONNX artifact의 공식 golden tensor 검증은 아니다. 한 장의 성공을 데이터셋 정확도로 확대하지 않는다.

| 관측 항목 | x86 서버 | ARM 엣지 |
|---|---:|---:|
| T1 전처리 | 70.701 ms | 96.130 ms |
| T2 CPU 추론 | 10.995 ms | 29.964 ms |
| T3 후처리 | 0.096 ms | 0.116 ms |
| T1→T2 tensor | 602,112 bytes | 602,112 bytes |
| T2→T3 scores | 4,000 bytes | 4,000 bytes |

위 시간은 각 노드의 **단일 cold 실행 관측**이다. image pull·pip 설치·asset 다운로드·모델 loading은 단계 시간 밖이며, 전체 배포 지연·warm profile·반복 측정·p95·정책 우위를 나타내지 않는다. 다음 전송 실험에서 payload 크기를 정하는 근거로만 사용한다.

[구조화 결과와 검증 항목](../../artifacts/e0/results.json), [ARM 전체 로그](../../artifacts/e0/arm.log), [x86 전체 로그](../../artifacts/e0/amd.log), [실제 설치 버전](../../artifacts/e0/requirements-resolved.txt).

## 실행·수집·정리

실제 사용한 배포 명령은 다음과 같다. manifest에는 run별 Job 이름·immutable ConfigMap·image digest·코드가 들어 있다. 재실행 때 기존 run ID를 재사용하지 말고 새 이름과 ConfigMap으로 만든다. 원시 manifest의 최초 requirements는 주요 세 패키지만 고정하므로 재현 시 전이 의존성도 위 실제 설치 버전으로 고정한다.

```bash
rtk proxy kubectl apply --dry-run=client -f artifacts/e0/manifests.json
rtk proxy kubectl apply -f artifacts/e0/manifests.json
```

로그 수집은 `kubectl logs -n scheduler-research <pod> --request-timeout=10s`로 수행했다. ARM edge 로그 경로도 이번에는 정상 동작했다. Job·Pod·ConfigMap의 정확한 이름은 run 정보와 manifest에 있다. 두 시도의 진단 리소스만 수집 후 삭제하고 namespace는 후속 실험용으로 보존한다.

## 단계 진행 상태

- [x] 기존 KubeEdge·장치 등록 인벤토리 확인
- [x] 실제 ARM/x86 노드에서 같은 3단계 CPU fixture 실행·결과 수집
- [x] Task를 서로 다른 Pod/노드에 배치하고 실제 데이터 경로·전송량·시간 확인: [전달 파일럿·EdgeCore 관측](e0-transport.md)
- [x] 선택한 서버 GPU 1종·NPU 1종의 같은 원본 모델/Task fixture 확인: [준비 경로·오차·제한](e0-accelerators.md). 엣지 가속기·데이터셋 품질 계약은 미검증
- [x] 같은 상주 worker·paced 입력의 세 client 블록 프로파일 수집: [576요청](e0-rpc-profile.md)
- [x] 세 Task 위치를 바꾸는 64배치 보정과 새 선택 평가: [정적 파일럿](e1-dag.md)
- [ ] 독립 배포 반복·데이터셋 품질·부하/SLO를 고정한 본 프로파일
- [ ] E1 kube-scheduler·Koordinator·기존 DAG·DRL 정책 비교

이번에 관측한 image pull/입력 계약 문제를 새 DAG 알고리즘의 실패 근거로 사용하지 않는다. 먼저 배포 경로와 artifact별 전처리 검증으로 해결할 운영·실행 계층 문제다.
