# Independent review of minimal baseline comparison

Read-only implementation review. You are not alone in the codebase; preserve all edits. Do not edit implementation, run cluster tools, commit, push or change config. Shell through rtk/rtk proxy. Your only writable file is the assigned review report below. Use code-review skill and applicable AGENTS.md; a second independent reviewer owns the other axis.

## Fixed point and exact scope

All three implementation files were absent at task start. Inspect their complete added-file diff (`rtk proxy git diff --no-index -- /dev/null <file>`, exit1 means diff) rather than treating all unrelated untracked repository content as new. There are no commits for this change. Tracked source baseline hashes are .scratch/scheduling-baseline-comparison/before-tracked-hashes.json. Assigned new files:
- experiments/real-topology-live/run_baseline_comparison.py
- experiments/real-topology-live/verify_baseline_comparison.py
- experiments/real-topology-live/test_baseline_comparison.py

## Spec axis

Authoritative .scratch/scheduling-baseline-comparison/implementation-task.md (reduced user scope supersedes larger docs); developer-report.md for commands, inspect actual code and tests. Report missing/partial requests, scope creep, wrong semantics; cite spec and code. Especially real unpinned scheduler, physical admission without inherited slot2, fresh calibration subset, equal policy candidates, no double-counted shared observations, retained failures, honest warm timing vs actual scheduler selection, validator must reject missing/wrong identities and no forged success. Write review-spec.md in this directory, under400 words, prioritized concrete findings or none. No reimplementation.

## Standards axis

Sources: /home/jinuk/codex-work/AGENTS.md, repo AGENTS.md, docs/agents/domain.md, issue-tracker.md, triage-labels.md; discover relevant CONTRIBUTING/CODING_STANDARDS if present. Verify ownership, rtk usage, preserved artifacts, reproducibility and control/cleanup safety.
Heuristic smell baseline: Mysterious Name (unclear name -> rename); Duplicated Code (same logic -> shared shape); Feature Envy (other object data -> move behavior); Data Clumps (same fields -> bundle); Primitive Obsession (missing domain type -> model concept); Repeated Switches (same cases -> consolidate); Shotgun Surgery (one change many modules -> gather); Divergent Change (unrelated responsibilities -> split); Speculative Generality (unused abstraction -> remove); Message Chains (caller navigates internals -> hide); Middle Man (pure forwarding -> direct boundary); Refused Bequest (subclass overrides most behavior -> consider composition). These are judgment calls, not hard violations; repo standards override and small controlled reuse may be better than refactoring. Skip tooling-enforced style. Write review-standards.md here, under400 words, separate hard violations/heuristics with code+rule evidence.

Each worker performs only its assigned axis and sends worker_done once with report path. May run the targeted local unittest suite if useful. Report actual model provenance; never claim test/cluster execution not done.
