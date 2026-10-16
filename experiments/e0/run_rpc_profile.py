"""Run frozen client blocks sequentially and freeze selection before holdout."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import time


base = Path("artifacts/e0-rpc-profile")
run = json.loads((base / "run.json").read_text())
kubectl = ["rtk", "proxy", "kubectl", "-n", run["namespace"]]


def call(*args):
    return subprocess.check_output([*kubectl, *args, "--request-timeout=15s"], text=True)


def records(raw):
    return [json.loads(line) for line in raw.splitlines() if line.startswith("{")]


ready = set()
deadline = time.monotonic() + 1100
while len(ready) != 4:
    if time.monotonic() > deadline:
        raise TimeoutError("Workers did not qualify within 1100 seconds")
    for target, name in zip(run["targets"], run["worker_pods"]):
        pod = json.loads(call("get", "pod", name, "-o", "json"))
        if pod["status"]["phase"] == "Pending":
            continue
        raw = call("logs", name)
        (base / ("worker-" + target["alias"] + ".log")).write_text(raw)
        rs = records(raw)
        if any(r["kind"] == "server_ready" for r in rs):
            ready.add(target["alias"])
        if pod["status"]["phase"] in {"Failed", "Succeeded"}:
            raise RuntimeError(f"Worker exited before profiling: {name}")
    print(json.dumps({"kind": "worker_preparation", "model_ready": sorted(ready)}), flush=True)
    if len(ready) != 4:
        time.sleep(20)


def freeze_selection():
    rows = []
    hashes = {}
    for block in [0, 1]:
        path = base / f"client-{block}.log"
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.extend(r for r in records(path.read_text()) if r["kind"] == "request" and not r["warmup"]
                    and r["condition"]["reuse"] and r["condition"]["nodelay"] == 1)
    estimates = {}
    for target in run["targets"]:
        subset = [r for r in rows if r["condition"]["target"] == target["alias"]]
        assert len(subset) == 20
        estimates[target["alias"]] = {
            "n_requests": len(subset), "backend_median_ms": statistics.median(r["server_metrics"]["backend_blocking_ms"] for r in subset),
            "rpc_connect_median_ms": statistics.median(r["connect_ms"] + r["rpc_wall_ms"] for r in subset)}
    fastest = min(estimates, key=lambda x: (estimates[x]["backend_median_ms"], x))
    data_aware = min(estimates, key=lambda x: (estimates[x]["rpc_connect_median_ms"], x))
    result = {"frozen_at": datetime.now(timezone.utc).isoformat(), "training_blocks": [0, 1],
              "heldout_block_not_started": 2, "training_log_sha256": hashes, "transport": "reuse-nd1",
              "estimates": estimates, "B2_fastest_feasible": fastest, "C1_data_aware": data_aware,
              "DAG_enumeration_equivalent_to_C1": True,
              "scope": "one_movable_task_four_candidates_not_JellyBean_or_Atlas_reproduction"}
    assert not (base / "selection.json").exists()
    (base / "selection.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"kind": "selection_frozen", "B2": fastest, "C1": data_aware}), flush=True)


for block, name in enumerate(run["client_jobs"]):
    assert not (base / f"client-{block}.log").exists(), "Do not mix repeat attempts into one block"
    if block == 2:
        freeze_selection()
    subprocess.run([*kubectl, "apply", "-f", str(base / f"client-{block}.json")], check=True)
    deadline = time.monotonic() + 330
    while True:
        job = json.loads(call("get", "job", name, "-o", "json"))
        if job["status"].get("succeeded") or job["status"].get("failed"):
            raw = call("logs", "job/" + name)
            (base / f"client-{block}.log").write_text(raw)
            rs = records(raw)
            (base / f"client-{block}-records.json").write_text(json.dumps(rs, indent=2) + "\n")
            assert job["status"].get("succeeded") == 1, f"Client block {block} failed; raw log preserved"
            assert rs[-1]["kind"] == "client_complete" and rs[-1]["requests"] == 192
            print(json.dumps({"kind": "block_complete", "block": block, "requests": 192}), flush=True)
            break
        if time.monotonic() > deadline:
            raise TimeoutError(f"Client block {block} did not complete")
        time.sleep(10)

for target, name in zip(run["targets"], run["worker_pods"]):
    (base / ("worker-" + target["alias"] + ".log")).write_text(call("logs", name))
for kind in ["pods", "jobs", "services", "endpointslices"]:
    (base / (kind + "-final.json")).write_text(call("get", kind, "-l", "research.scheduler/run=" + run["run_id"], "-o", "json"))
print(json.dumps({"kind": "collection_complete", "requests": 576}), flush=True)
