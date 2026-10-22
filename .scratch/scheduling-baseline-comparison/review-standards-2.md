# Standards review — second and final

## Hard violations

- **P2 — Environment stdout still persists through inherited capacity collection.** The dedicated scheduler snapshot is fixed, but `run_baseline_comparison.py:270` and `:239` call inherited `capacity()`: `run_budget_placement.py:52` → `run_placement.py:247` → `Session.call` (`run_load.py:50–55`). That path saves complete `kubectl get pods -A -o json` output, including the same scheduler Pod environment, before reducing resource fields. A local probe used `BaselinePilot.capacity()` and the real `Session.call`, mocking only subprocess output with a synthetic scheduler env canary: **`env_persisted_in=["002-pods.stdout"]`**. This remains incompatible with parent `CLAUDE.md:71` (no secrets/tokens/credentials recorded) and implementation-task.md’s “no secret data” requirement. It is an existing inherited behavior exposed by this new collector, not a modification to the original collector. Keep remediation inside the owned new collector: narrowly project the all-Pod capacity response before persistence, retaining required resource-accounting fields and command events. Exercise the actual persistence boundary; the current FakePilot replaces it, so its whole-artifact canary scan misses this path. The verifier’s new filename check only covers scheduler-snapshot stdout.

## Confirmed fixes and heuristics

`projected_call` keeps scheduler stdout in memory and saves only projected JSON; success/nonzero paths and command/phase finish events pass. An additional local probe supplied timeout partial output containing the entire env canary and found **no persisted canary**, including failure/traceback artifacts. The committed timeout fixture uses `raw[:50]`, which does not contain its canary; strengthening that fixture would preserve this regression coverage.

No material heuristic findings. The narrow capture helper and deliberate inherited deployment reuse do not justify broader redesign.

## Evidence and boundaries

Read the four requested review/fix documents, applicable parent/repository rules and code-review skill; inspected all three before-fix/current diffs. All **8,084** tracked baseline hashes remain unchanged; current three source hashes match fix-report.md. This check does not establish the history of unrelated untracked files.

Ran `rtk proxy python3 -B -m unittest discover -s experiments/real-topology-live -p test_baseline_comparison.py -v`: **28 passed**. Probes used temporary directories and mocked subprocesses only; no cluster access or implementation edits. Only this report was written durably. Model provenance: session identifies GPT-6; fix-task.md records host verification of reviewers as gpt-6-astra; no model substitution.
