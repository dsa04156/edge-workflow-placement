# Report — restartable-init-capacity (task_d1a76032d11a)

- Auth: `claude auth status` → claude.ai, firstParty, subscription max. Model claude-opus-5-5 (no substitution).
- Changed: experiments/real-topology-live/run_placement.py (Pilot.capacity), test_baseline_comparison.py (CapacityPersistenceTests: replaced "still rejected" test with 3 tests), docs/experiments/restartable-init-capacity.md. run_baseline_comparison.py unchanged (sha256 == before/).
- Diff vs before/: delta-vs-before.diff
- Red: evidence/red.txt (3 errors, same RuntimeError as failure.json, incl. saved-snapshot replay)
- Green: evidence/green-capacity.txt (7/7), evidence/full-discover.txt (90 OK)
- Live read-only probe: evidence/live-capacity-probe.json (preflight passed, 9 nodes positive, 34 sidecar Pods on ser0001)
- No phase/verifier change (coordinator instruction). No cluster mutation, benchmark, commit, or install.
- Limits: see docs/experiments/restartable-init-capacity.md §한계.
