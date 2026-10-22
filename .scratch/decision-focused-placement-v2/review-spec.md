# Spec review — needs correction

Reviewed the actual `before/` snapshot diff and all four new Python files; implementation untouched. Model verified through `orca orchestration worker-show --dispatch ctx_09d9f602ed44 --json`: effective launch and live provider are `gpt-6-astra`; terminal preview also reports GPT-6-Astra.

## P2 — Unknown non-common support reintroduces an excluded common coordinate

**Location:** `experiments/decision-focused-placement/policies_v2.py:32–33`.

**Specification:** “그 좌표를 측정 대상에서 제외한다” and “나머지 사용 좌표를 균등 순환한다.”

`obs.any_infinite()` reinstates *every* used coordinate, even when the infinite interval belongs exclusively to a non-common coordinate. Thus a finite global common term is measured unnecessarily; the explanation that infinite common terms block certification does not justify this case.

**Reproduction:** From the repository root, run `rtk proxy python3 -` with:

```python
import sys, copy
sys.path.insert(0, 'experiments/decision-focused-placement')
from test_synthetic_v2 import SHARED_PLUS_DIFF, run, BASELINE
fx = copy.deepcopy(SHARED_PLUS_DIFF)
fx['arms'][1]['support'] = [0, None]  # y only
r = run(fx, BASELINE, max_probes=2)
print(r['probe_sequence'], r['simulated_probe_cost'])
```

Observed `['z', 'y'] 5.0`; the specified exclusion requires `['y', 'y'] 2.0`. The finite common z does not block the runner’s infinite-interval gate. Keep finite common coordinates excluded when non-common intervals are infinite, add this regression case, and reconcile any retained infinite-common exception with the specification. The published 15-fixture matrix does not exercise this defect, so its numerical results are not invalidated by this finding.

## Verified evidence and limits

Independent suite: **68 tests passed in 11.672s**, including the original 52 and frozen-v1 compatibility replay. Existing artifact: strict JSON; 750 completed/0 skipped; 446 certified, 254 probe-budget exits, 50 censored; no planner-budget exits; all 12 source hashes match. Raw-record counts, cross-policy arm prefixes, 250 label-swap comparisons, and joint-certified pairing denominators verified. Source inspection confirms full-placement regret reuse, observation-only policy inputs, fixture values, exclusive output-directory creation, and post-hoc labeling.

No full benchmark rerun or implementation edits. No fallback cases occurred in the frozen swap matrix; its conclusions cannot establish fallback neutrality. Other inspected requirements yielded no actionable Spec findings.
