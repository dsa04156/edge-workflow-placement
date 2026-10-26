# Mobilint DAG pilot — 공개용 소형 증거

공개 위치는 `artifacts/mobilint-dag-pilot/`이다(coordinator가 이 디렉터리를 그대로 복사). 본문 보고서는 `docs/experiments/mobilint-dag-pilot.md`이고, 파일별 sha256과 byte 수는 `publication-manifest.json`에 있다.

- 포함
  - `frozen-protocol.json`: 표본 member 이름과 hash만 있고 이미지는 없다.
  - `feasibility.md`, `input-contract-erratum.md`
  - `release-final.json` / `release-writeable.json` / `release-normalized.json`
  - `fix-writeable.diff`, `fix-vendor-normalize.diff`, `developer-fix-r1.md`
  - `provenance/provenance.json`: 공식 `mblt-vision-python` 0.0.6 wheel의 URL·sha256과 `ResNet50.yaml`/`normalize.py` 등의 파일별 sha256.
  - `provenance/hf-resnet50-aries-rb-metadata.json`
  - `bundle-manifest.json`: cluster cache에 올린 로컬 bundle(모델·wheel·이미지 270장)의 hash 목록. 대상 파일은 공개하지 않는다.
  - `test-fix-normalize.log`
  - `coordinator-live-verification.json`: coordinator가 독립적으로 검증한 결과다.
    - full run 530행의 logits sha와 top-5 재계산이 일치했고, 소유 객체는 모두 부재했다.
    - broader 감사에서 기존 Pod 258개의 readiness와 restart는 변하지 않았다. 관련 없는 GC Pod 1개가 추가되었다.
  - `protected-workload-summary.json`: developer collector의 run별 UID/phase 요약이다. full run은 259개다.
  - `runs/mdp-*/`: run 4개의 phase/event/health/identities/probe/occupancy 판정/salvage/cleanup JSON.
    - full run에는 정본 `ledger-main.json`(요청별 receipt와 logits)과 `analysis.json`이 더 있다.
- 제외
  - 모델 MXQ/ONNX, 이미지, wheel. 로컬 `.scratch/mobilint-dag-pilot/assets/`에만 있다.
  - 제3자 원문 `ResNet50.yaml`, `normalize.py`. 로컬 `.scratch/mobilint-dag-pilot/provenance/`에 보존했고, 공개본에는 URL과 hash만 둔다.
  - `runs/mdp-20261026t092212/salvage-ledger-main.json`: 정본 ledger와 byte가 같은 중복이다. 둘 다 sha256 `21729bf8098e8a4fea255c958ed794a386e18eb42a041e256b9f7960a074f2d7`, 3,633,497 B.
  - 증분 checkpoint `ledger-main.jsonl`, kubectl 원시 stdout/stderr(클러스터 전체 Pod JSON), 클러스터 전체 Pod projection, transcript.
