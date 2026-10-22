#!/usr/bin/env python3
"""Read-only Sense HAT discovery capture. GET-only API calls, kubectl get with field projection."""
import base64, datetime as dt, hashlib, json, subprocess, sys, time, urllib.request
from pathlib import Path

API = "http://10.104.123.82:18080"  # edgeai/edgeai-api ClusterIP, port 18080
NS = "edgeai-devices"
OUT = Path(sys.argv[1])
CHANNELS = ["temp_humidity", "temp_pressure", "humidity", "pressure", "compass", "pitch", "roll", "yaw", "gyro_x", "gyro_y", "gyro_z"]


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def get(path):
    t0 = now(); m0 = time.monotonic_ns()
    with urllib.request.urlopen(API + path, timeout=20) as r:
        body = r.read(); code = r.status
    return {"requestedAt": t0, "fetchMs": (time.monotonic_ns() - m0) / 1e6, "status": code, "path": path}, body


def kubectl_json(*args):
    return json.loads(subprocess.run(["kubectl", *args, "-o", "json"], check=True, capture_output=True, text=True).stdout)


def write(rel, data):
    p = OUT / rel; p.parent.mkdir(parents=True, exist_ok=True)
    assert not p.exists(), f"refusing to overwrite {p}"
    p.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def channel_map():
    meta, body = get("/api/v1/data-store")
    cat = json.loads(body)
    rows = {s["property"]: s for s in cat["sensors"] if s["deviceId"].startswith("sensehat-001-")}
    return meta, cat, rows


def device_projection():
    devs = kubectl_json("-n", NS, "get", "devices.devices.kubeedge.io")
    sts = {i["metadata"]["name"]: i for i in kubectl_json("-n", NS, "get", "devicestatuses.devices.kubeedge.io")["items"]}
    out = []
    for d in devs["items"]:
        m = d["metadata"]
        if not m["name"].startswith("sensehat-001-"):
            continue
        s = d["spec"]; p = s["properties"][0]; v = p["visitors"]["configData"]
        st = sts.get(m["name"], {}); status = st.get("status") or {}
        twins = []
        for t in status.get("twins", []):
            rep = (t.get("reported") or {}); val = rep.get("value", "")
            decoded = None
            if val.startswith("edgeai.measurement/v1:"):
                enc = val.split(":", 1)[1]
                decoded = json.loads(base64.urlsafe_b64decode(enc + "=" * (-len(enc) % 4)))
            twins.append({"propertyName": t.get("propertyName"), "reportedTimestampMs": (rep.get("metadata") or {}).get("timestamp"), "reportedEnvelope": decoded})
        out.append({
            "device": m["name"], "deviceUid": m["uid"], "generation": m.get("generation"), "resourceVersion": m.get("resourceVersion"),
            "sourceGenerationAnnotation": (m.get("annotations") or {}).get("edgeai.io/source-generation"),
            "nodeName": s.get("nodeName"), "deviceModel": (s.get("deviceModelRef") or {}).get("name"),
            "protocolName": (s.get("protocol") or {}).get("protocolName"), "protocolConfig": (s.get("protocol") or {}).get("configData"),
            "property": p["name"], "collectCycleMs": p.get("collectCycle"), "reportCycleMs": p.get("reportCycle"),
            "mqttTopic": ((p.get("pushMethod") or {}).get("mqtt") or {}).get("topic"),
            "visitor": {k: v.get(k) for k in ["dataType", "unit", "scale", "offset", "jsonPath", "configurationSha256", "sourceGeneration"]},
            "deviceStatus": {"generation": (st.get("metadata") or {}).get("generation"), "resourceVersion": (st.get("metadata") or {}).get("resourceVersion"),
                             "state": status.get("state"), "lastOnlineTime": status.get("lastOnlineTime"), "twins": twins},
        })
    return sorted(out, key=lambda r: r["property"])


def point_read(label):
    rec = {"label": label, "capturedAt": now()}
    meta, cat, rows = channel_map()
    rec["catalog"] = {"request": meta, "asOf": cat.get("asOf"), "collector": cat.get("collector"), "sensehat": rows}
    rec["kube"] = device_projection(); rec["kubeCapturedAt"] = now()
    latest = {}
    for ch in CHANNELS:
        uid = rows[ch]["deviceUid"]
        m, body = get(f"/api/v1/data-store/sensors/{uid}/{ch}/history?order=desc&limit=3")
        latest[ch] = {"request": m, "response": json.loads(body)}
    rec["latestOriginals"] = latest
    return rec


def main():
    mode = sys.argv[2]
    if mode == "point":
        rec = point_read(sys.argv[3])
        print(write(f"reads/{sys.argv[3]}.json", json.dumps(rec, ensure_ascii=False, indent=1).encode()), f"reads/{sys.argv[3]}.json")
    elif mode == "history":
        _, cat, rows = channel_map()
        end = dt.datetime.now(dt.timezone.utc).replace(microsecond=0); start = end - dt.timedelta(minutes=15)
        f, t = start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")
        manifest = {"window": {"from": f, "to": t, "limit": 2000, "order": "asc", "followCursor": False}, "api": API, "files": {}}
        for ch in CHANNELS:
            uid = rows[ch]["deviceUid"]
            m, body = get(f"/api/v1/data-store/sensors/{uid}/{ch}/history?from={f}&to={t}&limit=2000&order=asc")
            rel = f"history/{ch}.json"
            manifest["files"][rel] = {"deviceUid": uid, "property": ch, "request": m, "sha256": write(rel, body), "bytes": len(body),
                                       "items": len(json.loads(body)["items"]), "truncated": json.loads(body)["truncated"]}
        write("history/manifest.json", json.dumps(manifest, indent=1).encode())
        print(json.dumps({k: (v["items"], v["truncated"]) for k, v in manifest["files"].items()}))


main()
