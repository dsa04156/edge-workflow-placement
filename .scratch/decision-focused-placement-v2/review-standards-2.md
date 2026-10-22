# Round 2 Standards review

Scope: `before-fix/` versus `experiments/decision-focused-placement/`; only README, policies_v2.py, and test_synthetic_v2.py differ. Applied AGENTS.md/common instructions, issue-tracker/domain rules, spec/fix contracts, module README, and all twelve smells in review-task.md. No implementation edits or agents.

## New finding

**P3 — README rule-label description contradicts preserved behavior.** In the changed `README.md:83` paragraph, “제외할 좌표가 없으면 … (rule `round_robin`)” omits the infinite-interval condition. `policies_v2.py:37` assigns that label only when `obs.any_infinite() and not excluded`; otherwise finite, non-common cases retain `cancel_common_round_robin`, expressly checked by the added `test_rule_label_without_common_terms_matches_frozen_v2`. The fix contract requires documenting the corresponding rule, and AGENTS.md requires separating verified facts from assumptions. The unconditional documentation can mislead readers interpreting raw rule records. Qualify the sentence with the infinite-interval condition and mention the preserved finite-case label. This is a documentation defect, not a numerical or acquisition-behavior defect.

## Code and preservation evidence

No new code-standard violation or actionable smell found. The small change uses existing observation intervals, preserves runner/statistical boundaries, and adds public-seam regressions without changing fixtures or budgets. All twelve baseline smells were considered; no new abstraction or duplication warrants refactoring.

Reused the recorded **71 tests passed (11.727s)** evidence in fix-report.md; no suite or benchmark rerun. Independently parsed stored JSON strictly and confirmed: all three frozen v1 hashes and original v2 hash match; all 12 final source hashes match disk; 750 final runs equal original v2 in every run-level non-time field; 55,925 raw records remain; intermediate reviewfix output is preserved. Final result SHA256 matches the report. The unchanged matrix does not exercise the repaired mixed finite/infinite boundary; regression tests supply that evidence.

## Historical limitations

Prior P2 ownership/deletion uncertainty and P3 unwrapped-shell history remain unresolved historical limitations, not new fix requests. No new preservation defect was found in this delta or fix report. No cluster claim is made.

Model evidence: Orca 1.4.235 `worker-show --dispatch ctx_fe2d27086008` reports effective launch and live provider `gpt-6-astra`, corroborated by the terminal banner.
