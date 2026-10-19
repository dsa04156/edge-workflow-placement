# Frozen design: DAG placement, 2026-10-19

Question: Does sensor-location and per-operator performance aware placement reduce fork/join completion latency against a resource-score proxy and location-only placement?

Topology: camera -> ExtractImageFeature -> VQA <- SpeechRecognition <- microphone. Camera fixed E0. Microphone fixed E0 or E1. Output returns E0 (1KiB assumed). Sensors generate one synchronized request pair at each arrival. These are two independent branches and one AND-join, not a serial chain. Three persistent task Pods, one replica per task; placement fixed during each replay.

Public profiles: JellyBean P50/P75/P90 CPU1, CPU5, GPU1; light=resnet18+wav2vec2-base-960h, heavy=resnet50+wav2vec2-large-960h-lv60-self; default VQA. All three stage timings and input/intermediate sizes are public measurements. No fabricated preprocessing stages. Simulation lognormal fitted from P50/P90; planning P75. Input distribution of profile source is not guaranteed to represent real sensors.

144 full factorial conditions: workload light/heavy x sensors same/split x WAN 2/10/100Mbps x rate0.1/1rps x background resource layout balanced/cloud-free/edge-free x VQA movable/cloud-fixed. Twelve paired seeds45001..45012. Duration120/rate, drain all. CV=1 arrivals; this study isolates location/performance rather than burst control. Finite replay initially empty, not steady-state estimation. Record unstable offered loads.

Assumed nodes E0/E1/cloud capacities CPU4/8/16 and memory8/16/32GiB. Every task reserves1CPU+1GiB. Background reservation fractions balanced=[.25,.25,.25], cloud-free=[.75,.5,0], edge-free=[0,.5,.75]. Reservations constrain admissible placements but are idle (no execution contention). CPU/memory numbers are synthetic scheduler inputs, not actual JellyBean machine specifications. Identical resource constraints for every method. Public GPU runtimes assume GPU-accessible execution at cloud without GPU resource accounting or exclusive device requests; NOT a deployable hardware specification.

k8s_resource_proxy: Python port of v1.35.0 NodeResourcesFit LeastAllocated CPU/memory integer scores + NodeResourcesBalancedAllocation, default weights1+1; sequential reservation and random tie-breaks. Six Pod deployment orders uniformly randomized independently of arrivals. Not real kube-scheduler. No taints, volumes, preferred affinity, image cache difference, controller/service selector topology grouping or other nonconstant scores modeled. Bare-Pod-like resource scoring scenario; no claim of general default Kubernetes equivalence.
location: prefer feature at camera, speech at microphone, VQA at camera if movable; fallback resource scoring. This is a location heuristic, NOT an exact NodeAffinity plugin reproduction.
compute: globally minimum sum of P75 execution times under constraints, ignores links.
network_greedy: topological per-task earliest completion based on predecessor predicted finishes and transfer, resource fit; ignores future tasks and contention.
dag_joint: enumerate all feasible placements (up to27), simulate one P75 request with fork/join and link/node serialization, minimize predicted E2E. No future evaluation arrivals or samples. A prototype exhaustive optimizer, not a new scalable algorithm.

Execution assumptions: one serial worker lane per node (including GPU node), FIFO links per direction, distinct node upload/download links; edge links1Gbps/.5ms each, cloudWAN chosen bandwidth/20ms each. Cross-edge routes use both edge links; cloud path uses edge+WAN. Multiple branches can run in parallel on different nodes. Single worker per node is a simplifying runtime assumption distinct from CPU admission capacities. No background execution, cold start, model-loading, placement overhead in E2E. Record planning time separately.

Primary mean E2E per-seed; secondary p95, WAN bytes and critical-path compute/network/queue decomposition. Record join waits separately, never sum both branches as E2E. Paired seed bootstrap5000 unadjusted95% intervals. No condition selected or dropped based on outcomes. No pooled request-level significance. Report all conditions and improvements/regressions. Analysis exploratory, no power claim.

Verification: source-port score fixtures, resource-fit, branch parallelism and join barriers, identical-plan reuse, decomposition, exact min of predicted costs. Preserve previous experiments. Semantica unavailable.
