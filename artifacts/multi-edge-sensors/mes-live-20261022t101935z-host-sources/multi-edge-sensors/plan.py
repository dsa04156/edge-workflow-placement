"""Policy plans for one frozen workflow profile (prepare.validate format); also a read-only CLI.

task-fastest: taskwise fastest-feasible baseline. Tasks in fixed order V, T, F; each takes the
  quality-valid candidate with the smallest measured execution cost (compute_ms in the profile,
  which for V is PNG decode + inference) among nodes whose remaining CPU/memory still fits it,
  after reserving the tasks already placed; ties by node name. Network and critical path are
  ignored. If a later task fits nowhere the plan is 'unavailable' (never handed to a joint optimizer).
greedy-network: real-topology-live placement_policies.sequential (network-aware greedy).
exact: real-topology-profile prepare.plan topology_profile (exhaustive over the frozen cost model).
source-local / server-fixed: diagnostics.
"""
import argparse
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
LIVE = HERE.parents[1]/'experiments/real-topology-live'
sys.path.insert(0, str(LIVE))
import placement_policies  # noqa: E402
from placement_policies import prepare as planner  # noqa: E402

TASK_ORDER = ('V', 'T', 'F')
POLICIES = ('task-fastest', 'greedy-network', 'exact')
DIAGNOSTICS = ('source-local', 'server-fixed')


def taskwise_fastest(profile):
    """(placement, trace) or raise ValueError('unavailable: ...'); see module docstring."""
    tasks = {t['id']: t for t in profile['tasks']}
    free = {n: [v['cpu_free_m'], v['memory_free_mib']] for n, v in profile['nodes'].items()}
    placement, trace = {}, []
    for task_id in TASK_ORDER:
        task = tasks[task_id]
        fitting = [n for n in task['candidates'] if free[n][0] >= task['cpu_m'] and free[n][1] >= task['memory_mib']]
        if not fitting:
            raise ValueError(f'unavailable: no remaining candidate fits {task_id} after reserving {placement}')
        node = min(fitting, key=lambda n: (task['compute_ms'][n], n))
        free[node][0] -= task['cpu_m']
        free[node][1] -= task['memory_mib']
        placement[task_id] = node
        trace.append(dict(task=task_id, node=node, cost_ms=task['compute_ms'][node],
                          skipped_for_resources=sorted(set(task['candidates']) - set(fitting))))
    return placement, trace


def plan_all(profile, video_node, text_node, destination_node, server):
    plans = {}
    for name in POLICIES + DIAGNOSTICS:
        start = time.perf_counter()
        extra = {}
        try:
            if name == 'task-fastest':
                placement, extra['selection'] = taskwise_fastest(profile)
            elif name == 'greedy-network':
                placement = placement_policies.sequential(profile)
            elif name == 'exact':
                placement = planner.plan(profile, 'topology_profile')['placement']
            elif name == 'source-local':
                placement = dict(V=video_node, T=text_node, F=destination_node)
            else:
                placement = dict(V=server, T=server, F=server)
            cands = {t['id']: t['candidates'] for t in profile['tasks']}
            if any(placement[t] not in cands[t] for t in TASK_ORDER) or not planner.feasible(profile, placement):
                raise ValueError('placement outside qualified candidates or resource-infeasible')
            plans[name] = dict(status='ok', placement=placement, predicted_ms=planner.cost(profile, placement, 'topology_profile'), **extra)
        except ValueError as error:
            plans[name] = dict(status='unavailable' if str(error).startswith('unavailable') else 'infeasible', error=str(error))
        plans[name]['decision_ms'] = (time.perf_counter()-start)*1000
    return plans


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--profile', type=Path, required=True, help='one frozen workflow profile JSON')
    parser.add_argument('--video-node', required=True)
    parser.add_argument('--text-node', required=True)
    parser.add_argument('--destination-node', required=True)
    parser.add_argument('--server', required=True)
    parser.add_argument('--allow-fixture', action='store_true', help='accept a non-measured profile (offline tests only)')
    args = parser.parse_args(argv)
    profile = json.loads(args.profile.read_text())
    planner.validate(profile, fixture=args.allow_fixture)
    print(json.dumps(plan_all(profile, args.video_node, args.text_node, args.destination_node, args.server), indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
