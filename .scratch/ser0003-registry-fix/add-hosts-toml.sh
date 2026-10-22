# Runs inside the owned maint Pod (hostPID, privileged). Adds only certs.d/10.254.192.47:5000/hosts.toml.
# No config.toml edit, no restart: use_local_image_pull=true splits config_path and reads hosts dirs per pull.
set -eu
H="nsenter -t 1 -m -u -i -n -p --"
D=/etc/containerd/certs.d/10.254.192.47:5000
echo "containerd_pid=$($H systemctl show -p MainPID --value containerd) since=$($H systemctl show -p ActiveEnterTimestamp --value containerd)"
$H containerd config dump > /tmp/dump.$$ 2>&1 || { echo "DUMP_FAILED_ABORT"; head -5 /tmp/dump.$$; exit 4; }
grep -n -E "config_path|use_local_image_pull|default_runtime_name|BinaryName|^imports" /tmp/dump.$$ || true
grep -q -x -F "    use_local_image_pull = true" /tmp/dump.$$ || { echo "NOT_LOCAL_PULL_ABORT"; exit 3; }
grep -q -x -F "      config_path = '/etc/containerd/certs.d:/etc/docker/certs.d'" /tmp/dump.$$ || { echo "CONFIG_PATH_UNEXPECTED_ABORT"; exit 3; }
if $H test -e "$D"; then echo "EXISTS_ABORT"; $H ls -la "$D"; exit 3; fi
$H mkdir -p "$D"
printf '%s\n' 'server = "http://10.254.192.47:5000"' '[host."http://10.254.192.47:5000"]' '  capabilities = ["pull", "resolve"]' | $H sh -c "cat > '$D/.hosts.toml.tmp' && mv '$D/.hosts.toml.tmp' '$D/hosts.toml'"
$H ls -la /etc/containerd/certs.d "$D"
$H sha256sum "$D/hosts.toml" /etc/containerd/config.toml /etc/containerd/conf.d/99-nvidia.toml
$H cat "$D/hosts.toml"
echo "containerd_pid_after=$($H systemctl show -p MainPID --value containerd) active=$($H systemctl is-active containerd)"
echo HOSTS_TOML_WRITTEN
