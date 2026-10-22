# 다중 엣지 센서 배치 — 무거운 모델 재실측 (2026-10-22)

이전 파일럿 [multi-edge-sensors-20261022.md](multi-edge-sensors-20261022.md)(`mes-live-20261022t101935z`)과
같은 scenario·source·정책·입력에서 **모델만 교체**해 다시 측정했다.

- 실행: `mes-heavy-20261022t113349z` (2026-10-22 11:33–12:23Z), namespace `topology-profile-lab`, native LAN, shaping 없음.
- 노드: Ready 9 (dev0002/0003/0006/0008/0009, ser0001–0004). NotReady dev0001/0004/0007 제외.
- 엣지 입력: 영상 RTSP cam-e0@dev0003, cam-e6@dev0006, cam-e8@dev0008, 텍스트 txt-e1@dev0002, txt-e9@dev0009 (합성 입력, 실제 센서 수집 아님).
- 증거 수준: 실제 클러스터 실행 + verify `passed`(`offline: false`). n=3 기술 통계이며 공식 수용·일반화 주장 아님.

## 모델 교체

| task | 이전 | 이번 | 고정 근거 |
|---|---|---|---|
| V | YOLO11n FP32 ONNX | **YOLO11m** FP32 ONNX (80.6 MB, ultralytics assets v8.3.0 `yolo11m.pt` sha256 `d5ffc1a6…`) | `export_yolo.py` 원본 .pt 대조 9/9 프레임 위반0, ultralytics ONNX 대조 위반0 |
| T | all-MiniLM-L6-v2 (384d) | **all-mpnet-base-v2** (768d, 436 MB ONNX, HF revision `e8c3b32e…`) | `check_minilm.py` vs sentence-transformers 5.1.2: 17문장 최소 cosine 0.99999994 |
| F | 결정적 fusion | 동일 | — |

모든 노드에서 같은 ONNX bytes, CPUExecutionProvider (V 2 threads, T 1 thread). GPU/NPU 미사용.

## 코드 변경 (이번 실행 전)

- `models/manifest.json`을 models bundle에 넣어 runtime/reference/smoke가 V 파일·T 디렉터리를 읽도록 함. `sources-*.json`의 `models` 블록이 없으면 기존 YOLO11n/MiniLM 기본값.
- mpnet ONNX는 `token_type_ids` 입력이 없어 Embedder가 그래프가 선언한 입력만 전달.
- 임베딩 차원 검증 384 고정 → {384, 768}, 비교 시 차원 불일치를 위반으로 보고.
- 자원: T memory 1Gi→2Gi, cache Pod 500m/512Mi→1 CPU/1Gi(bundle 790 MB).
- 검증: 빠른 공개 CLI 테스트 30개 통과, 로컬 실제 RTSP+모델 smoke `local-smoke-heavy-20261022b` 품질 4/4·RTSP 6/6. fake-kubectl 전체 수집 테스트(`test_execute`)는 이번에 재실행하지 않음.

## 명령

```bash
C=/mnt/data3tb/multi-edge-sensors-20261022; P=$C/envs/pod311/bin/python; E=experiments/multi-edge-sensors
(cd $C && envs/export311/bin/python -I $PWD/$E/export_yolo.py --weights downloads/yolo11m.pt \
  --weights-sha256 d5ffc1a674953a08e11a8d21e022781b1b23a19b730afc309290bd9fb5305b95 \
  --weights-url https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11m.pt \
  --images downloads/images/bus.jpg downloads/images/zidane.jpg downloads/images/coco-000000039769.jpg --output export-yolo11m-20261022a)
(cd $C && envs/st311/bin/python -I $PWD/$E/check_minilm.py --model-dir downloads/mpnet --output mpnet-check-20261022a.json)
$P $E/prepare.py --sources $E/sources-20261022-heavy.json --cache $C --output $C/prepared-heavy-20261022b
$P $E/smoke_local.py --prepared $C/prepared-heavy-20261022b --python /home/jinuk/.local/bin/python3.11 --output artifacts/multi-edge-sensors/local-smoke-heavy-20261022b
$P $E/collect.py --execute --prepared $C/prepared-heavy-20261022b --run-id mes-heavy-20261022t113349z
$P $E/reference.py --run artifacts/multi-edge-sensors/mes-heavy-20261022t113349z --prepared $C/prepared-heavy-20261022b
$P $E/verify.py --run artifacts/multi-edge-sensors/mes-heavy-20261022t113349z --prepared $C/prepared-heavy-20261022b \
  --output artifacts/multi-edge-sensors/mes-heavy-20261022t113349z.verify.json
```

host 측 코드 사본·SHA256SUMS: `artifacts/multi-edge-sensors/mes-heavy-20261022t113349z-host-sources/` (68 파일, topology는 이전 실행과 동일 bytes).

## 결과

**후보 qualification**: V/T는 8/9 노드 — dev0006은 capacity snapshot(남은 CPU 1,280m < V 2,000m, 메모리 1,857 MiB < T 2,048 MiB)으로 제외되어 측정하지 않았다(이전 실행에선 9/9). F는 9/9. 측정된 노드의 보정 품질 위반 0.

보정 compute median (ms, 보정 seq 0–2):

| 노드 | V YOLO11m | (이전 YOLO11n) | T mpnet | (이전 MiniLM) |
|---|---|---|---|---|
| dev0002/0003/0008 (Pi5) | 1,441–1,560 | 201–235 | 175–179 | 25–27 |
| dev0009 (Jetson CPU) | 2,243 | 289 | 362 | 54 |
| ser0001 | 272 | 36 | 64 | 14 |
| ser0002 | 271 | 40 | 53 | 8.5 |
| ser0003 | 299 | 55 | 64 | 6.8 |
| ser0004 | 531 | 70 | 83 | 14.6 |

**평가** (warmup 1 + 평가 3, seq 3–5): 16 job, 64 attempt 모두 ok, 품질 64/64 reference 허용치 이내. E2E는 목적지 source Pod에서 F `/run` 요청~응답(monotonic), 동결 PNG 입력 경로.

| wf | 정책 | V / T / F | median | mean | SD | Δmedian vs task-fastest | vs kube-default | 이전 median |
|---|---|---|---|---|---|---|---|---|
| w1 | greedy-network | ser0001 / ser0002 / dev0003 | 323.1 | 328.3 | 9.6 | −1.8% | −45.6% | 89.9 |
| w1 | exact | ser0001 / dev0002 / dev0003 | 327.3 | 328.5 | 5.2 | −0.5% | −44.9% | 90.2 |
| w1 | task-fastest | ser0001 / ser0002 / ser0003 | 328.9 | 332.6 | 11.4 | 0 | −44.7% | 95.6 |
| w1 | server-fixed | ser0002 ×3 | 329.7 | 332.8 | 9.5 | +0.2% | −44.5% | 97.2 |
| w1 | kube-default | ser0004 / ser0003 / dev0009 | 594.2 | 599.4 | 9.1 | +80.7% | 0 | 100.2 |
| w1 | source-local | — | infeasible | | | | | infeasible |
| w2 | greedy-network | ser0001 / ser0002 / dev0008 | 287.4 | 293.6 | 13.2 | −0.9% | −13.5% | 53.7 |
| w2 | exact | ser0001 / dev0002 / dev0008 | 289.1 | 295.7 | 14.3 | −0.3% | −13.0% | 53.1 |
| w2 | task-fastest | ser0001 / ser0002 / ser0003 | 290.0 | 294.6 | 8.8 | 0 | −12.8% | 55.5 |
| w2 | server-fixed | ser0002 ×3 | 290.6 | 295.8 | 10.4 | +0.2% | −12.6% | 53.3 |
| w2 | kube-default | ser0003 / ser0004 / dev0009 | 332.4 | 347.0 | 27.3 | +14.6% | 0 | 57.4 |
| w2 | source-local | — | infeasible (dev0006 capacity) | | | | | 224.0 |
| w3 | exact | ser0001 / dev0002 / dev0008 | 286.1 | 291.6 | 10.7 | −2.1% | −47.9% | 53.0 |
| w3 | task-fastest | ser0001 / ser0002 / ser0003 | 292.3 | 296.1 | 10.0 | 0 | −46.8% | 54.8 |
| w3 | greedy-network | ser0001 / ser0002 / dev0008 | 292.3 | 293.1 | 8.8 | 0.0% | −46.8% | 52.8 |
| w3 | server-fixed | ser0002 ×3 | 293.3 | 295.4 | 7.8 | +0.3% | −46.6% | 54.8 |
| w3 | kube-default | ser0004 / ser0003 / dev0009 | 549.1 | 558.0 | 16.0 | +87.8% | 0 | 56.7 |
| w3 | source-local | dev0008 / dev0002 / dev0008 | 1,457.3 | 1,469.6 | 26.3 | +398.5% | +165.4% | 214.7 |

단위 ms, Δ = (median_정책 − median_기준)/median_기준. 가설검정 없음.

해석(이 조건 한정):

- 모델이 무거워져 E2E가 약 5–6배 커졌고 V(YOLO11m) compute가 지배한다. 서버 x86 V(≈270 ms) 위 정책들(exact/greedy/task-fastest/server-fixed)은 서로 0–2% 차이로 SD(5–14 ms) 이내다. **DAG 공동 배치(exact)의 task-fastest 대비 이득은 사라졌다**(이전 3–6% → 0.3–2.1%).
- kube-default와의 차이는 커졌다(w1/w3 −45~−48%). default-scheduler가 V를 ser0004(V 531 ms)에 두었기 때문이며, 자원 요청만 보는 기본 스케줄러가 노드 간 compute 속도 차이를 모르는 효과다. 이는 프로파일 기반 정책 전반(단순 task-fastest 포함)의 이득이며 새 알고리즘의 고유 이득이 아니다. kube-default 배치는 이전 실행(V=ser0002)과 달라 같은 정책도 관측마다 배치가 바뀐다.
- Pi5에서 V를 실행하는 source-local은 ≈1.46 s로 서버 배치의 약 5배다.

**RTSP 프로토콜 probe** (배치 E2E와 분리): 27 중 18 ok, **9 error = cam-e8(dev0008) 전 reader `404 Not Found`**. cam-e8은 source 단계에서 path ready·12프레임 loop 동결까지 정상이었고 Pod는 재시작 없이 Running이었으나, 후보 검증 시점 이후 MediaMTX path가 사라졌다. edge 로그 포트(10369) 연결 거부로 Pod 로그를 회수하지 못해 원인은 확인하지 못했다(dev0008 4 GB 메모리 압박 가능성은 가설일 뿐). 동결 PNG는 source Pod의 HTTP로 제공되므로 배치 비교 입력에는 영향이 없다. ok probe에서 x86 reader(ser0001/0002/0004) decode hash 0/6 불일치는 이전과 같은 패턴이다.

**정리**: 생성 101개 UID 조건 삭제, remaining 0, unresolved 0, `cleanup_verified=true`; run-label 독립 조회 0개. 보호 Pod 254개 변화 0.

## 한계

- 동결 decoded PNG 입력 배치 비교이며 실시간 RTSP E2E·frame age 비교가 아니다.
- n=3, 한 번 배포, 한 시간대, native LAN, 동시 부하 없음. CPU 전용.
- dev0006이 V/T 후보에서 빠져 이전 실행과 후보 집합이 다르다(w2 source-local 미평가).
- cam-e8 RTSP 404, x86 decode 불일치 원인 미진단.
- 각 job이 517 MB models.tar를 다시 받으므로 배포 비용이 커졌다(E2E와 별도 기록).
