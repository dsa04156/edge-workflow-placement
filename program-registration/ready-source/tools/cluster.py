"""Read-only cluster preflight; collection remains in the existing runtime."""
import json
import ipaddress
from pathlib import Path
import shutil
import subprocess
from tools.assets import require_assets


def preflight(config_path):
    require_assets()
    config = json.loads(Path(config_path).read_text())
    if set(config["nodes"]) != {"e0", "e1", "c"} or len(set(config["nodes"].values())) != 3:
        raise ValueError("Configure three distinct nodes e0,e1,c")
    if not config.get("namespace") or not config.get("image") or not config.get("context"):
        raise ValueError("context,namespace,image are required")
    if not shutil.which("kubectl"): raise RuntimeError("kubectl is required for cluster execution")
    def command(*args):
        return subprocess.check_output(["kubectl", "--context", config["context"], "--request-timeout=20s", *args], text=True, timeout=30)
    current = command("config", "current-context").strip()
    if current != config["context"]: raise ValueError("Current kubectl context differs from cluster.json; select the intended context explicitly")
    nodes = {n["metadata"]["name"]:n for n in json.loads(command("get", "nodes", "-o", "json"))["items"]}
    for name in config["nodes"].values():
        if name not in nodes: raise ValueError("Missing node: " + name)
        node = nodes[name]
        cidrs = node['spec'].get('podCIDRs') or [node['spec'].get('podCIDR')]
        if not cidrs or any(not cidr or not ipaddress.ip_network(cidr).subnet_of(ipaddress.ip_network('10.0.0.0/8')) for cidr in cidrs):
            raise ValueError('Network control requires node Pod CIDRs within 10.0.0.0/8: ' + name)
        if node["spec"].get("unschedulable") or not any(c["type"] == "Ready" and c["status"] == "True" for c in node["status"].get("conditions",[])):
            raise ValueError("Node not schedulable/Ready: " + name)
        if node["status"]["nodeInfo"]["architecture"] not in ("amd64", "arm64"): raise ValueError("Unsupported node architecture")
    command("get", "namespace", config["namespace"])
    permissions = [(verb, resource, ['-n', config['namespace']])
                   for resource in ('pods', 'configmaps')
                   for verb in ('get', 'list', 'watch', 'create', 'patch', 'delete')]
    permissions += [('create', 'pods/exec', ['-n', config['namespace']]),
                    ('get', 'pods/log', ['-n', config['namespace']]),
                    ('list', 'events', ['-n', config['namespace']]),
                    ('list', 'pods', ['--all-namespaces']),
                    ('get', 'nodes', []), ('list', 'nodes', [])]
    for verb, resource, scope in permissions:
        if command('auth', 'can-i', verb, resource, *scope).strip() != 'yes':
            raise PermissionError('Missing permission: ' + verb + ' ' + resource)
    result = {"status":"passed","context":current,"nodes":list(config["nodes"].values()),"scope":"read-only readiness and authorization; image availability, resources and model quality checked by actual collection"}
    print(json.dumps(result))
    return result
