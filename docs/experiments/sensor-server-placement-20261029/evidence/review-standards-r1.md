# PASS — Standards review, round 1

No blocking standards regression found in the pinned candidate. References below are relative to `staged/experiments/gpu-mobilint-placement/` unless stated otherwise.

Reviewed root `AGENTS.md`, corrected `spec.md`, the complete actual diff, changed tests, `developer-report.md`, `test-evidence.json`, and release artifacts. Reconstructed diff exactly matches `review.diff`; all 130 candidate hashes match `review-hashes.json`, and all 36 release code hashes match the candidate. Orca 1.4.235 bundled orchestration guide loaded; current worker terminal identifies GPT-6-Astra high. No model substitution, cluster calls, Git, implementation edits, or test reruns.

Verified standards:

- Whole-job scope is enforced by `chain_design.py:90`, `chain_policy.py:48`, and `chain_generator.py:342`; aliases and four-job limit preserve one observation per condition/server. The new tests cover mirrored costs, invalid measurements, and rejected mixed placements.
- `chain_live.py:474` targets only N through the unchanged net controller. Gateway manifest `:132` confines NET_ADMIN to its Pod; stream guards `:228` require recorded ownership and a fresh UID/node check. Qualification `:583` checks colocated routes, gateway counters, and shaped-band byte deltas before plans freeze at `:637`.
- Source-bound release retains dynamically loaded `G.__file__` at `chain_live.py:179`. Device/model identity verification, foreign-resource refusal (`gpu-mobilint-vlm/live.py:315`), drain barriers, and UID-owned cleanup (`chain_live.py:825`) remain intact. Unchanged `multi-edge-sensors/collect.py:181` preserves timeout stdout/stderr and completion/error events.
- Analyzer distinguishes whole-server selection, experimental shaping, BF16/W4V8, shared observations, and unmeasured quality; significance comparisons are disabled at `chain_analyze.py:272`.

Nonblocking factual correction: `chain_policy.py:8` calls probes the “EXACT raw NPY payload,” but `chain_generator.py:243` sends `os.urandom(size)`. Describe these as size-matched synthetic payload probes over the actual route; this does not change the measured uncompressed transport cost or the real-input evaluation.

Existing transcript-derived evidence records 65 tests passing in 11.341 seconds; dry-run reports 36 bound code files. Live route/device behavior remains for coordinator validation. No consequential code-smell finding warrants expanding this limited diff.
