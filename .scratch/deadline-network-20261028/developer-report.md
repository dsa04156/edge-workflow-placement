# Developer report — deadline-network v1 (task_1f713c5b0a5f / ctx_f8ab9e4029fd)

Date: 2026-10-28. Worker: Claude Code, model `claude-opus-5-5`.

## Model / auth evidence (safe fields only)

- Transcript `~/.claude/projects/-home-jinuk-codex-work-scheduler/af1c878a-31c5-4aa4-aa93-4613791e5261.jsonl`: every `"model"` field is `claude-opus-5-5` (119 occurrences at check time, no other value).
- `claude auth status`: loggedIn=true, authMethod=claude.ai, apiProvider=firstParty, subscriptionType=max. No API key / billing provider / fallback used. Identity fields (email, org id) intentionally omitted.
- Orca CLI `/home/jinuk/.orca/cli/bin/orca` version 1.4.235. Heartbeats sent at investigating/implementing; inbox checked at checkpoints.
- Shell: every command went through `rtk` (`/home/jinuk/.local/bin/rtk`, "rtk shim 0.1.0", which just execs its arguments).

## Files created (all new; nothing else modified)

- `experiments/deadline-network/frozen_unstable_policies.py`: byte-identical copy of `experiments/unstable-network/policies.py`, sha256 f6ecfade9f52b6b2e70d4d39417b8086ac62f7e9a5e6ff9c6d9fd7cee0e88870, same as the plan `copied_sources` value.
- `experiments/deadline-network/SOURCES.json`: provenance of that copy.
- `experiments/deadline-network/deadline_policies.py`: `ExecutionOnly`, `DagDeadlineGuard(base.DagReopt)`, `policy_spec`, `build` (baseline specs go to base build).
- `experiments/deadline-network/audit.py`: CLI `spec` and `audit`.
- `experiments/deadline-network/test_deadline_policies.py` (18 tests), `test_audit.py` (14 tests).
- `docs/experiments/deadline-network-20261028.md` (Korean doc).
- `artifacts/deadline-network/audit-un-collect-r3-20261028T015311Z/{audit.json, rows.json}`.
- `.scratch/deadline-network-20261028/logs/*` and this report.
- Side effect: `experiments/deadline-network/__pycache__/` was created by running the tests.

Preserved hashes (`preserved-hashes.json`, 18 files): rechecked at the end, 0 mismatches. No cluster access, no commit/push, no dependency install, no nested workers.

## Tests actually run

1. `python3 -m unittest test_deadline_policies` with 1 test: red (ModuleNotFoundError) → `logs/tdd-01-red-anchor-hold.log`.
2. Same test after implementation: green → `logs/tdd-01-green-anchor-hold.log`.
3. 17 policy behavior tests: all OK on the first run → `logs/tdd-02-policy-behaviors.log`. This was not one red/green per behavior: I wrote the implementation in one step before the later tests, so they passed immediately.
4. Mutation check in the scratchpad, to show the tests can fail: `<=`→`<` (1 failure), fraction ignored (3 failures), reoptimize inside budget (3 failures), audit `unknown_service` emptied (1 failure) → `logs/tdd-03-mutation-check.log`.
5. 14 audit tests (incl. CLI subprocess on the real frozen run): OK → `logs/tdd-04-audit.log`.
6. Final run, `python3 -m unittest -v test_deadline_policies test_audit`: Ran 32 tests, OK → `logs/tests-final.log`.

Failures and fixes along the way:
- First trial audit crashed with `TypeError: dict() got multiple values for keyword argument 'cells'`. Fix: renamed the report count to `cell_count`, and the output directory is now created only after the report is fully built.
- Old unstable-network suite: not rerun (hash-identical base, no edits). No statistical tests.

## Shell approval / removal incident (coordinator msg_696de52a4282)

- A cleanup command of the form `rm -rf <scratchpad>/trial; ...` inside `bash -c` was automatically rejected by the Claude Code safety check (no person approved). It was not run and I did not retry or work around it.
- The non-deleting replacement worked: I wrote the trial output to a new scratch path (`scratchpad/trial2`, `scratchpad/mut2`), and the real audit went to a new timestamped path. No prior evidence was removed. The scratchpad `trial/` directory from the first crashed attempt still exists (it holds only a partial rows.json).

## Audit outcome (exact counts; observational, no success claims)

- 36 cells and 3,462 arrivals were validated against plan `ee0aba54…` (release binding, cells, request cohort, policy, state, profile source, deadline/stale contract).
- Anchor = all-edge plan. It matches dag_static logged decisions 577/577.
- Categories: exact 2,621, fallback 59, no_snapshot 187 (all admission_drop), non_reconstructible 595 (all of them a busy anchor worker with unlogged service_ms). Guard decision is determined for 2,680 of 3,462 rows (77.4%).
- Replay fidelity of the stateless logged policies: match 1,252, predicted_mismatch 1, non_reconstructible 360, plan/fallback mismatch 0.
- Guard vs logged: agree 954, differ 1,726. Guard keeps the anchor on 2,662 rows and moves on 18 (over_budget_reoptimized).
- The logged-status join is descriptive only and is not a guard outcome.

## Limitations / not done

- The runner/generator integration is not implemented. The existing generator does not accept `dag_deadline_guard`; the doc §4 names the boundary.
- No live run, no performance claim. The audit is empty exactly where the guard matters most (busy anchor).
- `deadline_policies._finite` keeps an unused `low_open` parameter. I left it as is so the code sha256 recorded in audit.json stays valid.
