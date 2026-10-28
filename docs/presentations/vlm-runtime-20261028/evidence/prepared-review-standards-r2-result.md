# STANDARDS final round 2 — PASS for the two P1 fixes

Both prior P1 code findings are closed; no new blocking code defect found in this bounded fix review. Actual Orca terminal preview confirms **GPT-6-Astra high**. Reviewed the real two-file diff against `review-source-sd-r1/`, the current implementation, test changes and red/green logs after coordinator completion mail.

- **P1 fsync boundary: fixed.** `vlm_runtime_cache.py:336` now gates each individual flush through `timed()` (`:325`), including the post-call deadline/statistics/busy check. The write-triggered sync cannot return to another chunk after that check fails. Latency feedback is per call. Tests exercise missing statistics, deadline expiry and busy-pause recovery between fsyncs and before the next write.
- **P1 stale cleanup: fixed.** `vlm_runtime_cache.py:551` starts the controller and shared budget before `remove_stale()` (`:391`). Each unlink/rmdir is gated, progress is published, symlinks are unlinked without following them, and cleanup failure exits before tar transfer/new staging creation. Tests cover nested leftovers, outside-symlink preservation, pause/resume, invalid statistics and deadline failure.

Verified current hashes: runtime `33c019700b08d6c501e336429cd53ecbd057d2e4ef4f3778b0a1c4cb2d669eb2`; tests `37b8801ea9cad698609b04b36b6ed2f08d89ed62398cc673142935a74bfe7ddc`. Collector, prepared bootstrap and builder still match round 1; protocol, CUDA gate/stage, worker and original tests match the source baseline. Prepared manifest remains `d8d0bcaed84afc0a1a1882310d14b0e6056bb0ff7dba9f104355336031899e8d`.

Inspected evidence: **54/54 pass**; the six new tests against the reviewed implementation produce five assertion failures and one missing-progress-field error. Existing assertions were retained. No suites or 8 GB validation were repeated; no cluster access or implementation edits occurred.

Operational limits remain explicit: regenerate the release draft for the new runtime hash; cleanup consumes the same budget and may exhaust it. Directory enumeration and the single post-publication parent fsync (`:584`) remain outside feedback, and kernel calls already executing cannot be interrupted. These are not evidence of measured SD performance or target CUDA/NPU qualification. The coordinator still owns the monitored run.

Process note: `fix-report.md` discloses commands bypassing the required `rtk` wrapper. This is a recorded execution-policy deviation, not repaired by passing tests; subsequent commands must follow the wrapper rule. No additional code refactoring is requested.
