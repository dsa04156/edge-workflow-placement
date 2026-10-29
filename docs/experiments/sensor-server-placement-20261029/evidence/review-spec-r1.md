# NEEDS WORK — Spec review r1

Reviewed the pinned `review.diff`, actual candidate source against `.scratch/gpu-spark-dag-20261029/staged`, active AGENTS scope, specification, developer report, release outputs, and developer transcript. All 130 candidate hashes match `review-hashes.json`; the transcript records **65 tests in 11.341s, OK**. No cluster calls, test reruns, implementation edits, or Git operations.

References below are relative to `staged/experiments/gpu-mobilint-placement/`.

1. **P2 — Probes do not carry the specified actual payload.** The spec requires “actual payload probe HTTP RTT via same route as request.” `chain_generator.py:243–248` sends `os.urandom(size)` from S, while `:253–254` requests size-only worker probes. These are real, same-route, payload-sized measurements, but not transfers of the frozen NPY/PNG/VLM bytes. Nevertheless, changed `chain_policy.py:7–10` claims “EXACT raw NPY payload” and `chain_analyze.py:47–48` reports “actual payload.” Use the frozen source input and retained same-host profile outputs for probes, recording/verifying their hashes. The existing byte-count test cannot establish payload identity. This is an inherited mechanism that does not meet the new explicit requirement.

2. **P2 — Ingestion bypasses the new invalid-cost guard.** The spec requires “missing/invalid costs fail closed.” `chain_policy.py:147` converts probe medians to float before `_cost` can reject booleans; `True` becomes valid `1.0`. Similarly, `chain_live.py:754` converts receipt timing values before validation, accepting `True` or a numeric string. The invalid-value tests inject already-built tables and miss these real ingestion boundaries. Validate raw measurements before conversion and cover ingestion → decision rejection; retain the existing finite/positive checks.

Otherwise the reviewed implementation matches the intended scope: GGG/NNN-only selection and qualifications; measured processing plus local HTTP and source transfer costs; both servers through the same gateway, N-only shaping; pre-evaluation frozen decisions, shared aliases, four main jobs; measured E2E distinct from predictions; honest baseline, precision, quality, topology, and singleton-statistics limits. The source/GPU/NPU identity paths, occupancy refusal, UID cleanup, and release dependency inclusion of dynamically loaded `G.__file__` are retained. Main-route failures remain visible as recorded evidence rather than being silently discarded. No unrelated scope expansion found.

Live execution remains coordinator-owned; this review is not live acceptance.
