# Final standards assessment — changes required

Scope: second/final round, final untracked implementation under `experiments/sensehat-placement/` (absent from `43f9d11`), against `implementation-task.md`, AGENTS.md and the prior standards findings. Implementation remained read-only; only this report was written. Exact Astra model identity is not independently attested by this session.

## Resolved

- **Receipt integrity:** `verify.py:43–78` now recomputes canonical payload/output hashes and byte counts, checks each stage run/request identity, links adjacent receipts, and compares actual outputs numerically. `stage_runtime.py:253–268` supplies outputs. `test_execute.py:179–212` covers identity/byte/hash tampering and within/beyond-tolerance drift.
- **Frozen-code provenance:** `forecast.py:215–221` checks all four frozen production sources; `run.py:240–245` rejects mismatches before artifact creation or cluster calls. `verify.py:218–227` reconciles preparation, collector copies, design hashes and shipped runtime. Coverage: `test_run.py:107–125`, `test_execute.py:214–220`.
- **Phase boundaries:** `run.py:345–347,405–420` records separate warmup/evaluation and teardown durations/status, including failure cleanup. `verify.py:275–286` requires these phases. Coverage: `test_execute.py:99–108,222–229,270–294`.
- **Related admission/logging fix:** `run.py:193–221,260–263,294,318` projects observed Pod safety fields and refuses unsafe admission before measurement; `verify.py:90–110,197–215` independently checks them. The sentinel regression covers artifact leakage (`test_execute.py:275–297`).

## Unresolved regression

**P2 — Projected reads lose timeout/failure stdout evidence.** `run.py:174–182` switches projected calls to `stdout=PIPE`, but writes stdout only on return code zero and never processes `TimeoutExpired.output`. Thus a timed-out Pod read leaves an empty stdout artifact even when captured output can be safely projected. This violates AGENTS.md’s explicit timeout-partial-output preservation rule and the task’s partial stdout/stderr contract. Preserve safely projectable failure output and explicitly record unavailable/unparseable projection evidence without persisting raw Pod secrets; cover timeout/nonzero paths.

Evidence: focused Python `-B` in-memory mocked timeout supplied valid JSON output; saved stdout was empty. No files or cluster processes were involved. Inspected final regression tests; reused the developer-reported 34-test pass, without repeating the suite. No additional blocking smell findings. Real-cluster behavior remains unverified; old prepared bundles require fresh preparation.
