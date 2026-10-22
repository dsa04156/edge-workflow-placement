#!/usr/bin/env bash
# edgecore r1 patch deploy/verify/rollback. Run ON the edge host via the
# coordinator's SSH session (sudo). Nothing here runs automatically.
# NEVER run "edgecore --version" on a host: it starts a second daemon.
#
#   ./edgecore-fix.sh preflight               # read-only
#   CONFIRM=install ./edgecore-fix.sh install /tmp/edgecore.new <sha256>
#   ./edgecore-fix.sh verify <since-UTC> [canary-pod-uid]   # read-only
#   CONFIRM=rollback ./edgecore-fix.sh rollback
set -euo pipefail

BIN=/usr/local/bin/edgecore
# installed original on dev0009; override per node after preflight.
ORIG_SHA=${ORIG_SHA:-d17f777d0ac97dc2cdb534e499f1df38401aa1434fba834e5ab19431e04682ac}
BACKUP=/usr/local/bin/edgecore.v1.23.0-${ORIG_SHA:0:12}.bak

preflight() {
  echo "## host";  uname -m; head -3 /etc/os-release; ldd --version | head -1
  echo "## binary"; sha256sum "$BIN"; ls -l "$BIN"; ls -l "$BACKUP" 2>/dev/null || echo "no backup yet"
  echo "## unit";  systemctl show edgecore -p ExecStart,Restart,RestartUSec,KillMode,NRestarts,ActiveEnterTimestamp,ActiveState
  echo "## config (non-secret keys only)"
  grep -n -E '^\s*(readOnlyPort|enable|featureGates):' /etc/kubeedge/config/edgecore.yaml | head -40
  echo "## stale (//deleted) pod volume mounts in running containers"
  grep -h -o 'kubernetes.io~[a-z-]*/[^ ]*//deleted' /proc/[0-9]*/mountinfo 2>/dev/null | sort | uniq -c || true
  df -h /usr/local/bin
}

install_bin() {
  [[ "${CONFIRM:-}" == install ]] || { echo "set CONFIRM=install"; exit 2; }
  local new=$1 want=$2
  [[ "$(sha256sum "$new" | cut -d' ' -f1)" == "$want" ]] || { echo "new binary sha mismatch"; exit 1; }
  [[ "$(sha256sum "$BIN" | cut -d' ' -f1)" == "$ORIG_SHA" ]] || { echo "current binary is not the expected v1.23.0 ($ORIG_SHA)"; exit 1; }
  [[ -e "$BACKUP" ]] || cp -p "$BIN" "$BACKUP"
  [[ "$(sha256sum "$BACKUP" | cut -d' ' -f1)" == "$ORIG_SHA" ]] || { echo "backup sha mismatch"; exit 1; }
  install -m 0755 "$new" "$BIN.new"
  "$BIN.new" --minconfig >/dev/null   # exits before any daemon start; proves glibc/arch
  mv -f "$BIN.new" "$BIN"             # atomic; the running process keeps the old inode
  date -u +%Y-%m-%dT%H:%M:%SZ | tee /tmp/edgecore-fix.restart-at
  systemctl restart edgecore
  sleep 15
  systemctl show edgecore -p NRestarts,ActiveEnterTimestamp,ActiveState,MainPID
}

verify() {
  local since=$1 uid=${2:-}
  local log; log=$(journalctl -u edgecore --since "$since" --no-pager -o short-iso-precise)
  echo "## version";            grep -m1 'server.go.*Version:' <<<"$log" || true
  echo "## panic count (expect 0)"; grep -c 'panic:' <<<"$log" || true
  echo "## startup order: populator must finish only after kubelet starts syncing pods"
  grep -n -E 'Skipping pod synchronization|Finished populating initial desired state|Start sync pod|Starting kubelet main sync loop' <<<"$log" | head -20 || true
  echo "## UnmountVolume started since restart (expect 0 for running pods)"
  grep 'operationExecutor.UnmountVolume started' <<<"$log" | grep -o 'pod \\"[0-9a-f-]*' | sort | uniq -c || true
  if [[ -n "$uid" ]]; then
    echo "## canary $uid unmounts (expect 0)"; grep -c "UnmountVolume.*$uid" <<<"$log" || true
    echo "## canary //deleted mounts on host (expect 0)"; grep -h "$uid" /proc/[0-9]*/mountinfo 2>/dev/null | grep -c '//deleted' || true
  fi
  systemctl show edgecore -p NRestarts,ActiveEnterTimestamp,ActiveState,MainPID
}

rollback() {
  [[ "${CONFIRM:-}" == rollback ]] || { echo "set CONFIRM=rollback"; exit 2; }
  [[ "$(sha256sum "$BACKUP" | cut -d' ' -f1)" == "$ORIG_SHA" ]] || { echo "backup missing/mismatch"; exit 1; }
  install -m 0755 "$BACKUP" "$BIN.new" && mv -f "$BIN.new" "$BIN"
  # NOTE: this restart runs the unpatched startup path again, so pods that are
  # running now will lose emptyDir/ConfigMap contents; recreate them afterwards.
  systemctl restart edgecore
  sleep 15; sha256sum "$BIN"; systemctl show edgecore -p NRestarts,ActiveEnterTimestamp,ActiveState
}

case "${1:-}" in
  preflight) preflight ;;
  install)   install_bin "$2" "$3" ;;
  verify)    verify "$2" "${3:-}" ;;
  rollback)  rollback ;;
  *) sed -n '2,7p' "$0"; exit 2 ;;
esac
