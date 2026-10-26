"""Calibration-only cost table and fixed-plan selection for the constrained VQA DAG.

Placement space: vision in {e0 (v-edge), c (v-cloud)}, speech in {e1 (s-edge), c (s-cloud)}, fusion fixed
on c: two free Task locations, four plans, all deployed at once (one deployment). Capacity is the
reserved operator slot set of that single deployment, so a plan can only use a node that hosts a
deployed operator of that task.

Selectors on one frozen profile (prepare.py format, measured calibration rows only):
- task-fastest: per task in topological order, min calibration compute among fitting candidates
  (same rule as multi-edge-sensors plan.taskwise_fastest); no network, no critical path.
- greedy-network: real-topology-live placement_policies.sequential (communication-aware greedy).
- dag-exact: real-topology-profile prepare.plan topology_profile, exact enumeration of the 4 plans.
  Small-DAG exhaustive search; not a new algorithm and not a reproduction of any paper.
- dag-exact-network-blind: the same enumeration with prepare's profile_only cost (transfers set to 0
  in selection only; the executed data path is unchanged).
- empirical calibration oracle: max measured on-time count of each full plan's calibration trace,
  then lower median latency. Measured selection, distinct from greedy/exact.
Quality filter: a plan is eligible iff its calibration official VQA quality >= Qmin. Each selector also
reports its no-filter choice, scheduled as its own evaluation alias (see schedule); with one fixed model every plan has the same quality, so the ablation is
expected to be equivalent and is reported as such. The no-queue cost model ignores load and contention:
plans chosen by low-load latency are not goodput-optimal policies.
"""
import itertools
import json
from pathlib import Path
import random
import sys
import time

HERE = Path(__file__).resolve().parent
LIVE = HERE.parent/'real-topology-live'
sys.path.insert(0, str(LIVE))
from placement_policies import prepare, sequential  # noqa: E402
from run_placement import quantile  # noqa: E402
from run_smoke import IMAGE, NODES  # noqa: E402

E0, E1, C = NODES['e0'], NODES['e1'], NODES['c']
TASKS = ('vision', 'speech', 'fusion')
CANDIDATES = dict(vision=(E0, C), speech=(E1, C), fusion=(C,))
POD = {('vision', E0): 'v-edge', ('vision', C): 'v-cloud', ('speech', E1): 's-edge', ('speech', C): 's-cloud',
       ('fusion', C): 'fusion'}
# run_load.Session operator Pod: worker 1 CPU / 2Gi plus netctl sidecar 10m / 32Mi.
TASK_CPU_M, TASK_MEMORY_MIB = 1010, 2080
LABEL_FIELDS = {'human_answers', 'multiple_choice_answer', 'answer', 'answers', 'vqa_accuracy', 'multiple_choices'}
EXHAUSTIVE = {'dag-exact': 'topology_profile', 'dag-exact-network-blind': 'profile_only'}
SELECTORS = ('task-fastest', 'greedy-network', 'dag-exact', 'dag-exact-network-blind')


def key(placement):
    return POD[('vision', placement['vision'])]+'+'+POD[('speech', placement['speech'])]


PLACEMENTS = {key(p): p for p in (dict(zip(TASKS, nodes)) for nodes in itertools.product(*(CANDIDATES[t] for t in TASKS)))}


def deployed_capacity():
    slots = {}
    for (task, node) in POD:
        slots[node] = slots.get(node, 0) + 1
    return {n: dict(cpu_free_m=k*TASK_CPU_M, memory_free_mib=k*TASK_MEMORY_MIB) for n, k in slots.items()}


def _calibration_only(rows, what):
    for row in rows:
        if row.get('split') != 'calibration':
            raise ValueError(f'{what}: only calibration rows may define costs (got split={row.get("split")})')
        if LABEL_FIELDS & set(row):
            raise ValueError(f'{what}: label field in a cost row: {sorted(LABEL_FIELDS & set(row))}')


def build_profile(condition, compute, transfers, capacity, evidence_id):
    """compute: [{task,node,question_id,split,compute_ms}]; transfers: [{kind,origin,destination,question_id,split,ms,bytes}]."""
    _calibration_only(compute, 'compute')
    _calibration_only(transfers, 'transfer')
    cost = {}
    for row in compute:
        cost.setdefault(row['task'], {}).setdefault(row['node'], []).append(row['compute_ms'])
    table, sizes = {}, {}
    for row in transfers:
        table.setdefault(row['kind'], {}).setdefault(row['origin']+'|'+row['destination'], []).append(row['ms'])
        sizes.setdefault(row['kind'], []).append(row['bytes'])
    p75 = lambda d: {k: quantile(v) for k, v in d.items()}
    mean = lambda v: sum(v)/len(v)
    tasks = []
    for task in TASKS:
        if task == 'fusion':
            inputs = [dict(task=t, bytes=mean(sizes[t]), transfer_ms=p75(table[t])) for t in ('vision', 'speech')]
        else:
            kind = 'image' if task == 'vision' else 'audio'
            inputs = [dict(source_node=E0 if task == 'vision' else E1, bytes=mean(sizes[kind]), transfer_ms=p75(table[kind]))]
        tasks.append(dict(id=task, candidates=list(CANDIDATES[task]), compute_ms=p75(cost[task]), image=IMAGE,
                          cpu_m=TASK_CPU_M, memory_mib=TASK_MEMORY_MIB, inputs=inputs))
    ret, control = p75(table['return']), p75(table['control'])
    output = {n+'|'+E0: ret[n+'|'+E0] + control[E0+'|'+n] for n in CANDIDATES['fusion']}
    profile = dict(provenance='measured', evidence_id=evidence_id, condition=condition, nodes=capacity, tasks=tasks,
                   outputs=[dict(task='fusion', destination_node=E0, transfer_ms=output)],
                   model='p75 calibration compute + p75 in-Pod payload POST per hop + return/control proxy; no queue/contention')
    prepare.validate(profile)
    return profile


def validate_choice(profile, placement):
    for task in TASKS:
        if placement.get(task) not in CANDIDATES[task] or placement[task] not in next(
                t['candidates'] for t in profile['tasks'] if t['id'] == task):
            raise ValueError(f'{task} on {placement.get(task)} is not a compatible deployed candidate')
    if not prepare.feasible(profile, placement):
        raise ValueError(f'{placement} exceeds the deployed capacity')


def task_fastest(profile):
    free = {n: [v['cpu_free_m'], v['memory_free_mib']] for n, v in profile['nodes'].items()}
    placement = {}
    for task in profile['tasks']:
        fitting = [n for n in task['candidates'] if free[n][0] >= task['cpu_m'] and free[n][1] >= task['memory_mib']]
        if not fitting:
            raise ValueError(f"no remaining candidate fits {task['id']}")
        node = min(fitting, key=lambda n: (task['compute_ms'][n], n))
        free[node][0] -= task['cpu_m']
        free[node][1] -= task['memory_mib']
        placement[task['id']] = node
    return placement


def _result(profile, placement, unfiltered, eligible, quality, started, status=None):
    out = dict(status=status or 'ok', placement=placement, key=key(placement) if placement else None,
               no_quality_filter=dict(placement=unfiltered, key=key(unfiltered)),
               quality_ablation_differs=placement != unfiltered, quality_of_unfiltered=quality[key(unfiltered)],
               eligible_unfiltered=eligible[key(unfiltered)], decision_ms=(time.perf_counter()-started)*1000)
    if placement:
        out.update(predicted_ms=prepare.cost(profile, placement, 'topology_profile'), predicted_ms_cost_model='topology_profile')
    return out


def select(profile, quality, qmin, quality_split='calibration'):
    """{selector: result}; quality = {plan key: calibration official VQA quality}."""
    if quality_split != 'calibration':
        raise ValueError('Plan quality must come from calibration outputs only')
    prepare.validate(profile)
    eligible = {k: quality[k] >= qmin for k in PLACEMENTS}
    results = {}
    for name in SELECTORS:
        started = time.perf_counter()
        if name in EXHAUSTIVE:
            mode = EXHAUSTIVE[name]
            unfiltered = prepare.plan(profile, mode)['placement']
            ranked = sorted((p for p in PLACEMENTS.values() if prepare.feasible(profile, p)),
                            key=lambda p: (prepare.cost(profile, p, mode), tuple(p[t] for t in TASKS)))
            if ranked[0] != unfiltered:
                raise RuntimeError('Ranking disagrees with prepare.plan')
            chosen = next((p for p in ranked if eligible[key(p)]), None)
            validate_choice(profile, unfiltered)
            results[name] = _result(profile, chosen, unfiltered, eligible, quality, started,
                                    None if chosen else 'quality_infeasible')
        else:
            unfiltered = task_fastest(profile) if name == 'task-fastest' else sequential(profile)
            validate_choice(profile, unfiltered)
            ok = eligible[key(unfiltered)]
            results[name] = _result(profile, unfiltered if ok else None, unfiltered, eligible, quality, started,
                                    None if ok else 'quality_rejected')
    return results


def empirical_oracle(measured, qmin):
    """measured = {plan key: {on_time, arrivals, p50_ms, quality, split}} from calibration traces of each full plan."""
    started = time.perf_counter()
    for k, m in measured.items():
        if m.get('split') != 'calibration':
            raise ValueError(f'Oracle measurement for {k} is not calibration')
    if set(measured) != set(PLACEMENTS):
        raise ValueError('Oracle needs a calibration measurement of every plan')
    order = list(PLACEMENTS)
    rank = lambda k: (-measured[k]['on_time'], measured[k]['p50_ms'] if measured[k]['p50_ms'] is not None else float('inf'), order.index(k))
    unfiltered = min(order, key=rank)
    chosen = min((k for k in order if measured[k]['quality'] >= qmin), key=rank, default=None)
    return dict(status='ok' if chosen else 'quality_infeasible', placement=PLACEMENTS[chosen] if chosen else None,
                key=chosen, no_quality_filter=dict(placement=PLACEMENTS[unfiltered], key=unfiltered),
                quality_ablation_differs=chosen != unfiltered,
                selector='empirical calibration oracle (measured full-plan); not a new algorithm',
                decision_ms=(time.perf_counter()-started)*1000)


UNFILTERED = '+no-quality-filter'


def schedule(plans, seed):
    """plans = {condition: {policy: result}} -> unique (condition, placement) jobs in seeded order.

    Each policy's no-quality-filter choice is a separate evaluation alias '<policy>+no-quality-filter', kept even
    when the filtered policy is quality-rejected/infeasible; aliases and filtered choices with the same placement
    in the same condition share one job. A condition therefore always has at least one evaluation job."""
    rng = random.Random(seed)
    conditions = sorted(plans)
    rng.shuffle(conditions)
    jobs = []
    for condition in conditions:
        groups = {}
        for policy in sorted(plans[condition]):
            result = plans[condition][policy]
            if not (result.get('no_quality_filter') or {}).get('placement'):
                raise ValueError(f'{condition}/{policy}: result has no no_quality_filter placement')
            if result['status'] == 'ok':
                groups.setdefault(json.dumps(result['placement'], sort_keys=True), []).append(policy)
            groups.setdefault(json.dumps(result['no_quality_filter']['placement'], sort_keys=True), []).append(policy+UNFILTERED)
        items = sorted(groups.items())
        rng.shuffle(items)
        jobs += [dict(condition=condition, placement=json.loads(k), policies=v) for k, v in items]
    return jobs
