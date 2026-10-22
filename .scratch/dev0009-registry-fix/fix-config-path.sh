set -eu
H="nsenter -t 1 -m -u -i -n -p --"
F=/etc/containerd/config.toml
B=/etc/containerd/config.toml.bak-dev0009-registry-fix-20261022
EXP_SHA=27b61697b146c7e2ac31b3af9310ff9fcb686a729f5b1e8c241da1b6aaec32c4
OLD='      config_path = "/etc/containerd/certs.d:/etc/docker/certs.d"'
NEW='      config_path = "/etc/containerd/certs.d"'
cur=$($H sha256sum $F | cut -d' ' -f1)
echo "pre_sha=$cur"
[ "$cur" = "$EXP_SHA" ] || { echo "UNEXPECTED_SHA_ABORT"; exit 3; }
[ "$($H grep -c -x -F "$OLD" $F)" = 1 ] || { echo "OLD_LINE_NOT_UNIQUE_ABORT"; exit 3; }
if $H test -e $B; then [ "$($H sha256sum $B | cut -d' ' -f1)" = "$EXP_SHA" ] || { echo "BACKUP_MISMATCH_ABORT"; exit 3; }; echo "backup_preexisting_kept"; else $H cp -a $F $B; fi
echo "backup_sha=$($H sha256sum $B | cut -d' ' -f1)"
$H sed -i "s#^      config_path = \"/etc/containerd/certs.d:/etc/docker/certs.d\"\$#$NEW#" $F
echo "post_sha=$($H sha256sum $F | cut -d' ' -f1)"
echo "--- diff"; $H diff -u $B $F || true
echo "--- config dump (validation, selected keys)"
if ! $H containerd config dump > /tmp/dump.$$ 2>&1; then echo "DUMP_FAILED_RESTORE"; head -5 /tmp/dump.$$; $H cp -a $B $F; exit 4; fi
grep -n -E "config_path|default_runtime_name|BinaryName|use_local_image_pull|^imports" /tmp/dump.$$ || true
grep -q -x -F "      config_path = '/etc/containerd/certs.d'" /tmp/dump.$$ || { echo "EFFECTIVE_NOT_UPDATED_RESTORE"; $H cp -a $B $F; exit 4; }
echo "--- restart containerd only"
echo "pre_restart_active=$($H systemctl is-active containerd) pid=$($H systemctl show -p MainPID --value containerd)"
$H systemctl restart containerd
sleep 3
echo "post_restart_active=$($H systemctl is-active containerd) pid=$($H systemctl show -p MainPID --value containerd)"
echo "edgecore_active=$($H systemctl is-active edgecore)"
echo CONFIG_PATH_FIX_DONE
