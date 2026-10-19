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

## Source HTTP transport follow-up

After a real E1execunexpectedEOF, diffd0b0273...346ebff was reviewed on both axes. Inputorigin/preparedcache/sourceJSONL/idempotentstart/incrementalpoll/drainwereconfirmed. Spec noted profile retries might create multipleobservations; c9e8009 cachesbyidentity+signature,waitsoriginalevent,andretainsfailureswithoutnewinference. A new HTTPtestfailedthenpassed. Spec recheck found no blocker. [load-local-validation-v2.json](load-local-validation-v2.json):9tests pass. HistoricalEOFrootcause remains unconfirmed; HTTPmitigation is not a rootcause claim.

## Native queue observer follow-up

The original recursive summary missed pyroute2 `nla_slot` values. Retained native snapshots allow independent extraction of real backlog/qlen; summary zero alone is insufficient evidence. The observer-only fix reads typed `get_attr('TCA_STATS')`, fails closed if missing, and records per-qdisc fields. No shape/route/model/source setting changed. Mounted revision is proven separately from ConfigMap application; raw captures identify v2. Parent/child counters are not summed as an independent queue-byte estimate. Final verification checks the last two native captures for every phase.

Final analysis review also replaced an E2E-only plot's “queue growth” label with “latency by request” and annotates failed-request denominators. Protected-resource observations are inspected separately; verifier success does not imply an unchanged external cluster.

## Final result verification

Run `wan-20261019t103603` completed all33 phases/606attempts:597success,3deadlineerrors,6driver_capacity. Offline verification passed with1791successful receipts and0issues. Independent native audit v2 checked66last-two captures:eachactual backlog/qlen0;10v2captures also matched typed summaries to raw fields. Final report explicitly records3external nonready Pod UIDs absent at end, not an unchanged-cluster claim. Cleanup owned resources0/errors0.

[Standards final review](load-final-standards-review.json) independently checked32completed phases before the last finished. Final33phase outcome is covered by verification-v1 and backlog-native-audit-v2 under the run directory; no broader approval is implied.

[Final spec review](wan-20261019t103603/spec-review-final.md) independently recomputed all33 phases/606attempts, all12high-load table rows,1791successfulreceipts and66native drain captures. No remaining blocking Spec finding. Both review axes are complete within their recorded scope.
