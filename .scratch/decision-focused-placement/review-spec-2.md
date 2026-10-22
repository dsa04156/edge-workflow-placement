# Final independent Spec review — changes requested

Reviewed the four original findings and affected contracts against implementation-task.md and fix-task.md. Only this report was written; implementation, tests, and artifacts were not edited. Session identifies as GPT-6; exact Astra runtime variant was not independently verified.

## Fix verification

1. **Invalid observations: fixed.** `p75.py:28–63` rejects invalid support, dimension, delta, and nonfinite/out-of-support uncensored samples before changing counts. Empty intersections remain noncertifiable.
2. **Planner exhaustion: fixed at the agreed checkpoints.** `synthetic.py` checks zero budget, elapsed regret computation before certification, and acquisition time before paid probes. Deterministic-clock tests pass. CPU preemption remains explicitly outside the contract.
3. **Infinite-interval certification: fixed, with one unresolved adjacent case below.** Used infinite intervals block runner certification, including one-plan runs; unused coordinates no longer block exact pairwise cancellation.
4. **Raw observations: fixed.** Saved batches, timeout positions, per-probe simulated costs, explicit bound statuses, strict JSON, and source hashes are present and consistent.

## Unresolved P2

**Unused infinite primitive crashes contrast acquisition.** `experiments/decision-focused-placement/synthetic.py:118,170` converts every interval upper to Fraction, including unused infinity. This incompletely implements fix-task #3’s unused-primitive contract and the public runner’s policy-result contract.

Independent reproduction: deep-copy `test_synthetic.NEAR_TIE`, append `uniform("unused", 0, 40, support=[0, None])`, then call `run_policy(..., "contrast", seed=0, config=cfg(max_probes=1))`. It raises `ValueError: non-finite numeric value: inf`; the other three policies return `budget_probes` with one probe. The new unused-infinite test certifies before acquisition, so misses this path. Minimum correction: fix coordinates unused by both witness placements to a finite value before contrast midpoint arithmetic, and add a public-runner regression requiring acquisition.

## Evidence

50/50 unittests pass, including temporary CLI strict-JSON/hash/overwrite checks. Independently audited all 120 corrected runs and 9,941 probe records: sequences, rules, costs, counts, censor positions, and source hashes agree. Original and corrected configs/fixtures and all existing non-timing run/deterministic/summary fields match. SHA256s match fix-report: original `b3849eba…d2a6`, corrected `472a5ade…e1c2`. Existing independent oracle evidence was reused; no unrelated research or benchmark rerun. No further material defect found in the affected paths; approval remains blocked by the P2 above.
