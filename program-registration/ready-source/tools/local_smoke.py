"""Actual three-model CPU inference through the packaged HTTP DAG, no cluster."""
import argparse
import base64
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "experiments/real-topology-live"
sys.path.insert(0, str(LIVE))
from http_runtime import serve, request
import model_adapter


def run(out, count):
    if out.exists(): raise FileExistsError(out)
    work = ROOT / "local-runtime"
    assets = ROOT / "artifacts/topology-profile-live"
    os.environ.update(MODEL_ROOT=str(work / "models"), JELLYBEAN_SOURCE=str(work / "jellybean"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", NODE_NAME="local-cpu", POD_UID="local-http-process")
    pairs = json.loads((assets / "load-pairs.json").read_text())["pairs"][:count]
    if len(pairs) != count or not 1 <= count <= 60: raise ValueError("count must be between1 and60")
    refs = {r["question_id"]: r for r in map(json.loads, (assets / "reference-v1/results.jsonl").read_text().splitlines())}
    models = json.loads((assets / "reference-v1/metadata.json").read_text())["stages"]
    results = []
    status = "failed"
    try:
        with ExitStack() as stack:
            endpoints = {}
            for task in ("vision", "speech", "fusion"):
                adapter, metadata = model_adapter.load(task)
                endpoints[task] = stack.enter_context(serve(task, adapter, metadata=metadata))
            for pair in pairs:
                qid = pair["question_id"]
                identity = {"run_id": "package-local-smoke", "request_id": str(qid)}
                start = time.perf_counter()
                request(endpoints["fusion"] + "/open", identity)
                for task, kind in (("vision", "image"), ("speech", "audio")):
                    data = (assets / "load-cache/corpus" / Path(pair[kind + "_path"]).name).read_bytes()
                    if hashlib.sha256(data).hexdigest() != pair[kind + "_sha256"]: raise ValueError("Input hash mismatch")
                    request(endpoints[task] + "/execute", dict(identity, data=data, fusion_url=endpoints["fusion"]), timeout=120)
                response = request(endpoints["fusion"] + "/result", identity, timeout=120)
                if response["status"] != "ok": raise RuntimeError("Workflow did not complete")
                payload = base64.b64decode(response["data"])
                output = json.loads(payload)
                reference = refs[qid]
                receipts = {r["task"]: r for r in response["receipts"]}
                if len(response["receipts"]) != 3 or set(receipts) != {"vision", "speech", "fusion"}: raise ValueError("Three stage receipts required")
                for task, receipt in receipts.items():
                    if receipt["model_sha256"] != models[task]["model_sha256"] or receipt["device"] != "cpu": raise ValueError("Model identity mismatch")
                for task, kind in (("vision", "image"), ("speech", "audio")):
                    if receipts[task]["input_sha256"]["source"] != pair[kind + "_sha256"]: raise ValueError("Receipt input mismatch")
                    if receipts["fusion"]["input_sha256"][task] != receipts[task]["output_sha256"]: raise ValueError("Join receipt mismatch")
                if hashlib.sha256(payload).hexdigest() != receipts["fusion"]["output_sha256"]: raise ValueError("Output receipt mismatch")
                error = max(abs(a-b) for a,b in zip(output["scores"], reference["scores"]))
                ok = output["answer_ids"] == reference["answer_ids"] and len(output["scores"]) == len(reference["scores"]) == 5 and error <= 1e-4
                results.append({"question_id":qid,"quality_ok":ok,"max_score_abs_error":error,"e2e_ms":(time.perf_counter()-start)*1000,"output":output,"receipts":response["receipts"]})
                request(endpoints["fusion"] + "/ack", identity)
                if not ok: raise ValueError("CPU reference quality mismatch")
        status = "passed"
    finally:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("x") as f: json.dump({"status":status,"scope":"real models and HTTP DAG on one local CPU host; not cluster performance or independent replication","workflows":results},f,indent=2)
    print(json.dumps({"status":status,"workflows":len(results),"receipts":sum(len(r["receipts"]) for r in results),"out":str(out)}))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--out",type=Path,required=True); parser.add_argument("--count",type=int,default=2)
    args=parser.parse_args(); run(args.out.resolve(),args.count)
