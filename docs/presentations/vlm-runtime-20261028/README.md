# Qwen3-VL 실장치 반복 실측 · 2026-10-28

완료: `gmv-20261028t-bench1`에서 GPU·Mobilint 각 40회, 총 **80/80 성공**. 별도 qualification 8/8 성공. 실패·중단·미실행 0, 실행 exit0, 소유 Pod/ConfigMap 정리 및 fresh API 부재 확인 완료.


## 반복 실측 결과

| 실행 구성 | 측정 수 | 요청 p50 | 요청 p95 | 생성 TTFT p50 | host 평균 core 상당 |
|---|---:|---:|---:|---:|---:|
| Jetson Orin / BF16 | 40 | 26.208s | 27.460s | 25.870s | 0.985 |
| Mobilint ARIES / W4V8 | 40 | 0.367s | 0.472s | 0.263s | 1.497 |

본 측정 11:25:00.970–11:42:50.961Z, 17.83분. 캐시 서버 준비·양쪽 모델 준비·qualification을 포함한 수집 시작부터 최종 검증까지 34.83분(정리 시간 별도). 준비/qualification 구간 16.98분을 위 추론 지연에서 제외했다. 모든 시간은 UTC, 한국 시각은 +9시간이다.

양쪽 모두 출력은 2–4토큰(EOS 포함)이었다. GPU는 첫 토큰까지의 시간이 대부분을 차지했다. 이후 추가 조회한 driver GPU load 72개 표본의 중앙값은 28.1%, 최대35.5%였으나 kernel 시간·클럭·전력 상태를 측정한 것은 아니다. 현재 CUDA 경로가 느린 원인은 아직 분리하지 못했다. 이 값을 일반적인 Jetson 또는 GPU 성능 한계로 일반화하거나 NPU의 순수 하드웨어 우월성으로 해석하지 않는다.

호스트 CPU 평균 core 상당값은 각 요청의 전처리+생성 CPU시간/해당 벽시계 비율을 평균한 값이다. CPU runtime 제어·조회·busy wait 등의 기여는 분리되지 않았다. 신경망 CPU fallback 근거는 없으며, CUDA 전체 파라미터/버퍼와 출력 장치, ARIES vision/text MXQ backend를 확인했다. NPU 내부 aggregate 실행시간은 SDK 경로상 측정 불가로 남겼다.

CUDA 캐시 hit는 **transfers=0**, 전체 검증 **415.466초**, model process load **182.796초**였다. 파일 캐시가 있어도 두 준비 단계 합계가 **9.97분**이었다. 이는 같은 device/OS cache까지 독립인 반복 실험이 아닌 두 별도 실행의 관측이다. 수집기 서버의 upload 후 local writeback 대기 또한 cache phase에 포함되어 있고, 장치 추론 시간에 합치지 않았다.

각 arm의 sequence가 5→44로 연속 증가하고 최종 queue는 active=waiting=0, completed=44였다. 80개 입력/출력/모델/contract/UID/device receipt와 실행 소스 해시를 검증했다. 외부 Pod 전후 snapshot은 추가10·제거1·상태변경0이며 변화 원인이나 실행 중 무영향을 단정하지 않는다. 사용한 GPU/NPU 자원은 정리했고 PVC는 동일 UID의 Bound 상태로 보존했다.

## 슬라이드에 바로 넣을 자료

- [지연 비교 PNG](vlm-latency.png) · [SVG](vlm-latency.svg): 전체 요청 점·입력별 중앙값·p5–p95.
- [준비 상태 비용 PNG](readiness-cost.png) · [SVG](readiness-cost.svg): 초기 fill과 cache hit의 실제 준비 단계.
- [SD 준비 trace PNG](sd-preparation.png) · [SVG](sd-preparation.svg): 쓰기 진행·상한·busy 관측.
- [발표 문구](SLIDES.md), [요약 CSV](summary.csv), [입력별 CSV](by-input.csv), [80개 관측 CSV](measurements.csv).
- [재현 명령과 지표 정의](reproduce.md). 원시 ledger·release·해시·리뷰·cleanup은 evidence/에 보존했다.

## 실험 범위

- GPU: dev0009 Jetson Orin, Qwen3-VL-2B-Instruct BF16, torch 2.9.1+cu130. 준비 실행에서 파라미터 2,127,532,032개와 버퍼가 모두 cuda:0임을 확인했다. 할당된 모델 메모리 4,256,128,000 bytes.
- NPU: ser0004 Mobilint ARIES, 같은 계열 모델의 vendor W4V8 MXQ. vision/text MXQ 실행과 host 처리 경계를 receipt로 검증한다.
- 고정 입력 4개, 장치별 qualification 4회 후 입력당 10회씩 총 80회. 동일 입력 pair 안의 장치 순서를 seed 20261028로 배정했다. plan 파일의 SHA-256은 e7fc2487a50aab0f5dcc00966783965fc93980e8fc315cad15d946507ddafafc.
- 요청은 이 실행의 coordinator 수집기에서 worker Pod로 전송한다. 엣지 센서 source의 실제 DAG 본 비교가 아닌 runtime 진단이다.
- 한 Pod 안의 반복 관측이다. 독립 장치·데이터셋 표본, 검정력·유의성, 품질 동등성, 포화 처리량, SLO/goodput 또는 배치 정책 우월성을 주장하지 않는다.
- GPU BF16과 NPU W4V8은 정밀도·호스트·runtime·통신 경로가 다르다. 결과는 이 두 실행 구성의 예비 실측이다.
- CPU는 전처리·토큰화 및 vendor가 요구하는 host embedding/lookup 등에 사용된다. GPU/NPU 신경망 실행을 CPU fallback으로 대체하지 않는다. host CPU 시간과 벽시계 시간은 별개다.

## SD 준비 실측

`gmv-20261028t-sd-io-pair1`에서 준비 runtime과 모델 8,853,614,095 bytes / 19,003 files를 검증하여 persistent cache에 게시했다. cache_fill 4,393.644초(73.23분), 자동 pause 17회/56.87초, slowdown 58회, 단일 fsync 최대 3.705초였다. 기존 4MiB/s 상한을 유지했다.

준비 중 수집한 73개 cache-phase snapshot에서 dev0009 Ready=True를 확인했다. 연속 가용성 보장은 아니다. 그림의 busy는 외부 snapshot 시점의 최근 내부 diskstats 값이며 전체 1초 trace가 아니다. 모델 로딩의 읽기 I/O는 다운로드 제어 대상 밖이다.

해시 검증 후 별도 GPU child 로딩 wall time은 194.217초였다(내부 가중치 hash 50.379초, 모델 load 106.777초 포함). 준비 실행 추론 호출은 0회이며 사용자의 NPU 해제 요청 후 의도적으로 중단했다. 소유 Pod/ConfigMap은 UID 기반으로 정리 완료, 캐시 PVC는 보존했다. 이 준비 시간은 반복 추론 지연 통계와 합치지 않는다.

이전 pip 설치 실행의 SD busy 중앙값 97.48%, 최대 99.05%와 Ready 상실은 별도 관측이다. 설치 경로와 시점이 달라 제어 방법의 인과 효과나 성능 개선율로 해석하지 않는다.

## 파일 및 방법

- `benchmark-plan.json`: 사전 고정 실행 순서.
- `sd-fill-trace.csv`: 준비 중 관측 trace.
- `evidence/`: 준비 완료 health, release/hash, cleanup 및 기존 설치 I/O 요약.
- `statistics.json`, `summary.csv`, `by-input.csv`, `measurements.csv`: 원시 ledger에서 재계산한 결과.

설계 도구 출처: Kassis et al. (2026), *Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents*, https://doi.org/10.48550/arXiv.2609.00065. experimental-design randomization helper와 Matplotlib 기반 그림 제작 절차를 사용했다. 이는 실측 성능 근거가 아니다.
