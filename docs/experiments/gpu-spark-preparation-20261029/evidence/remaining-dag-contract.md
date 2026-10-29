# Remaining GPU/Mobilint DAG comparison

User scope: heterogeneous compute/transfer-aware placement, one measurement per unique placement; exclude dev0009. Mobilint launch is held until the user reports it ready. No Resource Advisor experiment.

Replacement GPU: ser0003 NVIDIA GB10, ARM64 CUDA 13.0 driver, explicit existing RuntimeClass nvidia-spark, resource nvidia.com/gpu:1. Initial allocation without RuntimeClass exposed no libcuda and was cleaned; RuntimeClass probe verified cuInit=0 and GB10. Main smoke is one request and must not be treated as a warm latency profile.

The prior dev0009 release is invalid for these hosts. New frozen hardware/contract/plan is required. Do not reuse dev0009 PVC affinity, prepared-cache verification, or old measured profiles. Preserve model/input/generation settings and calibrate changed runtime/host. GPU BF16 and Mobilint W4V8 remain different numeric configurations, with quality unevaluated.

Source remains dev0003 if Ready. ser0003 is a server, not an edge GPU; do not retain a physical edge label for it. Revise route/zoning definitions before relay/bottleneck comparison and report imposed logical links honestly. Native transfer preparation is separate from shared-link contention qualification.

Current GPU-only execution and transfer probes provide readiness/baseline evidence, not a policy comparison, p95, goodput, or proof of superiority. No Mobilint pod creation or modification before user readiness notice.
