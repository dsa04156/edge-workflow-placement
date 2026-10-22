#!/usr/bin/env bash
# usage: run_probe.sh <pod> <label>  -> runs probe.py via exec in netctl and worker containers
set -u
for c in netctl worker; do
  rtk proxy kubectl --request-timeout=20s -n topology-profile-lab exec "$1" -c "$c" -- python3 -c "$(cat "$(dirname "$0")/probe.py")" "$2-$c"
done
