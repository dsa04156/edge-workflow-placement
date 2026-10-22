# Standards review

## Hard violations

- **P2 — Scheduler provenance persists environment values before sanitization.** `experiments/real-topology-live/run_baseline_comparison.py:230` requests complete scheduler Pod JSON through `Session.call`; `run_load.py:50–55` writes that response to `NNN-scheduler-snapshot.stdout` before `scheduler_provenance()` removes `env` at line 231. If a scheduler container has a literal credential/token environment value, the new collector preserves it in the experiment artifacts. This violates `/home/jinuk/codex-work/CLAUDE.md:71` (“비밀정보·토큰·서버 자격 증명 기록 금지”), applicable through the parent AGENTS instructions. The report’s claim that environment values are not saved is consequently inaccurate. A local probe using the real `Session.call` and mocked subprocess reproduced `raw_stdout_contains_canary=true`, while the sanitized summary omitted the same synthetic canary; no cluster or real secret was accessed. `test_baseline_comparison.py:256–260` checks only the sanitized return value and replaces the logging boundary. Project the required fields before persistent stdout capture, and test the complete persisted artifact set, including partial output, for excluded values.

## Heuristic baseline

No actionable heuristic findings. In particular, the explicit `Pilot.__init__` / `Pilot.deploy_operators` calls in `run_baseline_comparison.py:202–215` resemble possible Refused Bequest, but deliberately preserve BudgetPilot’s journaled controls while removing its unrelated slot constraint. The documented preference for small, controlled reuse takes precedence; a broad inheritance refactor is unwarranted here. Reviewed the remaining named smells without material findings.

## Evidence and scope

Read all three added-file diffs, developer report, parent/repository instructions, and domain/issue/triage rules. No relevant CONTRIBUTING/CODING_STANDARDS or existing glossary/ADR found. Ownership check: all 8,084 baseline tracked-file hashes unchanged. Exclusive artifact writes, owned-resource cleanup, and preserved timeout output are reused from the existing implementation.

Ran `rtk proxy python3 -B -m unittest discover -s experiments/real-topology-live -p test_baseline_comparison.py -v`: **21 passed**. No cluster access or implementation edits. Only this report was written durably. Reviewer provenance: session identifies GPT-6; Astra variant not independently verifiable, no model substitution.
