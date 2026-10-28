"""Rebuild the SD preparation slide figure from curated CSV + raw event summary."""
from pathlib import Path
import csv
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
rows = list(csv.DictReader((ROOT / "sd-fill-trace.csv").open()))
event = json.loads((ROOT / "evidence/preparation-summary.json").read_text())["cache_event"]
x = [float(r["elapsed_min"]) for r in rows]
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 13, "axes.spines.top": False,
                     "axes.spines.right": False, "svg.fonttype": "none"})
fig, axes = plt.subplots(3, 1, figsize=(13.33, 7.5), sharex=True,
                         gridspec_kw={"height_ratios": [1.3, 1, 1]})
fig.subplots_adjust(left=.10, right=.96, top=.82, bottom=.16, hspace=.34)
fig.text(.1, .945, "SD-backed runtime preparation completed", fontsize=23, weight="bold")
fig.text(.1, .885, "8.85 GB verified  |  73.23 min fill  |  4 MiB/s maximum  |  17 automatic pauses", fontsize=14)
axes[0].plot(x, [float(r["written_bytes"])/1e9 for r in rows], color="#0072B2", lw=2.5)
axes[0].axhline(event["bytes"]/1e9, color="#777777", ls=":", lw=1)
axes[0].set(ylabel="Written (GB)", ylim=(0, 9.5))
axes[1].plot(x, [float(r["cap_MiBps"]) for r in rows], color="#009E73", lw=1.8, drawstyle="steps-post")
axes[1].set(ylabel="Cap (MiB/s)", ylim=(0, 4.4))
axes[2].plot(x, [float(r["busy_pct"]) for r in rows], color="#D55E00", lw=1.4, marker=".", ms=4)
axes[2].axhline(85, color="#777777", ls=":", lw=1)
axes[2].set(ylabel="SD busy (%)", xlabel="Minutes since preparation worker started", ylim=(0, 105))
for ax in axes:
    ax.grid(axis="y", alpha=.18)
    ax.set_xlim(0, 75)
fig.text(.1, .055, "73 sampled Ready=True observations; intermittent samples, not continuous-uptime proof. No inference in this run.", fontsize=11, color="#555555")
for ext in ("png", "svg"):
    fig.savefig(ROOT / ("sd-preparation." + ext), dpi=220, facecolor="white")
plt.close(fig)

# A separate observed cache-hit run, once its final verified health is preserved.
hit_path = ROOT / "evidence/benchmark-health-final-cuda.json"
if hit_path.exists():
    first = json.loads((ROOT / "evidence/preparation-summary.json").read_text())
    hit = json.loads(hit_path.read_text())
    event_hit = next(e for e in hit["bootstrap"]["events"] if e["kind"] == "cache_hit")
    assert event_hit["transfers"] == 0
    fill_min = first["cache_event"]["ms"] / 60000
    verify_min = event_hit["ms"] / 60000
    first_load = first["load"]["process"]["load_wall_ms"] / 60000
    hit_load = hit["stages"]["vlm"]["process"]["load_wall_ms"] / 60000
    fig, ax = plt.subplots(figsize=(13.33, 7.5))
    fig.subplots_adjust(left=.19, right=.96, top=.76, bottom=.28)
    fig.text(.10,.94,"Cached files still need verification and model loading",fontsize=21,weight="bold")
    fig.text(.10,.875,f"Reuse run: zero model/runtime retransfers; {verify_min:.2f} min verification + {hit_load:.2f} min load",fontsize=14)
    ax.barh(1,fill_min,color="#0072B2",height=.43,label="Initial fill + verify")
    ax.barh(0,verify_min,color="#D55E00",height=.43,label="Cached-tree verification")
    ax.barh([1,0],[first_load,hit_load],left=[fill_min,verify_min],color="#009E73",height=.43,label="Model process load")
    for y,a,b in [(1,fill_min,first_load),(0,verify_min,hit_load)]:
        ax.text(a+b+1,y,f"{a+b:.2f} min",va="center",fontsize=14,weight="bold")
    ax.set(yticks=[0,1],yticklabels=["Cache reuse run","Initial fill run"],xlabel="Sum of the two measured GPU preparation phases (minutes)",xlim=(0,91))
    ax.grid(axis="x",alpha=.18)
    ax.legend(loc="upper center",bbox_to_anchor=(.5,-.22),ncol=3,frameon=False,fontsize=12)
    fig.text(.10,.10,"Two separate observations; no controlled speedup claim. Excludes coordinator upload and NPU setup.",fontsize=11,color="#555555")
    fig.text(.10,.055,"File-cache state and loaded-model state are distinct; these costs are excluded from warm inference statistics.",fontsize=11,color="#555555")
    for ext in ("png","svg"):
        fig.savefig(ROOT / ("readiness-cost."+ext),dpi=220,facecolor="white")
    plt.close(fig)

# Matplotlib SVG path lines carry trailing spaces; normalize exported text only.
for svg in (ROOT / "sd-preparation.svg", ROOT / "readiness-cost.svg"):
    if svg.exists():
        svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines())+"\n")
