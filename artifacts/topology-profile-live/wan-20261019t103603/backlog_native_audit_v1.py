"""Read retained native qdisc statistics; never trust observer backlog summary."""
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def provenance(path):
    data = path.read_bytes()
    return {"path": path.name, "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data)}


def audit(root):
    report = {"utc": datetime.now(timezone.utc).isoformat(),
              "method": "ast.literal_eval only serialized TCA_STATS/TCA_STATS2 attributes; last two native qdisc captures for each completed phase ledger",
              "scope": "Completed *.drain.json files present when this audit enumerated them. Later phases require another audit. Raw observer backlog_bytes is ignored.",
              "issues": [], "phases": [], "independent_sample_claim": False}
    ledgers = sorted(root.glob("*.drain.json"))
    for ledger in ledgers:
        phase = ledger.name.removesuffix(".drain.json")
        files = sorted(root.glob("*-drain-qdisc-" + phase + ".stdout"),
                       key=lambda p: int(p.name.split("-")[0]))[-2:]
        item = {"phase": phase, "ledger": provenance(ledger), "captures": []}
        if len(files) != 2:
            report["issues"].append({"phase": phase, "reason": "Expected two raw captures", "count": len(files)})
        for path in files:
            raw = json.loads(path.read_text())
            capture = {"source": provenance(path), "qdiscs": [],
                       "ignored_observer_backlog_bytes": raw.get("backlog_bytes")}
            for suffix in (".event.json", ".stderr"):
                companion = path.with_suffix(suffix)
                if companion.exists():
                    capture[suffix.removeprefix(".")] = provenance(companion)
            for qdisc in raw["qdiscs"]:
                parsed = {}
                for value in qdisc["attrs"]:
                    if isinstance(value, str) and value.startswith(("('TCA_STATS'", "('TCA_STATS2'", "('TCA_KIND'")):
                        key, data = ast.literal_eval(value)
                        parsed[key] = data
                stats = parsed.get("TCA_STATS")
                stats2 = dict(parsed.get("TCA_STATS2", {}).get("attrs", []))
                queue = stats2.get("TCA_STATS_QUEUE")
                entry = {"handle": qdisc["handle"], "parent": qdisc["parent"],
                         "kind": parsed.get("TCA_KIND"), "TCA_STATS": stats,
                         "TCA_STATS_QUEUE": queue}
                fields = [(name, value) for name, value in (("TCA_STATS", stats), ("TCA_STATS_QUEUE", queue))
                          if isinstance(value, dict) and isinstance(value.get("backlog"), int)]
                entry["all_present_backlogs_zero"] = bool(fields) and all(v["backlog"] == 0 for _, v in fields)
                entry["all_present_qlens_zero"] = bool(fields) and all(v.get("qlen") == 0 for _, v in fields)
                if not fields or not entry["all_present_backlogs_zero"] or not entry["all_present_qlens_zero"]:
                    report["issues"].append({"phase": phase, "file": path.name, "qdisc": entry})
                capture["qdiscs"].append(entry)
            if not capture["qdiscs"]:
                report["issues"].append({"phase": phase, "file": path.name, "reason": "No qdiscs"})
            item["captures"].append(capture)
        report["phases"].append(item)
    report["completed_phase_count"] = len(report["phases"])
    report["native_capture_count"] = sum(len(p["captures"]) for p in report["phases"])
    report["all_completed_phase_last_two_backlogs_zero"] = bool(ledgers) and not report["issues"]
    report["script"] = provenance(Path(__file__))
    return report


if __name__ == "__main__":
    root = Path(__file__).parent
    output = root / "backlog-native-audit-v1.json"
    if output.exists():
        raise RuntimeError("Refusing to overwrite prior audit")
    result = audit(root)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("utc", "completed_phase_count", "native_capture_count", "all_completed_phase_last_two_backlogs_zero", "issues")}))
