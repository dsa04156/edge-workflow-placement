#!/usr/bin/env python3
"""Descriptive analysis of captured originals. No interpolation, no resampling."""
import collections, datetime as dt, hashlib, json, statistics, sys
from pathlib import Path

A = Path(sys.argv[1])
CH = ["temp_humidity", "temp_pressure", "humidity", "pressure", "compass", "pitch", "roll", "yaw", "gyro_x", "gyro_y", "gyro_z"]
ms = lambda s: int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
pct = lambda xs, q: sorted(xs)[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))]
out = {"history": {}, "alignment": {}, "freshness": {}, "mqtt": {}}

obs_sets = {}
for ch in CH:
    items = json.loads((A / f"history/{ch}.json").read_bytes())["items"]
    t = [ms(i["observedAt"]) for i in items]
    raw = [json.loads(i["rawPayload"]) for i in items]
    sessions = collections.Counter(i["sessionId"] for i in items)
    seq_breaks, seq_dups = [], 0
    for a, b in zip(items, items[1:]):
        if a["sessionId"] == b["sessionId"]:
            d = b["sequence"] - a["sequence"]
            if d == 0: seq_dups += 1
            elif d != 1: seq_breaks.append({"after": a["observedAt"], "seqFrom": a["sequence"], "seqTo": b["sequence"]})
        else:
            seq_breaks.append({"after": a["observedAt"], "sessionChange": [a["sessionId"], b["sessionId"]], "seqTo": b["sequence"]})
    dts = [b - a for a, b in zip(t, t[1:])]
    vals = [i["value"] for i in items]
    mapper_lag = [r["receivedAt"] - r["observedAt"] for r in raw]          # dev0003 clock, publish - origin
    coll_lag = [ms(i["receivedAt"]) - ms(i["observedAt"]) for i in items]  # server clock - dev0003 clock (skew included)
    obs_sets[ch] = set(t)
    out["history"][ch] = {
        "rows": len(items), "first": items[0]["observedAt"], "last": items[-1]["observedAt"],
        "sessions": dict(sessions), "sequenceRange": [items[0]["sequence"], items[-1]["sequence"]],
        "sequenceBreaks": seq_breaks, "sequenceDuplicates": seq_dups,
        "duplicateObservedAt": len(t) - len(set(t)), "nonMonotonic": sum(d <= 0 for d in dts),
        "intervalMs": {"min": min(dts), "p50": statistics.median(dts), "p99": pct(dts, .99), "max": max(dts), "count>1500": sum(d > 1500 for d in dts)},
        "qualities": dict(collections.Counter(i["quality"] for i in items)), "timeBasis": dict(collections.Counter(i["timeBasis"] for i in items)),
        "configurationSha256": sorted({i["configurationSha256"] for i in items}), "sourceGeneration": sorted({i["sourceGeneration"] for i in items}),
        "unit": sorted({i["unit"] for i in items}), "value": {"min": min(vals), "max": max(vals), "distinct": len(set(vals)), "repeatsOfPrevious": sum(a == b for a, b in zip(vals, vals[1:]))},
        "mapperPublishMinusOriginMs": {"p50": statistics.median(mapper_lag), "max": max(mapper_lag)},
        "collectorReceiveMinusOriginMs_crossClock": {"p50": statistics.median(coll_lag), "min": min(coll_lag), "max": max(coll_lag)},
    }
common = set.intersection(*obs_sets.values()); union = set.union(*obs_sets.values())
out["alignment"] = {"observedAtSharedByAll11": len(common), "observedAtUnion": len(union),
                    "perChannelNotInCommon": {c: len(s - common) for c, s in obs_sets.items()}}

reads = [json.loads((A / f"reads/read-{n}.json").read_bytes()) for n in (1, 2)]
for r in reads:
    out["freshness"][r["label"]] = {
        "capturedAt": r["capturedAt"],
        "latestObservedAt": {c: r["latestOriginals"][c]["response"]["items"][0]["observedAt"] for c in CH},
        "latestQuality": {c: r["latestOriginals"][c]["response"]["items"][0]["quality"] for c in CH},
        "latestQualifiedObservedAt": {c: next(i["observedAt"] for i in r["latestOriginals"][c]["response"]["items"] if i["quality"] == "QUALIFIED") for c in CH},
        "latestSequence": {c: next(i["sequence"] for i in r["latestOriginals"][c]["response"]["items"] if i["quality"] == "QUALIFIED") for c in CH},
        "twinReportedTsMs": {k["property"]: k["deviceStatus"]["twins"][0]["reportedTimestampMs"] for k in r["kube"]},
        "twinEnvelopeCollectedAt": {k["property"]: (k["deviceStatus"]["twins"][0]["reportedEnvelope"] or {}).get("collectedAt") for k in r["kube"]},
        "deviceStatusState": {k["property"]: k["deviceStatus"]["state"] for k in r["kube"]},
        "lastOnlineTime": sorted({k["deviceStatus"]["lastOnlineTime"] for k in r["kube"]}),
        "collector": r["catalog"]["collector"],
    }
d1, d2 = (out["freshness"][k] for k in ("read-1", "read-2"))
out["freshness"]["deltaSequence"] = {c: d2["latestSequence"][c] - d1["latestSequence"][c] for c in CH}
out["freshness"]["deltaObservedAtMs"] = {c: ms(d2["latestObservedAt"][c]) - ms(d1["latestObservedAt"][c]) for c in CH}

lines = (A / "mqtt/live-25s.raw.txt").read_text().splitlines()
topics = collections.defaultdict(list)
for ln in lines:
    recv, topic, payload = ln.split("|", 2)
    if "sensehat-001-" not in topic: continue
    topics[topic].append((float(recv), payload))
q = {}
for topic, msgs in sorted(topics.items()):
    if topic.startswith("edgeai/observations/"):
        p = [json.loads(x) for _, x in msgs]
        seqs = [x["sequence"] for x in p]
        q[topic] = {"messages": len(p), "sessions": sorted({x["sessionId"] for x in p}), "seq": [seqs[0], seqs[-1]],
                    "seqContiguous": all(b - a == 1 for a, b in zip(seqs, seqs[1:])), "firstObservedAt": p[0]["observedAt"], "lastObservedAt": p[-1]["observedAt"]}
    else:
        q[topic] = {"messages": len(msgs)}
out["mqtt"] = {"linesTotal": len(lines), "sensehatTopics": len(topics), "topics": q,
               "rawSha256": hashlib.sha256((A / "mqtt/live-25s.raw.txt").read_bytes()).hexdigest()}
print(json.dumps(out, indent=1, ensure_ascii=False))
