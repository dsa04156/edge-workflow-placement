"""Read-only audit of one scheduling-baseline smoke run.

Prints a small JSON summary and exits nonzero for invalid or partial evidence.
Failed or timed-out requests stay in the counts; they are never dropped.
Candidates come from the run's frozen topology-frozen.json; runs without it are
audited against the legacy 3-node topology.
An optional --output path is opened exclusively and never overwrites.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from run_smoke import ASSETS
from run_placement import TASKS
from budget_placement import validate_placement
from placement_policies import prepare
from verify_load import native_backlogs
import run_baseline_comparison as design_module
import topology

POLICIES = set(design_module.POLICIES)
PROFILE_POLICIES = POLICIES - {'kube-default'}
REQUIRED_PHASES = ['scheduler-provenance', 'deployment-helpers', 'network-preflight', 'qualification']


def transfer_sizes(frozen):
    """Payload observations per kind for the calibration pairs: origins x destinations x pairs."""
    return topology.transfer_counts(frozen, design_module.CALIBRATION_PAIRS)


TRANSFER_SIZES = transfer_sizes(topology.LEGACY)


def read(path):
    return json.loads(path.read_text())


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(run):
    run = Path(run).resolve()  # run.name is the collector run_id (Session.out = ASSETS/run_id).
    errors = []

    def check(condition, message):
        if not condition:
            errors.append(message)
        return condition

    def load(name):
        path = run/name
        if not check(path.is_file(), 'missing '+name):
            return None
        return read(path)

    pairs_all = read(ASSETS/'load-pairs.json')['pairs']
    pairs = {p['question_id']: p for p in pairs_all}
    refs = {r['question_id']: r for r in map(json.loads, (ASSETS/'reference-v1/results.jsonl').read_text().splitlines())}
    models = read(ASSETS/'reference-v1/metadata.json')['stages']
    explicit = (run/'topology-frozen.json').is_file()
    frozen = topology.validate(read(run/'topology-frozen.json')['topology']) if explicit else topology.LEGACY
    nodes = topology.aliases(frozen)
    candidates = list(nodes.values())
    expected_design = design_module.build_design(pairs_all, frozen)
    design = load('design.json')
    if design is not None:
        check(design == expected_design, 'design differs from the declared reduced design')
    calibration_ids, evaluation_ids = expected_design['calibration_ids'], expected_design['evaluation_ids']
    conditions = {c['condition']: c for c in expected_design['conditions']}
    failure = (run/'failure.json').is_file()
    completed = (run/'completed.json').is_file()
    check(not failure, 'failure.json present: partial run')
    check(completed, 'completed.json missing: partial run')
    if explicit:
        check_topology(check, run, frozen)

    # Source provenance: frozen copies must match the recorded hashes.
    provenance = load('baseline-provenance.json')
    if provenance is not None:
        for name, digest in provenance['sources'].items():
            copy = run/'collector-source'/Path(name).name
            if Path(name).parent.name == 'real-topology-profile':
                copy = run/'planner-source.py'
            check(copy.is_file() and sha256(copy) == digest, 'source provenance '+name)
        check('experiments/real-topology-live/run_baseline_comparison.py' in provenance['sources'], 'collector source hash recorded')
    git_source = load('git-source.json')
    if git_source is not None:
        for name, digest in git_source['files'].items():
            check(sha256(run/'collector-source'/name) == digest, 'git-source copy '+name)
    planner = load('planner-source.json')
    if planner is not None:
        check(sha256(run/'planner-source.py') == planner['sha256'], 'planner source hash')
    scheduler = load('scheduler-provenance.json')
    if scheduler is not None:
        check(bool(scheduler['pods']) and all(c.get('image') for p in scheduler['pods'] for c in p['containers']), 'scheduler version provenance')
        check(not any('env' in c for p in scheduler['pods'] for c in p['containers']), 'scheduler provenance excludes env')
    check(not list(run.glob('*-scheduler-snapshot.stdout')), 'no raw scheduler snapshot persisted')

    # Qualification on the reduced input subset only.
    qualification = load('qualification-all.json')
    if qualification is not None:
        check(len(qualification) == expected_design['qualification']['expected_rows'], 'qualification row count')
        sequence = [calibration_ids[0]]*5 + calibration_ids + evaluation_ids
        for task in TASKS:
            for alias, node in nodes.items():
                rows = [r for r in qualification if r['task'] == task and r['node'] == node]
                check([r['question_id'] for r in rows] == sequence, f'qualification inputs {task}/{node}')
                check([r['warmup'] for r in rows] == [True]*5+[False]*(len(sequence)-5), f'qualification warmup {task}/{node}')
                check(all(r['quality_ok'] for r in rows), f'qualification quality {task}/{node}')
                deployment = run/f'qualify-{task}-{alias}-deployment.json'
                uid = read(deployment)['pods'][task]['metadata']['uid'] if check(deployment.is_file(), 'missing qualification deployment '+task+'/'+alias) else None
                for r in rows:
                    receipt = r['receipt']
                    check(receipt['model_sha256'] == models[task]['model_sha256'] and receipt['pod_uid'] == uid
                        and receipt['node'] == node and receipt.get('serving_torch_threads') == 1, f'qualification identity {task}/{node}')

    # Frozen physical profiles, recomputed selections, observed scheduler placement.
    capacity = load('capacity-frozen.json')
    plans = load('plans-frozen.json') or {}
    profile_plans = load('profile-plans-frozen.json') or {}
    profiles = {}
    for condition in conditions:
        profile = load('profile-'+condition+'.json')
        if profile is None:
            continue
        profiles[condition] = profile
        check('admission_budget' not in profile, condition+': no synthetic admission budget')
        check(profile['nodes'] == capacity, condition+': physical frozen capacity')
        check(profile['condition'] == condition and profile['representation'] == 'compressed', condition+': profile identity')
        check(set(profile['nodes']) == set(candidates) and all(t['candidates'] == candidates for t in profile['tasks']),
            condition+': topology candidates for every task')
        for task in profile['tasks']:
            for node, value in task['compute_ms'].items():
                times = sorted(r['receipt']['compute_ms'] for r in qualification or [] if r['task'] == task['id']
                    and r['node'] == node and not r['warmup'] and r['split'] == 'calibration')
                # Linear p75 of 4 values: position 2.25 -> sorted[2]*.75 + sorted[3]*.25.
                check(len(times) == 4 and abs(value-(times[2]*.75+times[3]*.25)) < 1e-8, condition+': p75 from 4 calibration samples')
        transfer = load('transfer-'+condition+'.json')
        if transfer is not None:
            check({k: len(v) for k, v in transfer['payload_bytes'].items()} == transfer_sizes(frozen), condition+': transfer calibration subset')
        digest = prepare.digest(profile)
        check(set(plans.get(condition, {})) == POLICIES, condition+': exact six-policy set')
        try:
            recomputed = design_module.profile_plans(profile)
        except (ValueError, RuntimeError) as error:
            check(False, condition+': profile policies not recomputable '+repr(error))
            recomputed = {}
        for policy in PROFILE_POLICIES:
            plan = plans.get(condition, {}).get(policy)
            if not check(plan is not None and plan['status'] == 'ok', condition+': plan '+policy):
                continue
            check(plan['profile_sha256'] == digest, condition+': profile digest '+policy)
            check(plan['placement'] == recomputed.get(policy, {}).get('placement'), condition+': recomputed selection '+policy)
            check(profile_plans.get(condition, {}).get(policy, {}).get('placement') == plan['placement'], condition+': frozen before scheduler/eval '+policy)
        selection = load('scheduler-'+condition+'.json')
        kube = plans.get(condition, {}).get('kube-default')
        if selection is not None and check(kube is not None, condition+': kube-default plan'):
            deployment = load('scheduler-'+condition+'-deployment.json')
            manifest = load('scheduler-'+condition+'.manifest.json')
            check(kube['placement'] == selection['placement'], condition+': kube-default equals observed selection')
            check(selection['validation'] == 'ok', condition+': observed selection valid')
            if deployment is not None:
                check(deployment['placement'] == selection['placement'], condition+': observed deployment placement')
                check(all(deployment['pods'][t]['spec'].get('schedulerName') == 'default-scheduler' for t in TASKS), condition+': default-scheduler')
            if manifest is not None:
                for pod in manifest['items']:
                    check(whitelist(pod) == sorted(nodes.values()), condition+': unpinned candidate-only scheduler path')
            if deployment is not None and manifest is not None:
                check_scheduler_pods(check, condition, run.name, selection, deployment, manifest, profile, nodes)
        for policy in POLICIES:
            plan = plans.get(condition, {}).get(policy)
            if plan is None:
                continue
            try:
                validate_placement(profile, plan['placement'])
            except ValueError as error:
                check(False, condition+': infeasible '+policy+' '+repr(error))

    # Frozen schedule, observation sharing, policy map.
    schedule = load('schedule-frozen.json')
    mapping = load('policy-observation-map.json')
    jobs = schedule['jobs'] if schedule else []
    if schedule is not None and plans and set(plans) == set(conditions):
        try:
            check(jobs == design_module.schedule_jobs(plans), 'schedule matches seeded grouping')
        except (KeyError, ValueError) as error:
            check(False, 'schedule recomputation failed '+repr(error))
    labels = [j['label'] for j in jobs]
    check(len(labels) == len(set(labels)), 'unique observation labels')
    for condition in conditions:
        keys = [tuple(sorted(j['placement'].items())) for j in jobs if j['condition'] == condition]
        check(len(keys) == len(set(keys)), condition+': no duplicate observation of the same placement')
        covered = [p for j in jobs if j['condition'] == condition for p in j['policies']]
        check(sorted(covered) == sorted(POLICIES), condition+': each policy in exactly one group')
    if mapping is not None:
        expected_map = {c: {p: j['label'] for j in jobs if j['condition'] == c for p in j['policies']} for c in conditions}
        check(mapping == expected_map, 'policy-observation map matches schedule')
    for job in jobs:
        plan_placements = [plans.get(job['condition'], {}).get(p, {}).get('placement') for p in job['policies']]
        check(all(p == job['placement'] for p in plan_placements), job['label']+': group placement equals policy plans')

    # Evaluation rows: identity, bytes, hashes and quality for every receipt.
    attempts = successes = failures = receipts = 0
    observations = {}
    for job in jobs:
        label, condition = job['label'], job['condition']
        deployment = run/(label+'-deployment.json')
        if not check(deployment.is_file(), label+': missing deployment'):
            continue
        deployment = read(deployment)
        check(deployment['placement'] == job['placement'], label+': deployed placement')
        for name, split, ids in [(label+'-warm', 'calibration', calibration_ids[:1]), (label, 'evaluation', evaluation_ids)]:
            config_path, rows_path = run/(name+'.config.json'), run/(name+'.rows.json')
            if not (check(config_path.is_file(), name+': missing config') and check(rows_path.is_file(), name+': missing rows')):
                continue
            config, rows = read(config_path), read(rows_path)
            check(config['placement'] == job['placement'], name+': config placement')
            check((config['mbps'], config['oneway_delay_ms'], config['representation'], config['rate_rps'], config['deadline_s'])
                == (job['mbps'], 20, 'compressed', .1, 120), name+': condition contract')
            check(config['question_ids'] == ids, name+': subset inputs')
            check(config.get('run_id') == run.name and config.get('phase') == name, name+': config run/phase identity')
            check(sorted(r['index'] for r in rows) == list(range(len(ids))), name+': complete terminal ledger')
            for row in rows:
                check(row['question_id'] == ids[row['index']] if row['index'] < len(ids) else False, name+': request identity')
                # load_trace tags every terminal row with its phase; failed rows carry no run/request id.
                check(row.get('event') == 'terminal' and row.get('phase') == name, name+': terminal phase identity')
                if row['status'] == 'ok' or 'run_id' in row or 'request_id' in row:
                    check(row.get('run_id') == run.name and row.get('request_id') == f"{name}-{row['index']}",
                        name+': run/request identity')
                check(pairs[row['question_id']]['split'] == split, name+': split')
                if split == 'evaluation':
                    attempts += 1
                if row['status'] != 'ok':
                    failures += split == 'evaluation'
                    continue
                successes += split == 'evaluation'
                receipts += len(row['receipts'])
                check_row(check, name, row, deployment['pods'], job['placement'], pairs, refs, models, nodes)
            drain = run/(name+'.drain.json')
            if check(drain.is_file(), name+': drain ledger'):
                ledger = read(drain)
                check(len(ledger) >= 2 and all(d['idle'] for d in ledger[-2:]), name+': two idle captures')
                for entry in ledger[-2:]:
                    snapshot = run/(name+f"-drain-network-{entry['attempt']}.json")
                    check(snapshot.is_file() and backlog_zero(read(snapshot)), name+': native backlog zero')
            if split == 'evaluation':
                observations[label] = dict(attempts=len(rows), success=sum(r['status'] == 'ok' for r in rows),
                    failures=sum(r['status'] != 'ok' for r in rows))
    check({p.name.removesuffix('.rows.json') for p in run.glob('eval-*.rows.json')} <= {n for l in labels for n in (l, l+'-warm')},
        'no unscheduled evaluation rows')
    check(attempts <= expected_design['max_workflow_requests'], 'within declared request budget')

    # Phase ledger: every start has an end; failures kept.
    starts = {p.name.removesuffix('.start.json') for p in run.glob('phase-*.start.json')}
    ends = {p.name.removesuffix('.end.json'): read(p) for p in run.glob('phase-*.end.json')}
    check(starts == set(ends), 'every phase start has an end record')
    failed_phases = sorted(n for n, e in ends.items() if e['status'] != 'ok')
    check(not failed_phases, 'failed phases '+repr(failed_phases))
    names = {e['name'] for e in ends.values()}
    expected_phases = REQUIRED_PHASES + ['topology-preflight']*explicit + [f'{kind}-{c}' for kind in ('transfer-calibration', 'planning', 'scheduler-selection') for c in conditions]
    check(all(n in names for n in expected_phases) and {'evaluation-'+l for l in labels} <= names, 'phase coverage')
    top_level_ms = sum(e['duration_ms'] for e in ends.values() if e['parent'] is None)

    cleanup = load('cleanup.json')
    if cleanup is not None:
        check(cleanup['remaining_owned'] == 0 and cleanup['errors'] == [], 'clean owned resource removal')
    protected = load('protected-after.json')
    if protected is not None:
        check(protected['changes'] == [], 'protected workloads unchanged')
    status = 'passed' if not errors else 'partial' if failure or not completed else 'invalid'
    return dict(status=status, errors=errors, groups=len(jobs), evaluation_attempts=attempts,
        evaluation_successes=successes, evaluation_failures=failures, receipts=receipts, observations=observations,
        failed_phases=failed_phases, top_level_phase_ms=top_level_ms,
        verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def check_topology(check, run, frozen):
    """Frozen topology, its digest and the pre-mutation node preflight must agree."""
    record = read(run/'topology-frozen.json')
    check(record.get('sha256') == topology.digest(frozen), 'topology digest')
    path = run/'topology-preflight.json'
    if not check(path.is_file(), 'missing topology-preflight.json'):
        return
    saved = read(path)
    observed = saved.pop('observed', [])
    check(saved.get('status') == 'passed' and saved == topology.preflight(frozen, observed), 'topology preflight passed for every candidate')
    raws = list(run.glob('*-topology-nodes.stdout'))
    check(len(raws) == 1 and topology.project_nodes(read(raws[0])) == observed, 'topology preflight matches raw node snapshot')


def whitelist(pod):
    """Sorted node names allowed by a Pod's required node affinity (one matchFields term per candidate)."""
    terms = pod['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']
    values = sorted(v for term in terms for f in term['matchFields'] for v in f['values'])
    return values if len(terms) == len(values) else None


def check_scheduler_pods(check, condition, run_id, selection, deployment, manifest, profile, nodes):
    """Selection must be carried by the captured scheduler Pods themselves, not only by placement dicts."""
    message = condition+': scheduler Pod evidence '
    check(sorted(selection.get('candidates', [])) == sorted(nodes.values()), message+'candidate list')
    manifests = {p['metadata']['name']: p for p in manifest['items']}
    candidates = {t['id']: t['candidates'] for t in profile['tasks']}
    events = {}
    for event in selection.get('events', []):
        if event.get('reason') == 'Scheduled':
            events.setdefault(event.get('involvedObject', {}).get('uid'), []).append(event)
    uids = set()
    for task in TASKS:
        pod, record = deployment['pods'].get(task), selection['pods'].get(task)
        if not check(pod is not None and record is not None, message+'missing '+task):
            continue
        name, uid, node = pod['metadata']['name'], pod['metadata'].get('uid'), pod['spec'].get('nodeName')
        uids.add(uid)
        check(name == run_id+'-'+task and name in manifests and record['name'] == name
            and pod['metadata'].get('labels', {}).get('run-id') == run_id, message+'name '+task)
        check(bool(uid) and record['uid'] == uid, message+'uid '+task)
        check(node == record['node'] == selection['placement'][task] == deployment['placement'][task], message+'node '+task)
        check(node in nodes.values() and node in candidates.get(task, []), message+'candidate node '+task)
        check(name in manifests and whitelist(pod) == whitelist(manifests[name]) == sorted(nodes.values())
            and node in whitelist(pod), message+'unpinned whitelist '+task)
        check(pod['spec'].get('schedulerName') == record['scheduler'] == 'default-scheduler', message+'scheduler '+task)
        scheduled = next((c for c in pod.get('status', {}).get('conditions', []) if c.get('type') == 'PodScheduled'), None)
        check(scheduled is not None and scheduled.get('status') == 'True' and (record['scheduled'] or {}).get('status') == 'True',
            message+'scheduled state '+task)
        bound = events.get(uid, [])
        check(bool(bound) and all(node in e['message'] for e in bound if e.get('message')), message+'Scheduled event '+task)
    check(len(uids) == len(TASKS), message+'distinct Pods')


def backlog_zero(snapshot):
    try:
        return all(v == 0 for v in native_backlogs(snapshot))
    except ValueError:
        return False


def check_row(check, name, row, pods, placement, pairs, refs, models, nodes):
    rs = {r['task']: r for r in row['receipts']}
    check(len(row['receipts']) == 3 and set(rs) == set(TASKS), name+': three receipts')
    if set(rs) != set(TASKS):
        return
    for task, r in rs.items():
        pod = pods[task]
        check(r['pod_uid'] == pod['metadata']['uid'] and r['node'] == pod['spec']['nodeName'] == placement[task], name+': runtime identity '+task)
        check(r['device'] == 'cpu' and r['model_sha256'] == models[task]['model_sha256'], name+': model '+task)
        check(r.get('serving_torch_threads') == r.get('serving_torch_interop_threads') == 1, name+': serving thread contract')
    pair, ref = pairs[row['question_id']], refs[row['question_id']]
    for task, kind in [('vision', 'image'), ('speech', 'audio')]:
        check(rs[task]['input_sha256']['source'] == pair[kind+'_sha256'], name+': input hash '+task)
        check(rs[task]['input_bytes']['source'] == pair[kind+'_bytes'], name+': input bytes '+task)
        check(rs['fusion']['input_sha256'][task] == rs[task]['output_sha256'], name+': join hash '+task)
        check(rs['fusion']['input_bytes'][task] == rs[task]['output_bytes'], name+': join bytes '+task)
    check(row['source_node'] == nodes['e0'] and row['speech_source_node'] == nodes['e1'], name+': fixed edge source')
    output = json.dumps(row['output'], separators=(',', ':')).encode()
    check(hashlib.sha256(output).hexdigest() == rs['fusion']['output_sha256'], name+': final payload hash')
    check(len(output) == rs['fusion']['output_bytes'], name+': final payload bytes')
    check(row['output']['answer_ids'] == ref['answer_ids'] and row['top5_order_matches'], name+': Top5 order')
    error = max(abs(a-b) for a, b in zip(row['output']['scores'], ref['scores']))
    check(len(row['output']['scores']) == 5 and error <= 1e-4 and abs(error-row['max_score_abs_error']) < 1e-12, name+': numeric quality')
    check(not row['deadline_exceeded'], name+': success within deadline')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('run', type=Path)
    parser.add_argument('--output', type=Path, help='new audit file; refuses to overwrite')
    args = parser.parse_args(argv)
    try:
        result = verify(args.run)
    except (OSError, KeyError, TypeError, ValueError) as error:
        result = dict(status='invalid', errors=['unreadable evidence: '+repr(error)])
    if args.output:
        with args.output.open('x') as handle:
            json.dump(result, handle, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != 'observations'}))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
