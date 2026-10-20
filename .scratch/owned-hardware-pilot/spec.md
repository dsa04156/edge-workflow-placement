# Owned hardware automatic placement pilot
Status: ready-for-agent

User authorized bringing latest Git work and running experiments; explicitly prioritized automatic placement and confirmed tests at profile→plan/Pod and HTTP DAG→receipts/cleanup seams.

Integrate docs branch 0a03487 and live executor 4dc7550 in isolated worktree; preserve original artifacts. E0/E1/C CPU only; current Jetsons NotReady. Fusion movable on all quality-passing nodes. Respect current requests, including helper and netctl requests; no existing workloads modified.

First stage is an execution/calibration pilot, not the five-block confirmatory study. One time block; freshly deployed selected operators only (no unselected model Pods). Fixed image E0/audio E1; same models, one Torch thread, 1 CPU/2GiB per operator plus common 10m/32Mi netctl. Actual default-scheduler shares candidates. Policies: server-only, greedy no look-ahead, HEFT serving-adapted, full exact DAG, no-network, no-node-profile, kube-default. Exact enumeration is a reference selector, not new algorithm. HEFT's static rank/EFT heuristic is adapted to independent per-stage services; not reproduction of a node-exclusive processor scheduler.

Qualification: all 60 existing inputs at each task×node; first20 calibration only, last40 evaluation. Warm5 per task before qualification. p75 compute and payload echo RPC cost proxies collected only using calibration inputs; include measured colocated paths. Echo costs include serialization/HTTP response and are not one-way link time or exact /execute,/join timing. Include two source↔fusion small control exchanges in the prediction as an explicit proxy. No queue/link concurrency prediction. Frozen plans before evaluation; no evaluation feedback into profiles.

Pilot conditions: unlimited relay/no delay, shared100/10/2Mbps with20ms per direction. Compressed input primary; raw100/10/2 separate representation diagnostic. Existing network cap/bypass/drain contracts reused. Source-local openloop0.1rps; warmup2 calibration requests, first6 heldout evaluation requests per unique placement/condition, seed20261020. No inferential claims or p95 robustness claims from six requests. Same placements share actual observations. Baseline scheduling/selected deployment durations separately retained. One block cannot estimate block variance; five-block study, S3/source-position, GPU S4, and stress S5 remain later stages.

Before deployment: existing16 tests; targeted red/green public tests for transfer observation idempotency and policy/resource contract; code-review against integrated base. Save source/hash, phase timestamps, partial stdout/stderr, plans, observations, actual UID/node, all failures, cleanup/protected workload diffs. Generate result report and durable Semantica record.
