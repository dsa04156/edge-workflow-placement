# Developer report: ready-node expansion (task_b4f28eb76ac7)

2026-10-22, Claude Opus 5.5 (Claude Code subscription). Level reached: **code exists + related tests pass + offline dry-run**. No live deployment, no cluster mutation, no commit/push.

## Files

Changed existing (before copies in `.scratch/ready-node-expansion/before/`, hashes matched `before-hashes.json` before editing):
- `experiments/real-topology-live/run_load.py`: `Session.nodes = NODES` class default; `Session.pod` pins `self.nodes[zone]`.
- `experiments/real-topology-live/run_placement.py`: `Pilot.roles = LEGACY_ROLES`, `Pilot.helpers()`. deploy, zones, deploy_operators, qualify_all, capacity, transfer_profiles and drain use `self.nodes`/`self.roles`. Fixed `NODES['e0'/'e1']` source/destination references are unchanged.
- `experiments/real-topology-live/run_baseline_comparison.py`:
  - `BaselinePilot(config)` and `use_topology()` inject per instance; class defaults stay legacy.
  - capacity fails closed on a missing candidate.
  - `topology_preflight()` is the first phase for an explicit topology and runs before any mutation.
  - `build_design(pairs, frozen)` keeps the legacy design identical and adds `topology` and `workload_estimate` only for an explicit topology. `workload_estimate()` was added.
  - CLI: `--topology`, plus `--inventory` (dry-run only).
  - `topology.py` was added to the frozen source hashes.
- `experiments/real-topology-live/verify_baseline_comparison.py`:
  - Candidates come from the run's `topology-frozen.json`, or legacy when it is absent.
  - `transfer_sizes(frozen)` replaces the constant; `TRANSFER_SIZES` is kept as the legacy value.
  - New checks: `check_topology()` (digest, preflight re-judgement, raw-snapshot re-projection), the topology-candidates check per profile, and the `topology-preflight` phase requirement.
  - Node names are passed into `check_scheduler_pods` and `check_row`.

New:
- `experiments/real-topology-live/topology.py`
- `experiments/real-topology-live/topologies/ready-nodes-20261022.json` (9 candidates with explicit roles, 3 excluded with reasons)
- `experiments/real-topology-live/test_ready_node_expansion.py` (20 tests)
- `docs/experiments/ready-node-expansion.md`
- `.scratch/ready-node-expansion/dry-run-expanded.json`, `before/verify-*.json`

Not modified: `test_baseline_comparison.py` (all 65 pre-existing tests unchanged), artifacts, frozen collector-source copies.

## Tests run

1. Ran `rtk proxy python3 -m unittest test_ready_node_expansion` first: 11 errors before the implementation existed. After the implementation: 20/20 OK.
   - The collect tests run the real `Pilot.deploy`, `qualify_all`, `transfer_profiles` and `drain`, plus `BaselinePilot.capacity` and `deploy_operators`, on 9 nodes. Only kubectl, cache upload, data-plane RPC and netctl are faked.
   - The fixture is hand-derived and tie-free. At 10 Mbps the profile policies all pick ser0004 (171 ms proxy). At 2 Mbps they pick e0/e1/e0 (408 ms), and no-network picks ser0004.
   - Covered: 324 qualification rows and transfer rows per kind (image 36 / vision 324 ...), 1,584 transfer RPCs, the 9-node whitelist for both the scheduler and the profiles, the 18 helpers with explicit zones, the drain over every helper, and the fixed sources.
   - Tamper and failure cases: verifier rejection for a role change, a dropped candidate, a digest edit, a preflight edit, a raw snapshot edit, a profile candidate drop and a deleted topology file. NotReady, cordoned and NoExecute nodes stop the run with zero apply/exec/delete calls and zero subprocess calls.
2. `rtk proxy python3 -m unittest discover -s . -p 'test_*.py'` in `experiments/real-topology-live`: 85/85 OK.
3. Legacy proof: `build_design` from the before copy equals the new default `build_design`. Verifier output for frozen runs `wan-20261022t024224`, `wan-20261021t101203` and `wan-20261021t094522` is identical except for `verifier_sha256`. All three were and still are partial (exit 1).
4. Offline dry-run (no kubectl): `rtk proxy python3 experiments/real-topology-live/run_baseline_comparison.py --dry-run --topology experiments/real-topology-live/topologies/ready-nodes-20261022.json --inventory .scratch/ready-node-expansion/inventory.json` exited 0.
   - 9 candidates passed preflight; 3 nodes were excluded (observed_ready=false); no nodes were unlisted.
   - Fixed: image_source/result_destination e0=dev0003, audio_source e1=dev0002, helper/gateway/cache c=ser0002.
   - Qualification 27 deployments / 324 requests (legacy 9/108). Transfer RPCs 792 per condition, 1,584 total (legacy 240). Helper Pods 20 (legacy 8). Max workflow requests 48 (unchanged).

## Limitations / honest notes

- **Outstanding live blocker (not fixed):** kubectl exec `unexpected EOF` on guard control (sink-e1, `wan-20261022t024224`). Guard exec count roughly triples with 18 helpers, which increases exposure to it.
- Runtime and image compatibility of the 6 added nodes is unverified until qualification. This includes registry pull on new nodes, Jetson L4T and the ser0003 arm64 server. The arch check only admits amd64/arm64, the arches previously qualified with this image.
- The workload estimate counts requests only. The duration (linear extrapolation from legacy: qualification ~24 min, calibration ~3 min) is in the doc, explicitly as an estimate.
- Calibration necessarily measures all 9×9 vision/speech pairs because `prepare.validate` requires a measured transfer for every candidate pair. No synthetic values are filled. Nothing ran live.
- Intra-cloud (c↔c1/c3/c4) and intra-edge direct paths are unshaped real LAN, the same semantics as the legacy intra-zone paths.
- `HELPERS` in run_placement.py is now unused there but kept for import compatibility.

## Next run (not executed)

```bash
rtk proxy python3 -u experiments/real-topology-live/run_baseline_comparison.py --execute \
  --topology experiments/real-topology-live/topologies/ready-nodes-20261022.json
rtk proxy python3 experiments/real-topology-live/verify_baseline_comparison.py artifacts/topology-profile-live/<run-id> \
  --output .scratch/ready-node-expansion/<run-id>-audit.json
```
