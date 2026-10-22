# Fleet repair checkpoint 2026-10-22 UTC

User explicitly excludes dev0001/dev0004/dev0007 (not connected yet). Repair targets only dev0002 (.192.123), dev0003 (.164.138), dev0006 (.164.136), dev0008 (.192.111), dev0009 (.192.128). Prefix all IPs 10.254. SSH user etri on non-main hosts. Credentials are not recorded here.

## Applied
- Added /etc/containerd/certs.d/192.168.0.56:5000/hosts.toml on connected five, pointing server and host to http://10.254.192.47:5000 with pull,resolve. Old files absent on these five, so no overwrite. dev0001 mutation attempt timed out; unchanged.
- No edgecore/containerd restart or binary replacement performed.
- Main registry HEAD confirms same pinned digests for node-inventory-python and edgemesh-agent.
- dev0009 ctr -n k8s.io images pull --hosts-dir /etc/containerd/certs.d OLD-REGISTRY@DIGEST succeeded both images. inventory and edgemesh recovered 1/1 Running, all seven node9 pods Running. No Pod deletion.
- Main edgecore node9 PID1336203 NRestarts13 still unchanged.

## Findings
- Edgecore configured endpoints all point at new mainIP10.254.192.47 on all six successfully audited hosts. Node9 actual sockets10000/10004 ESTABLISHED.
- send-on-closed panic confirmed journals dev2/dev6/dev9; live //deleted pod volume mounts on dev2/dev3/dev6/dev8/dev9 (also excluded dev1).
- Deleted mount pod map from audit: dev2 flannel,node-gpu-exporter,inventory,edgemesh; dev3 flannel,node-gpu-exporter,inventory,edgemesh plus agentops/grafana and kubeedge/edgeai-nanomq; dev6 flannel,node-gpu-exporter,inventory,edgemesh; dev8 flannel; dev9 flannel.
- Remaining stale registry in live templates: kube-system DS node-gpu-exporter, node-hardware-inventory; kubeedge DS edgemesh-agent; agentops deployments api,worker; edgeai-devices deployment edgeai-serial-json-5d70b0f0619adeda; sh-vlm-offload deployment vlm-service. No template mutation. DS edgemesh OnDelete, others RollingUpdate. These source manifests not found in scheduler repo. Do not edit frozen experiment files to erase old evidence.
- dev9 /etc/hosts has stale slurm-w1/master records but edgecore uses literal new IP. l4tbr0 192.168.55.1 down is Jetson USB bridge, not main network.

## Incident during read-only intent
Running /usr/local/bin/edgecore --version (worker-suggested) actually launched another daemon PID1426293 at07:17:40UTC, initialized DB modules then exited bind10550 + nil cleanup panic. User informed immediately; original service PID/restart count unchanged, single process verified. NEVER run this flag again. Host binary hash d17f777d0ac97dc2cdb534e499f1df38401aa1434fba834e5ab19431e04682ac; glibc2.39; Ubuntu24.04 arm64; readOnlyPort10350 /pods200.

## Pending implementation
Orca run run_8987b78a1650. Current ClaudeOpus5.5 worker task_a228ddcf8c95 dispatch ctx_283f765f2ad8 terminal term_38bf65c4-5572-48b9-ab0b-7b03ff4ecb4a, actual model verified from previous same-terminal transcript.
Old attempt task_504a17aa18ae/ctx_35f827a33339 settled/reused, patches BOTH REJECTED by independent Astra reviews: #6836 RLock held on full-buffer blocking send deadlocks with no reader; custom /pods timeout fails open deleting live volumes and ignores HTTPcontext. DO NOT install old builds in build/.
Reviewers spec ctx_d314e0cac411 and standards ctx_dc06c32ac8ff both settled/released. Actual gpt-6-astra verified. First review round complete. Need final independent review of revised patches (max2 rounds).
Revision scratch src/kubeedge-r1, evidence-r1. P1 commit ffd81d3a7 per-connection cancel/close guard exec/attach/logs/metrics. Red original panic and PR6836 deadlock proven; targeted -race green incl full-buffer/no-consumer and real exec failed TLS dial; existing pkg/stream mock races persist on pristine. P2 currently being implemented: restore upstream PodConfig seenSources readiness semantics in kubeedge/kubernetes fork, buffer pre-list events in edged; no HTTP/UID/time fallback. Need tests + build + review before any host application.
Coordinator requested inbound exec -i stress (old script only stdout inadequate), minimal nonprivileged canary with emptyDir marker AND ConfigMap preserved after restart; sequential dev9 first then remaining4 only after pass. No benchmark currently running.

## Main host DiskPressure incident07:42UTC
At07:40 canaryPods all Pending/no events. cloudcore old ContainerStatusUnknown, replacement qt4ms Pending due main disk-pressure taint. Node transition07:36:40UTC, root468GiB total377used67available85%, imageGC wanted27GB. GOCACHE17GiB, scratch2GiB may contribute. Verified no running go; preserved cache migration completed to /mnt/data3tb/codex-scheduler-build-cache-20261022/go-build and ~/.cache/go-build now symlink. rsync --remove-source-files moved entries, empty staging directory removed. No source/evidence deletion. Root after migration355used89available81%. Worker told use data-disk GOTMPDIR and resume. Need wait actual DiskPressureFalse/cloudcore ready; no taint masking.
CanaryCM+5Pods created name diag-ec-fleet-20261022[-e2/e3/e6/e8/e9], label diag-owner=edgecore-fleet-20261022 namespace topology-profile-lab. Owned cleanup required before final. Manifests+UIDs in canaries.json/canaries-before.json. They continuously check emptyDir marker+ConfigMap; Never restart. Before snapshot at07:41 Pending because cloudcore outage.
