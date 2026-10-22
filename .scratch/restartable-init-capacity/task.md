# Restartable init capacity fix

## Goal
Fix failure in wan-20261022t050417 at capacity() after 324/324 quality pass, then coordinator reruns Ready9 experiment.

## Ownership
Developer owns experiments/real-topology-live/run_placement.py, test_baseline_comparison.py, narrowly needed run_baseline_comparison.py projection/preflight, focused tests, .scratch/restartable-init-capacity/ and docs/experiments/restartable-init-capacity.md. Other agents share tree: preserve unrelated edits and all frozen artifacts. Fixed review baseline is before/ snapshots, not repository HEAD with unrelated changes.

## Design
Use Kubernetes v1.31.14 pkg/api/v1/resource/helpers.go PodRequests default spec requests semantics. Per resource: steady = regular sum + all restartable init sum; startup peak = max over ordered init stages(current init + earlier restartable init sum); request=max(steady,startup peak)+overhead. Preserve normal init handling, zero/missing requests, terminal/unassigned exclusion, negative remaining values and CPU/memory units. Do not count limits. Preserve privacy projection and restartPolicy/order. No speculative Pod-level/resize support; document limits. Existing public capacity() seam is approved by prior implementation/tests; red-green tests at actual raw/projected capacity boundary, not helper-only mirroring. Test sidecar order (early ordinary init must not overlap later sidecars), several restartable init, resource-specific peaks, overhead, regression ordinary init, real failed snapshot.

## Acceptance
First reproduce old failure from saved projected node/pod snapshots without cluster changes; focused test red before code then green; run unittest discover in experiments/real-topology-live. Read-only current Ready9 capacity probe must pass before expensive qualification. Add inexpensive early capacity preflight to explicit topology collection only if needed to prevent repeating expensive qualification on deterministic failure; retain later capacity snapshot used for planning. Update phase verifier/tests if adding phase. No resume/reuse of old compute profiles.

## Constraints
No benchmark by developer; coordinator starts after review. No operational workload/host changes, dependency installs, commits/push/merge, or model substitution. Official Claude subscription only; confirm auth and actual model. All shell commands rtk/proxy. Read relevant AGENTS/GLOSSARY. Source https://raw.githubusercontent.com/kubernetes/kubernetes/v1.31.14/pkg/api/v1/resource/helpers.go lines46-118. Save exact delta against before/ plus test evidence/report.
