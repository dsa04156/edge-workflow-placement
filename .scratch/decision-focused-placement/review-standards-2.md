# Final independent Standards review

**PASS: prior P2 resolved; no material standards regression found.**

Scope: read review-task.md, review-standards.md, fix-task.md and fix-report.md; inspected affected implementation, tests and README. Reused the first review’s repository standards inventory and unchanged baseline evidence. Only this report was written; implementation, tests and artifacts were not edited.

- **Pair-level LP cap:** `regret.py:120–122,176–179` computes `len(p.paths) * C(2*d + len(q.paths), d+1)` before any path LP and preserves the simple upper bound when the pair exceeds the cap. This matches `README.md:32` and fix-task requirement 5. Each path constructs exactly `2*d + len(q.paths)` constraints. The regression at `test_regret.py:127` passes: cap 3 returns simple bound 9; cap 6 completes exact bound 9. No partial feasible optimum is promoted to an upper bound.
- **Changed contracts:** p75 validation rejects invalid observations before count mutation; runner certification and paid-probe checkpoints, used-primitive infinity gate, raw batch/censoring/simulated-cost records, and strict JSON/source hashes match the affected README contracts. Synthetic/proxy versus measured/end-to-end claims remain separated. No actionable additional smell was found.

Evidence independently checked:

- `rtk proxy env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s experiments/decision-focused-placement -p 'test_*.py' -v`: **50 passed**, 0.159 s, including tempfile CLI/overwrite protection.
- Corrected results parse with nonstandard numeric tokens rejected: **120 runs, 0 skipped**. All eight recorded Python source hashes match current files. Every run’s record count, arm/rule sequence and summed simulated cost match its aggregates.
- Corrected artifact SHA256: `472a5ade21d4d0a58677e2f2edaf8aedecb08f01ce7b3c053c3c3ded5180e1c2`.
- Original artifact remains `b3849eba2f77cfd61d6baff09f03b18de8514bb186d1e01b4bcdd476ccb5d2a6`. Full non-time benchmark equivalence is reused from fix-report, not independently rerun here.

Unresolved standards defects: **none**. Documented limits remain: checkpoint-based timing permits computation overrun, DKW arithmetic uses floats, and evidence is synthetic only. This is not Spec-axis or cluster approval. Exact reviewer model ID and developer subscription authentication were not independently verified. The developer’s reported early rtk-wrapper procedural deviation remains recorded in fix-report; no implementation remediation is outstanding for it. Orca CLI 1.4.235 bundled orchestration guide was loaded before lifecycle messaging.
