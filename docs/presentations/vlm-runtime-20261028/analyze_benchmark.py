"""Reproduce descriptive tables/plots from this run's preserved evidence only.
Run: python analyze_benchmark.py (NumPy and Matplotlib required).
"""
from pathlib import Path
import base64
import csv
import hashlib
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "evidence"
summary = json.loads((EVIDENCE / "benchmark-summary.json").read_text())
ledger = [json.loads(s) for s in (EVIDENCE / "benchmark-ledger.jsonl").read_text().splitlines()]
plan = json.loads((ROOT / "benchmark-plan.json").read_text())["rows"]
assert summary["completed"] == summary["planned"] == len(ledger) == len(plan) == 80
assert summary["failed"] == summary["interrupted"] == summary["unattempted"] == 0
assert summary["error"] is None
rows = []
sequences = {a: 4 for a in ("cuda", "mobilint")}
for r, want in zip(ledger, plan):
    assert all(r[k] == want[k] for k in ("measurement_id", "round", "pair", "arm", "input"))
    assert r["status"] == "ok"
    receipt = r["receipt"]
    data = base64.b64decode(r["data"])
    assert hashlib.sha256(data).hexdigest() == receipt["output_sha256"]
    result = json.loads(data)
    assert result == r["result"]
    sequences[r["arm"]] += 1
    assert receipt["sequence"] == sequences[r["arm"]]
    assert receipt["device"] == {"cuda": "cuda:0", "mobilint": "npu:aries-rb"}[r["arm"]]
    t, h = result["timing"], result["host"]
    cpu_ms = (h["cpu_s_preprocess"] + h["cpu_s_generate"]) * 1000
    row = {k: r[k] for k in ("measurement_id", "round", "pair", "arm", "input", "started_at", "finished_at", "collector_wall_ms")}
    row.update(sequence=receipt["sequence"], worker_wall_ms=receipt["worker_wall_ms"],
               preprocess_ms=t["preprocess_ms"], generate_ms=t["generate_ms"], ttft_generate_ms=t["ttft_ms"],
               host_cpu_ms=cpu_ms, host_mean_cores=cpu_ms/(t["preprocess_ms"]+t["generate_ms"]),
               generated_tokens=len(result["token_ids"]), prompt_tokens=result["prompt_tokens"], text=result["text"])
    rows.append(row)

def csv_write(name, records):
    with (ROOT / name).open("w") as f:
        w = csv.DictWriter(f, fieldnames=list(records[0]), lineterminator="\n"); w.writeheader(); w.writerows(records)

def stats(selected, arm, inp="all"):
    values = [r["collector_wall_ms"] for r in selected]
    return dict(arm=arm, input=inp, n=len(selected), p50_collector_ms=float(np.percentile(values, 50, method="linear")),
                p95_collector_ms=float(np.percentile(values, 95, method="linear")),
                mean_collector_ms=float(np.mean(values)), min_collector_ms=min(values), max_collector_ms=max(values),
                p50_worker_ms=float(np.median([r["worker_wall_ms"] for r in selected])),
                p50_generate_ms=float(np.median([r["generate_ms"] for r in selected])),
                p50_ttft_generate_ms=float(np.median([r["ttft_generate_ms"] for r in selected])),
                mean_host_cpu_ms=float(np.mean([r["host_cpu_ms"] for r in selected])),
                mean_host_cores=float(np.mean([r["host_mean_cores"] for r in selected])),
                generated_tokens_min=min(r["generated_tokens"] for r in selected),
                generated_tokens_max=max(r["generated_tokens"] for r in selected),
                serial_service_rate_calls_s=len(selected)/(sum(values)/1000))

arms = ("cuda", "mobilint")
inputs = list(dict.fromkeys(r["input"] for r in plan))
aggregate = [stats([r for r in rows if r["arm"] == a], a) for a in arms]
by_input = [stats([r for r in rows if r["arm"] == a and r["input"] == i], a, i) for i in inputs for a in arms]
csv_write("measurements.csv", rows)
csv_write("summary.csv", aggregate)
csv_write("by-input.csv", by_input)
(ROOT / "statistics.json").write_text(json.dumps(dict(aggregate=aggregate, by_input=by_input,
    benchmark_wall_ms=summary["phases"]["benchmark"]["duration_ms"],
    quantiles="NumPy linear interpolation; descriptive within-Pod observations, not confidence intervals",
    scope="4 fixed inputs x 10 repeats per arm; GPU BF16 vs NPU W4V8; host/runtime/network differ; no quality or SLO/goodput claim",
    cpu="Child process CPU time in preprocess+generate; mean cores = CPU time / matching wall time. Not neural CPU fallback.",
    ttft="Generation-start to first streamer token, excludes preprocessing and collector RPC",
    serial_rate="Successful calls / summed collector durations per arm; excludes other-arm waits, not saturated throughput"
), indent=2) + "\n")

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 13, "axes.spines.top": False,
                     "axes.spines.right": False, "svg.fonttype": "none"})
colors = {"cuda": "#0072B2", "mobilint": "#D55E00"}
labels = {"cuda": "Jetson Orin / BF16", "mobilint": "Mobilint ARIES / W4V8"}
fig, ax = plt.subplots(figsize=(13.33, 7.5))
fig.subplots_adjust(left=.10, right=.96, top=.79, bottom=.21)
fig.text(.1, .945, "Qwen3-VL-2B: measured request latency", fontsize=23, weight="bold")
fig.text(.1, .885, "40 measured calls per configuration; 4 warm-up calls each excluded", fontsize=14)
for ai, a in enumerate(arms):
    for ii, inp in enumerate(inputs):
        selected = [r["collector_wall_ms"] for r in rows if r["arm"] == a and r["input"] == inp]
        x = ii + (-.16 if ai == 0 else .16)
        med = float(np.median(selected))
        lo, hi = np.percentile(selected, [5,95], method="linear")
        ax.scatter(x+np.linspace(-.045,.045,len(selected)), selected, color=colors[a], alpha=.35, s=25)
        ax.errorbar(x,med,yerr=[[med-lo],[hi-med]],fmt="o",color=colors[a],capsize=6,ms=8,lw=2,
                    label=labels[a] if ii==0 else None)
        ax.annotate(f"{med:,.0f}", (x,hi), xytext=(0,10), textcoords="offset points",ha="center",fontsize=12,color=colors[a])
ax.set(yscale="log", ylabel="Collector round-trip latency (ms, log scale)", xticks=range(len(inputs)),
       xticklabels=[i.replace("smoke-", "smoke\n").replace("cal-", "cal\n") for i in inputs])
ax.set_ylim(bottom=min(r["collector_wall_ms"] for r in rows)*.7,top=max(r["collector_wall_ms"] for r in rows)*2)
ax.grid(axis="y",which="both",alpha=.15)
ax.legend(loc="lower left",bbox_to_anchor=(0,1.015),ncol=2,frameon=False)
fig.text(.1,.10,"Dots: all calls. Large markers: median; whiskers: observed p5-p95 (not confidence intervals).",fontsize=11,color="#555555")
fig.text(.1,.055,"Different precision, host and network path. Same-Pod pilot; no quality, hardware-only or scheduler claim.",fontsize=11,color="#555555")
for ext in ("png","svg"):
    fig.savefig(ROOT / ("vlm-latency."+ext),dpi=220,facecolor="white")
    if ext == "svg":
        svg = ROOT / ("vlm-latency."+ext)
        svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines())+"\n")
plt.close(fig)
print(json.dumps(aggregate,indent=2))
