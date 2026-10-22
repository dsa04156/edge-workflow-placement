# Fix report: protected Pod snapshot projection (2026-10-21)

## Defect (host-validation.md)
`run.py` protected-before/after called `kubectl get pods -A -o json` through `Recorder.call`, which writes raw
stdout to `NNN-protected-*.stdout` before Python projection — unrelated Pod spec/env persisted in artifacts.

## Change (owned files only)
- `run.py`: `project_protected(raw JSON)` replaced by `protected_pods(rec, stem)`, which calls
  `get pods -A -o jsonpath=...` (`PROTECTED_JSONPATH`) emitting only `uid, namespace, name, phase, Ready` per
  line (tab-separated). kubectl projects before printing, so prohibited fields never enter captured stdout.
  Recorder unchanged: per-command argv/start/end/rc, partial stdout/stderr files and phase events kept.
  `protected-before.json` / `protected-after.json` shape unchanged (`{uid: {name, namespace, phase, ready}}`;
  empty phase/Ready -> null), so `verify.py` is unaffected.
- `fake_kubectl.py`: protected Pod now carries `spec.containers[].env` with `$FAKE_PROTECTED_SECRET`.
  `get pods -A` with exactly the runner template prints the projected lines (missing keys -> empty, as
  kubectl jsonpath does); any other `-o` prints raw JSON, so a regression leaks visibly.
- `test_execute.py`: `FakeCluster` sets sentinel; new
  `SharedSchedulerPlacement.test_unrelated_pod_spec_never_reaches_artifacts` scans every file under the run
  directory for the sentinel and asserts projected identity/Ready present in JSON and both raw stdout files.

## Red / green (actual)
- Red: `rtk proxy python3 -m unittest test_execute.SharedSchedulerPlacement.test_unrelated_pod_spec_never_reaches_artifacts`
  -> FAILED, leaked in `001-protected-before.stdout`, `078-protected-after.stdout`.
- Green: `rtk proxy python3 -m unittest test_forecast test_stage_runtime test_run test_execute`
  (in experiments/sensehat-placement) -> Ran 27 tests, OK (17.6s).

## Limits
- Fake kubectl only. The jsonpath template (incl. `{"\t"}` escapes and the Ready filter with missing keys)
  is not verified against a real kubectl/cluster; no cluster commands were run. Recommend one read-only
  `kubectl get pods -A -o '<template>'` check by the coordinator before execution.
- Out of scope, not changed: `get events -n topology-profile-lab -o json` (scheduler-events / teardown
  events) still captures raw namespace Event objects (messages, not Pod spec); owned-resource
  `get pod`/`remaining` JSON captures only this run's own Pods/ConfigMap.
- No independent review performed; no deploy.
