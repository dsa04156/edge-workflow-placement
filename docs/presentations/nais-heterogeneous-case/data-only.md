# 면접 발표용 수치·실험 조건

이미지·그래프·발표 대본 없이 정리한 데이터 자료. 서로 다른 실험 A/B/C를 구분한다. 단위는 별도 표시가 없으면 ms다.

아키텍처는 [architecture.md](architecture.md)에 구성요소·Task 입출력·실행 후보·배치 결정·데이터 전달·측정 과정별로 정리했다. A/B/C의 실행 구조와 수치가 각각 어디에 해당하는지도 설명한다.

## 1장 자료 — 이기종 실행환경과 국산 NPU 검증

### A. 멀티모달 5단계 DAG의 작업별 실행시간

| 항목 | 조건 |
|---|---|
| 실행 ID | qnb-compare-r2 |
| DAG | 영상 전처리 → 영상 추론 / 음성 전처리 → 음성 추론 → 결과 결합 |
| 입력 발생 위치 | Raspberry Pi 5 엣지 Pod, 고정 corpus 재생 |
| 실행환경 | Kubernetes/KubeEdge, x86 CPU·ARM CPU·Jetson CUDA GPU·Intel OpenVINO NPU |
| 아래 통계량 | 비교 run의 사전 보정 실행시간 중앙값, 보고서 반올림 값 |
| Mobilint 포함 여부 | 이 실험에는 포함하지 않음. 별도 B 실험 |

| 작업 | x86 서버 CPU | ARM 엣지 CPU | Jetson GPU | Intel NPU |
|---|---:|---:|---:|---:|
| 영상 추론 | 118.5 | 289.8 | 35.9 | 11.4 |
| 음성 추론 | 277.4 | 590.0 | 52.5 | — |
| 결과 결합 | 26.4 | 288.0 | 27.0 | — |

- —: 해당 실험의 실행 후보 없음. 0 ms 또는 장치의 일반적 미지원으로 해석하지 않음.
- 단계별 보정 실행시간이며 종단 지연·포화 처리량이 아님.
- 최종 VQA 품질은 0.355, 사전 기준은 0.40. 품질 적격 성과로 제시하지 않음.
- 근거: [기존 방법 비교 결과](../../experiments/quality-network-baseline-results.md), “보정 median compute”의 compare 값.

### B. Mobilint 실추론 3단계 DAG

| 항목 | 조건 |
|---|---|
| 실행 ID | mdp-20261026t092212 |
| 경로 | Pi CPU 전처리 → 서버 추론 → Pi CPU 후처리 |
| 비교 장치 | Mobilint ARIES / 같은 서버의 CPU |
| 모델 | 같은 공식 ResNet50 원본 계열의 MXQ / FP32 ONNX |
| NPU 런타임 | qbruntime 1.4.0, aries-rb, Cluster0/Core0 Single |
| CPU 런타임 | ONNX Runtime 1.23.2, CPUExecutionProvider, intra/inter 1스레드 |
| 평가 데이터 | ImageNetV2 matched-frequency 200장, 두 경로에서 동일 입력 비교 |
| 실행 방식 | 단일 run, closed loop, 동시성 1, 표본·경로 순서 사전 동결 |
| 입력 전송 | 양쪽 모두 uint8 HWC 150,528 B, backend에서 각 입력 형식으로 정규화 |

**종단·단계별 지연**

| 지표 | Mobilint NPU | CPU FP32·1스레드 |
|---|---:|---:|
| 종단 평균 | 21.26 | 130.24 |
| 종단 중앙값 | 19.94 | 128.80 |
| 종단 p95 | 28.23 | 141.17 |
| 종단 p99 | 50.32 | 158.04 |
| Backend 호출 중앙값¹ | 4.59 | 112.31 |
| Backend 호출 p95¹ | 6.77 | 121.10 |
| 전처리 평균 | 8.99 | 8.82 |
| 후처리 평균 | 0.75 | 0.67 |

¹ Backend 호출에는 입력 정규화가 포함된다. 순수 장치 연산시간이 아니다. RPC·server·backend는 포함 관계가 있어 합산하지 않는다. 단계별 중앙값을 합해 종단 중앙값으로 대신하지 않는다.

- 이 설정에서 종단 중앙값 감소율: **84.52%**. 원 JSON의 반올림 전 값으로 계산. CPU 전체 성능 상한·일반적인 가속기 성능비가 아님.

**정답 품질 및 CPU 출력 일치**

| 지표 | Mobilint NPU | CPU FP32·1스레드 |
|---|---:|---:|
| Top-1 | 120/200 = 60.0% | 119/200 = 59.5% |
| Top-5 | 169/200 = 84.5% | 167/200 = 83.5% |
| Top-1 95% Wilson CI | 53.08–66.54% | 52.58–66.06% |
| Top-5 95% Wilson CI | 78.84–88.86% | 77.73–88.00% |

| 동일 200쌍의 출력 비교 | 결과 |
|---|---:|
| Top-1 일치 | 188/200 = 94.0% |
| Top-5 집합 일치 | 156/200 = 78.0% |
| Top-5 순서 일치 | 118/200 = 59.0% |
| Mobilint만 Top-1 정답 | 3쌍 |
| CPU만 Top-1 정답 | 2쌍 |

정답 정확도와 CPU 출력 일치는 다른 지표다. 품질 우월성·동등성을 선언하지 않는다. 서비스 품질 문턱은 이 실험에서 정하지 않았다.

**실행 수**

| 구분 | 두 경로 합계 | 결과 |
|---|---:|---|
| 워밍업 | 10 | 10 성공 |
| ImageNetV2 평가 | 400 = 200장 × 2경로 | 400 성공 |
| COCO 출력 비교 | 120 = 60장 × 2경로 | 120 성공 |
| 전체 | 530 | 530 성공, 실패 0 |

530회를 “Mobilint 단독 평가 530장”으로 표시하지 않는다. 이 실행 수는 장기 가용성·포화 처리량·SLO 달성률의 증거가 아니다.

근거: [Mobilint 결과](../../experiments/mobilint-dag-pilot.md), [원 분석 JSON](../../../artifacts/mobilint-dag-pilot/runs/mdp-20261026t092212/analysis.json).

## 2장 자료 — 전송 조건에 따른 DAG 배치 효과

### C. SqueezeNet 3단계 배치 실험

| 항목 | 조건 |
|---|---|
| 실행 ID | 20261016t111955534665 |
| DAG | T1 전처리 → T2 SqueezeNet 추론 → T3 후처리 |
| 후보 위치 | A: ARM Raspberry Pi / N: Intel NPU 서버 |
| 실제 실행 장치 | T1·T3는 어느 위치에서도 CPU, T2만 N 위치에서 Intel NPU |
| 배치 후보 | 2개 위치 × 3개 Task = 8개 조합 |
| 입력 위치 | 모든 조건에서 ARM 엣지 고정 |
| 전달 조건 | native / 50 Mbps 제한 / 20 Mbps 제한 |
| 제약 방식 | 실험 Pod netns의 TBF, 물리 링크 교체 아님 |
| 보정·평가 | 조건별 사전 보정 → 계획 동결 → 별도 무작위 순서 평가 |
| 반복 | 새 Pod 배포 3회. 같은 물리 host·날짜·cache 공유 |
| 측정 | 배포·입력·조건별 고유 선택 배치당 warmup 2회 + 측정 5회 |
| 아래 통계량 | 배포별 측정 5회 평균을 3개 배포에 균등 평균 |
| 동일 배치 | 정책이 같은 배치를 고르면 동일 관측을 공유 |

**입력·전달 데이터 크기**

| 입력 | 원본 JPEG |
|---|---:|
| rocket | 112,525 B |
| chelsea | 240,512 B |
| hubble_deep_field | 527,940 B |
| 전처리 출력·입력 공통 | 602,112 B |
| 추론 출력·입력 공통 | 4,000 B |

Task payload 크기이며 HTTP header·trace·제어 트래픽 전체 합계가 아니다. AAA는 원격 Task payload 0 B, NNN은 원본 JPEG를 원격 전송한다.

**정책별 종단 지연 평균 — 전체 9조건**

| 입력 | 전달 조건 | Task-fastest | Data greedy | Current DAG | Native 고정 DAG |
|---|---|---:|---:|---:|---:|
| rocket | native | 26.89 | 46.93 | 27.04 | 27.04 |
| rocket | cap50 | 32.14 | 45.69 | 32.82 | 32.82 |
| rocket | cap20 | 58.99 | 51.59 | 51.59 | 56.53 |
| chelsea | native | 39.79 | 50.50 | 38.91 | 38.91 |
| chelsea | cap50 | 54.57 | 53.20 | 53.20 | 54.23 |
| chelsea | cap20 | 115.20 | 51.75 | 51.75 | 112.25 |
| hubble_deep_field | native | 67.66 | 59.58 | 59.58 | 59.58 |
| hubble_deep_field | cap50 | 108.69 | 63.61 | 63.61 | 63.61 |
| hubble_deep_field | cap20 | 239.12 | 63.05 | 63.05 | 63.05 |

| 정책 | 결정 기준 |
|---|---|
| Task-fastest | 현재 비용표에서 Task별 연산시간 최소 |
| Data greedy | 같은 비용표로 현재 Task의 연산·이동 비용을 단계별 고려 |
| Current DAG | 같은 비용표로 전체 8개 배치 정확 열거 |
| Native 고정 DAG | native 조건에서 정한 계획 유지. 현재 조건 정보를 주지 않은 대조 |

**대표 수치와 반대 조건**

| 조건 | 비교 | 관측 |
|---|---|---|
| 큰 입력·20 Mbps | Task-fastest 239.12 → Current DAG 63.05 ms | 원 보고서 감소율 73.63%, greedy와 동일 배치·관측 |
| 중간 입력·20 Mbps | Native 고정 DAG 112.25 → Current DAG 51.75 ms | 원 보고서 감소율 53.89%, 현재 정보 제공 여부가 다름 |
| 작은 입력·native | Task-fastest 26.89 / Current DAG 27.04 ms | DAG가 더 빠르지 않음 |
| 중간 입력·50 Mbps | 고정 대비 Current DAG 배포별 이득 −0.52 / +4.75 / −1.16 ms | 반복 간 개선 방향이 일관되지 않음 |

**규모와 검증**

| 항목 | 결과 |
|---|---:|
| 전체 실행 | 1,340 = 보정 864 + 평가 476 |
| 평가 중 워밍업 / 측정 | 136 / 340 |
| 유효 완료 | 1,340 |
| protocol 실패 | 0 |
| CPU reference Top-5 순서 불일치 | 0 |
| 단계별 실행 receipt | 4,020 |

- 63.05 ms의 AAA 계획은 전체 Task를 ARM CPU에서 실행한다. “NPU 사용에 따른 개선”이 아니라 데이터 이동을 줄인 배치 효과다.
- Current DAG는 사전 보정한 현재 조건을 안다. 온라인 관측·재배치 전체 비용을 해결한 결과가 아니다.
- 새로운 알고리즘 또는 선행 논문 구현 재현이 아니다. 단순 greedy도 큰 손실을 줄였다.
- 정답 라벨 정확도·SLO·goodput·p95·통계적 유의성은 이 실험에서 검증하지 않았다.
- 근거: [전송 조건별 배치 실험](../../experiments/edge-network-placement.md), [기계 판독 결과](../../../artifacts/edge-network-placement/results.json).

## 편집용 파일

- [작업별 보정값 CSV](task-profile.csv): A의 서버 CPU·Jetson·Intel NPU 표
- [Mobilint 상세 지연 CSV](mobilint-detailed.csv): B의 단계별 평균·중앙값·p95·p99, 반올림 전 값
- [Mobilint 품질·지연 CSV](mobilint-results.csv): B의 품질 및 핵심 지연, 정확도는 0–1 비율
- [네트워크 전체 조건 CSV](network-all-conditions.csv): C의 9조건 × 4정책, 보고서 반올림 값

각 실험을 별도 표·범례로 표시한다. A/B/C를 동일 모델·동일 조건의 가속기 성능 순위로 합치지 않는다. 연구 목표인 “품질 조건을 만족하는 유효 처리량 개선”은 미검증이다.
