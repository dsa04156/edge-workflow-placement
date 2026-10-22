# Host validation

2026-10-21: `rtk proxy python3 -m unittest test_forecast test_stage_runtime test_run test_execute` in experiments/sensehat-placement: 26 tests OK, 17.447s. Fake kubectl only; no cluster performance claim.

Pre-deploy host finding: run.py protected-before/after calls get pods -A -o json through Recorder, persisting raw unrelated Pod specs before projection. Must use safe server-side output projection; do not deploy this version.

## Independent review blocked
Both effective gpt-6-astra Orca workers exited their turns without delivered worker_done: ctx_3962a1549c36 (Spec), ctx_3397a90fdc39 (Standards). worker-read transcripts prove STOP / INTENT PreToolUse rejected rtk proxy pwd/cat and rtk read with MUTABILITY_UNPROVEN; report write MODE_FORBIDS_MUTATION; lifecycle send also rejected. No independent review occurred. Stopped exact workers after transcript inspection under Orca recovery guide. No cluster deployment was performed. Host raw-Pod-log finding remains unresolved; do not execute current runner before fix and independent review.

## Resume after user go (2026-10-21)
Opus fix dispatch ctx_7f8c57afe3fa succeeded/released; developer27 tests OK. Host `python3 -m unittest test_execute`:7 tests OK17.133s. Actual kubectl JSONPath projection: exit0,358 rows,all5columns, output in host-projection.stdout. No full Pod specs collected in this check. Reviewer embedded directive did not change review mode; documented first-line report-creation contract sent directly to existing active review sessions, implementation read-only and no guard config changes.

## Actual cluster result
Host full34tests OK20.137s, later timeout3 and affinity1 targeted OK. Last developer affected suites22OK. Final prepared prepared-20261021t1146. First run114403z admission failure, zero measurement/remaining0. Second run114702z rc0,36/36 requests verified,3unique placements9evaluation each. Read-only verifier exit1 solely protected-workload changes:3existing Pods in ebpf-baseline-main-20261021-0 absent after; no attribution. Owned11resources removed/errors0/remaining0. Never claim global verifier pass. Stats and mutation command audit stored run/host-analysis.json. All8 resumed Orca workers released, reclaimable0.
