# Round 2 Spec review — P2 resolved

Reviewed the Spec contract, first Spec review, fix-task, fix-report, and actual `rtk proxy diff -ru --exclude=__pycache__ .scratch/decision-focused-placement-v2/before-fix experiments/decision-focused-placement` (exit 1: differences). Only policies_v2.py, test_synthetic_v2.py, and README.md differ. Implementation files were not edited.

Model evidence: `orca orchestration worker-show --dispatch ctx_01d52db43732 --json` reports requested/effective and live provider model `gpt-6-astra`; terminal preview also identifies GPT-6-Astra.

## Resolution and independent evidence

`policies_v2.py:30–37` excludes only common coordinates whose current upper bound is finite. Infinite non-common coordinates cannot restore finite common coordinates; infinite common coordinates remain eligible in original arm order. The existing empty-target fallback and finite/no-common rule label are preserved. The unchanged observation gate uses the same INF sentinel. No truth access, runner/statistical changes, fixture/config changes, or implementation scope expansion appear in this diff.

Independent targeted command, from experiments/decision-focused-placement:

`rtk proxy python3 -B -m unittest test_synthetic_v2.CancelCommonBaseline -v`

**9 tests passed in 0.012s.** This includes the exact P2 reproduction (`['y','y']`, cost 2), mixed finite-common w/infinite-common z/non-common y (`['z','y','z','y']`, zero w probes), infinite-common retention, all-finite-common zero-probe certification, partial-path/third-placement non-common preservation, and frozen finite/no-common rule-label compatibility.

No new implementation regression found within the targeted scope.

## Open issue — P3 documentation mismatch

`README.md:83` newly says “제외할 좌표가 없으면 … (rule `round_robin`)”. This omits the infinite-interval condition. With finite intervals and no common coordinates, `policies_v2.py:31,36–37` correctly retains `cancel_common_round_robin`, as the new rule-label regression test confirms. The fix contract requires the README’s corresponding rule description, so qualify that sentence with the infinite-interval condition; otherwise readers can misinterpret raw probe-rule labels. No numerical or policy-action impact.

## Limits

The host’s 71-test result and equality of all 750 final runs’ non-time fields/source-hash verification are supplied evidence, not independently repeated here. No full benchmark rerun. The frozen matrix does not exercise the repaired mixed-infinity boundary; its direct evidence is the targeted tests above.
