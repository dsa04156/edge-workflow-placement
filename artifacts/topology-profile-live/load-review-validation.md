# Shared WAN load review

Fixed base46fd850; implementation62a3cca, fixes67718a8. Spec: [.scratch/edge-load-validation/spec.md](../../.scratch/edge-load-validation/spec.md). Two parallel reviewers, followed by independent rechecks of fixes.

## Standards

- P1: drain checked model queues only; source child RPC/gateway/kernel traffic could cross phase boundaries. Fixed: wait for child RPC completion, expose all HTTP activity/pending joins and kernel backlog; require two idle observations, otherwise abort.
- P2: E1 audio first-use reading/hashing entered timed interval. Fixed: preload/hash all60 audio fixtures before source readiness; preserve readiness evidence.
- Recheck: both resolved, no remaining blocker found. NET_ADMIN/root confined to owned Pod network-control containers, no host namespace/mounts/privileged mode.

## Spec

- P1: stop/drain incomplete. Same complete-path correction above.
- P2: E1 input-ready boundary incomplete. Same preloading correction above.
- P2: route restoration lacked connectivity probes. Fixed: successful direct health probes on every source/operator after blackhole removal.
- Recheck: all three resolved; no new blocking issue found.

## Validation and limits

Seven HTTP/open-loop seam tests pass; command/stdout/stderr/hashes: [load-local-validation-v1.json](load-local-validation-v1.json). KernelAPI/server admission dry-run is separate from actual network preflight. Deadline120s governs acceptance; terminal wall time may include child RPC cleanup afterwards, and scheduled-to-terminal/deadline-exceeded are recorded. Runtime FIFO is per operator Pod. Single deployment diagnostic does not establish six-policy superiority or dynamic optimization.

Summary: Standards2findings fixed; Spec3findings fixed. Each axis' worst finding was incomplete drain.

## Serving-thread follow-up

A later source-backed audit found main-thread configuration did not directly establish serving-worker OpenMP/MKL state. [thread-context-audit.json](thread-context-audit.json) preserves separate synthetic Torch probes, official sources and read-only Pod process observations. The partial first attempt `wan-20261019t100804` was intentionally interrupted and fully cleaned; no assertion that its model actually used >1 threads is made. Its performance is not pooled with retry. Worker now sets intraop1 once, asserts intraop/inter op1, records native ID+UTC+effective settings before readiness. Every real receipt must confirm servingthreads1. New model calibration and all evaluation are rerun.
