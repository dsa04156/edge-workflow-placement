"""Explicit per-run placement topology for the baseline collector and verifier.

The legacy default is exactly run_smoke.NODES (e0/e1 edge, c cloud). An explicit
topology may add candidates, but every node needs an explicit role, the edge
sources (image e0, audio e1, result destination e0) and the helper/gateway/cache
host c stay fixed, and node readiness is checked from a snapshot before any
mutation. Nothing here fills a measurement; runtime compatibility of an added
node is only established by qualification.
"""
import hashlib
import json
from pathlib import Path
import re

from run_smoke import NODES

ROLES = ('edge', 'cloud')
FIXED = dict(e0=dict(name=NODES['e0'], role='edge'), e1=dict(name=NODES['e1'], role='edge'),
    c=dict(name=NODES['c'], role='cloud'))
LEGACY = dict(name='legacy-3-node', nodes=FIXED, excluded={})
# Architectures on which the pinned image already passed qualification (raspi5 arm64, ser0002 amd64).
QUALIFIED_ARCH = ('amd64', 'arm64')
# The collector Pods carry no tolerations, so these effects block scheduling; taints are never removed.
BLOCKING_TAINTS = ('NoSchedule', 'NoExecute')
ALIAS = re.compile(r'[a-z][a-z0-9]{0,7}')


def validate(raw):
    """Normalized copy of a topology; ValueError on any implicit or inconsistent field."""
    if not isinstance(raw, dict) or not isinstance(raw.get('name'), str) or not raw['name']:
        raise ValueError('Topology name required')
    nodes = raw.get('nodes')
    if not isinstance(nodes, dict) or not nodes:
        raise ValueError('Topology nodes required')
    if list(nodes)[:3] != list(FIXED):
        raise ValueError('Topology must start with fixed aliases e0, e1, c in that order')
    normalized = {}
    for alias, node in nodes.items():
        if not isinstance(alias, str) or not ALIAS.fullmatch(alias):
            raise ValueError('Invalid node alias '+repr(alias))
        if not isinstance(node, dict) or set(node) != {'name', 'role'} or not isinstance(node['name'], str) or not node['name']:
            raise ValueError(f'Node {alias} needs exactly an explicit name and role')
        if node['role'] not in ROLES:
            raise ValueError(f'Node {alias} role must be one of {ROLES}, got {node["role"]!r}')
        normalized[alias] = dict(name=node['name'], role=node['role'])
    for alias, fixed in FIXED.items():
        if normalized[alias] != fixed:
            raise ValueError(f'Fixed {alias} must stay {fixed}')
    names = [n['name'] for n in normalized.values()]
    if len(set(names)) != len(names):
        raise ValueError('Duplicate node name')
    excluded = raw.get('excluded', {})
    if not isinstance(excluded, dict) or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in excluded.items()):
        raise ValueError('Excluded nodes need a reason')
    if set(excluded) & set(names):
        raise ValueError('Excluded node is also a candidate: '+repr(sorted(set(excluded) & set(names))))
    return dict(name=raw['name'], nodes=normalized, excluded=dict(excluded))


def load(path):
    return validate(json.loads(Path(path).read_text()))


def digest(topology):
    return hashlib.sha256(json.dumps(validate(topology), sort_keys=True).encode()).hexdigest()


def is_legacy(topology):
    return validate(topology)['nodes'] == FIXED and not topology.get('excluded')


def aliases(topology):
    """alias -> node name, in topology order (the NODES shape)."""
    return {alias: node['name'] for alias, node in topology['nodes'].items()}


def roles(topology):
    return {alias: node['role'] for alias, node in topology['nodes'].items()}


def candidates(topology):
    return list(aliases(topology).values())


def describe(topology):
    """Frozen design entry of an explicit topology."""
    names = aliases(topology)
    return dict(name=topology['name'], sha256=digest(topology), candidates=candidates(topology),
        roles={names[a]: r for a, r in roles(topology).items()}, excluded=sorted(topology['excluded']),
        fixed=dict(image_source=names['e0'], audio_source=names['e1'], result_destination=names['e0'],
            helper_gateway_cache=names['c']))


def transfer_counts(topology, calibration_pairs):
    """Transfer-profile RPCs per payload kind, mirroring Pilot.transfer_profiles origins x destinations."""
    n = len(topology['nodes'])
    pairs = dict(image=1*n, audio=1*n, vision=n*n, speech=n*n, control=1*n, **{'return': n*1})
    return {kind: count*calibration_pairs for kind, count in pairs.items()}


def project_nodes(snapshot):
    """Readiness/scheduling fields of `kubectl get nodes -o json`; same shape as the inventory file."""
    items = []
    for node in snapshot.get('items', []):
        status, spec, labels = node.get('status', {}), node.get('spec', {}), node.get('metadata', {}).get('labels', {})
        items.append(dict(name=node['metadata']['name'],
            ready=any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in status.get('conditions', [])),
            unschedulable=bool(spec.get('unschedulable', False)),
            taints=[{k: t[k] for k in ('key', 'value', 'effect') if k in t} for t in spec.get('taints', [])],
            arch=labels.get('kubernetes.io/arch', status.get('nodeInfo', {}).get('architecture'))))
    return items


def preflight(topology, observed):
    """Per-node diagnostics from projected nodes; errors means no mutation may start."""
    seen = {n['name']: n for n in observed}
    errors, checked = [], []
    for alias, node in topology['nodes'].items():
        name, state = node['name'], seen.get(node['name'])
        if state is None:
            errors.append(f'{alias}={name}: missing from cluster snapshot')
            continue
        blocking = [t for t in state.get('taints', []) if t.get('effect') in BLOCKING_TAINTS]
        problems = [message for failed, message in [
            (state.get('ready') is not True, 'NotReady'),
            (state.get('unschedulable') is not False, 'cordoned (spec.unschedulable)'),
            (bool(blocking), 'unsupported scheduling taints '+json.dumps(blocking, sort_keys=True)),
            (state.get('arch') not in QUALIFIED_ARCH, f'unsupported arch {state.get("arch")!r}')] if failed]
        errors.extend(f'{alias}={name}: {p}' for p in problems)
        checked.append(dict(alias=alias, name=name, role=node['role'], arch=state.get('arch'), ok=not problems))
    unlisted = sorted(set(seen) - set(candidates(topology)) - set(topology['excluded']))
    return dict(status='passed' if not errors else 'failed', errors=errors, candidates=checked,
        excluded={name: dict(reason=reason, observed_ready=seen.get(name, {}).get('ready')) for name, reason in topology['excluded'].items()},
        unlisted_not_candidates=unlisted,
        runtime='image/runtime compatibility unverified until qualification; arch check uses previously qualified arches only')
