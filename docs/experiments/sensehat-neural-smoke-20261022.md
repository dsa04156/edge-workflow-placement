# Sense HAT 습도 NumPy MLP CPU smoke (2026-10-22)

## 범위와 한계 (먼저 읽기)

- **입력은 과거 replay**: `artifacts/sensehat-placement/discovery-20261021/history/humidity.json`(2026-10-21 10:36:40–10:51:40Z, 900행, sha256 `50062dd1…3d036`)을 `sensehat-placement/forecast.load_channel`로 manifest(sha256/bytes/행 수)·연속성 검증 후 사용. fresh live 센서 데이터 아님.
- **CPU-only forward smoke**: 엣지 1 Pod(etri-dev0003-raspi5)와 서버 1 Pod(etri-ser0002-cgnmsb)에서 동일 모델 bytes로 forward만 측정. HTTP/DAG/네트워크 E2E, 배치 비교, 처리량 아님.
- **입력 3개**: test 구간의 첫/중간/끝 window(index 0/50/100). window끼리 행이 겹치며 독립 표본이 아니고 유의성 주장 불가.
- 측정 시점에 같은 namespace에서 다른 작업의 `wan-20261022t024224-*` Pod가 두 노드에서 실행 중이었음(간섭 가능, 통제하지 않음).

## 설계

- 분할: 시간순 540/180/180(training seq 395–934, validation 935–1114, test 1115–1294). window 64행 입력 → 다음 16행 예측, 구간 경계를 넘지 않음(window 수 461/101/101).
- 정규화: training 행만 사용(mean 23.3002, std 0.37284 %RH).
- 모델: NumPy float32 MLP 64→512→512→16 ReLU, 304,144 parameters, `model.bin` 1,216,584 B, sha256 `993c1d851754f2f6bb6230957d94858b7d6a2f704b8d57824953da31848bfdc4`.
- 학습: 로컬 coordinator 호스트(etri-ser0001, `/usr/bin/python3` 3.12.3)에서 1회만 수행. NumPy 1.26.4는 `uv run --with numpy==1.26.4` 임시 환경으로, uv cache에 2026-10-19부터 있던 cp312 wheel을 그대로 사용(이번 작업에서 다운로드·전역 설치 없음). seed 20261022, Adam lr 1e-3, batch 32, 업데이트 정확히 50회, hyperparameter search 없음. wall 0.185 s, MSE(정규화) 2.322 → 0.395.
- Pod: image `192.168.0.56:5000/node-inventory-python@sha256:370a8339…a67f`, CPU 1/메모리 1Gi(request=limit), `OMP/OPENBLAS_NUM_THREADS=1`, runAs/fsGroup 65532, readOnlyRootFilesystem, drop ALL, privileged/hostNetwork/hostPath/serviceaccount token 없음, emptyDir `/work`. ConfigMap 미사용.
- 업로드: `kubectl exec -i` stdin tar 하나(NumPy 1.26.4 cp311 wheel + `infer.py` + `model.bin` + `inputs.json`), wheel은 `/work/site`에 zip 해제. torch 등 다른 wheel 미전송.
- 측정: Pod 안에서 warmup 1회(validation 첫 window) 후 timed forward 3회. `perf_counter_ns`/`thread_time_ns`를 `forward()` 호출만 감싸며, `kubectl exec` wall(python 기동·import·모델 로드·JSON 포함)은 별도 기록.

## 실행 명령

```bash
PYTHONPATH=experiments/sensehat-neural-smoke uv run --python /usr/bin/python3 --no-build --with numpy==1.26.4 \
  python -m unittest test_neural -v                      # 8 tests OK (nonfinite parity 테스트 포함)
PYTHONPATH=experiments/sensehat-neural-smoke uv run --python /usr/bin/python3 --no-build --with numpy==1.26.4 \
  python experiments/sensehat-neural-smoke/smoke.py --prepare --output artifacts/sensehat-neural-smoke/prepared-20261022
PYTHONPATH=experiments/sensehat-neural-smoke uv run --python /usr/bin/python3 --no-build --with numpy==1.26.4 \
  python experiments/sensehat-neural-smoke/smoke.py --execute --prepared artifacts/sensehat-neural-smoke/prepared-20261022
```

(모든 명령은 `rtk proxy` 접두로 실행.) live budget 300 s, 실제 apply→cleanup 완료 10.1 s.

## 결과 (run `nsmoke-20261022t025228z`)

정확도(로컬, shipped bytes 그대로 평가, %RH):

| 범위 | MLP MAE | last-value MAE |
|---|---|---|
| test 전체 101 window × 16 horizon | 0.2687 | 0.2886 |
| test-1115 (first) | 0.2322 | 0.3726 |
| test-1165 (mid) | 0.2522 | 0.2986 |
| test-1215 (last) | 0.2882 | 0.2686 |

마지막 window에서는 last-value가 더 낫다(반례). 전체 차이 0.02 %RH는 겹치는 window 하나의 180행 구간 기준이며 일반화 근거가 아니다.

노드별 forward(ms, timed 3회):

| site | node / Pod UID | Python / NumPy / arch | warmup | forward wall | forward CPU | kubectl exec wall |
|---|---|---|---|---|---|---|
| edge | etri-dev0003-raspi5 / a4259f7e-c91e-4ee9-929b-2704a5787ec7 | 3.11.16 / 1.26.4 / aarch64 | 0.154 | 0.095, 0.095, 0.094 | 0.095, 0.095, 0.094 | 815.8 |
| server | etri-ser0002-cgnmsb / 32396e24-f74d-4b9b-a1e3-88f72bd91fdd | 3.11.16 / 1.26.4 / x86_64 | 0.067 | 0.040, 0.039, 0.038 | 0.040, 0.039, 0.038 | 314.2 |

- 두 Pod에서 계산한 model sha256이 로컬과 일치.
- 수치 parity: 로컬 reference 대비 edge/server 모두 max |diff| 1.9e-6 %RH, edge↔server 1.9e-6 (허용 1e-3).
- 실행 후 coordinator 지적으로 `neural.parity`가 NaN/inf를 명시적으로 거부하도록 수정(run 디렉터리의 `source/` 사본은 수정 전 버전, 불변 유지). 저장된 `results.json` 출력을 수정된 parity로 오프라인 재검사한 결과 동일(전부 finite, max |diff| 1.9e-6, ok).
- 단계 시간: apply 0.73 s, Ready 1.37 s, 업로드 edge 1.93 s(15.46 MB tar) / server 0.49 s(19.49 MB), cleanup 4.28 s.
- forward는 0.1 ms 미만이라 E2E 비용은 exec/기동 오버헤드(수백 ms)가 지배한다. 이 smoke로 엣지/서버 배치 우열을 말할 수 없다.

## 정리

`kubectl delete pod` 대상은 owned Pod 2개 이름만. 이후 `-l run-id=…` pods/configmaps 0개, `-l experiment=sensehat-neural-smoke` 0개 확인.

## Evidence

- 코드: `experiments/sensehat-neural-smoke/{infer.py,neural.py,smoke.py,test_neural.py}` (sensehat-placement 모듈은 import만, 수정 없음)
- 준비: `artifacts/sensehat-neural-smoke/prepared-20261022/{model.bin,inputs.json,prepared.json}`
- 실행: `artifacts/sensehat-neural-smoke/nsmoke-20261022t025228z/` (`summary.json`, `results.json`, `phases.jsonl`, `pods.json`, `uploads.json`, kubectl 호출별 event/stdout/stderr, 소스 사본)
