# Jetson qualification preflight — 2026-10-20 19:36 KST

Scope: read-only cluster status and TCP/22 reachability from scheduler host.

- etri-dev0001-jetorn (10.254.164.139): Ready=Unknown/NodeStatusUnknown; last heartbeat 2026-10-20T08:27:58Z. TCP/22 timed out.
- etri-dev0007-jetagx (10.254.164.140): Ready=Unknown/NodeStatusUnknown; last heartbeat 2026-10-20T08:30:14Z. TCP/22 timed out.
- Control hosts 10.254.164.138, 10.254.164.136, 10.254.192.128: TCP/22 reachable.

These observations do not distinguish power-off, route/VPN/firewall failure, or host/service failure. Kubernetes records are last-reported state; Running pod phase is not proof of current execution. No device identity/GPU/model qualification occurred. The jetson-diagnostic host snapshot could not run because host access was unavailable. No workloads or services were changed.

Next prerequisite: restore access to both Jetsons or provide their current reachable addresses. Then run the jetson-diagnostic snapshot and verify actual model/runtime execution and output quality before measuring task-node costs.

Evidence: nodes.json, reachability.json, control-reachability.json, jetson-pods.json. Semantica unavailable: tool discovery returned unsupported call; no graph record written.
