# Standards review R2 — PASS

Final narrow review of the R1 fix, using `review-standards-r1.md`, `review-spec-r1.md`, `fix-report-r1.md`, and the pinned R1 manifest. R1's broader review remains the evidence for unchanged behavior.

**Documented-standard violations: none.** The explicit `files.add(Path(G.__file__).resolve())` closes the dynamically loaded collector's release-binding gap while preserving the existing dependency discovery and shipped-file union. This satisfies the AGENTS.md requirement to bind actual execution dependencies and preserve frozen evidence; no implementation or cluster edits were made by this reviewer.

**Judgement calls: no blocking correctness, safety, or maintainability finding.** The new regression verifies the staged collector path and real hash, accepts an unchanged approved fixture, then selectively mocks only that dependency's hash and requires exactly `['code differs from the release']`. The mock is scoped and restored; no existing assertion is removed or weakened. No speculative refactoring is warranted.

**Exact delta verified:** only `chain_live.py` and `test_staged_native.py` differ among the ten pinned candidate files. Removing just the added line and just the new test in memory reproduces their exact R1 SHA-256 values (`a8b1147a…` and `e0b14786…`). All eight other candidate hashes, the pinned diff/manifest, and frozen contract/assets remain unchanged. Across the release's dependency map, the only existing hash change is `chain_live.py`; `gpu-mobilint-vlm/live.py` is the sole addition, with no removed entries or unrelated dependency changes.

**Validation:** independently ran `rtk proxy env PYTHONDONTWRITEBYTECODE=1 /home/jinuk/codex-work/scheduler/.scratch/gpu-mobilint-vlm-20261028/env/mblt-x86/bin/python -m unittest -v test_staged_native.UnpreparedRelease` from the staged placement directory: **2 tests passed, 0.251 s, exit 0**. Recomputed the fresh release draft and verified exact equality with `fix-r1-release-draft.json`, all **36** code entries against actual files, and unchanged non-code bindings versus the R1 draft. The developer's hashed full-suite log reports **51 tests passed in 12.297 s**; this final narrow pass did not repeat R1's broad investigation.

Remaining limits: this is an offline standards verdict, not release approval or accelerator evidence. The fixed draft remains unapproved; live execution and the 30-minute budget are unverified. Only this report was written.

## Actual SHA-256

Code-map paths below are relative to `staged/`; this is the complete validated 36-entry map.

```text
c01dea4fa93c02a56c1c64322cad3e25300e36de8963521221aaa48daa878da3  experiments/gpu-mobilint-placement/chain_design.py
6e9a499a56c7b1b70ebfa66ac8d3c31411ab94aba4b2e97e8137e6be0aea2f65  experiments/gpu-mobilint-placement/chain_generator.py
e8f975fb024ddb84547c5187446b908c17582fa3e795b1eab078d98f2e11df69  experiments/gpu-mobilint-placement/chain_live.py
df58d20a38718687964f261d8a2827fc24f03d646091c63a204e458a3a5bac4e  experiments/gpu-mobilint-placement/chain_policy.py
06991f69f988b9508949a456d222fadb7deee69cfc99febe6e4c4b553e037d74  experiments/gpu-mobilint-placement/chain_stages.py
69aba78c796d9ad31fed59af212a291989f6d59de05fc8de98b9d41863d3fca3  experiments/gpu-mobilint-placement/chain_worker.py
a6719d17ca340aa7e8c66a4b7df263c686671f0d4e348e0ad8483a0405b8b1c9  experiments/gpu-mobilint-vlm/live.py
fdc61f346445b5358657ef1568bcdbad36d21c14c28bd08ba7f3b6e9f806e9f3  experiments/gpu-mobilint-vlm/prepare.py
94224e9039920db425f9744769d7dae89573a7de40e0fd658675746287a70b8c  experiments/gpu-mobilint-vlm/protocol.py
c9b0dcffc7e6f2d43c32b0fc46e20a8313a319ec15e782b4398c064e61ffdb26  experiments/gpu-mobilint-vlm/vlm_bootstrap.py
f703990cdd1c93fb197216390765fd041ca82749107d975144ba18431cf047e8  experiments/gpu-mobilint-vlm/vlm_prepared_bootstrap.py
33c019700b08d6c501e336429cd53ecbd057d2e4ef4f3778b0a1c4cb2d669eb2  experiments/gpu-mobilint-vlm/vlm_runtime_cache.py
ffde17cbdc25a153ef7bd8105c0581dd9b27af2f1df22b9064bb721280f6c607  experiments/gpu-mobilint-vlm/vlm_stage.py
1dd12c09caac1ce2e8b18d450be3287bce335c97f18654e145bbc36c9c598f67  experiments/gpu-mobilint-vlm/vlm_worker.py
aabcf190cc55e730b580cbc559d508b48a325148b2881b5f8f7f55bdbf4c6ec7  experiments/multi-edge-sensors/collect.py
3bca67c4f7fd536f532b44b6c9e7c2bf079e36f353d8dbd3a9c8d5c71f1414e1  experiments/multi-edge-sensors/models.py
628fcd54c202e7858091ef5fb8e89eb7b4d3adefb85df24bb513502edf051e71  experiments/multi-edge-sensors/plan.py
04356cba7cb50f10b9cb4889e54e19d4824b227a69603347cd7aa8e4344a35bf  experiments/multi-edge-sensors/scenario.py
2a8be506e9ce64438145e2019b4b67ca864270ae875b458295082da0334bdce8  experiments/quality-network-baselines/bounded_http.py
46b791ffe1076af47f74c849ddae9b6c3c2fa571f7c6ae94a54226562c331ca1  experiments/quality-network-baselines/dag_worker.py
fd8be27e5bf90e3f336615d21f328c5372006b54ad18d26ea431303c4580ad54  experiments/quality-network-baselines/gateway.py
20029fed8e6f2c6c4a83529b476ce99743d258f30571f2fba7035711d3dbee4a  experiments/quality-network-heterogeneous/bootstrap.py
9afa0e1e1b6b021dfb1c144d8c2659094e2f2ece59cd3bd45ab56d09236489be  experiments/quality-network-heterogeneous/proc.py
e62c74e02fb67097f85d5c85817a719961be899f382fa0fcd71839fc3d9b255f  experiments/quality-network-heterogeneous/stages.py
78a2da635275c230bbd55c007d6bcc4e5de0ccc24abb03445a4a4b3b5e163d69  experiments/quality-network-heterogeneous/worker.py
bd06dd6fe4d9597b1b62a15ad0f3b5f23d7f734079c35ba9731cc691298d6cb3  experiments/real-topology-live/budget_placement.py
abd1dc1709f5cbd46ee432b543b3a16558d2b7a9a71d374e5f4f99c93f9f41a6  experiments/real-topology-live/control_receipt.py
4ded5d962bf1b8440a63b95d13161c75688f1e42ad354d90522492088b4f30a5  experiments/real-topology-live/http_runtime.py
192e675c5b34b9cabd1850016e40b082eaa854fefaac05b086d941a8e8262af6  experiments/real-topology-live/net_control.py
5d3f10a0ae05ffa9af91d3745df897cc2164b8c13be9e0f698789c2f66cc727b  experiments/real-topology-live/placement_policies.py
d0a0f1f4ddf041107ee9da5128df0c1c3fb6f249a67b51fba3fe394221db6edf  experiments/real-topology-live/run_baseline_comparison.py
6f81cdc3c52eae98d9612adcb3d7f9c0421bb893a4619481a5b5d722c528108e  experiments/real-topology-live/run_budget_placement.py
17acae3c58ff9a8222dd0bbdaf12432bcf47aeb19ca59a4e1b190a2eca3c66ef  experiments/real-topology-live/run_load.py
5ea350fb6ca90e82178d4c274e650d89b5c8926c9ac693e0a5f5ba7478b8a79b  experiments/real-topology-live/run_placement.py
d9a38854244a968fde6d26a67b8bd940fd3102a85643c05ab5534d74c022c690  experiments/real-topology-live/run_smoke.py
b20a3a0b731b762fe143eea2530b11fc7426ae694bc4b9f3c42b10870a7393ee  experiments/real-topology-live/topology.py
```

Additional reviewed files (paths relative to this scratch directory):

```text
156f38f9b07c1e95aee3ca00d0bf33490b16b60e30ea8971be47b8893b98e429  staged/experiments/gpu-mobilint-placement/chain_analyze.py
968913b4fad37f1e89a6468676bce145d3046e77873edb9712ce1da02f4b032f  staged/experiments/gpu-mobilint-placement/test_chain.py
d93dd8199acef617850b89c48c9f91ec6db66518fa9f1ab984552a2aec1f24c9  staged/experiments/gpu-mobilint-placement/test_chain_live.py
b671115da75d70bd3912cc5db1e7fcc7c2faa609a448a6735bdf77fd1bcb4da9  staged/experiments/gpu-mobilint-placement/test_chain_loopback.py
3b96914de80d6cbb3dfd62adbb0ee1d9213d4e51f010f010e24252b3e1ef1ce5  staged/experiments/gpu-mobilint-placement/test_chain_r1.py
c46c01bd644f758d7889d05399dd4fdbdbcbcbbdaaf33fb452a766a8a31ae5d8  staged/experiments/gpu-mobilint-placement/test_staged_native.py
e9f2af72b7640015fb376dd08ce1143f2da1df5624894bffcb7b0fb581697777  coordinator-review.diff
50d42e686c41b428a7a5e549007f1ff6468dd142fa8f8eeeff1149cc55be5cc6  reviewed-candidate-hashes.json
a4b643e41959ffde561e4fcc0f9c68ec62a0f53902e5d60822f816d2396dff6b  fix-r1-release-draft.json
bddab2b78ad42f7df627d6abef3788cd1b62658723571734094ec683b7f8863a  fix-r1-tests.log
4b902ef01fb0f95378d5db7fa142e47a7f253d962236256a7df6bd9dd21a97fb  frozen/contract.json
371880695df6b03ea50171f9c505b194233cf78e6313405e1eaa12adb87490aa  frozen/assets-manifest.json
```
