# 살아 있는 노드의 GPU·Mobilint VLM 재실험 — 2026-10-28

Status: 이전 pair는 GPU 설치 중 Ready Unknown으로 중단·정리됨. 09:42Z부터 서버에서 준비한 런타임을 SD I/O에 따라 저속 전송하는 새 pair 실행 중. 아래 이전 실패와 합산하지 않는다.

## 후보 동결

사용자 요청: 복구된 장비를 포함하되 현재 살아 있는 노드만 사용한다. Ready와 해당 모델 실행 가능 여부를 구분한다. CPU 신경망 대체 경로는 사용하지 않는다.

| 노드 | 최초 Ready | 이번 역할/제외 이유 |
|---|---|---|
| etri-dev0001-jetorn | True | 보류: 기존 CUDA12/torchvision ABI 오류, 현재 Qwen cu130 recipe와 불일치 |
| etri-dev0002-raspi5 | True | Hailo8 등록; 이번 Qwen3-VL 실행 산출물 미검증 |
| etri-dev0003-raspi5 | True | CPU 노드, 이번 가속기 모델 실행 대상 아님 |
| etri-dev0004-tedger | Unknown | 제외: Unknown |
| etri-dev0006-raspi5 | True | Hailo8 등록; 이번 Qwen3-VL 실행 산출물 미검증 |
| etri-dev0007-jetagx | Unknown | 제외: Unknown |
| etri-dev0008-raspi5 | True | CPU 노드, 이번 가속기 모델 실행 대상 아님 |
| etri-dev0009-jetorn | True | CUDA 후보: GPU 슬롯 비어 있음; 실제 driver/모델 실행 재확인 |
| etri-ser0001-cg0msb | True | GPU 점유: 기존 서비스 유지 |
| etri-ser0002-cgnmsb | True | GPU 점유; Intel NPU의 기존 CPU vision 경로는 이번 후보에서 제외 |
| etri-ser0003-cg0ms0 | True | GPU 점유: 기존 서비스 유지 |
| etri-ser0004-cgnms0 | True | Mobilint ARIES 후보 및 파일 준비 서버; NPU 비어 있음 |

## 고정 조건

Qwen3-VL-2B 계열, 기존 입력 4개와 질문, greedy 최대64토큰을 유지한다. NPU는 공개 W4V8 MXQ, CUDA는 원본 BF16이므로 동일 정밀도 하드웨어 비교로 해석하지 않는다. 품질 채점·정책 goodput 비교가 아니라 현재 노드에서의 실행 검증이다.

Jetson 파일 준비는 전송 속도8MiB/s, 최대16MiB 쓰기마다 파일 flush/fsync, 지원되는 경우 해당 파일의 clean page cache 반환을 적용한다. 시스템 전체 cache 초기화·driver 변경·새 합성 디스크 부하는 수행하지 않는다. 이 수정의 실제 노드 안정성 효과는 이번 실행에서 관측해야 한다.

원본 조건 및 이전 결과: [GPU·Mobilint 검증 기록](gpu-mobilint-vlm-qualification-20261028.md). 노드·Pod 초기 snapshot과 수정 검증은 `.scratch/live-nodes-vlm-20261028/`에 저장한다.

## 코드 검증과 실행

초기 전체44개 테스트 통과. 독립 검토에서 대기 후 deadline을 넘겨도 다음 읽기를 시작할 수 있는 P2를 발견하여 수정했다. 수정된 경계·전송 테스트10개 통과, 최종 독립 SPEC/STANDARDS R2 모두 통과(각 관련4개 테스트). 수정된 bootstrap SHA256 `57accc85c18266d5297e9f910f5227ec2b2cc43eb8a596f4cb2f8961642431ff`. 기존 모델/입력 계약·공유 helper는 유지한다.

08:12Z에 `gmv-20261028t-ready-pair1` 실제 pair 실행을 시작했다. 결과는 완료 후 아래 기록하며, 소스 테스트 통과를 장치 실행 성공으로 세지 않는다.

## 현재 스토리지 확인

실행 중 사용자 질문에 따라 Kubernetes StorageClass/PV/PVC/Service를 읽기 전용으로 확인했다. PV46개 중 local5개/hostPath41개이며 ReadWriteMany0개, 등록된 NFS/CSI 공유 파일 PV는 없었다. StorageClass는 local-path, edgeai-hdd, agx-sd-static이다. agentops·edgeai·kubeflow에 MinIO Service가 존재한다(서비스 등록 확인이며 bucket·권한·용량을 검증한 것은 아님). 기존 runtime/cache PVC도 특정 노드에 묶여 있다.

현재 실험은 emptyDir를 사용하므로 준비 파일을 실행마다 다시 전송한다. 모델 저장소와 노드별 영구 캐시를 분리하고 hash 검증 후 재사용하는 개선이 필요하다. 이번 실행 중에는 마운트/소스를 바꾸지 않았다. PV 근거는 `.scratch/live-nodes-vlm-20261028/storage-pv-snapshot.json`.

## 이번 실행 결과

실행 ID: `gmv-20261028t-ready-pair1`. 아티팩트: `artifacts/gpu-mobilint-vlm/gmv-20261028t-ready-pair1/`. 시각은 UTC다.

- GPU 준비 파일 7,166,263,060 bytes 전송을 1,294.250초에 완료했다. 전송 중 수집한 관측에서는 dev0009가 Ready였으며 파일별 제한·fsync·fadvise 진행이 확인됐다. 연속 무중단 상태를 보장하는 관측은 아니다.
- 이전 실패 지점인 `pip --version`을 통과하고 08:36:01에 GPU 패키지 설치를 시작했다. 마지막 보존 로그는 `Installing collected packages`이며 설치 완료/모델 로딩/추론 성공은 없다.
- dev0009의 마지막 kubelet heartbeat는 08:41:06, Ready→Unknown 전환은 08:41:58이다. API 메시지는 `Kubelet stopped posting node status.`였다. 08:42:01 관측 시 HTTP bootstrap health는 여전히 응답했다. 노드 상태 갱신 중단과 완전한 네트워크 단절을 동일시하지 않는다.
- 08:42:17에 이 실행의 collector를 SIGINT로 중단하여 정상 finally 정리를 수행했다. 08:43:46 재확인에서 dev0009·ser0004 모두 Ready였고 실험 소유 Pod/ConfigMap은 0개였다. 강제 삭제나 장치 reset은 수행하지 않았다.
- Mobilint는 모델 로드 후 ready였지만 pair가 GPU 준비를 기다리므로 이번 실행의 추론은 GPU 0건/NPU 0건이다. 과거 NPU 4/4 결과를 이번 성공으로 합산하지 않는다. 이번 pair의 지연·처리량 비교 결과는 없다.

정리 기록은 UID 기준 4개 모두 deleted, unresolved 0, problems 0, verified true다. fresh API 조회에서도 소유 객체 0개를 확인했다. release의 코드 25개 및 동결 입력 파일 2개 hash는 실행 후에도 일치했다. 검증 요약은 `.scratch/live-nodes-vlm-20261028/pair1-verification.json`, 중단 사유는 `stop-pair1.json`, 설치 stdout/stderr tail과 노드 조건은 시각별 `monitor-*.json`에 보존했다.

보호 워크로드의 전후 snapshot에는 dev0003의 sensehat Pod 1개 Ready True→False와 dev0009의 외부 `jetson9-status-check-20261028` Pod 1개 등장이 있다. 전후 변화만으로 이번 실행의 인과적 영향을 단정할 수 없으며 '다른 워크로드 변화 없음'으로 보고하지 않는다.

## 남은 준비 경로 문제

전송 제어만으로 전체 준비 단계의 안정성을 확보하지 못했다. 설치 중 상태 갱신 중단을 관측했으나 디스크·메모리 등 단일 원인을 확정한 것은 아니다. 현재 구현은 패키지 설치 I/O를 제한하지 않는다. 같은 emptyDir 준비를 다시 반복하지 말고, GPU 의존성을 미리 설치한 호환 이미지와 hash 검증된 노드별 영구 모델 캐시를 준비한 뒤 별도 실행으로 검증해야 한다. MinIO는 원본 배포 저장소 후보이며 현재 bucket 가용성은 미검증이다. 이 기록에서는 이미지/영구 캐시가 구현되었다고 주장하지 않는다.

## 복구 후 SD카드 포화 확인

사용자가 SD카드 포화로 장애가 났다고 알렸다. 복구 후 node-exporter에는 `/dev/mmcblk0p1` 루트 파일시스템(123.8GB, 여유72.6GB)과 mmcblk0 장치가 보였고, 현재 수집된 디스크 지표에는 별도 SSD/NVMe가 나타나지 않았다. 미마운트/미등록 장치의 부재까지 단정하지 않는다.

Prometheus의 08:36:01–08:42:17Z 설치 구간(15초 간격, I/O rate는 1분 창)에서 mmcblk0 busy rate 최대 약99.1%, 파일시스템 여유 최소 약61GB, MemAvailable 최소 약6.36GB를 확인했다. 용량 부족보다 I/O 포화와 부합하는 관측이다. 이 지표만으로 kubelet 정지의 단일 원인을 확정하지 않는다. 원자료와 요약은 `.scratch/vlm-persistent-ready-20261028/dev9-install-storage-history.json`, `storage-summary.json`이다.

SD에 동일 준비를 재시도하지 않는다. 서버에서 미리 준비한 CUDA 런타임과 재사용 캐시 경로를 구현하되 실제 캐시 저장장치를 확인하기 전 엣지 배포를 보류한다. 기존 볼륨/파일을 삭제해 공간을 확보하지 않았다.

## 사용자 지시 반영: SD I/O를 보며 저속 전송

사용자는 dev0009가 SD카드만 사용하는 것을 확인했고, SD 사용을 금지하는 대신 "io 보면서 천천히 받어"라고 명시했다. 따라서 앞선 비-SD 저장장치 요구는 폐기한다. 서버에서 준비한 8.85GB 런타임 파일을 SD I/O 관측에 따라 속도를 제한하여 한 번 전달하고 영구 캐시로 재사용하는 경로로 수정한다. Jetson 내 pip 설치는 제거한다. 이 단계는 아직 실험 결과가 아니다.

전용 PV/PVC `gmv-dev9-prepared-cache-20261028`를 생성해 Bound를 확인했다. 16Gi RWO, Retain, dev0009 전용 affinity, 경로 `/var/lib/edgeai/gmv-prepared-cache-20261028`다. 호스트 경로의 16Gi는 물리 quota 보장이 아니며 실제 잔여 공간을 별도 확인한다. 기존 파일을 삭제하지 않았다. 이 볼륨은 Pod 정리 때 삭제하지 않는다. UID 기록은 `.scratch/vlm-persistent-ready-20261028/persistent-resource-records.json`이다.

## 준비 런타임·SD 적응형 쓰기 검증과 새 실행

새 실행 `gmv-20261028t-sd-io-pair1`을 09:42Z에 시작했다. 서버에서 기존 동결 wheel로 준비한 19,003개 파일(8,853,614,095 bytes)을 스트리밍 복사한다. Jetson에서는 pip 설치를 하지 않는다. 준비 tar SHA256은 `9659682ec8875c62fa3d08397b6315f20d9f02b0ba15a631d9f011a57d5d2e31`이며 원 모델·입력 계약은 유지한다. 완료 후 전체 파일 hash/모드 검증을 통과한 캐시만 원자적으로 공개하고 다음 Pod에서 재사용한다. 로컬 실제 tar fill/hit 검증은 통과했지만 Jetson ABI·모델 실행 증거는 아니다.

SD 프로파일은 시작 1MiB/s, 최대 4MiB/s, 최소 256KiB/s다. 전체 mmcblk0 지표를 1초 간격으로 확인하며 busy 70% 이상 또는 단일 fsync 1초 이상이면 감속, busy 85% 이상이면 정지, 50% 미만 3초 유지 후 재개한다. 미동기화 쓰기는 최대 1MiB, chunk는 최대 256KiB다. 지표 소실/역행은 실패로 종료하며, 진행 없음 300초·준비 전체 10,800초 제한을 적용한다. 개별 fsync 전후와 중단 잔여 파일의 개별 unlink/rmdir에도 같은 제어를 적용한다. 디렉터리 열거와 공개 후 마지막 부모 디렉터리 fsync는 관측 제어 밖에 있고, 실행 중인 커널 호출을 강제 중단하지 않는다.

Claude Opus 5.5 공식 구독 경로로 구현했고 실제 transcript 모델을 확인했다. 준비/SD 테스트 54개와 앞서 관련 회귀 테스트 21개가 통과했다. 독립 Astra SPEC/STANDARDS 검토에서 발견한 두 P1을 수정했고, 2차 검토 모두 통과했다. 조정자도 새 경계 회귀 6개를 직접 실행해 통과했다. 구현 worker의 일부 초기 읽기 명령이 rtk 래퍼를 우회한 사실은 보고서에 남겼고 이후 준수를 요청했다. 현재 실행 release는 새 runtime hash `33c019700b08d6c501e336429cd53ecbd057d2e4ef4f3778b0a1c4cb2d669eb2`와 프로파일·PVC UID·시간 제한을 모두 묶는다.

Pod 수명은 SD 준비 대기 때문에 두 가속기 모두 15,300초, 서버 cache 17,400초다. Mobilint가 GPU 준비를 기다리는 예약 시간은 준비 비용이며 추론 성능 이득으로 해석하지 않는다. 실험 자료는 `artifacts/gpu-mobilint-vlm/gmv-20261028t-sd-io-pair1/`, 별도 I/O/health 관측과 검토 기록은 `.scratch/vlm-persistent-ready-20261028/`에 보존한다.

### 사용자 지시로 Mobilint 해제 (10:27Z)

사용자가 준비된 Mobilint를 내려 달라고 요청하여 `gmv-20261028t-sd-io-pair1-mobilint` UID `110efe91-519e-4b8c-a5b2-5d20671a7e59`에 UID precondition 삭제를 적용했다. fresh API에서 Pod 부재와 ser0004의 Mobilint 요청 점유 0을 확인했다. GPU 다운로드(당시 5.89GB)와 서버 cache는 유지했다. 이 실행의 pair qualification은 사용자 지시로 취소되며 GPU·NPU 추론 비교 결과로 보고하지 않는다.

사용자는 속도 변경에 재시작이 필요한 것을 확인한 뒤 기존 4MiB/s 상한 유지를 선택했다. 로컬 pair 수집기는 두 장비 검증을 계속 시도하지 않도록 일시정지했고, GPU 캐시 준비 결과를 보존한 뒤 기존 finally의 UID 정리를 실행한다. 파일을 받는 GPU Pod는 독립적으로 계속 실행 중이다. 증거는 `user-stop-mobilint*.json`, `pair-coordinator-paused.json`에 저장했다.


### SD 캐시 및 GPU 로딩 완료 (11:01Z 관측)

`gmv-20261028t-sd-io-pair1`의 cache_fill은 8,853,614,095 bytes / 19,003 files를 4,393.644초에 검증하고 원자적으로 게시했다. 4MiB/s 상한 유지, pause 17회/56.87초, slowdown 58회였다. cache-phase 외부 snapshot 73개 모두 dev0009 Ready=True였으며 연속 가용성을 보증하지는 않는다.

GPU 로딩 health에서 Orin CUDA 13.0 / torch2.9.1+cu130, BF16 파라미터 2,127,532,032개와 버퍼가 모두 cuda:0에 있음을 확인했다. child load wall time 194.217초, CUDA memory allocated 4,256,128,000 bytes. 이 준비 실행의 추론은 0회다. 로딩 읽기 I/O는 다운로드 쓰기 제어 범위 밖이다.

GPU 완료 health를 보존한 뒤 일시정지했던 정확한 collector PID/start identity를 확인하고 SIGINT+SIGCONT로 기존 finally를 실행했다. exit130(의도한 중단), 소유 Pod/ConfigMap 모두 UID 정리/부재 verified, unresolved0이며 PVC는 동일 UID로 Bound 상태를 유지했다. 준비 완료 근거와 SD 그래프는 [슬라이드용 자료](../presentations/vlm-runtime-20261028/README.md)에 보존했다.

사용자의 후속 “실험까지” 및 Git 푸시 지시에 따라 별도 `gmv-20261028t-bench1`을 시작했다. 이전 준비 실행의 0회 결과나 과거 NPU 단독4회를 새 비교에 합치지 않는다. [사전 반복 측정 계획](vlm-repeat-measurement-plan-20261028.md)을 따르며, 최종 결과는 위 슬라이드용 자료에 기록한다.

최종: `gmv-20261028t-bench1` qualification8/8 + 본 측정80/80 성공, GPU/NPU 각40회, 실패·중단·미실행0. GPU p50/p95 26.208/27.460초, Mobilint0.367/0.472초. cache hit transfer0, GPU 검증415.466초·model load182.796초. exit0, UID 정리 verified, fresh API 잔여0, PVC retained. 원시 결과·해석 제한·슬라이드는 위 자료 링크를 따른다.
