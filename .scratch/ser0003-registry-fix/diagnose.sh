#!/bin/sh
# Read-only: shows whether a node's containerd can find an HTTP hosts.toml for a registry.
# Usage: diagnose.sh <node> [registry=10.254.192.47:5000]
set -eu
N=$1; R=${2:-10.254.192.47:5000}
P=$(kubectl -n kube-system get pod -l app.kubernetes.io/name=prometheus-node-exporter --field-selector spec.nodeName=$N -o jsonpath='{.items[0].metadata.name}')
kubectl get node $N -o jsonpath='runtime={.status.nodeInfo.containerRuntimeVersion}{"\n"}'
kubectl -n kube-system exec $P -- sh -c "
cd /host/root/etc/containerd
grep -h -n -E 'use_local_image_pull|config_path|^imports' config.toml conf.d/*.toml 2>/dev/null
for d in certs.d /host/root/etc/docker/certs.d; do echo \"## \$d/$R\"; cat \"\$d/$R/hosts.toml\" 2>&1 || true; done"
# Interpretation (containerd 2.2.1 source): use_local_image_pull=true splits a ':'-joined config_path;
# false (transfer service) does not, so a ':'-joined value finds no hosts.toml (dev0009 case).
