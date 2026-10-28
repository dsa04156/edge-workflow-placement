# SPEC review R1 — NEEDS WORK

Reviewer model: this terminal’s live Codex process argv specifies `-m gpt-6-astra` (`term_30d3f578-34e6-49ad-9aa6-996128c15c80`). Reviewed only the two new files against `/dev/null`, plus the relevant reused live/worker dependencies. Both current SHA-256 values match `review-hashes.json`; reconstructed additions in `review.diff` exactly match current files.

**P2 — An interrupted measured request is incorrectly reported as unattempted.** `experiments/gpu-mobilint-vlm/benchmark_live.py:193` (and summary accounting at line 239).

Requirement: “첫 실패에서 중단하고 failure/unattempted/partial timing을 보존한다.” The spec also requires “중단·실패 포함 모든 경로” preservation.

`live.call_arm` catches `Exception`, not `KeyboardInterrupt`. If interruption occurs during an HTTP request or before its return, `collect_benchmark` never appends the attempted row. The outer finally saves a summary, but derives attempts solely from ledger length, losing the current measurement ID and call-level partial elapsed time and labelling an already submitted request unattempted.

Local probe: used the existing loopback harness, wrapped the real `live.call_arm` to raise `KeyboardInterrupt` after one worker response, then executed the normal collector entry point. The worker recorded **1 call**, while summary reported **completed=0, failed=0, unattempted=80, stopped_at=[]**, with no benchmark ledger. Cleanup was verified. No cluster operations occurred.

Fix: retain in-flight row identity and monotonic start before calling; on interruption preserve an interrupted/unknown-outcome record with partial collector duration, count it as attempted, and re-raise through cleanup. Add a test interrupting the measured-call path, including no subsequent calls and correct counts; the existing interrupt test only interrupts qualification before any measured call.

Evidence reviewed: developer report and `logs/test_benchmark_live.log` record 10/10 mock tests passing. These mocks do not establish actual accelerator performance. No additional SPEC findings or implementation edits. Real measurements and downstream analysis/slides remain coordinator-owned.
