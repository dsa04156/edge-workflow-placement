# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Authoritative instructions

`AGENTS.md` is the binding project charter (research scope, evidence rules, which doc/script is canonical for each experiment). Read it before any task; this file only adds orientation. Docs and commit text are in Korean — write new research docs in Korean to match.

This is a **research repo**, not a product: the goal is to measure when DAG-joint placement beats per-task fastest-feasible placement on a real heterogeneous KubeEdge cluster (ARM edge Pis, x86 server with GPU/Intel NPU), and whether existing DAG re-optimization / hysteresis leaves residual loss. Code exists to produce frozen, auditable measurements. Never claim a new algorithm is superior, and never overwrite existing `artifacts/` or frozen sources (see AGENTS.md).

## Layout (big picture)

- `README.md` — dated log of the current experiment axis and latest results; the top section is the most recent handoff.
- `docs/research/` — problem definition (`problem-definition.md` is the reference for RQs/cost boundaries), literature reviews, AnchorDL reproduction contract.
- `docs/experiments/` — one doc per experiment (E0 → E3, policy runner, measured bridge, fresh-placement, edge-network-placement, topology-profile-*). Each doc records the exact commands actually run and the limits of the claim.
- `experiments/e0/` — the real-cluster toolchain, flat module layout imported via `PYTHONPATH=experiments/e0`:
  - `probe.py` / `transport.py` / `model_lifecycle.py` — SqueezeNet ONNX three-task fixture (preprocess → inference → postprocess) served over HTTP between Pods; stage receipts carry hashes/bytes of every edge so results can be audited.
  - `policy_runner.py` — common observe → policy callback → HTTP apply path. Builds an immutable snapshot (profile, worker readiness/generation), validates the plan against candidates, re-checks readiness before sending, records every attempt including failures. It deliberately does not sum costs or amortize prep; policies supply that.
  - `measured_policy_bridge.py`, `fresh_placement.py`, `placement_cost_diagnostic.py`, `edge_network_design.py` — policy/cost-table layers per experiment generation (calibrate → freeze candidates/cost table/plan → separate evaluation).
  - Naming pattern per experiment: `prepare_*.py` (manifests/inputs) → `run_*.py` (cluster execution, phase events preserved) → `*_client.py` (in-Pod driver) → `analyze_*.py` / `plot_*.py` → `test_*.py`.
- `experiments/real-topology-profile/` — current axis: prepare plans/Pod specs from a topology + task×node profile (`prepare.py`), node profiling (`profile.py`), read-only cluster snapshot (`capture.py`), failure-inclusive aggregation (`analyze.py`). Commands are in `docs/experiments/topology-profile-system.md`.
- `experiments/topology-profile-20261019/`, `dag-placement-20261019/`, `simulation-20261019/`, `profiled-placement-20261019/` — offline simulations. The kube-scheduler scoring in `dag-placement-20261019` is a Python approximation of vendored Go plugins in `sources/`, not the real scheduler. `simulation-20261019` uses vendored JellyBean profiles under `data/jellybean`.
- `artifacts/<experiment>/` — raw logs, attempts, verification JSON. Immutable once written.
- `.scratch/<feature-slug>/spec.md` (+ `issues/NN-slug.md`) — local issue tracker; rules in `docs/agents/issue-tracker.md`, labels in `docs/agents/triage-labels.md`. Domain docs (`GLOSSARY.md`, `docs/adr/`) do not exist yet; create them only per `docs/agents/domain.md`. Do not use `gh` for issues.
- `program-registration/` — untracked bundle with its own vendored venv; ignore it when searching code.

## Commands

No build step or linter is configured. Tests use stdlib `unittest`; dependencies are pinned ad hoc through `uv` (no project venv).

```bash
# Full e0 regression (spawns local ORT CPU HTTP workers; takes several minutes)
PYTHONPATH=experiments/e0 uv run --python /usr/bin/python3 --no-build \
  --with numpy==2.2.6 --with onnxruntime==1.23.2 --with Pillow==11.3.0 \
  python -m unittest discover -s experiments/e0 -p 'test_*.py'

# Single module / single test: same prefix, then
#   python -m unittest test_policy_runner
#   python -m unittest test_policy_runner.<TestClass>.<test_method>

# Topology-profile preparation tests (stdlib only)
cd experiments/real-topology-profile && python3 -m unittest test_prepare

# Simulation suite (run from its directory)
cd experiments/simulation-20261019 && python3 verify_suite.py && python3 run_suite.py \
  && python3 analyze_suite.py && python3 make_report.py
```

`probe.fetch` downloads the hash-pinned model/image on each use (no local cache), so tests need network access. Cluster-facing `run_*.py` scripts require `kubectl` access to the KubeEdge lab; do not run them as part of local verification.
