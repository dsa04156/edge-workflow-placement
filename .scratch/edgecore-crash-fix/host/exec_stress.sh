#!/usr/bin/env bash
# Exercise the dev0009 panic path on the patched node: the cloud keeps sending
# exec stdin (MessageTypeData -> ReadChan) while the container side has already
# closed, so edge writes hit broken pipe and the local connection is deleted
# with data still in flight. Run from the kubectl workstation.
#   ./exec_stress.sh <namespace> <pod> <container> [iterations]
# Pass criteria: edgecore NRestarts unchanged and no 'panic:' in the journal
# since the start (check with edgecore-fix.sh verify on the host).
set -uo pipefail
ns=$1 pod=$2 c=$3 n=${4:-50}
for i in $(seq 1 "$n"); do
  head -c 64M /dev/zero | timeout -s KILL 10 rtk proxy kubectl -n "$ns" exec -i "$pod" -c "$c" -- \
    python3 -c "import sys,os; sys.stdin.buffer.read(1); os._exit(0)" >/dev/null 2>&1
  printf '%s ' "$i"
done
echo
