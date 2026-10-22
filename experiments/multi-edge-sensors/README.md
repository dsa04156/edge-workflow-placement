# multi-edge-sensors (2026-10-22)

다중 엣지 가상 RTSP 영상·텍스트 입력 → YOLO11n(V) ∥ all-MiniLM-L6-v2(T) → 결정적 fusion(F) DAG 배치 실험 도구.
명세: `.scratch/multi-edge-sensors/spec.md`, 결과·한계: `docs/experiments/multi-edge-sensors-20261022.md`.

**상태: 클러스터 실행 2회 — 경량(`mes-live-20261022t101935z`), 무거운 모델(`mes-heavy-20261022t113349z`, `sources-20261022-heavy.json`).**

## 파일

| 파일 | 역할 |
|---|---|
| `scenario-20261022.json` | source 매핑(영상 e0/e6/e8, 텍스트 e1/e9), 3 workflow, RTSP 고정값, 보정 0–2/평가 3–5 |
| `sources-20261022.json` | 외부 원본(URL+sha256): cp311 wheels(x86_64/aarch64), MediaMTX v1.21.2, yolo11n.pt, 파생 ONNX, MiniLM revision 고정 파일, 공개 이미지 |
| `export_yolo.py` | yolo11n.pt→ONNX export + 원본 대조(같은 텐서/ultralytics ONNX/rect 진단) |
| `check_minilm.py` | MiniLM ONNX 경로 vs sentence-transformers 대조 |
| `prepare.py` | hash 검증 → 결정적 bundle tar + `prepared.json` (새 디렉터리만, 덮어쓰기 거부) |
| `scenario.py`, `rtsp.py`, `models.py`, `runtime.py`, `bootstrap.py` | Pod 코드(ConfigMap으로 배포) |
| `collect.py` | `--dry-run`(kubectl 호출 0) / `--execute`(수집) |
| `plan.py` | 정책 계획(`plan_all`, taskwise `task-fastest`); collector와 같은 함수의 읽기 전용 CLI |
| `reference.py` | run의 동결 입력에 대한 로컬 CPU reference (`<run>/reference.json`, 배타 생성) |
| `verify.py` | 읽기 전용 감사; 증거 오류 시 nonzero |
| `smoke_local.py` | 실제 bootstrap/pip + MediaMTX/PyAV RTSP + 모델 로컬 smoke |
| `fake_kubectl.py` | 테스트 전용 kubectl 대역(로컬 프로세스). 클러스터 증거 아님 |
| `test_prepare.py`, `test_collect.py`, `test_execute.py`, `test_ownership.py`, `test_verify_numeric.py`, `test_plan.py`, `test_remote_batch.py` | 공개 CLI 경계 테스트 (review r1: 소유권·수치 검증·후보 gate) |

## 환경 (전용 캐시, 전역 설치 없음)

캐시 루트 `/mnt/data3tb/multi-edge-sensors-20261022/`. 모든 shell은 `rtk proxy` 접두.

```bash
C=/mnt/data3tb/multi-edge-sensors-20261022
# Pod와 같은 cp311 wheel로 만든 로컬 실행 env (x86_64 wheel, --no-index)
rtk proxy uv venv --python /home/jinuk/.local/bin/python3.11 $C/envs/pod311
rtk proxy uv pip install --python $C/envs/pod311/bin/python --no-index --find-links $C/wheels/x86_64 \
  numpy==2.2.6 onnxruntime==1.23.2 Pillow==11.3.0 tokenizers av
# YOLO export 전용 env
rtk proxy uv venv --python /home/jinuk/.local/bin/python3.11 $C/envs/export311
rtk proxy uv pip install --python $C/envs/export311/bin/python --index-url https://download.pytorch.org/whl/cpu \
  --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match \
  torch==2.9.1 torchvision==0.24.1 ultralytics==8.4.174 onnx==1.19.1 numpy==2.2.6 onnxruntime==1.23.2
# MiniLM 대조 전용 env
rtk proxy uv venv --python /home/jinuk/.local/bin/python3.11 $C/envs/st311
rtk proxy uv pip install --python $C/envs/st311/bin/python --index-url https://download.pytorch.org/whl/cpu \
  --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match \
  torch==2.9.1 sentence-transformers==5.1.2 numpy==2.2.6 onnxruntime==1.23.2
```

wheel/MediaMTX/모델/이미지 다운로드는 `sources-20261022.json`의 URL·sha256 그대로이며, `prepare.py --fetch`가 누락분을 받아 hash 검증한다.

## 실제 실행한 명령

```bash
C=/mnt/data3tb/multi-edge-sensors-20261022; P=$C/envs/pod311/bin/python; E=experiments/multi-edge-sensors
# 1) ONNX export + 원본 대조 (gate: 같은 텐서 0/9, ultralytics ONNX 0/9 위반)
rtk proxy bash -c "cd $C && envs/export311/bin/python -I $PWD/$E/export_yolo.py --weights downloads/yolo11n.pt \
  --weights-sha256 0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1 \
  --images downloads/images/bus.jpg downloads/images/zidane.jpg downloads/images/coco-000000039769.jpg --output export-20261022c"
# 2) MiniLM 대조
rtk proxy bash -c "cd $C && envs/st311/bin/python -I $PWD/$E/check_minilm.py --model-dir downloads/minilm --output minilm-check-20261022a.json"
# 3) 준비 (새 디렉터리)
rtk proxy $P $E/prepare.py --cache $C --output $C/prepared-<id>
# 4) 테스트
rtk proxy bash -c "cd $E && $P -m unittest test_prepare test_collect test_verify_numeric test_ownership test_plan test_remote_batch -v"   # 빠름(~18 s)
rtk proxy bash -c "cd $E && MES_TEST_RETAIN=<new dir> $P -m unittest test_execute -v"   # fake kubectl 전체 수집 ~8.5 분, 증거 보존
# 보존 run의 Collection 단언만 재검사(새 수집 없음): MES_TEST_REUSE=<retained dir>
# 5) dry-run (kubectl 호출 없음)
rtk proxy $P $E/collect.py --dry-run --prepared $C/prepared-<id> --inventory .scratch/multi-edge-sensors/inventory-before.json
# 6) 로컬 smoke
rtk proxy $P $E/smoke_local.py --prepared $C/prepared-<id> --python /home/jinuk/.local/bin/python3.11 \
  --output artifacts/multi-edge-sensors/local-smoke-<id>
```

## 클러스터 실행 (검토 후 coordinator 지시 시에만)

```bash
rtk proxy $P $E/collect.py --execute --prepared $C/prepared-<id>          # artifacts/multi-edge-sensors/mes-<utc>/
rtk proxy $P $E/reference.py --run artifacts/multi-edge-sensors/mes-<utc> --prepared $C/prepared-<id>
rtk proxy $P $E/verify.py --run artifacts/multi-edge-sensors/mes-<utc> --prepared $C/prepared-<id>
```

`prepared-<id>`는 현재 코드 hash로 다시 만들어야 한다(`collect.py`가 코드/scenario hash 불일치를 거부).
