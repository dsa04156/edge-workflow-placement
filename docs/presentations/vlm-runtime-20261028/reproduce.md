# 재현과 검증

## 보존된 결과 다시 분석

이 폴더의 evidence/benchmark-ledger.jsonl, benchmark-summary.json, benchmark-plan.json을 사용한다. 준비 비용 그림은 sd-fill-trace.csv와 preparation-summary.json에서 재생성한다.

```bash
rtk proxy .scratch/vlm-progress-20261028/export-venv/bin/python docs/presentations/vlm-runtime-20261028/analyze_benchmark.py
rtk proxy .scratch/vlm-progress-20261028/export-venv/bin/python docs/presentations/vlm-runtime-20261028/plot_preparation.py
```

실제로 사용한 로컬 환경: Python3.11.15, NumPy2.2.6, pandas2.3.3 및 Matplotlib3.11.2. 위 venv는 Git에 포함하지 않으므로 새 checkout에서는 해당 Python 분석 의존성을 별도 준비한다. 코드는 파일 위치 기준으로 evidence를 읽는다.

## 실제 장치 실행 명령

다음은 이번 실행에서 사용한 명령이다. `.scratch`의 모델·wheel·준비 tar와 PVC binding은 운영 자원이며 Git에 포함하지 않는다. Git의 frozen contract/assets manifest와 release는 원본 revision 및 모든 가중치·의존성·준비 content·collector·계획 hash를 보존한다. 새 장치나 PVC에는 UID와 backing device를 직접 검증하고 새 release를 만들어야 하며 과거 release를 무조건 재사용하지 않는다.

```bash
rtk proxy python3 -B -u experiments/gpu-mobilint-vlm/benchmark_live.py \
  --frozen .scratch/gpu-mobilint-vlm-20261028/developer-frozen-r1 \
  --benchmark-plan .scratch/vlm-slide-benchmark-20261028/plan.json \
  --prepared .scratch/vlm-persistent-ready-20261028/prepared-r1 \
  --cuda-cache-pvc .scratch/vlm-persistent-ready-20261028/cuda-cache-pvc-sd.json \
  --io-profile sd-adaptive-v1 \
  --runs artifacts/gpu-mobilint-vlm \
  --release .scratch/vlm-slide-benchmark-20261028/release.json \
  --run-id gmv-20261028t-bench1
```

새 실행은 고유 run-id를 사용한다. release 생성은 위 입력으로 `--print-release-draft`를 사용하며 source/plan/준비 artifact/PVC/SD profile에 결합된다. CUDA cache hit도 전체 파일 해시 검증을 수행하며 transfer=0 확인과 새 모델/device receipt 검증이 필요하다.

## 검증 경계

- 준비 runtime/SD 관련 테스트54개, 기존 관련21개 통과; SD 수정 경계6개 coordinator 재검증. 증거는 기존 실험 문서에 기록했다.
- 새 반복 수집기: 12개 mock 테스트 통과. 두 independent Astra 리뷰에서 같은 P2 요청 중단 집계 결함을 발견하여 Claude Opus5.5가 수정, 두 R2 리뷰 PASS. 보고서와 최종 테스트 로그를 evidence/에 보존했다.
- 중단 기록 자체가 다시 중단되거나 부분 파일 write가 남는 경계는 검증하지 않았다. SIGKILL/호스트 장애까지 내구성을 보장하지 않는다.
- 실제 반복 실행의 완료/정리는 benchmark-summary.json 및 cleanup.json으로 확인한다. mock 통과만으로 실제 장치 성공을 주장하지 않는다.

`evidence/prepared-manifest.json.gz`는 가중치가 아닌 파일 목록·hash·mode 메타데이터다. 압축을 풀었을 때 SHA-256은 `d8d0bcaed84afc0a1a1882310d14b0e6056bb0ff7dba9f104355336031899e8d`이다. 모델·wheel·8.87GB tar는 포함하지 않는다.

## 지연 지표의 경계

- collector_wall_ms: 수집기의 HTTP body 직렬화·전송부터 응답 JSON을 읽을 때까지의 요청 벽시계 시간(receipt/output hash 검증 이전에 타이머를 끝냄). 이 실행의 수집기→각 worker 데이터 경로를 포함한다.
- worker_wall_ms: worker가 단계 함수를 실행한 시간. 상위 collector 시간에 더해 합산하지 않는다.
- TTFT: generate 시작→첫 생성 토큰 streamer 시각. 전처리와 RPC는 제외한다.
- host_cpu_ms: 모델 자식 프로세스의 전처리+생성 CPU 사용시간 합. 다중 스레드가 있으면 벽시계보다 클 수 있고, GPU/NPU 연산 시간과 더하지 않는다. host 평균 core 상당값은 해당 CPU시간/동일 구간 벽시계이다.
- serial_service_rate: 장치별 성공 횟수 / 해당 장치 collector 요청시간 합. 장치를 번갈아 호출하면서 기다린 시간과 준비시간을 제외하며, 실제 offered-load 처리량이나 goodput이 아니다. 전체 측정 구간 wall time도 별도로 보존한다.
- p50/p95: NumPy linear quantile, 이 Pod 안의 관측 분포. 독립 표본에 대한 신뢰구간·유의성 검정이 아니다.
