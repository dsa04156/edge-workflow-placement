# Final SPEC review round 2 — PASS for the two P1 fixes

Actual model `gpt-6-astra` verified from this session’s turn context. Reviewed `fix-report.md`, actual source, and the diff against `review-source-sd-r1/`; `fix.diff` matches the actual diff byte-for-byte. Runtime SHA-256: `33c019700b08d6c501e336429cd53ecbd057d2e4ef4f3778b0a1c4cb2d669eb2`; tests: `37b8801ea9cad698609b04b36b6ed2f08d89ed62398cc673142935a74bfe7ddc`. Other three reviewed source hashes remain unchanged.

- **P1 #1 resolved:** `experiments/gpu-mobilint-vlm/vlm_runtime_cache.py:169,325–337`. Each pending fd now runs through individually timed, pre/post-gated flush. Missing diskstats, a busy transition, or an expired deadline during fsync is handled before another fsync/write starts. Per-call latency now drives backoff. A running kernel syscall remains non-preemptible.
- **P1 #2 resolved:** same file `:391–408,551–569`. Cleanup shares the fill deadline/controller and removes one entry per gated operation, including symlink-safe unlink. Failures leave unpublished remnants, report `cache_cleanup_failed`, and do not open the tar or create fresh staging. Removal progress is observable and counts toward the stall timer.

Independently ran the six new regression tests with `rtk proxy python3 -B -m unittest -v` in `experiments/gpu-mobilint-vlm`: **6/6 passed (0.188 s)**. These cover per-fsync backoff, missing stats, deadline expiry, busy recovery, per-entry cleanup, and cleanup interruption. Inspected developer evidence: **54/54 green**, and the same six regressions fail against the reviewed version. Existing evidence covers cache hit/integrity, lock/path behavior, pacing/unsynced bounds, non-SD recovery, release binding and old-mode plans. No new blocking regression found in this fix scope.

Operational limits: regenerate the release/plan for the changed runtime hash before execution. Cleanup consumes the existing 10800-second budget, so completion is not guaranteed. Directory enumeration and the final parent-directory fsync (`:584`) remain outside adaptive instrumentation; this review does not certify every filesystem operation as sampled. Actual SD latency/health and target runtime loading still require the coordinator’s monitored run under the explicitly permitted SD profile.

Only this report was written. No implementation/cluster changes, full-artifact rebuild/copy/hash, or device execution.
