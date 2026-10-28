# Standards review R1 — NEEDS WORK

Reviewer: actual `gpt-6-astra`, effort `high`, verified from this session’s `turn_context` ([provenance](review-standards-evidence/provenance.json)). Baseline `d87bde1fde21f014c1baebd1132673984e960a51`; no intervening commits. Reviewed frozen additions against `/dev/null` and six copied modules against their origins. All 16 current source/test hashes match the frozen manifest.

**Documented violations**

1. **P1 — Stop admitted generation before error salvage.** `experiments/unstable-network/live.py:534–540,557–559`: an ordinary `/status` timeout exits to the finalizer without `/stop`; `/ledger` rejects running traces, so only an error is saved, while drain checks workers/gateway without checking pending generator arrivals. Violates [spec.md:82](spec.md) (“모든 결과 salvage”) and the same line’s inflight/drain invariant. A real loopback reproduction left 109 scheduled rows pending, generator `running`, no saved ledger, yet drain `idle=true` and netctl already torn down. Subsequent cleanup can destroy the only remaining ledger. Stop admission on every accepted/uncertain-start error, wait boundedly for terminal state, then salvage; preserve partial rows if termination fails. Evidence: [reproduce.json](review-standards-evidence/reproduce.json), `status_timeout`.

2. **P2 — Enforce the post-cell drain result before root deletion.** `experiments/unstable-network/live.py:538–540`: `attempt(drain)` returning `idle=false` is discarded and `/teardown` still executes. Violates [spec.md:56](spec.md) (“복원→drain/idle barrier→reset”) and the documented after-drain teardown contract in `netctl.py:10–12`. The boundary reproduction keeps worker queues busy through the entire 60-second drain, yet records teardown with no fault/skip. Stop the run on failed drain; distinguish emergency teardown from successful cell reset, retain its evidence, and prevent subsequent comparison cells. Evidence: [reproduce.json](review-standards-evidence/reproduce.json), `failed_drain`.

**Verification and limits**

35 focused existing tests pass (exit 0); both additional failure probes reproduce the defects (exit 0 with assertions). [Commands/results](review-standards-evidence/verification.md). Reused the coordinator’s existing 86-test result; no duplicate full suite, kernel mutation, cluster call, implementation edit, or nested agent. Verified UID-conditioned cleanup, protected snapshots, timeout stdout/stderr retention, copied-module imports, exclusive artifacts, and gateway-only NET_ADMIN scope. Root AGENTS and domain/issue/triage rules were read; no applicable CONTRIBUTING/CODING_STANDARDS, root glossary, or ADR was found. All twelve smell heuristics considered; no additional material heuristic finding. Frozen-source copying is required, not flagged.

Result: **2 documented violations (P1/P2), 0 additional smell findings**. Fixes and developer regression coverage remain before live release.
