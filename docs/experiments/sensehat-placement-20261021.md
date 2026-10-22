# Sense HAT 실제 데이터 첫 예측 검증 — 2026-10-21

상태: 실제 데이터 확보·예측 평가·클러스터 36회 실행 완료. 출력/배치/코드 검증은 통과했지만 기존 타 네임스페이스 Pod3개 소멸로 전체 verifier는 실패(10/11 checks)다. 통제된 성능 비교의 최종 수용으로 표시하지 않는다.

## 입력과 방법

실제 dev0003 Sense HAT /dev/i2c-1 원본이다. 이력 조회 구간은 2026-10-21 19:36:40~19:51:40 KST (10:36:40~10:51:40 UTC), 채널마다900행이다. QUALIFIED/SOURCE, seq395~1294, 단일 세션이며 sequence 누락이 없다. 전체11채널 이력과 별도의25초 MQTT 실시간 수신을 확보했다. compass는 yaw의 별칭이다.

예측에는 temp_humidity·humidity·pressure를 독립 사용했다. 540/180/180행으로 시간순 학습/보정/평가를 분리하고, 과거8개 평균으로 다음 값을 예측하는 최소제곱 직선을 학습했다. 마지막 관측값을 유지하는 persistence와 대조했다. 각 평가 구간172개 창은 서로 겹치므로 독립 표본172개로 해석하지 않는다. 보드 온도를 실내 기온으로 가정하지 않는다.

| 채널 | 단위 | 회귀 MAE | persistence MAE | 회귀 RMSE | persistence RMSE |
|---|---|---:|---:|---:|---:|
| temp_humidity | °C | 0.056844 | 0.077957 | 0.071258 | 0.099835 |
| humidity | %RH | 0.216202 | 0.302836 | 0.291456 | 0.391919 |
| pressure | hPa | 0.014946 | 0.012088 | 0.017984 | 0.015576 |

온도·습도에서는 이 구간의 오차가 작아졌고 기압에서는 나빠졌다. 단일15분 세션 결과이며 일반화·이상 탐지·신경망 성능을 뜻하지 않는다.

## 구현 및 검증

Claude Opus5.5 공식 구독으로 새 experiments/sensehat-placement/를 구현했다. T1 원본창 검증/특징 → T2 동결 회귀 → T3 결과 포장. 원본·모델·입출력 hash와 단계별 실제 노드/PodUID, 계산시간/전체 응답시간을 분리한다.

호스트 재실행: `rtk proxy python3 -m unittest test_forecast test_stage_runtime test_run test_execute` (experiments/sensehat-placement 작업 디렉터리): 26 tests OK, 17.447s. 실제 로컬 HTTP 다중 프로세스 및 fake kubectl 검증이며 클러스터 성능 증거가 아니다. 로컬 prepare와 dry-run reference 재현도 통과했다.

동결 모델·원본 사본·평가: [prepared artifact](../../artifacts/sensehat-placement/prepared-20261021t1116/). 모델 SHA256: `5ae6e406ae15491a989f7b0c5ba57c22cf084a6fb111becd400a4cb9f85758f3`.

## 실제 클러스터 결과

실행: 2026-10-21 20:47:02~20:48:56 KST, wall114.19s. Source는 etri-dev0003-raspi5, 후보는 같은 엣지와 etri-ser0002-cgnmsb다. 실제 물리 측정 이력을 source Pod에서 재생했다. 연속 라이브 센서→결과 E2E가 아니다. 네트워크 제한/지연 주입 없음.

T1 원본창 검증/평균 → T2 학습 회귀 → T3 결과 포장. 각 배치 warmup3+평가9=12회, 고유3배치 총36회 모두 정상 응답. 기본 스케줄러는 세 Task 모두 서버에 배치했으므로 server-all과 관측을 공유했다. 정책을 독립 표본으로 중복 계산하지 않았다.

| 배치(T1/T2/T3) | 평가 n | 중앙값 ms | 평균 ms | 최소~최대 ms | 단계 계산시간 합 중앙값 ms |
|---|---:|---:|---:|---:|---:|
| 엣지/엣지/엣지 | 9 | 4.214 | 7.824 | 4.010~36.545 | 0.276 |
| 엣지/서버/서버 | 9 | 6.581 | 10.387 | 5.012~40.349 | 0.275 |
| 서버/서버/서버 (=default-scheduler) | 9 | 7.790 | 8.677 | 4.489~14.402 | 0.382 |

**이 소규모 관측에서는 엣지 전체의 응답 중앙값이 서버 전체보다45.9% 낮았다.** 평균 차이는 훨씬 작고, 엣지/혼합에서36~40ms 꼬리값이 있었다. 입력9개·한 블록만으로 유의성/일반적 우위/꼬리지연 안정성을 주장하지 않는다. 가벼운 회귀 계산보다 HTTP 연결·직렬화·전달 등의 비용이 큰 워크플로다. 차이를 순수 네트워크 시간으로 분해하지 않는다.

Task 입력은 rawPayload 등 원본 출처 metadata를 포함해 T1 기준8,250~8,440bytes다. 센서 스칼라8개만의 payload가 아니며, receipt/HTTP header는 별도다. 단계 receipt에는 중간 출력이 포함되고 그 직렬화/전달 비용도 응답시간에 포함된다.

측정 시간은 source Pod의 T1 요청~최종 응답 monotonic 구간이다. 외부 kubectl exec·결과 수집은 제외한다. 준비 비용을 포함한 전체 운영상 이익으로 해석하지 않는다: scheduler probe3.39s, pool 배포64.45s, health5.87s, 측정/수집 phase29.91s, 정리8.92s. 배치별 warmup phase2.40~2.71s, 평가 수집 phase7.35~7.56s이며 이 phase 시간에는 제어/수집 오버헤드가 있다.

## 검증과 남은 제한

[실행 artifact](../../artifacts/sensehat-placement/sensehat-20261021t114702z/) / [최종 prepared](../../artifacts/sensehat-placement/prepared-20261021t1146/) / [검증 결과](../../artifacts/sensehat-placement/sensehat-20261021t114702z/verification.json).

- 원본/모델/코드 hash, 원본에서 분할·학습 재구성, 미래 정답 분리, 실제 Pod 안전성, 배치별 입력 순서,36개 receipt/중간 출력/bytes/노드/수치 비교(1e-9), 모든 phase 통과.
- 소유 Pod10개+ConfigMap1개 삭제, 잔여0, 삭제 오류0.
- **전체 verifier exit1**: 보호 대상으로 관측한361개 Pod 중 `ebpf-baseline-main-20261021-0`의 `backend-a`, `backend-b`, `router`가 종료 시 조회에서 사라졌다. 원인/삭제 주체는 확인하지 않았다. 이번 실행의 apply/delete는 소유 manifest에 한정됐지만, 외부 워크로드 불변 조건을 충족하지 못했다. 관측값을 폐기하거나 실패를 성공으로 바꾸지 않는다. 동시 워크로드 변화가 성능에 준 영향도 미확인이다.
- 재실행을 반복해 통과 결과만 고르지 않았다. 통제된 성능 주장은 다른 작업 변화가 없는 별도 구간의 반복 측정이 필요하다. 현재 결과는 실제 입력 연결 및 배치 진단이다.

## 수정·실행 이력

이전 Astra 읽기 차단은 구현을 읽기 전용으로 유지하면서 보고서 작성만 허용하는 문서화된 세션 계약으로 해소했다. guard 설정을 변경하지 않았다. 독립 Spec/Standards2회에서 관측 Pod 검증, 중간 receipt 무결성, 코드 동결, phase 경계를 보완했다. 최종 Standards의 timeout 출력 누락은 Opus가 수정하고 호스트3개 회귀 테스트로 확인했다. 모델은 Opus5.5 구독/검토 gpt-6-astra를 Orca 실행 기록에서 확인했다.

호스트 전체34테스트 통과(20.137s) 후 timeout 수정 관련3개 및 affinity1개를 추가 확인. 개발자 최종 영향 범위 test_run+test_execute22개 통과. 모든 테스트는 로컬/fake kubectl이며 실장비 증거와 분리한다.

첫 실제 실행 `sensehat-20261021t114403z`는 metadata.name field selector In에 두 값을 넣어 API server dry-run에서 실패했다. 측정0, ConfigMap정리/잔여0. Opus가 후보별 OR term으로 수정하고 새 prepared/새 run으로 실행했다. 첫 실패 artifact를 보존하고 성공 구간에 섞지 않았다.

실행 명령(프로젝트 루트):

```bash
rtk proxy python3 experiments/sensehat-placement/run.py --prepare artifacts/sensehat-placement/discovery-20261021/history --output <NEW_PREPARED_DIR>
rtk proxy python3 experiments/sensehat-placement/run.py --execute --prepared <NEW_PREPARED_DIR>
rtk proxy python3 experiments/sensehat-placement/verify.py --prepared <NEW_PREPARED_DIR> --run <RUN_DIR>
```

## 근거

- [원본 확보 보고](../../.scratch/sensehat-placement/discovery-report.md)
- [구현 보고](../../.scratch/sensehat-placement/developer-report.md)
- [호스트 검증 및 차단 기록](../../.scratch/sensehat-placement/host-validation.md)
- [동결 실험 설계·절차 출처](../../.scratch/sensehat-placement/spec.md)
