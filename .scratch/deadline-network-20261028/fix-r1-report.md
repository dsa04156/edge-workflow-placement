# Fix R1 report: deadline-network v1 (task_7ec52c823d0d / ctx_5fe989e3ae3b)

Date: 2026-10-28. Worker: Claude Code, terminal term_1f6f4433-eeb1-4b11-80ea-b86d47250892. Outcome: **succeeded** (code, tests, re-audit, doc). Nothing ran live and nothing is claimed about performance.

## Model / auth evidence (safe fields only)

- Transcript `~/.claude/projects/-home-jinuk-codex-work-scheduler/6f244481-0419-4b55-b8f7-b6d8fc988584.jsonl`: every assistant message has `message.model` = `claude-opus-5-5` (75 at check time, no other value).
- `claude auth status`: loggedIn=true, authMethod=claude.ai, apiProvider=firstParty, subscriptionType=max. No API key, billing provider, or fallback was used. Identity fields are omitted.
- Orca CLI `/home/jinuk/.orca/cli/bin/orca` 1.4.235. Heartbeats were sent (investigating, implementing). The inbox was checked at the start, before the audit re-run, and before worker_done.
- **Process deviation:** my first two shell commands (`cat` of fix-r1-task.md, then spec/reviews/report/status/preserved-hashes) ran **without** an `rtk` prefix. Both were read-only. Every later command went through `rtk proxy`.

## Scope handled

1. Spec r1 #1 / Standards r1 #2 (same defect): missing, null, or non-dict estimate tables were read as "no receipts", and negative durations were accepted.
2. Spec r1 #2: an invalid `stale_after_s` disabled the inherited fallback.
3. Standards r1 #1 (added by coordinator msg_e7c145085a54): the non_reconstructible `worker` depended on the hash seed, so `rows_sha256` was nondeterministic.
4. Coordinator doc addition: the next runner must log the same atomic observation snapshot that `decide()` consumes (with service_ms and evidence ages). The runner is not implemented.

## TDD: one issue at a time (logs in `logs/`)

| # | Regression test(s) written first | Red | Minimal fix | Green |
|---|---|---|---|---|
| 1 | `test_audit.Malformed`: `..._estimate_tables_are_refused_not_read_as_no_receipts` (busy snapshot; both tables × missing/null/list), `test_negative_estimate_durations_are_refused`, `test_logged_empty_estimate_tables_remain_valid` (passes before and after; it protects the `{}` case) | `fix-r1-01-red-estimate-tables.log`: 8 subtest failures | `audit.reconstruct`: both tables must be dicts (logged `{}` is still valid). Values go through new `_duration` (finite, ≥ 0). | `fix-r1-02-green-estimate-tables.log` (Malformed + Reconstruction + NoLeakage, 12 OK) |
| 2 | `test_deadline_policies.Config`: `..._invalid_stale_after_s_is_refused...` (NaN, inf, -1, 0, True, '3'), `..._valid_integer_stale_after_s_still_triggers_the_fallback`, `test_baseline_builds_keep_the_original_stale_after_s_handling` | `fix-r1-03-red-stale-after-s.log`: 6 subtest failures | `DagDeadlineGuard.__init__` validates `stale_after_s` with `_finite` (finite, > 0, not bool) before calling the base. Baseline builds are untouched. `_finite` now takes the value directly; its unused `low_open` arg was removed (private, single caller, same behavior for `policy_spec`). | `fix-r1-04-green-stale-after-s.log` (21 OK) |
| 3 | `test_audit.Cli.test_audit_rows_are_byte_identical_across_hash_seeds` (subprocess audit of the real run, PYTHONHASHSEED 0/1/3) | `fix-r1-05-red-hash-seed.log`: seed 0 and 1 gave `90ce618f…`, seed 3 gave `cb9cac83…` (reproduces the reviewer's numbers) | `audit.guard_on_snapshot` reports the sorted-first busy unlogged worker of the same part (anchor/alternative), through a new `AuditObservation.unlogged()` that `queue_ms` also uses. The frozen base is unchanged. | `fix-r1-06-green-hash-seed.log` |

Final run: `rtk proxy env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_deadline_policies test_audit` gave **Ran 39 tests, OK** (32 existing + 7 regression), in `fix-r1-07-tests-final.log`. No other suites were run. No statistical tests were run.

## Re-audit (new path; old artifact immutable)

Command (repo root):
```
rtk proxy env PYTHONDONTWRITEBYTECODE=1 python3 experiments/deadline-network/audit.py audit --run artifacts/unstable-network/un-collect-r3 --plan artifacts/unstable-network/plan-20261027T111532Z --out artifacts/deadline-network/audit-un-collect-r3-20261028T020451Z
```
- Output is in `fix-r1-08-audit-run.log`: 36 cells, 3,462 arrivals. Categories: exact 2,621 / fallback 59 / no_snapshot 187 / non_reconstructible 595. Agree 954, differ 1,726. Replay: match 1,252 / predicted_mismatch 1 / non_reconstructible 360.
- **All counts are unchanged from R0.** `rows.json` sha256 `90ce618f06a85a74a6a255157aa816d9852893d50351834fa9c67d34163a24d7` is byte-identical to R0 (R0 happened to pick the canonical worker).
- New `audit.json` sha256 is `da9bf11a8e6266322b769772fd65326eef370e8d2e6c1ed408113c47382a2a2e`. The only field that differs from R0 is `code_sha256`: audit.py `fe759c124e4b3acd58aa377f94fb7913cb66c5d1331ce441d24fb0a8f639abf5`, deadline_policies.py `ae01b5f6a288c550f7c413bad43a66ce1ffc03cddb938ec9bebbd4fa94c6d9d0`, frozen base `f6ecfade…` (unchanged).
- The real r3 snapshots contained no missing tables and no negative estimates, so fixes 1 and 2 change no real row.
- Non_reconstructible rows by reported worker: jgpu-dev0009-cu130 488, cpu-e0 107, all `anchor_worker_busy_unlogged_service`. 35 rows had both anchor workers busy and unlogged; these were the seed-sensitive rows before R1.
- The R0 artifact `audit-un-collect-r3-20261028T015311Z/` is unchanged: audit.json `f75dda7b5d785868ea791de55e44ae4271181fb0a82a120ab4ebe62a6331da5a`, rows.json `90ce618f…`.

## Files changed (owned scope only)

- `experiments/deadline-network/audit.py` (now `fe759c12…`), `deadline_policies.py` (now `ae01b5f6…`), `test_audit.py` (now `bf88ab3e…`), `test_deadline_policies.py` (now `14357f97…`).
- `docs/experiments/deadline-network-20261028.md` (now `e5f46880…`): status/test count, stale_after_s rule, `rtk proxy` commands with the new audit path (R0 path kept as preserved), estimate-table rule, canonical worker rule, R1 hashes next to R0 hashes, atomic-snapshot logging in §7.2, and a new §9 with the R1 history table. The R0 TDD deviation text in §5 is kept as written.
- New `artifacts/deadline-network/audit-un-collect-r3-20261028T020451Z/{audit.json, rows.json}`.
- New `.scratch/deadline-network-20261028/logs/fix-r1-0[1-8]-*.log` and this report.
- Not touched: `frozen_unstable_policies.py` (`f6ecfade…`), `SOURCES.json` (`a09d8de5…`), review-source-r1 snapshot, prior audit, experiments/unstable-network, coordinator files. `experiments/deadline-network/__pycache__/` was already there from R0; this round's runs used PYTHONDONTWRITEBYTECODE=1.

## Preservation check

`preserved-hashes.json`: all 18 files rechecked, drift `[]`. No cluster access, commit/push, dependency install, nested workers, or deletions.

## Remaining / limitations

- Runner/generator integration (including atomic snapshot logging) is still not implemented. The doc gives only the contract.
- The audit is still empty in the busy-anchor case (595 rows), which is exactly where the guard matters. Nothing is claimed about live performance.
