# Timeout/failure projection fix — report (task_c90b60576b58)

Scope: `review-standards-final.md` P2 only. Changed `experiments/sensehat-placement/run.py` (`Recorder.call`) and
`experiments/sensehat-placement/test_execute.py` (new `ProjectedCallFailureEvidence` + `import subprocess`). No frozen
artifacts, cluster, dependencies, or commits.

## Change
- Projected calls now project captured stdout on every outcome: rc=0, nonzero rc, and `TimeoutExpired.output`,
  using the same `project` callable as the success path (`project_pod` for `get_pod`).
- Event JSON gets `projection`: `{'status': 'projected', 'captured_bytes': N}` or
  `{'status': 'unavailable', 'captured_bytes': N, 'reason': '<ExceptionType>; raw stdout omitted'}`.
  The reason is only the exception type name; raw bytes and exception messages are never written.
- Primary outcome kept: a timeout re-raises the original `TimeoutExpired` (event status `timeout`); nonzero rc still
  raises `RuntimeError ... rc=N` through `check`. A projection error is raised only when rc=0 (as before, when it was
  itself the failure). stderr still streams to the `.stderr` file unchanged.

## Verification (level: code + mocked local tests; real-cluster behavior unverified)
- `python3 -B -m unittest test_execute.ProjectedCallFailureEvidence -v` → 3/3 OK (mocked `subprocess.run`):
  timeout+valid JSON → projected uid, TimeoutExpired kept; nonzero+valid JSON → projected name, RuntimeError rc=1 kept;
  truncated invalid JSON containing secret sentinel (nonzero and timeout) → empty stdout, `unavailable` metadata with
  byte count, original exception type kept; every test asserts stderr preserved and no file contains the sentinel.
- Existing relevant: `python3 -B -m unittest test_execute test_run` → 21 tests OK (includes the 3 new ones).
  `test_forecast`/`test_stage_runtime` not rerun (they do not touch `Recorder`).
