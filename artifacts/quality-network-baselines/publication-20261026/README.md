# 2026-10-26 published diagnostic evidence

This publication includes experiment source/tests, research and result documents, the frozen plan/catalog/load specification, per-cell pilot/compare analysis, collection/cleanup status and a hash inventory of local raw artifacts. Model weights, corpus media, dependency packages, full raw HTTP/Pod logs and orchestration scratch work are not bundled. The JSON analysis is an aggregate; raw evidence remains local at the listed paths and SHA-256 hashes. Reproducing collection requires the cluster and separately prepared assets named in the plan, not merely a repository checkout.

- Actual inference: x86/ARM CPU, Jetson CUDA GPU, Intel NPU. Mobilint device allocation succeeded previously but this VQA model has no qualified MXQ artifact/compiler path; Mobilint did not execute these comparisons.
- Pilot: 36 cells / 666 arrivals.
- Compare: 70 collected / 72 planned cells, 6,672 offered arrivals; 69 cells before OOM, one fault-affected cell, two skipped. CPU worker OOM at 05:55:26 UTC is retained, not excluded silently.
- All performance is diagnostic: reused-pair service quality .355 < Qmin .40. No named-paper reproduction or quality-qualified superiority claim.
- Tests before source freeze: coordinator integration 103 passed; affected round-2 tests 16 passed; developer heterogeneous 88 and split-stage 5 passed. All 28 frozen source-hash entries matched after collection. Offline independent analysis matched developer output exactly. No new live run during publication.
- Cleanup: both runs zero owned Pods/ConfigMaps remaining; protected-resource audit partial because other workloads changed concurrently.

See [result report](../../../docs/experiments/quality-network-baseline-results.md). Relative paths in older research/provenance documents can refer to local-only scratch or asset files; they do not imply those assets are included here.
