#!/usr/bin/env bash
# dev0009 edgecore patch deploy/verify/rollback. Run ON the edge host via the
# coordinator's SSH session (sudo). Nothing here runs automatically.
#
#   ./edgecore-fix.sh preflight               # read-only
#   CONFIRM=install ./edgecore-fix.sh install /tmp/edgecore.new <sha256>
#   ./edgecore-fix.sh verify <since-UTC> [canary-pod-uid]   # read-only
#   CONFIRM=rollback ./edgecore-fix.sh rollback
set -euo pipefail

BIN=/usr/local/bin/edgecore
ORIG_SHA=d17f777d0ac97dc2cdb534e499f1df38401aa1434fba834e5ab19431e04682ac
BACKUP=/usr/local/bin/edgecore.v1.23.0-${ORIG_SHA:0:12}.bak

preflight() {
  echo "## host";  uname -m; head -3 /etc/os-release; ldd --version | head -1
  echo "## binary"; sha256sum "$BIN"; ls -l "$BIN"; ls -l "$BACKUP" 2>/dev/null || echo "no backup yet"
  echo "## unit";  systemctl show edgecore -p ExecStart,Restart,RestartUSec,KillMode,NRestarts,ActiveEnterTimestamp,ActiveState
  echo "## config (non-secret keys only)"
  grep -n -E '^\s*(readOnlyPort|enable|featureGates):' /etc/kubeedge/config/edgecore.yaml | head -40
  echo "## kubelet read-only /pods (patch 2 dependency)"
  curl -s -o /dev/null -w '%{http_code}\n' http://localhost:10350/pods
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
  "$BIN.new" --minconfig >/dev/null   # binary runs on this host (glibc/arch)
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
  echo "## panic count";        grep -c 'panic:' <<<"$log" || true
  echo "## sources-ready gate (patch 2) vs populator";
  grep -n -E 'initial pods, marking pod sources ready|Finished populating initial desired state' <<<"$log" || true
  echo "## UnmountVolume started at startup (expect none for running pods)"
  grep -c 'operationExecutor.UnmountVolume started' <<<"$log" || true
  if [[ -n "$uid" ]]; then
    echo "## canary $uid unmounts (expect 0)"; grep -c "UnmountVolume.*$uid" <<<"$log" || true
    echo "## canary mounts on host"; grep -h "$uid" /proc/[0-9]*/mountinfo 2>/dev/null | grep -c '//deleted' || true
  fi
  systemctl show edgecore -p NRestarts,ActiveEnterTimestamp,ActiveState
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
