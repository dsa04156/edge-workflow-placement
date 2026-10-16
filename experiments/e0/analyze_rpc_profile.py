"""Validate both ends of every request and describe blocks without inference tests."""

from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
import statistics


base = Path("artifacts/e0-rpc-profile")
run = json.loads((base / "run.json").read_text())
selection = json.loads((base / "selection.json").read_text())
schedule = json.loads((base / "schedule.json").read_text())
targets = {t["alias"]: t for t in run["targets"]}


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.startswith("{")]


rows = []
for block in range(3):
    rs = records(base / f"client-{block}.log")
    assert rs[-1]["kind"] == "client_complete" and rs[-1]["requests"] == 192
    requests = [r for r in rs if r["kind"] == "request"]
    assert len(requests) == 192 and len([r for r in requests if not r["warmup"]]) == 160
    environment = next(r for r in rs if r["kind"] == "client_environment")
    assert environment["block"] == block and environment["schedule"] == schedule[str(block)]
    observed_order = list(dict.fromkeys(r["condition"]["id"] for r in requests))
    assert observed_order == [r["id"] for r in schedule[str(block)]]
    rows.extend(requests)
assert len({r["request_id"] for r in rows}) == 576
receipts = {}
for alias in targets:
    rs = records(base / f"worker-{alias}.log")
    assert any(r["kind"] == "server_ready" for r in rs)
    for r in rs:
        if r["kind"] == "served":
            assert r["request_id"] not in receipts
            receipts[r["request_id"]] = r
assert len(receipts) == 576
groups = defaultdict(list)
for row in rows:
    condition = row["condition"]
    target = targets[condition["target"]]
    server = receipts[row["request_id"]]
    assert row["input_sha256"] == server["input_sha256"] == "791a0f0caae0b9e4a81d7ad9fe3db67514bc2f67fbff71fb4d29f6cd66ff011d"
    assert row["output_sha256"] == server["output_sha256"]
    assert row["input_bytes"] == server["input_bytes"] == 602112
    assert row["output_bytes"] == server["output_bytes"] == 4000
    assert row["inference_node"] == server["node"] == target["node"]
    assert row["backend"] == server["backend"] == target["backend"]
    assert row["connection_id"] == server["connection_id"]
    assert row["client_nodelay"] == row["server_nodelay"] == server["server_nodelay"] == condition["nodelay"]
    assert row["server_metrics"] == server["metrics"]
    assert row["top5"] == [258,259,157,279,152]
    metrics = row["server_metrics"]
    if target["backend"] == "gpu":
        assert metrics["output_device"] == "cuda:0" and metrics["cuda_event_ms"] > 0
        assert metrics["h2d_blocking_wall_ms"] >= 0 and metrics["d2h_blocking_wall_ms"] >= 0
    elif target["backend"] == "intel_npu":
        assert metrics["execution_devices"] == ["NPU"]
    else:
        assert metrics["execution_device"] == "CPU"
    groups[(row["block"], condition["id"])].append(row)
for key, group in groups.items():
    assert len(group) == 12 and [r["iteration"] for r in group] == list(range(12))
    assert all(r["warmup"] == (r["iteration"] < 2) for r in group)
    if group[0]["condition"]["reuse"]:
        assert len({r["connection_id"] for r in group}) == 1
        assert len({tuple(r["local_address"]) for r in group}) == 1
        assert all(r["connect_ms"] == 0 for r in group[1:])
    else:
        assert len({r["connection_id"] for r in group}) == 12
jobs = json.loads((base / "jobs-final.json").read_text())["items"]
heldout_job = next(x for x in jobs if x["metadata"]["name"] == run["client_jobs"][2])
frozen_at = datetime.fromisoformat(selection["frozen_at"])
created_at = datetime.fromisoformat(heldout_job["metadata"]["creationTimestamp"].replace("Z", "+00:00"))
# Kubernetes serializes these timestamps at second resolution; compare like precision.
assert frozen_at.replace(microsecond=0) <= created_at
pods = json.loads((base / "pods-final.json").read_text())["items"]
heldout_pod = next(p for p in pods if p["metadata"].get("labels",{}).get("job-name") == run["client_jobs"][2])
container_start = heldout_pod["status"]["containerStatuses"][0]["state"]["terminated"]["startedAt"]
assert frozen_at < datetime.fromisoformat(container_start.replace("Z", "+00:00"))
for filename, digest in selection["training_log_sha256"].items():
    assert hashlib.sha256((base / filename).read_bytes()).hexdigest() == digest
measured = [r for r in rows if not r["warmup"]]


def describe(values):
    values = list(values)
    return {"n":len(values),"median":statistics.median(values),"mean":statistics.mean(values),
            "sd":statistics.stdev(values),"min":min(values),"max":max(values)}


summary = {}
for condition_id in sorted({r["condition"]["id"] for r in measured}):
    subset = [r for r in measured if r["condition"]["id"] == condition_id]
    summary[condition_id] = {
        "backend_ms": describe(r["server_metrics"]["backend_blocking_ms"] for r in subset),
        "rpc_ms": describe(r["rpc_wall_ms"] for r in subset),
        "workflow_ms": describe(r["workflow_wall_ms"] for r in subset),
        "block_median_rpc_ms": {str(b):statistics.median(r["rpc_wall_ms"] for r in subset if r["block"]==b) for b in range(3)},
        "block_median_workflow_ms": {str(b):statistics.median(r["workflow_wall_ms"] for r in subset if r["block"]==b) for b in range(3)},
        "connect_ms":describe(r["connect_ms"] for r in subset),
        "max_error_vs_local_cpu":max(r["max_abs_error_vs_server_cpu"] for r in subset)}
heldout = {}
for policy in ["B2_fastest_feasible", "C1_data_aware"]:
    alias = selection[policy]
    subset = [r for r in measured if r["block"]==2 and r["condition"]["target"]==alias
              and r["condition"]["reuse"] and r["condition"]["nodelay"]==1]
    assert len(subset)==10
    heldout[policy] = {"target":alias,"workflow_ms":describe(r["workflow_wall_ms"] for r in subset),
                       "request_ids":[r["request_id"] for r in subset]}
result = {"run_id":run["run_id"],"validated_requests":len(rows),"measured_requests":len(measured),
          "holdout_chronology":{"selection_frozen_at":selection["frozen_at"],"job_created_at_seconds":heldout_job["metadata"]["creationTimestamp"],
                                 "container_started_at_seconds":container_start,"selection_precedes_container_start":True},
          "warmup_requests":96,"paired_server_receipts":len(receipts),"groups":summary,
          "pacing_overruns":sum(r["pacing_overrun"] for r in rows),
          "selection":selection,"heldout":heldout,
          "heldout_B2_minus_C1_median_workflow_ms":heldout['B2_fastest_feasible']['workflow_ms']['median']-heldout['C1_data_aware']['workflow_ms']['median'],
          "same_plan_and_same_heldout_observations":selection['B2_fastest_feasible']==selection['C1_data_aware'],
          "limitations":["One fixture, one shared worker instance per backend, three sequential client blocks.",
                         "No independent deployment replication, CI, p-values or p95 inference.",
                         "One movable task: DAG enumeration equals data-aware C1; no general DAG or scheduler comparison.",
                         "RPC and host copy boundaries include protocol/framework overhead; not pure network or DMA time.",
                         "GPU architecture warning and missing CUDA kernel trace remain; actual CUDA tensors/events were validated.",
                         "Diagnostic bootstrap download failure retained; preparation outside measured requests."]}
(base / "results.json").write_text(json.dumps(result,indent=2)+"\n")
(base / "requests.jsonl").write_text("".join(json.dumps(r)+"\n" for r in rows))
(base / "receipts.jsonl").write_text("".join(json.dumps(r)+"\n" for r in receipts.values()))
print(json.dumps({"validated_requests":len(rows),"heldout":heldout,"median_difference_ms":result['heldout_B2_minus_C1_median_workflow_ms']}))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, axes = plt.subplots(1,4,figsize=(14,4),sharey=True)
modes=[(False,0),(False,1),(True,0),(True,1)]
colors=['#2563eb','#d97706','#059669']
jitter=random.Random(7)
for ax, alias in zip(axes, targets):
    for i,(reuse,nd) in enumerate(modes):
        for b in range(3):
            values=[r['rpc_wall_ms'] for r in measured if r['condition']['target']==alias and r['block']==b
                    and r['condition']['reuse']==reuse and r['condition']['nodelay']==nd]
            ax.scatter([i+jitter.uniform(-.12,.12) for _ in values],values,s=10,alpha=.55,color=colors[b],label=f'block {b}' if i==0 else None)
        values=[r['rpc_wall_ms'] for r in measured if r['condition']['target']==alias
                and r['condition']['reuse']==reuse and r['condition']['nodelay']==nd]
        ax.plot([i-.2,i+.2],[statistics.median(values)]*2,color='black',lw=2)
    ax.set_title(alias);ax.set_xticks(range(4),['new\nND0','new\nND1','reuse\nND0','reuse\nND1'])
    ax.grid(axis='y',alpha=.2)
axes[0].set_ylim(0,max(r['rpc_wall_ms'] for r in measured)*1.06)
axes[0].set_ylabel('Application RPC wall time (ms)');axes[-1].legend(fontsize=8)
fig.suptitle('Edge source → four worker locations | 3 shared-worker blocks, 10 measured requests/condition/block')
fig.tight_layout()
fig.savefig(base/'rpc-transport.png',dpi=180);fig.savefig(base/'rpc-transport.svg')
