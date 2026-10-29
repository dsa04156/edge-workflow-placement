# GPU·Mobilint 실제 실행 진단 — 2026-10-29

`gmv-diag-20261029t0749`: **8/8 성공**, GPU와 Mobilint 각 probe 1회 + qualification 3회. 실패·미실행 0, exit 0. 07:49:47–08:06:28 UTC(16:49:47–17:06:28 KST), 총 16분 40초. 소유 Pod 3개와 ConfigMap 1개 UID 정리 완료, 새 API 조회에서도 잔여 0. CUDA 영구 캐시는 보존했다.

CPU 추론 arm은 없다. GPU는 `etri-dev0009-jetorn` Jetson Orin / Qwen3-VL-2B BF16, NPU는 `etri-ser0004-cgnms0` Mobilint ARIES / 같은 계열 W4V8 MXQ다. CPU는 전처리·embedding 조회·런타임 제어 등에 사용되며 신경망 CPU fallback으로 대체하지 않았다.

## 결과

| 입력 | GPU 요청(ms) | Mobilint 요청(ms) | GPU 첫 토큰(ms) | GPU vision host 구간(ms) | GPU LM prefill host 구간(ms) |
|---|---:|---:|---:|---:|---:|
| smoke-1713820 (초기 probe) | 27,501.9 | 488.2 | 25,352.2 | 22,475.2 | 897.8 |
| cal-3733440 | 18,939.1 | 354.5 | 18,666.2 | 18,473.9 | 143.3 |
| cal-4556100 | 18,623.4 | 354.8 | 18,411.2 | 18,224.4 | 137.1 |
| cal-3355890 | 19,334.9 | 382.4 | 19,115.7 | 18,924.7 | 141.4 |

초기 probe를 제외한 GPU 3건에서 이미지 인코더 호출의 host 벽시계 구간이 첫 토큰 대기시간의 **98.97–99.00%**였다. 전체 모델 호출만 재던 기존 기록에서 병목 구간을 vision encoder로 좁혔다. Mobilint qualification 요청은 354.5–382.4ms였다. 각 입력에서 두 arm의 processor hash와 출력 token ids가 일치했다. 이 일치는 정답 품질 평가가 아니다.

[전체 측정 CSV](measurements.csv), [검증 결과](evidence/verification.json), [원시 ledger](evidence/ledger.jsonl)를 함께 보존했다. 데이터는 서로 다른 입력 4개의 단일 Pod 실행이며 독립 반복 블록·품질 적격 goodput·배치 정책 비교가 아니다. GPU/NPU의 정밀도·호스트·런타임이 다르므로 순수 하드웨어 우열로 일반화하지 않는다.

## 분리한 것과 남은 것

- CUDA vision/LM prefill/lm_head에 forward hook과 CUDA event를 붙였다. 원본 Engine.generate·모델·입력·생성 계약은 유지했다. NPU는 vendor 모델 구조를 가정하지 않고 환경 snapshot만 추가했다.
- CUDA event 값은 stream 위 두 event 사이 시간이며 kernel-active 시간이 아니다. host/thread/process 시간과 더하지 않는다. vision host 구간에는 CPU 측 처리·호출·대기가 포함될 수 있다. **병목 위치 확인까지이며 내부 원인은 미확정**이다.
- CUDA 4건에서 cgroup CPU throttling 누적 증가량은 0이었다. Major page fault 증가량은 초기 probe 198, 이후 4/0/0이었다. 뒤의 두 요청도 vision 구간이 약 18초였다. 이 관측만으로 메모리·I/O 영향을 모두 배제하지 않는다.
- GPU 클럭은 요청 전후 snapshot만 있다. 실행 중 클럭·전력·kernel 프로파일은 확보하지 않았다. PSI 파일은 unavailable로 기록됐다.
- 이번 qualification의 18–19초를 기존 반복 측정의 약 26초 대비 개선율로 해석하지 않는다. 최적화 변경이나 동일 조건의 독립 A/B 실험을 하지 않았다.
- source는 coordinator다. 실제 센서 획득, 엣지 source DAG, 공유 네트워크 경합, 부하·SLO 및 정책 선택은 이 실행의 검증 범위가 아니다. **GPU·Mobilint 다중 Task 배치 본 비교는 아직 수행하지 않았다.** 6번 슬라이드의 진단 보조 자료이며 Resource Advisor 9번/JCT 근거가 아니다.

## 준비·검증

CUDA cache hit는 전송 0회, 파일 검증 414.729초, CUDA child load 185.158초였다. 모델 다운로드를 반복하지 않았다. 기존 `sd-adaptive-v1` 설정을 유지했다. 서버 준비용 bundle 전송과 Jetson 모델 다운로드를 구분한다.

[출력 검증](evidence/verification.json)에서 8개 응답의 run/UID/node/device/model/contract/input/output hash, arm별 sequence 1–4, 최종 queue idle/completed=4를 재확인했다. CUDA diagnostic 오류·누락·행 버림 0, 각 vision 호출 1회 및 LM 호출 수와 토큰 수 일치를 확인했다. [cleanup](evidence/cleanup.json)은 verified=true다.

코드는 기존 파일을 수정하지 않고 네 파일을 추가했다: `experiments/gpu-mobilint-vlm/{diag_live.py,vlm_diag_stage.py,ttft_probe.py,test_live_diagnostic.py}`. 실제 장치 실행 전에 로컬 테스트 10개, 독립 [Spec](evidence/review-spec-r1.md)·[Standards](evidence/review-standards-r1.md) R1 리뷰가 통과했다. coordinator도 10개 테스트를 재실행했다. 로컬 tiny-model 테스트는 실제 GPU/NPU 실행 근거와 구분한다.

## 재현

실제 명령 argv와 시작 시각은 [launch](evidence/live1-launch.json), 종료는 [exit](evidence/live1-exit.json), 해시 바인딩은 [release](evidence/live-diagnostic-release.json)에 있다. 실행 시 고유 run-id와 새 release 검증을 사용한다. 모델·prepared tar·PVC binding은 로컬 운영 자산이며 Git에 포함하지 않는다.

```bash
rtk proxy timeout -s INT 1800 python3 -B -u experiments/gpu-mobilint-vlm/diag_live.py \
  --frozen .scratch/gpu-mobilint-vlm-20261028/developer-frozen-r1 \
  --prepared .scratch/vlm-persistent-ready-20261028/prepared-r1 \
  --cuda-cache-pvc .scratch/vlm-persistent-ready-20261028/cuda-cache-pvc-sd.json \
  --io-profile sd-adaptive-v1 --runs artifacts/gpu-mobilint-vlm \
  --release .scratch/gpu-mobilint-placement-20261029/live-diagnostic-release.json \
  --stage qualification --run-id <new-unique-run-id>
```

API 경계는 배포 버전의 [PyTorch 2.9 CUDA Event](https://docs.pytorch.org/docs/2.9/generated/torch.cuda.Event.html)·[Module hooks](https://docs.pytorch.org/docs/2.9/generated/torch.nn.Module.html) 및 로컬 고정 transformers 4.57.1 소스로 확인했다. Context7은 quota 초과였고 GitHub MCP와 공식 문서를 함께 확인했다.
