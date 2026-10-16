"""Freeze a fresh run and its seeded client-block schedule before collection."""

import argparse
import copy
from datetime import datetime, timezone
import importlib.util
import itertools
import json
from pathlib import Path

import pandas as pd


parser = argparse.ArgumentParser()
parser.add_argument("--randomization", required=True, type=Path)
args = parser.parse_args()
spec = importlib.util.spec_from_file_location("randomization", args.randomization)
randomization = importlib.util.module_from_spec(spec)
spec.loader.exec_module(randomization)
base = Path("artifacts/e0-rpc-profile")
base.mkdir(exist_ok=True)
assert not (base / "run.json").exists(), "Archive the previous output directory before preparing a new run"
run_id = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%S")
prefix, namespace = "e0-rpc-" + run_id, "scheduler-research"
labels = {"app.kubernetes.io/part-of": "scheduler-research", "research.scheduler/stage": "e0-rpc-profile",
          "research.scheduler/run": run_id}
meta = lambda name: {"name": name, "namespace": namespace, "labels": labels}
old = json.loads(Path("artifacts/e0-transport/servers.json").read_text())
template = next(x for x in old["items"] if x["kind"] == "Pod")
accel = json.loads(Path("artifacts/e0-accelerators/run.json").read_text())["targets"]
targets = [{"alias": "arm", "node": "etri-dev0003-raspi5", "backend": "cpu"},
           {"alias": "amd", "node": "etri-ser0004-cgnms0", "backend": "cpu"},
           *[{**x, "backend": x["alias"]} for x in accel]]
for t in targets:
    t["url"] = f"http://{prefix}-{t['alias'].replace('_','-')}.{namespace}.svc.cluster.local:8080"
rows = [{"id": f"{t['alias']}-{'reuse' if reuse else 'new'}-nd{nd}", "target": t["alias"],
         "reuse": reuse, "nodelay": nd} for t, reuse, nd in itertools.product(targets, [False, True], [0, 1])]
schedules = {str(b): randomization.assign_factorial_runs(pd.DataFrame(rows), seed=20261016+b).to_dict("records")
             for b in range(3)}
data = {name: (Path("experiments/e0") / name).read_text() for name in
        ["probe.py", "transport.py", "accelerator_inference.py", "rpc_profile.py"]}
data["requirements-cpu.txt"] = Path("artifacts/e0/requirements-resolved.txt").read_text()
data["requirements-intel_npu.txt"] = Path("artifacts/e0-accelerators/requirements-intel_npu.txt").read_text()
gpu_packages = Path("artifacts/e0-accelerators/gpu-installed.txt").read_text().splitlines()
gpu_urls = Path("artifacts/e0-accelerators/requirements-gpu.txt").read_text().splitlines()[:2]
data["requirements-gpu.txt"] = "\n".join(gpu_urls + ["==".join(s.rsplit("-", 1)) for s in gpu_packages
                                                          if not s.startswith(("torch-", "torchvision-"))]) + "\n"
cm = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": meta(prefix), "immutable": True, "data": data}
worker_items = [cm]
workers, services = [], []
for target in targets:
    name = prefix + "-" + target["alias"].replace("_", "-")
    workers.append(name); services.append(name)
    pod = copy.deepcopy(template)
    pod["metadata"] = meta(name)
    pod["metadata"]["labels"] = {**labels, "research.scheduler/worker": target["alias"]}
    pod["metadata"]["annotations"] = {"sidecar.istio.io/inject": "false"}
    s = pod["spec"]
    s["activeDeadlineSeconds"] = 1800
    s["nodeSelector"] = {"kubernetes.io/hostname": target["node"]}
    for v in s["volumes"]:
        if "configMap" in v: v["configMap"]["name"] = prefix
    c = s["containers"][0]
    c["env"] = [{"name": "NODE_NAME", "valueFrom": {"fieldRef": {"fieldPath": "spec.nodeName"}}},
                *[{"name": k, "value": v} for k, v in {"MODE": "server", "BACKEND": target["backend"],
                  "PYTHONDONTWRITEBYTECODE": "1", "XDG_CACHE_HOME": "/work/cache", "TORCH_HOME": "/work/torch-cache"}.items()]]
    c["workingDir"] = "/work"
    bootstrap = "import subprocess,sys,runpy,time,json; t=time.perf_counter(); subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','--no-cache-dir','--no-compile','--retries','0','--timeout','60','--target','/work/site','-r','/config/requirements-" + target["backend"] + ".txt'],check=True); print(json.dumps({'kind':'diagnostic_install','ms':(time.perf_counter()-t)*1000}),flush=True); sys.path[:0]=['/work/site','/config']; runpy.run_path('/config/rpc_profile.py',run_name='__main__')"
    c["command"] = ["python3", "-u", "-c", bootstrap]
    if target["backend"] != "cpu":
        c["image"] = target["image"]
        s["securityContext"] = {"runAsUser": 0, "seccompProfile": {"type": "RuntimeDefault"}}
        c["resources"] = {"requests": {"cpu": "250m", "memory": "1Gi", target["resource"]: "1", "ephemeral-storage": "1Gi"},
                          "limits": {"cpu": "2", "memory": "8Gi" if target["backend"] == "gpu" else "3Gi", target["resource"]: "1", "ephemeral-storage": "16Gi"}}
        for v in s["volumes"]:
            if "emptyDir" in v: v["emptyDir"]["sizeLimit"] = "12Gi" if v["name"] == "work" else "8Gi"
        if "runtimeClassName" in target: s["runtimeClassName"] = target["runtimeClassName"]
    worker_items += [pod, {"apiVersion": "v1", "kind": "Service", "metadata": meta(name),
                          "spec": {"selector": {**labels, "research.scheduler/worker": target["alias"]},
                                   "ports": [{"port": 8080, "targetPort": 8080}]}}]
client_items = []
for block in range(3):
    client = json.loads(Path("artifacts/e0-transport/client-arm.json").read_text())
    client["metadata"] = meta(prefix + f"-client-{block}")
    client["spec"]["activeDeadlineSeconds"] = 300
    client["spec"]["template"]["metadata"] = {"labels": labels, "annotations": {"sidecar.istio.io/inject": "false"}}
    s = client["spec"]["template"]["spec"]
    s["volumes"][0]["configMap"]["name"] = prefix
    c = s["containers"][0]
    c["command"] = copy.deepcopy(next(x for x in worker_items if x["kind"] == "Pod")["spec"]["containers"][0]["command"])
    c["env"] = [{"name": "NODE_NAME", "valueFrom": {"fieldRef": {"fieldPath": "spec.nodeName"}}},
                *[{"name": k, "value": v} for k,v in {"MODE":"client", "BLOCK":str(block), "RUN_ID":run_id,
                  "TARGETS_JSON":json.dumps(targets), "SCHEDULE_JSON":json.dumps(schedules[str(block)]),
                  "PYTHONDONTWRITEBYTECODE":"1", "XDG_CACHE_HOME":"/work/cache"}.items()]]
    client_items.append(client)
    (base / f"client-{block}.json").write_text(json.dumps(client,indent=2)+"\n")
(base / "workers.json").write_text(json.dumps({"apiVersion":"v1","kind":"List","items":worker_items},indent=2)+"\n")
(base / "schedule.json").write_text(json.dumps(schedules,indent=2)+"\n")
run = {"run_id": run_id, "namespace": namespace, "configmap": prefix, "worker_pods": workers,
       "services": services, "client_jobs": [x["metadata"]["name"] for x in client_items], "targets": targets,
       "design": {"blocks": 3, "seed_base": 20261016, "conditions_per_block":16,"warmup_per_condition":2,
                  "measured_per_condition":10,"cycle_interval_s":0.2,"shared_warm_worker_instances":True,
                  "scope":"single_fixture_paced_profile_not_policy_or_independent_deployment_comparison"}}
(base / "run.json").write_text(json.dumps(run,indent=2)+"\n")
print(json.dumps({"run_id": run_id, "workers": workers, "planned_requests":576,"measured_requests":480}))
