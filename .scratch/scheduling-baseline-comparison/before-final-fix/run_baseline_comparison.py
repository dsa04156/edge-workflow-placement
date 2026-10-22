"""Small one-block baseline comparison: real default-scheduler versus profile policies.

Smoke-scale collector only: compressed 10/2 Mbps with 20 ms directional delay,
first4 calibration and first3 evaluation pairs, warmup1 + eval3 per unique
placement. No synthetic server budget, no CI/p95/throughput claim. The real
scheduler placement is observed once per condition and only re-pinned for the
evaluation; that is a scheduler-selected-placement evaluation, not a reproduction
of the original scheduling overhead. Use --dry-run to inspect the design without
any Session or cluster access; live collection requires --execute.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import statistics
import subprocess
import time
import traceback

from run_smoke import ASSETS, HERE, NODES, NS
from run_placement import Pilot, TASKS
from run_budget_placement import BudgetPilot
from budget_placement import validate_placement, simple_alternatives
from placement_policies import prepare, select_policies

POLICIES = ('kube-default', 'greedy', 'heft', 'greedy-swap', 'exact', 'no-network')
# Output name -> existing select_policies key; greedy-swap comes from simple_alternatives.
PROFILE_SOURCES = {'greedy': 'greedy', 'heft': 'heft', 'exact': 'ours', 'no-network': 'no-network'}
SEED = 20261023
RATES = (10, 2)
DELAY_MS = 20
REPRESENTATION = 'compressed'
CALIBRATION_PAIRS = 4
EVALUATION_PAIRS = 3
QUALIFY_WARMUP = 5
WARMUP = 1
EVALUATION_COUNT = 3
RATE_RPS = .1
DEADLINE_S = 120
SPEC = HERE.parents[1] / '.scratch/scheduling-baseline-comparison/implementation-task.md'
# Reused implementation whose hashes are frozen with each run.
REUSED = [HERE/name for name in ('run_baseline_comparison.py', 'verify_baseline_comparison.py', 'run_placement.py',
    'run_budget_placement.py', 'run_load.py', 'run_smoke.py', 'budget_placement.py', 'placement_policies.py',
    'control_receipt.py')] + [Path(prepare.__file__).resolve()]


def condition_name(rate):
    return f'{REPRESENTATION}-{rate}'


def condition_order(seed=SEED):
    order = list(RATES)
    rng = random.Random(seed)
    rng.shuffle(order)
    return order, rng


def reduced_pairs(pairs):
    """First calibration and evaluation pairs in the original asset order."""
    calibration = [p for p in pairs if p['split'] == 'calibration'][:CALIBRATION_PAIRS]
    evaluation = [p for p in pairs if p['split'] == 'evaluation'][:EVALUATION_PAIRS]
    if len(calibration) != CALIBRATION_PAIRS or len(evaluation) != EVALUATION_PAIRS:
        raise ValueError('Original asset pairs are too few for the reduced design')
    return calibration + evaluation


def build_design(pairs):
    subset = reduced_pairs(pairs)
    order, _ = condition_order()
    per_task_node = QUALIFY_WARMUP + len(subset)
    return dict(comparison='scheduling-baseline-smoke-v1', scope='one block smoke comparison; no CI, p95, '
        'throughput, independent-environment, new-holdout or original-HEFT reproduction claim',
        seed=SEED, blocks=1, conditions=[dict(condition=condition_name(r), mbps=r, oneway_delay_ms=DELAY_MS,
            representation=REPRESENTATION) for r in order],
        policies=list(POLICIES), policy_sources=dict(PROFILE_SOURCES, **{'greedy-swap': 'budget_placement.simple_alternatives',
            'kube-default': 'actual default-scheduler, unpinned within the same candidate nodes'}),
        calibration_ids=[p['question_id'] for p in subset if p['split'] == 'calibration'],
        evaluation_ids=[p['question_id'] for p in subset if p['split'] == 'evaluation'],
        qualification=dict(warmup_per_task_node=QUALIFY_WARMUP, rows_per_task_node=per_task_node,
            task_nodes=len(TASKS)*len(NODES), expected_rows=len(TASKS)*len(NODES)*per_task_node),
        warmup=WARMUP, evaluation_count=EVALUATION_COUNT, rate_rps=RATE_RPS, deadline_s=DEADLINE_S,
        max_workflow_requests=len(RATES)*len(POLICIES)*(WARMUP+EVALUATION_COUNT),
        capacity='physical requests-based snapshot; no admission_budget or server slot reduction',
        scheduler='one fresh unpinned default-scheduler observation per condition, then re-pinned evaluation',
        observation_sharing='same condition and placement share one observation; never across conditions')


@contextmanager
def phase(session, name, **meta):
    """Exclusive start/end records with monotonic duration, failures included."""
    session.phase_seq = getattr(session, 'phase_seq', 0) + 1
    stack = session.__dict__.setdefault('phase_stack', [])
    record = dict(seq=session.phase_seq, name=name, parent=stack[-1] if stack else None, **meta)
    tag = f'phase-{session.phase_seq:03d}-{name}'
    session.save(tag+'.start.json', dict(record, started_at=datetime.now(timezone.utc).isoformat()))
    start = time.monotonic()
    status, error = 'error', None
    stack.append(name)
    try:
        yield
        status = 'ok'
    except BaseException as exc:
        status = 'timeout' if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) else 'error'
        error = repr(exc)
        raise
    finally:
        stack.pop()
        session.save(tag+'.end.json', dict(record, status=status, error=error,
            duration_ms=(time.monotonic()-start)*1000, finished_at=datetime.now(timezone.utc).isoformat()))


def profile_plans(profile, server=NODES['c']):
    """Five profile policies on one frozen physical profile."""
    if 'admission_budget' in profile:
        raise ValueError('Synthetic admission budget is not allowed in the baseline comparison')
    base = select_policies(profile, server)
    plans = {name: dict(base[key], source_policy=key) for name, key in PROFILE_SOURCES.items()}
    start = time.perf_counter()
    swap = simple_alternatives(profile, base['greedy']['placement'])['greedy-one-pair-swap']
    plans['greedy-swap'] = dict(swap, status='ok', profile_sha256=prepare.digest(profile),
        decision_ms=(time.perf_counter()-start)*1000, source_policy='budget_placement.simple_alternatives',
        decision_scope='repair after greedy; excludes the greedy selection itself')
    for name, plan in plans.items():
        if plan['status'] != 'ok':
            raise RuntimeError('Profile policy infeasible: '+name)
        validate_placement(profile, plan['placement'])
    return {name: plans[name] for name in POLICIES if name in plans}


def schedule_jobs(plans, seed=SEED):
    """Unique placements per condition in seeded condition/group order."""
    order, rng = condition_order(seed)
    jobs = []
    for rate in order:
        condition = condition_name(rate)
        if set(plans[condition]) != set(POLICIES):
            raise ValueError('Exact six-policy set required for '+condition)
        groups = {}
        for policy in POLICIES:
            plan = plans[condition][policy]
            groups.setdefault(tuple(plan['placement'][t] for t in TASKS), []).append(policy)
        items = list(groups.items())
        rng.shuffle(items)
        for key, policies in items:
            jobs.append(dict(condition=condition, mbps=rate, oneway_delay_ms=DELAY_MS, representation=REPRESENTATION,
                placement=dict(zip(TASKS, key)), policies=policies))
    for index, job in enumerate(jobs):
        job['label'] = f"eval-{index}-{job['condition']}"
    return jobs


def scheduler_provenance(snapshot):
    """Version/command/config references of kube-scheduler Pods; env and secrets omitted."""
    pods = []
    for pod in snapshot.get('items', []):
        statuses = {s['name']: s for s in pod.get('status', {}).get('containerStatuses', [])}
        pods.append(dict(name=pod['metadata']['name'], uid=pod['metadata'].get('uid'),
            node=pod['spec'].get('nodeName'), config_hash=pod['metadata'].get('annotations', {}).get('kubernetes.io/config.hash'),
            containers=[dict(name=c['name'], image=c.get('image'), image_id=statuses.get(c['name'], {}).get('imageID'),
                command=c.get('command', []), args=c.get('args', []),
                mounts=[m.get('mountPath') for m in c.get('volumeMounts', [])]) for c in pod['spec']['containers']],
            host_paths=[v['hostPath']['path'] for v in pod['spec'].get('volumes', []) if 'hostPath' in v]))
    return dict(pods=pods, configuration='captured command/args/mount paths only; config file contents and env not read')


def projected_call(session, stem, args, project, timeout=40):
    """Session.call event/timeout semantics, but stdout stays in memory and only project(stdout) is saved.

    Used for kube-system Pods whose raw JSON can hold env values; Session.call would
    persist that raw stdout before any sanitization.
    """
    session.counter += 1
    tag = f'{session.counter:03d}-{stem}'
    command = ['rtk', 'proxy', 'kubectl', '--request-timeout=20s', *args]
    event = dict(started_at=datetime.now(timezone.utc).isoformat(), command=command, timeout_s=timeout,
        stdout='projected in memory; raw stdout not persisted')
    try:
        with (session.out/(tag+'.stderr')).open('wb') as stderr:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=stderr, timeout=timeout)
        event.update(returncode=result.returncode, status='completed')
        if result.returncode:
            raise RuntimeError(tag+' failed; inspect phase stderr')
        projection = project(json.loads(result.stdout))
        session.save(tag+'.projected.json', projection)
        return projection
    except BaseException as error:
        # TimeoutExpired keeps partial stdout in .output; only its type and command are recorded.
        event.update(status='timeout' if isinstance(error, subprocess.TimeoutExpired) else 'error',
            error=type(error).__name__ if isinstance(error, subprocess.TimeoutExpired) else repr(error))
        raise
    finally:
        event['finished_at'] = datetime.now(timezone.utc).isoformat()
        session.save(tag+'.event.json', event)


def source_hashes():
    return {str(p.relative_to(HERE.parents[1])): hashlib.sha256(p.read_bytes()).hexdigest() for p in REUSED}


def observe_scheduler(session, condition, profile):
    """Fresh unpinned default-scheduler placement within the same candidates."""
    label = 'scheduler-'+condition
    actual = session.deploy_operators({t: None for t in TASKS}, label)
    pods = {t: session.actual[t] for t in TASKS}
    uids = {p['metadata']['uid'] for p in pods.values()}
    events = [e for e in json.loads(session.call(label+'-events', ['get', 'events', '-n', NS, '-o', 'json']))['items']
        if e.get('involvedObject', {}).get('uid') in uids]
    try:
        validate_placement(profile, actual)
        validation = 'ok'
    except ValueError as error:
        validation = repr(error)
    session.save(label+'.json', dict(condition=condition, placement=actual, validation=validation,
        candidates=list(NODES.values()), pods={t: dict(name=p['metadata']['name'], uid=p['metadata']['uid'],
            node=p['spec']['nodeName'], scheduler=p['spec'].get('schedulerName'),
            created_at=p['metadata'].get('creationTimestamp'),
            scheduled=next((c for c in p['status'].get('conditions', []) if c['type'] == 'PodScheduled'), None))
            for t, p in pods.items()}, events=events,
        scope='actual scheduler selection only; later re-pinned evaluation excludes this scheduling overhead'))
    session.remove_operators(label)
    if validation != 'ok':
        raise RuntimeError('Observed scheduler placement violates frozen profile: '+validation)
    return actual


class BaselinePilot(BudgetPilot):
    """BudgetPilot control/readonly retries without the slot2 deploy validator."""

    def __init__(self):
        self.calibration_run = None
        Pilot.__init__(self, SPEC)
        self.pairs = reduced_pairs(self.pairs)
        self.active_profile = None

    def deploy_operators(self, placement, label):
        if self.active_profile is not None:
            physical = self.capacity()
            self.save(label+'-capacity-current.json', physical)
            if all(node is not None for node in placement.values()):
                validate_placement(self.active_profile, placement)
                validate_placement(dict(self.active_profile, nodes=physical), placement)
        return Pilot.deploy_operators(self, placement, label)


def summarize(observations):
    return {label: dict(attempts=len(rows), success=sum(r['status'] == 'ok' for r in rows),
        mean_ms=statistics.mean([r['e2e_ms'] for r in rows if r['status'] == 'ok']) if any(r['status'] == 'ok' for r in rows) else None,
        failures=[r for r in rows if r['status'] != 'ok']) for label, rows in observations.items()}


def collect(session, design):
    session.save('design.json', design)
    session.save('baseline-provenance.json', dict(sources=source_hashes(), spec_sha256=hashlib.sha256(SPEC.read_bytes()).hexdigest()))
    mapping, observations, jobs = {}, {}, []
    try:
        with phase(session, 'scheduler-provenance'):
            provenance = projected_call(session, 'scheduler-snapshot',
                ['get', 'pods', '-n', 'kube-system', '-l', 'component=kube-scheduler', '-o', 'json'], scheduler_provenance)
            session.save('scheduler-provenance.json', provenance)
        with phase(session, 'deployment-helpers'):
            session.deploy()
        with phase(session, 'network-preflight'):
            session.network_preflight()
        with phase(session, 'qualification'):
            session.qualify_all()
        capacity = session.capacity()
        session.save('capacity-frozen.json', capacity)
        conditions = [(c['condition'], c['mbps']) for c in design['conditions']]
        session.guard(True)
        profiles = {}
        for condition, rate in conditions:
            with phase(session, 'transfer-calibration-'+condition, mbps=rate):
                profiles[condition] = session.transfer_profiles(condition, rate, DELAY_MS, REPRESENTATION, capacity)
                session.drain('cal-'+condition)
        session.guard(False)
        plans = {}
        for condition, _ in conditions:
            with phase(session, 'planning-'+condition):
                plans[condition] = profile_plans(profiles[condition])
        session.save('profile-plans-frozen.json', plans)
        for condition, _ in conditions:
            session.active_profile = profiles[condition]
            with phase(session, 'scheduler-selection-'+condition):
                observed = observe_scheduler(session, condition, profiles[condition])
            plans[condition] = dict(kube_default_plan(profiles[condition], observed, condition), **plans[condition])
        session.save('plans-frozen.json', {c: {p: plans[c][p] for p in POLICIES} for c in plans})
        jobs = schedule_jobs(plans)
        session.save('schedule-frozen.json', dict(seed=SEED, jobs=jobs))
        mapping = {condition: {} for condition, _ in conditions}
        for job in jobs:
            label = job['label']
            session.active_profile = profiles[job['condition']]
            print('deploy', label, job['policies'], job['placement'], flush=True)
            with phase(session, 'evaluation-'+label, condition=job['condition'], policies=job['policies']):
                actual = session.deploy_operators(job['placement'], label)
                session.guard(True)
                session.shape(job['mbps'], DELAY_MS, label+'-shape')
                session.trace(label+'-warm', actual, job['mbps'], DELAY_MS, RATE_RPS, WARMUP, representation=REPRESENTATION, split='calibration')
                observations[label] = session.trace(label, actual, job['mbps'], DELAY_MS, RATE_RPS, EVALUATION_COUNT, representation=REPRESENTATION)
                for policy in job['policies']:
                    mapping[job['condition']][policy] = label
                session.remove_operators(label)
        session.save('policy-observation-map.json', mapping)
        session.save('pilot-summary.json', dict(mapping=mapping, observations=summarize(observations), plans=plans))
        session.shape(None, 0, 'restored-final')
        session.save('completed.json', dict(status='one-block scheduling baseline smoke completed',
            remaining='repeated blocks, larger inputs, load stage and statistical comparison'))
    except BaseException as error:
        session.save('failure.json', dict(error=repr(error), traceback=traceback.format_exc(),
            partial_mapping=mapping, completed_labels=list(observations), scheduled=len(jobs)))
        raise
    finally:
        session.active_profile = None
        session.close()


def kube_default_plan(profile, placement, condition):
    return {'kube-default': dict(status='ok', placement=placement, source='scheduler-'+condition+'.json',
        source_policy='default-scheduler observation', predicted_e2e_proxy_ms=prepare.cost(profile, placement, 'topology_profile'),
        decision_scope='observed selection; proxy is a post-hoc diagnostic, not used to choose')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true', help='print the reduced design; no Session or cluster access')
    mode.add_argument('--execute', action='store_true', help='run the live one-block collection')
    args = parser.parse_args(argv)
    design = build_design(json.loads((ASSETS/'load-pairs.json').read_text())['pairs'])
    if args.dry_run:
        print(json.dumps(dict(design, dry_run=True, sources=source_hashes()), indent=2))
        return 0
    collect(BaselinePilot(), design)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
