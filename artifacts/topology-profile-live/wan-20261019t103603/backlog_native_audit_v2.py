"""Final complete audit, retaining v1 and checking native observer v2 summaries."""
import importlib.util
import json
from pathlib import Path

root = Path(__file__).parent
spec = importlib.util.spec_from_file_location("prior_audit", root / "backlog_native_audit_v1.py")
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
report = prior.audit(root)
report["version"] = 2
report["scope"] = "All 33 completed experiment phases; last two native qdisc captures each. V1 remains immutable."
report["script"] = prior.provenance(Path(__file__))
report["reused_parser"] = prior.provenance(root / "backlog_native_audit_v1.py")
if report["completed_phase_count"] != 33:
    raise RuntimeError(f"Final audit requires 33 completed phases, got {report['completed_phase_count']}")
native_v2_captures = []
for phase in report["phases"]:
    for capture in phase["captures"]:
        raw = json.loads((root / capture["source"]["path"]).read_text())
        capture["observer"] = raw.get("observer", "original-recursive-summary")
        capture["native_v2_summary_checked"] = False
        if raw.get("observer") == "native-TCA_STATS-v2":
            summaries = {q["handle"]: q for q in raw["qdisc_summary"]}
            for q in capture["qdiscs"]:
                summary = summaries.get(q["handle"], {})
                stats = q["TCA_STATS"]
                expected = {"bytes": stats["bytes"], "packets": stats["packets"],
                            "drops": stats["drop"], "overlimits": stats["overlimits"],
                            "qlen": stats["qlen"], "backlog": stats["backlog"]}
                differences = {k: {"raw_native": v, "summary": summary.get(k)}
                               for k, v in expected.items() if summary.get(k) != v}
                if differences:
                    report["issues"].append({"phase": phase["phase"], "capture": capture["source"]["path"],
                                             "handle": q["handle"], "summary_differences": differences})
            capture["native_v2_summary_checked"] = True
            native_v2_captures.append(capture["source"]["path"])
report["native_v2_capture_count"] = len(native_v2_captures)
report["native_v2_captures"] = native_v2_captures
report["all_completed_phase_last_two_backlogs_zero"] = not report["issues"]
report["observer_summary_consistent_with_raw"] = bool(native_v2_captures) and not report["issues"]
output = root / "backlog-native-audit-v2.json"
if output.exists():
    raise RuntimeError("Refusing to overwrite prior final audit")
output.write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({k: report[k] for k in ("utc", "completed_phase_count", "native_capture_count",
                                       "native_v2_capture_count", "all_completed_phase_last_two_backlogs_zero",
                                       "observer_summary_consistent_with_raw", "issues")}))
