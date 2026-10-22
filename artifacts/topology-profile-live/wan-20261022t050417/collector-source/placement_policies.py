"""Same-information static selectors for persistent independent stage services.

HEFT-serving uses upward ranks and earliest finish, but does NOT serialize all
tasks on one node-wide processor lane. It is an explicitly adapted heuristic.
"""
import itertools
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'real-topology-profile'))
import prepare


def sequential(c, ranked=False):
    tasks = {t['id']: t for t in c['tasks']}
    ranks = {}
    for task in reversed(c['tasks']):
        children = [(child, edge) for child in c['tasks'] for edge in child['inputs']
                    if edge.get('task') == task['id']]
        ranks[task['id']] = statistics.mean(task['compute_ms'].values()) + max(
            (statistics.mean(edge['transfer_ms'].values()) + ranks[child['id']]
             for child, edge in children), default=0)
    feasible = []
    for nodes in itertools.product(*(t['candidates'] for t in c['tasks'])):
        placement = dict(zip(tasks, nodes))
        if prepare.feasible(c, placement):
            feasible.append(placement)
    selected, finish = {}, {}
    while len(selected) < len(tasks):
        ready = [t for t in c['tasks'] if t['id'] not in selected and
                 all(e.get('task') in selected for e in t['inputs'] if 'task' in e)]
        task = min(ready, key=lambda t: (-ranks[t['id']], t['id'])) if ranked else ready[0]
        choices = []
        for node in task['candidates']:
            proposal = dict(selected, **{task['id']: node})
            # Resource completion feasibility only; no descendant cost look-ahead.
            if not any(all(p[k] == v for k, v in proposal.items()) for p in feasible):
                continue
            ready_at = max((finish[e['task']] if 'task' in e else 0) +
                e['transfer_ms'][(selected[e['task']] if 'task' in e else e['source_node'])+'|'+node]
                for e in task['inputs'])
            end = ready_at + task['compute_ms'][node]
            terminal = max((e['transfer_ms'][node+'|'+e['destination_node']]
                for e in c['outputs'] if e['task'] == task['id']), default=0)
            choices.append((end + terminal, node, end))
        if not choices:
            raise ValueError('No feasible placement')
        _, node, finish[task['id']] = min(choices)
        selected[task['id']] = node
    return selected


def select_policies(c, server_node):
    prepare.validate(c)
    results = {}
    for policy in ('server-only', 'greedy', 'heft', 'ours', 'no-network', 'no-node-profile'):
        start = time.perf_counter()
        try:
            if policy == 'server-only':
                placement = {t['id']: server_node for t in c['tasks']}
                if any(server_node not in t['candidates'] for t in c['tasks']) or not prepare.feasible(c, placement):
                    raise ValueError('Server-only is infeasible')
            elif policy in ('greedy', 'heft'):
                placement = sequential(c, ranked=policy == 'heft')
            else:
                mode = {'ours': 'topology_profile', 'no-network': 'profile_only',
                        'no-node-profile': 'topology_only'}[policy]
                placement = prepare.plan(c, mode)['placement']
            results[policy] = dict(status='ok', placement=placement,
                predicted_e2e_proxy_ms=prepare.cost(c, placement, 'topology_profile'),
                profile_sha256=prepare.digest(c))
        except ValueError as error:
            results[policy] = dict(status='infeasible', error=str(error))
        results[policy]['decision_ms'] = (time.perf_counter() - start) * 1000
    return results
