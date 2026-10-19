# Frozen design: topology + node performance profiles for DAG placement

Primary method fixed before execution: sensor-anchored DAG placement minimizing predicted workflow completion time using infrastructure routes/link costs AND task-by-node P75 performance profiles, subject to CPU/memory admission constraints. Up to27 fixed placements exhaustively evaluated; exact search is a small-instance implementation, not the claimed novelty. Keep workflow branch/join semantics in every ablation.

Workload: camera -> image feature -> VQA <- speech recognition <- microphone. Camera fixed E0; microphone E0 or E1. VQA movable or required cloud. Persistent Pods. Public JellyBean CPU1/CPU5/GPU1 P50/P75/P90; light=resnet18+wav2vec2-base, heavy=resnet50+wav2vec2-large; defaultVQA. Input and intermediate sizes public, answer1KiB assumed.

Topology graph (full-duplex directed links, FIFO per direction):
- edge_fast: E0--G 1Gbps/0.5ms, E1--G 1Gbps/0.5ms, G--Cloud WAN/20ms.
- edge1_slow: E0--G 1Gbps/0.5ms, E1--G 5Mbps/5ms, G--Cloud WAN/20ms.
- cloud_hub: E0--Cloud and E1--Cloud each WAN/20ms; no direct edge route. E0 to E1 must pass cloud, without executing there. These are assumed stress topologies, not measured deployments.
Routing minimizes no-queue serialization+propagation for each payload; graph is a tree, route unique. WAN bytes count each traversal of a link adjacent to cloud (cross-edge cloud-hub transfer counts twice). No WAN charging/currency interpretation.

144 conditions: 2workloads x 2sensor placements x 3topologies x 3WAN(2/10/100Mbps) x 2rates(0.1/1rps) x 2VQA constraints. Twelve independent paired seeds56001..56012, exponential arrivals, duration120/rate; initially empty, drain all. No condition exclusion. Only one background resource layout balanced25% to isolate topology/profile factors; CPU capacities4/8/16,memory8/16/32GiB, task requests1CPU+1GiB, idle reservations. Inherited execution model: node-wide single service lane; no background runtime, real CPU core concurrency, GPU admission, cold starts or model transfer. See previous DAG design for limitations.

Four policies:
1 resource_proxy: source-ported Kubernetes v1.35.0 LeastAllocated+BalancedAllocation, random deployment order/ties; not actual kube-scheduler.
2 topology_only: same DAG exhaustive planner with stage-wise average P75 replicated across nodes, real topology and sensor locations. Removes only node-specific performance knowledge, not execution feasibility.
3 profile_only: same planner with real per-node P75 but all transfer costs set to zero; real DAG and resource constraints retained.
4 topology_profile (proposed): same planner with both real modeled topology and per-node profile.
All placements evaluated on identical true modeled topology, performance samples and request traces. Only planner inputs differ. Predicted-cost ties selected with deterministic independent random seed. No future evaluation trace is given to planner.

Primary outcome per-seed mean sensor-to-answer E2E. Secondary p95, WAN bytes, critical-path compute/network/queue, max offered link/node utilization, planning time. Paired seed bootstrap5000, unadjusted95% CI. Relative effect is mean of paired seed ratios, not ratio of group means. Report both and distinguish. Full method need not dominate masked methods under uncertain service times and contention; report regressions.

Validation before run: branch/join unit fixtures, link routing/cloud-hub two WAN crossings, decomposition, resource feasibility, shared evaluation reuse, full planner minimum over feasible placements. Execution scope simulation only; actual kube-scheduler comparison outstanding. Semantica unavailable. Experimental-design skill principles applied (fixed factorial, paired blocks, seed replication).
