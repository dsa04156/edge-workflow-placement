# 대체 GPU 실행·전송 비용 준비 결과 — 2026-10-29

9번 Jetson을 제외하고 서버 3번 NVIDIA GB10에서 Qwen3-VL-2B-Instruct BF16의 실제 CUDA 실행을 확인했다. Mobilint는 별도 `ra-hetero-kd-teacher-20261029` 파드가 점유해 실행하지 않았다. 이 결과는 **이기종 자원 성능·전송 비용 인지형 DAG 배치 실증을 위한 준비 결과**이며, Resource Advisor나 배치 정책 비교 결과가 아니다.

## GPU 단일 요청

| 항목 | 결과 |
|---|---:|
| 측정 요청 수 | 1 |
| 수집기에서 본 요청 완료시간 | 3481.997 ms |
| Worker 처리 구간 | 3471.321 ms |
| 전처리 | 23.613 ms |
| 모델 생성 구간 | 3436.079 ms |
| 첫 토큰 시간 | 3355.803 ms |
| Worker 큐 대기 | 0.007 ms |
| 모델 로딩 구간 | 28.988 s |
| 전체 준비·실행·정리 | 474.0 s |

첫 추론 요청이며 별도 워밍업·반복은 없다. 입력은 `smoke-1713820`(426×640 RGB NPY), 출력은 `skateboard`. 가중치와 버퍼, 생성 출력이 `cuda:0`임을 확인했다. CPU 전처리·토큰화 등 호스트 처리는 포함되며, 생성 구간은 순수 GPU 커널 활성시간이 아니다. 품질 평가·정밀도 동등성은 검증하지 않았다.

모델은 revision `89644892e4d85e24eaac8bacfd4f463576704203`, 가중치 SHA256 `7de1838c87a5349b016c26a1c3f7d2bc400a3d485f95ef39a7059ffd734977a0`, PyTorch `2.9.1+cu130`, CUDA driver API `13000`, GB10 compute capability `12.1`이다. 새 인터넷 다운로드 없이 보유 모델·휠을 사용했다. 서버 2번 캐시→서버 3번의 준비 파일 7,166,263,060 bytes 전달은 218.163초였다(32 MiB/s 제한 및 fsync 포함). 이 설치·전달시간을 추론 입력 전송시간이나 장치 성능 차이로 해석하지 않는다.

## 엣지→GPU 호스트의 전송 측정

고정 엣지 source `etri-dev0003-raspi5`에서 GPU 호스트 `etri-ser0003-cg0ms0`로 전달했다. 수신 Pod에서 HTTP GET 전체를 monotonic clock으로 측정했고, 크기별 **1회** 실행했다. GPU 추론 파드 정리 후 수행했다.

| 페이로드 | HTTP 전달시간 |
|---|---:|
| 64 bytes 제어 크기 | 4.831 ms |
| 818,048 bytes (NPY 크기) | 10.180 ms |
| 593,966 bytes (계획된 PNG 크기) | 9.232 ms |

크기가 같은 **0으로 채운 probe 데이터**다. 593,966 bytes는 후속 DAG 입력 `cal-3733440`의 PNG 크기이며, 이번 GPU smoke 입력과 구분한다. 응답 bytes·SHA256·발신/수신 Pod UID와 노드를 확인했다. HTTP 및 서버 처리 비용을 포함하므로 순수 링크 RTT·대역폭·실제 DAG E2E로 부르지 않는다. 대역폭 제한·공유 링크 경합 실험은 하지 않았다. GPU 요청 완료시간은 로컬 수집기 경로의 측정이므로 이 별도 전송시간과 단순 합산하지 않는다.

## 실행 구성과 증거

기존 `experiments/gpu-mobilint-vlm/live.py`를 별도 staged 디렉터리에 복사해 배포 설정만 바꿨다. [설정 차이](spark-configuration.patch)는 GPU 노드·자원·메모리, 캐시 서버, 32 MiB/s 준비 전송 제한, 기존 `nvidia-spark` RuntimeClass를 기록한다. 기존 실험 소스·9번 PVC·동결 자료는 변경하지 않았다. RuntimeClass 없는 첫 장치 확인은 libcuda 미노출로 실패했고, 수정 후 `cuInit=0`, GB10 접근을 확인했다. 두 장치 확인은 추론 측정이 아니다.

실행 명령과 시각은 [launch](evidence/launch.json), [exit](evidence/exit.json), 코드 hash는 [release](evidence/gpu-release.json), 원시 요청은 [ledger](evidence/gpu-ledger.jsonl), 요약은 [summary](summary.json)에 있다. GPU와 네트워크 실험의 소유 객체를 UID로 삭제하고 부재를 확인했다.

다음 단계는 Mobilint 사용 가능 확인 후 새 하드웨어 조건으로 비용표와 DAG 계획을 동결하고, 같은 배치는 관측을 공유하며 배치별 1회 비교하는 것이다. 기존 Jetson 비용표·release를 재사용하지 않는다. 현재 결과로 정책 우월성, p95, goodput 또는 통계적 유의성을 주장하지 않는다.
