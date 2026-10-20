# Completion attempt evidence, 2026-10-20

The experiment is **incomplete**. `partial-verification-v1.json` passes only the
observed first-block evidence and preserves five full-run failures. The second
block and final restored-connectivity proof are absent.

- `fresh-single-move.json`: post-hoc cost-only diagnosis, no new measured policy.
- `previous-partial-single-move-v2.json`: earlier profiles with implementation
  provenance checked. The first version is historical, superseded by v2.
- `negative-audit.json`: separate-copy score corruption was rejected; original
  evidence hash unchanged.
- `final-evidence/`: selected original summary, plan, cleanup and error files.
- `raw-evidence-manifest.json`: SHA256 for all files in the three new runs.
  Raw files/model bundles remain local; they are required for complete reaudit.
- `unvalidated-worker-fallback.patch`: inactive candidate, not applied to the
  default collector. Live diagnostic stopped after two of six worker proofs.
- `diagnostic-prototypes/verify_control_receipts.py`: exploratory audit utility.
  An earlier version verified111 completed controls during the run. No final
  whole-run pass is claimed; this utility is not part of the active collector.

Final Standards and Spec reviews found no blocking code/report findings after
unvalidated fallback/reuse changes were removed. Reviews confirmed incomplete
status, no pooling of earlier observations, and separation of measured effects
from the swap-plus-relocation cost-table diagnosis. They do not resolve the
remaining E1 exec reliability blocker.

Local verification:23 runtime tests passed on the active collector; observed
24/24 evaluations and96 receipts passed the partial audit. Diagnostic081744
completed3guard/restore cycles and4receipt replays. Later diagnostic085217 failed.
All three new runs ended with remaining_owned0 and cleanup errors[].
