# SPEC review R2 — PASS

Final narrow review of the R1 dependency-binding fix. No remaining SPEC blocker in this delta; the R1 P1 is resolved. Implementation and cluster were not modified.

Read `review-spec-r1.md`, `fix-report-r1.md`, the actual dependency function and the new test. Removing exactly the added `files.add(Path(G.__file__).resolve())` line in memory reproduces the R1 `chain_live.py` hash; removing exactly the new regression test reproduces the R1 test-file hash. All eight other pinned candidate files retain their R1 hashes. The frozen contract, assets manifest, original review diff and candidate-hash record remain unchanged.

Independent offline checks using the existing NumPy environment established:

- A newly generated in-memory release draft exactly equals `fix-r1-release-draft.json`. Its **36 code entries all match SHA-256 of the actual staged files**.
- Compared with `dev-dryrun/release-draft.json`, the sole added entry is `experiments/gpu-mobilint-vlm/live.py`, with the real hash shown below. `G.__file__` resolves to this staged file. The only changed existing code hash is `chain_live.py`; no entries were removed and no other draft key changed, including design and frozen bindings.
- With offline test-only approval values, the old 35-entry draft fails with exactly `['code differs from the release']`, while the current draft passes. These checks do not approve either saved artifact; the new draft retains `approved_by=null`.
- The added regression verifies the real VLM hash, then mocks only that dependency's hash to simulate changed content and requires the prior release to fail with exactly the same error. It does not mutate the dependency file.

**Independent test evidence:** from `staged/experiments/gpu-mobilint-placement`, `rtk proxy env PYTHONDONTWRITEBYTECODE=1 /home/jinuk/codex-work/scheduler/.scratch/gpu-mobilint-vlm-20261028/env/mblt-x86/bin/python -m unittest -v test_staged_native.UnpreparedRelease` passed **2 tests in 0.316 s**, including the new regression. The read-and-hash verification above also completed with exit 0. The existing developer log, independently read and hashed, records **51 tests passing in 12.297 s**; the broad suite was not rerun for this narrow review, as requested.

R1's other findings and verification remain the baseline. Real release approval, live accelerator evidence and the 30-minute workload budget remain outside this offline SPEC review.

## Actual SHA-256

Paths are relative to this scratch directory; `design_sha256` is the release's canonical design digest, not a file hash.

```text
e8f975fb024ddb84547c5187446b908c17582fa3e795b1eab078d98f2e11df69  staged/experiments/gpu-mobilint-placement/chain_live.py
c46c01bd644f758d7889d05399dd4fdbdbcbcbbdaaf33fb452a766a8a31ae5d8  staged/experiments/gpu-mobilint-placement/test_staged_native.py
c01dea4fa93c02a56c1c64322cad3e25300e36de8963521221aaa48daa878da3  staged/experiments/gpu-mobilint-placement/chain_design.py
a6719d17ca340aa7e8c66a4b7df263c686671f0d4e348e0ad8483a0405b8b1c9  staged/experiments/gpu-mobilint-vlm/live.py
a4b643e41959ffde561e4fcc0f9c68ec62a0f53902e5d60822f816d2396dff6b  fix-r1-release-draft.json
bddab2b78ad42f7df627d6abef3788cd1b62658723571734094ec683b7f8863a  fix-r1-tests.log
550a6c70b5bede700a6cedb2cfe07385965d275370e9940a5f77631a4a7e75df  design_sha256
4b902ef01fb0f95378d5db7fa142e47a7f253d962236256a7df6bd9dd21a97fb  frozen/contract.json
371880695df6b03ea50171f9c505b194233cf78e6313405e1eaa12adb87490aa  frozen/assets-manifest.json
e9f2af72b7640015fb376dd08ce1143f2da1df5624894bffcb7b0fb581697777  coordinator-review.diff
50d42e686c41b428a7a5e549007f1ff6468dd142fa8f8eeeff1149cc55be5cc6  reviewed-candidate-hashes.json
```
