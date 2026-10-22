"""Generate a minimal no-model replay of the source Pod's /work lifecycle (diagnosis only)."""
import json, sys
IMAGE = "10.254.192.47:5000/node-inventory-python@sha256:370a8339507905053693d178e1cda8fbf77937f045ead58a3c32d8cead2ca67f"
name, node = sys.argv[1], sys.argv[2]
worker = ("import os,time,json;os.makedirs('/work/config');os.makedirs('/work/tmp',exist_ok=True);"
          "print(json.dumps(dict(event='worker_ready',ino=os.stat('/work').st_ino)),flush=True);time.sleep(3600)")
netctl = ("import os,time,json,pathlib;os.makedirs('/work/net-config');pathlib.Path('/work/netctl-ready').write_text('ready');"
          "print(json.dumps(dict(event='netctl_ready',ino=os.stat('/work').st_ino)),flush=True);time.sleep(3600)")
pod = {
 "apiVersion": "v1", "kind": "Pod",
 "metadata": {"name": name, "namespace": "topology-profile-lab",
              "labels": {"experiment": "dev0009-workdir-diagnosis", "diag-owner": "task_25483a5c9141"}},
 "spec": {
  "activeDeadlineSeconds": 3600, "automountServiceAccountToken": False, "restartPolicy": "Never",
  "terminationGracePeriodSeconds": 3, "nodeName": node,
  "securityContext": {"fsGroup": 65532},
  "containers": [
   {"name": "worker", "image": IMAGE, "imagePullPolicy": "IfNotPresent", "command": ["python3", "-u", "-c", worker],
    "resources": {"limits": {"cpu": "200m", "memory": "64Mi", "ephemeral-storage": "8Gi"},
                  "requests": {"cpu": "10m", "memory": "32Mi", "ephemeral-storage": "128Mi"}},
    "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]},
                        "readOnlyRootFilesystem": True, "runAsGroup": 65532, "runAsNonRoot": True, "runAsUser": 65532},
    "volumeMounts": [{"mountPath": "/work", "name": "work"}]},
   {"name": "netctl", "image": IMAGE, "command": ["python3", "-u", "-c", netctl],
    "readinessProbe": {"exec": {"command": ["python3", "-c", "from pathlib import Path;assert Path('/work/netctl-ready').is_file()"]},
                       "periodSeconds": 2, "timeoutSeconds": 3},
    "resources": {"limits": {"cpu": "200m", "memory": "96Mi"}, "requests": {"cpu": "10m", "memory": "32Mi"}},
    "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"add": ["NET_ADMIN"], "drop": ["ALL"]},
                        "readOnlyRootFilesystem": True, "runAsNonRoot": False, "runAsUser": 0},
    "volumeMounts": [{"mountPath": "/work", "name": "work"}]}],
  "volumes": [{"name": "work", "emptyDir": {"sizeLimit": "8Gi"}}]}}
print(json.dumps(pod, indent=1))
