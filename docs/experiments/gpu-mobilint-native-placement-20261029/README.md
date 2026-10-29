# 이기종 자원 성능·전송 비용 인지형 배치 실증 — 단기 파일럿

2026-10-29 21:26:04–21:34:36 KST, `gmp-spark-20261029t122604`. **4개 고유 배치를 각 1회 실행했고 모두 성공했다.** Network Greedy와 DAG는 같은 GNN 배치를 선택했다. 측정된 E2E는 401.617 ms로 GPU 서버 고정 2391.209 ms, Mobilint 서버 고정 447.558 ms, Task-fastest 408.942 ms보다 낮았다. DAG만의 추가 이득은 확인되지 않았다.

| 정책 | 배치 T1→T2→T3 | 예측 비용 (ms) | 실제 E2E (ms) | 본 측정 수 |
|---|---|---:|---:|---:|
| GPU 서버 고정 | GGG | 2397.373 | 2391.209 | 1 |
| Mobilint 서버 고정 | NNN | 463.850 | 447.558 | 1 |
| Task-fastest | GNG | 417.713 | 408.942 | 1 |
| Network Greedy / DAG | GNN | 417.580 | 401.617 | 1 |


G는 서버 3번 NVIDIA GB10, N은 서버 4번 Mobilint ARIES의 **호스트 위치**다. T1은 호스트 CPU의 무손실 PNG 패킹, T2는 GPU 또는 NPU VLM 추론, T3는 호스트 CPU의 결과 포맷팅이다. GGG가 모든 단계를 GPU 커널로 실행한다는 뜻은 아니다. CPU 전용 추론 후보와 dev0009는 제외했다. 입력은 dev0003 Raspberry Pi Pod에서 발생시킨 고정 이미지 재생이며 실제 센서 스트림은 아니다.

## 관측값의 해석

- GNN의 관측 지연은 GPU 고정 대비 83.20%, NPU 고정 대비 10.26%, Task-fastest 대비 1.79% 낮았다. 모두 1회 관측 간 차이이며 평균 개선률이나 통계적 우월성으로 일반화하지 않는다.
- Task-fastest는 GNG, 통신 인지 Greedy와 8개 배치 정확 열거 DAG는 GNN을 선택했다. T3를 N에 남기는 결정은 달랐으나, 예측 차이는 0.133 ms에 불과했다. 실제 7.325 ms 차이 전체를 전송 절감 효과로 인과 해석할 수 없다. 실제 T1·T2 시간도 실행 사이에 달랐다.
- GPU BF16과 NPU W4V8은 같은 Qwen3-VL-2B 계열의 서로 다른 정밀도·런타임이다. GPU 고정 대비 큰 차이에는 실행 설정 차이가 포함된다. NPU 고정과 GNN은 T2에 동일한 NPU 변형을 사용했다.
- 원시 ledger와 identity에서 CUDA 가중치/버퍼/출력 장치 및 ARIES vision/text MXQ 실행 경로를 검증했다. Mobilint 런타임에는 호스트 embedding/position 처리도 남아 있다. NPU 전체 모델이 CPU 연산 없이 실행된다는 주장은 하지 않는다.
- 4개 요청 모두 5초 진단 SLO를 충족했고 실패·미완료는 없었다. 모두 출력 문자열은 `no`였다. 정답 품질, 동일 정밀도 동등성, 포화 처리량은 평가하지 않았다.

## 사전 실측 비용표

| 단계 | G 호스트 (ms) | N 호스트 (ms) |
|---|---:|---:|
| T1 이미지 패킹 — host CPU | 37.874 | 87.704 |
| T2 VLM — GPU/NPU 및 호스트 처리 포함 | 2342.315 | 354.419 |
| T3 결과 포맷 — host CPU | 0.039 | 0.084 |

입력 `cal-3733440`: RGB NPY 818,048 bytes → lossless PNG 593,966 bytes. 비용표 구성 전에 모델 워밍업을 수행했다. 사전 점검은 8개 무손실 roundtrip + 8개 warmup + 8개 profile 항목이며, T3는 두 VLM 출력 형식을 각각 점검한다. 별도 GNG 경로 검증 1회가 있다. 이 사전 점검을 본 평가 표본에 합치지 않는다.

| HTTP probe 경로/크기 | 시간 (ms) |
|---|---:|
| 엣지 S → G, 818,048 bytes | 13.126 |
| 엣지 S → N, 818,048 bytes | 14.295 |
| G → N, 593,966 bytes | 9.594 |
| N → G, 593,966 bytes | 9.153 |

전체 비용표와 probe는 [plans](evidence/plans.json), [probes](evidence/probes-native.json)에 있다. HTTP probe 시간에는 서버 처리와 응답이 포함된다. 물리 링크 RTT나 순수 네트워크 시간으로 해석하지 않는다. 네이티브 Pod-IP 직접 경로만 사용했고 공유 병목·대역폭 제한·동시 부하는 실험하지 않았다.

## 측정 경계와 재현

E2E는 엣지 source의 monotonic clock에서 예약 시각부터 최종 응답 완료까지 측정했다. 정책은 동일한 새 비용표를 사용했고, 본 평가 이전에 계획을 동결했다. GNG → NNN → GNN → GGG 순으로 각 1회 실행했다. Greedy/DAG의 GNN 관측은 공유하며 5개 독립 표본으로 세지 않는다. 예측값은 T1–T3 처리 및 source/단계 간 probe 비용 합으로, 최종 반환·모든 실행기 overhead를 완전히 모델링한 JCT가 아니다. 중첩 RPC 시간을 합산하지 않는다.

본 결과는 warm 고정 배치 진단이다. Cold 배포·모델 준비 비용은 요청 E2E에서 제외되며 원시 준비 이벤트에 별도로 보존했다. 앞선 cold GPU smoke와의 차이를 배치 최적화 이득으로 계산하지 않는다. 반복 없는 표본이므로 SD·CI·유의성·p95 성능·goodput 주장은 없다. 기존 analyzer의 p50/p95 필드는 n=1 동일 관측값이며 꼬리 지연 증거가 아니다. 이 실험은 DAG 배치이며 Resource Advisor JCT 정책 실험과 구분한다.

- [원시 요청 CSV](analysis/requests.csv), [단계 CSV](analysis/stages.csv), [정책 CSV](analysis/policies.csv), [간결 요약](summary.json)
- [전체 원시 실행 기록](raw-run.tar.gz): phase start/end, 명령 stdout/stderr, 검증·실패한 readiness wait까지 보존한 331개 파일
- [동결 실행 소스](source-snapshot.tar.gz), [소스 manifest](source-snapshot-manifest.json), [release](evidence/release.json), [실행 명령·종료코드](evidence/live-exit.json)
- [최종 명세 검토](evidence/review-spec-r2.md), [최종 구현 검토](evidence/review-standards-r2.md), [51개 테스트 기록](evidence/fix-r1-tests.log), [호스트 보완 검증](evidence/coordinator-fix-tests.log)
- [정리 기록](evidence/cleanup.json), [정리 후 API 재확인](evidence/absence-after-cleanup.json): 이번 실행 소유 Pod 5개와 ConfigMap 1개 삭제, 미해결 객체 없음

재분석: 소스 archive를 원래 `.scratch/gpu-spark-dag-20261029/` 구조로 풀고 raw archive를 해제한 뒤 `python3 -B staged/experiments/gpu-mobilint-placement/chain_analyze.py <run-directory> <new-output-directory>`를 실행한다. 원시 파일은 수정하지 않는다. 실제 재실행에는 release에 바인딩된 로컬 모델/휠/입력과 클러스터가 필요하며 이 문서 작성 과정에서 추가 측정을 실행하지 않았다.

배포용 CSV는 Git 검증을 위해 줄바꿈만 LF로 정규화했다. 원시 JSON과 실행 archive는 그대로 보존했다.
