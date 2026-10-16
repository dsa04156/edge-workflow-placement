"""Read only host EdgeCore process metadata and its recent system journal.

Never reads process environments, command lines, keys, or other service logs.
"""

from collections import Counter
import ctypes as c
import ctypes.util
import datetime
import json
import os
from pathlib import Path
import re
import time


def main():
    result = {"node": os.environ["NODE_NAME"],
              "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "processes": [], "journals": []}
    uptime = float(Path("/host/proc/uptime").read_text().split()[0])
    for directory in Path("/host/proc").iterdir():
        if not directory.name.isdigit():
            continue
        try:
            if (directory / "comm").read_text().strip() != "edgecore":
                continue
            stat = (directory / "stat").read_text().rsplit(")", 1)[1].split()
            rss = next(line for line in (directory / "status").read_text().splitlines()
                       if line.startswith("VmRSS:"))
            result["processes"].append({"pid": int(directory.name), "state": stat[0],
                                       "uptime_seconds": uptime - int(stat[19]) / os.sysconf("SC_CLK_TCK"),
                                       "rss_kib": int(rss.split()[1])})
        except (OSError, StopIteration):
            continue
    lib = c.CDLL(ctypes.util.find_library("systemd"))
    ptr = c.c_void_p
    lib.sd_journal_open_directory.argtypes = [c.POINTER(ptr), c.c_char_p, c.c_int]
    lib.sd_journal_add_match.argtypes = [ptr, c.c_char_p, c.c_size_t]
    lib.sd_journal_seek_tail.argtypes = [ptr]
    lib.sd_journal_previous.argtypes = [ptr]
    lib.sd_journal_get_data.argtypes = [ptr, c.c_char_p, c.POINTER(ptr), c.POINTER(c.c_size_t)]
    lib.sd_journal_get_realtime_usec.argtypes = [ptr, c.POINTER(c.c_uint64)]
    lib.sd_journal_close.argtypes = [ptr]
    lib.sd_journal_close.restype = None

    def field(journal, name):
        data, size = ptr(), c.c_size_t()
        if lib.sd_journal_get_data(journal, name.encode(), c.byref(data), c.byref(size)) < 0:
            return ""
        return c.string_at(data, size.value).decode(errors="replace").split("=", 1)[1]

    since = int((time.time() - 900) * 1_000_000)
    for label, root in [("persistent", Path("/host/log/journal")),
                        ("runtime", Path("/host/run-log/journal"))]:
        directories = sorted({p.parent for p in root.glob("**/*.journal")})
        for directory in directories:
            journal = ptr()
            opened = lib.sd_journal_open_directory(c.byref(journal), str(directory).encode(), 0)
            if opened < 0:
                result["journals"].append({"storage": label, "open_errno": -opened})
                continue
            try:
                assert lib.sd_journal_add_match(journal, b"_SYSTEMD_UNIT=edgecore.service", 0) >= 0
                assert lib.sd_journal_seek_tail(journal) >= 0
                count, severity, samples, timestamps, mentions = 0, Counter(), [], [], 0
                levels, experiment_samples = Counter(), []
                errors, warnings, workflow_samples, workflow_mentions = [], [], [], 0
                while count < 10000 and lib.sd_journal_previous(journal) > 0:
                    stamp = c.c_uint64()
                    lib.sd_journal_get_realtime_usec(journal, c.byref(stamp))
                    if stamp.value < since:
                        break
                    count += 1
                    severity[field(journal, "PRIORITY")] += 1
                    text = field(journal, "MESSAGE")
                    timestamps.append(stamp.value)
                    is_experiment = os.environ["RUN_ID"] in text
                    mentions += is_experiment
                    is_workflow = ("e0-wire-" + os.environ["RUN_ID"]) in text
                    workflow_mentions += is_workflow
                    match = re.match(r"([IWEF])\d{4}", text)
                    levels[match[1] if match else "other"] += 1
                    text = re.sub(r"https?://\S+", "[url]", text)
                    text = re.sub(r"(?i)(bearer\s+|token[=: ]+|password[=: ]+)\S+", r"\1[redacted]", text)
                    if len(samples) < 5:
                        samples.append(text[:500])
                    if is_experiment and len(experiment_samples) < 3:
                        experiment_samples.append(text[:500])
                    if is_workflow and len(workflow_samples) < 3:
                        workflow_samples.append(text[:500])
                    if match and match[1] in "EF" and len(errors) < 5:
                        errors.append(text[:500])
                    if match and match[1] == "W" and len(warnings) < 5:
                        warnings.append(text[:500])
                result["journals"].append({"storage": label, "window_seconds": 900,
                                           "entry_count": count, "count_limit_reached": count == 10000,
                                           "priority_counts": dict(severity),
                                           "message_level_counts": dict(levels),
                                           "first_realtime_usec": min(timestamps, default=None),
                                           "last_realtime_usec": max(timestamps, default=None),
                                           "experiment_mentions": mentions, "last_messages": samples,
                                           "experiment_messages": experiment_samples,
                                           "workflow_mentions": workflow_mentions,
                                           "workflow_messages": workflow_samples,
                                           "error_samples": errors, "warning_samples": warnings})
            finally:
                lib.sd_journal_close(journal)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
