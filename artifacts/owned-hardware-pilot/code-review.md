# Predeployment review
Base: 0c327c525ba66a6945d653bf20d72d9317c15393. Initial implementation: 00a00d6.

## Standards
Reviewer found one P1: restore loop probed connectivity before the opposite endpoint removed its blackhole route. Fixed with separate restore-all and probe-all loops, matching existing Session behavior. No other documented-standard violation reported.

## Spec
Reviewer found one P2: scheduler version/config provenance absent in runner. Added full scheduler Pod capture; command lacks explicit --config in current preflight, so plugin/weight defaults are inference from v1.31.14, not introspected active configuration.

Colocated echo originally used the same helper Pod. Replaced with distinct per-node sink helpers so same-node calibration crosses Pod interfaces. Their requests are included in capacity. Echo endpoint remains an explicitly documented proxy for actual stage forwarding, not pure network time.

Also corrected final-output proxy from final-payload echo + two control echoes to final-payload echo + one open-control echo to avoid counting result-fetch control twice.

Totals on initial review: Standards1, Spec1; both fixed before deployment. No algorithmic novelty or formal repeated-block result claimed.

## Incremental frozen-evidence review (b42dcca...3669c8b)
Standards reported3 findings; Spec reported4, with two shared findings. Fixed before using the audit/report: require every warmup phase; distinguish zero-success execution from infeasible planning; audit per-condition configuration and forbid cross-condition label reuse; reconcile summary means/counts/failure ledgers with raw rows; validate every bypass/restore output and expected per-deployment coverage. Report now refuses a non-passing audit.

## Config lifetime mitigation review (c514f41...72078d3)
Both reviewers found no blocking startup syntax or bootstrap compatibility defects. Two P2 gaps were corrected in cfca3dc: inherited control snippets now import from the frozen worker directory, and netctl readiness requires a marker written only after code verification and dependency extraction. Five generated Pod manifests compiled locally; all12 runtime tests passed. The missing ConfigMap file root cause remains unconfirmed.
