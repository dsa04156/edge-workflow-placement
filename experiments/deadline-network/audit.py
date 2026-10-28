"""Observational decision audit of dag_deadline_guard on existing unstable-network ledgers, and the exact policy spec.

  python3 audit.py spec [--deadline-ms 2000 --anchor-fraction 0.75]
  python3 audit.py audit --run RUN_DIR --plan PLAN_DIR --out NEW_DIR [--deadline-ms --anchor-fraction]

What the audit is: for every logged row, the decision dag_deadline_guard WOULD make on that row's logged observation
snapshot (row['observation'], recorded by the generator right before the logged policy decided), compared with the plan
the logged policy actually chose. What it is not: a replay. The guard's own plans would have changed the queues and
every later snapshot; no latency, deadline hit, goodput or success rate of the guard is estimated, and the logged outcome
of a row belongs to the logged plan only (it is joined afterwards, for descriptive stratification).

Snapshot reconstruction (normalized clock NOW; success_at = NOW - age_ms/1000):
  queue states, ages, compute_est, link_ms_per_byte are logged and restored as is.
  Observation.service_ms is NOT logged. It exists for a worker exactly when compute_est has an entry for that worker
  (record_receipt writes both). A queue estimate of a busy worker (waiting+active > 0) that has a service_ms is therefore
  not reconstructible and is never manufactured from compute_est: such a decision is counted non_reconstructible. An idle
  worker (queue 0) or a busy worker without receipts (base falls back to its frozen profile mean) is exact.
  The snapshot and the logged decision are two reads of a live object a few microseconds apart (poller/learn threads may
  update in between); the replay of the stateless logged policies on their own rows measures that fidelity.
Selector input is the snapshot only: status, latency, branches, answers, the logged decision and the trace phase are
never read before the guard decision is made.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deadline_policies as dp  # noqa: E402
import frozen_unstable_policies as base  # noqa: E402

HERE = Path(__file__).resolve().parent
NOW = 1000.0
REPLAYED = ('greedy_comm', 'dag_reopt', 'dag_reopt_no_network')  # stateless observation policies: replay = fidelity check
PREDICTED_TOL_MS = 1e-6
NO_CLAIM = ('none: no latency, deadline hit, goodput, completion or success rate of dag_deadline_guard is estimated; '
            'per-snapshot decisions are not a counterfactual trace')


class NotReconstructible(Exception):
    def __init__(self, worker):
        super().__init__(worker)
        self.worker = worker


class AuditObservation(base.Observation):
    """Logged snapshot as an Observation; a busy worker whose unlogged service_ms existed raises NotReconstructible."""

    unknown_service = frozenset()

    def unlogged(self, worker):
        q = (self.workers.get(worker) or {}).get('state')
        return q is not None and int(q.get('waiting', 0)) + int(q.get('active', 0)) > 0 and worker in self.unknown_service

    def queue_ms(self, worker, profile):
        if self.unlogged(worker):
            raise NotReconstructible(worker)
        return super().queue_ms(worker, profile)


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    return sha_bytes(Path(path).read_bytes())


def _number(value, what, allow_none=False):
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{what}: not a finite number ({value!r})')
    return float(value)


def _duration(value, what):
    value = _number(value, what)
    if value < 0:
        raise ValueError(f'{what}: negative ({value!r})')
    return value


def _split(key, sep, what, left, right):
    parts = key.split(sep)
    if len(parts) != 2 or parts[0] not in left or parts[1] not in right:
        raise ValueError(f'{what}: unknown key {key!r}')
    return tuple(parts)


def reconstruct(snapshot, workers):
    """Logged row['observation'] -> AuditObservation at NOW. Refuses (ValueError) anything it cannot read exactly."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('workers'), dict):
        raise ValueError('snapshot: missing workers')
    if set(snapshot['workers']) != set(workers):
        raise ValueError(f"snapshot: workers {sorted(snapshot['workers'])} != candidates {sorted(workers)}")
    obs = AuditObservation()
    for key in sorted(workers):
        w = snapshot['workers'][key]
        age = _number(w.get('age_ms'), f'{key}.age_ms', allow_none=True)
        if age is None:
            obs.record_poll(key, None, w.get('last_error') or 'never answered', NOW)
            continue
        if age < 0:
            raise ValueError(f'{key}.age_ms negative')
        state = {}
        for k in ('waiting', 'active', 'joins_open'):
            v = w.get(k)
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise ValueError(f'{key}.{k}: not a non-negative integer ({v!r})')
            state[k] = v
        obs.record_poll(key, state, None, NOW - age / 1000.0)
    locations = set(workers) | {base.ORIGIN}
    for table in ('compute_est', 'link_ms_per_byte'):  # logged {} = no receipt yet; absent/null/other = unreadable
        if not isinstance(snapshot.get(table), dict):
            raise ValueError(f'snapshot: {table} missing or not a dict ({snapshot.get(table)!r})')
    for k, v in snapshot['compute_est'].items():
        obs.compute_est[_split(k, '@', 'compute_est', base.TASKS, workers)] = _duration(v, f'compute_est {k}')
    for k, v in snapshot['link_ms_per_byte'].items():
        obs.link_est[_split(k, '->', 'link_ms_per_byte', locations, locations)] = _duration(v, f'link_ms_per_byte {k}')
    obs.unknown_service = frozenset(w for _, w in obs.compute_est)
    return obs


def guard_on_snapshot(policy, snapshot, workers):
    """The guard decision on one logged snapshot. Reads the snapshot only."""
    obs = reconstruct(snapshot, workers)
    try:
        d = policy.decide(obs, NOW)
    except NotReconstructible as missing:
        anchor = set(policy.anchor.values())
        part = 'anchor' if missing.worker in anchor else 'alternative'
        # the base predict() visits a plan's workers in set (hash-seed) order: report the sorted-first one of that part
        worker = min(w for w in policy.workers if obs.unlogged(w) and (w in anchor) == (part == 'anchor'))
        return dict(category='non_reconstructible', reason=f'{part}_worker_busy_unlogged_service', worker=worker)
    return dict(category='fallback' if d['fallback'] else 'exact', reason=d['choice_reason'], plan_label=d['plan_label'],
                anchor_predicted_ms=d['anchor_predicted_ms'], threshold_ms=d['threshold_ms'], selected_predicted_ms=d['selected_predicted_ms'],
                fallback_reason=(d['fallback'] or {}).get('reason'), anchor_kept=d['plan_label'] == base.plan_label(policy.anchor))


def replay_logged(policy, snapshot, workers, logged):
    """Fidelity of the reconstruction: the stateless logged policy re-decided on its own snapshot vs its logged decision."""
    obs = reconstruct(snapshot, workers)
    try:
        d = policy.decide(obs, NOW)
    except NotReconstructible:
        return 'non_reconstructible'
    if (d['fallback'] or {}).get('reason') != (logged.get('fallback') or {}).get('reason'):
        return 'fallback_mismatch'
    if d['plan_label'] != logged['plan_label']:
        return 'plan_mismatch'
    return 'match' if abs(d['predicted_ms'] - float(logged['predicted_ms'])) <= PREDICTED_TOL_MS else 'predicted_mismatch'


def audit_row(policy, row, workers, replay=None):
    """One ledger row -> audit record. The guard decision comes first, from the snapshot alone; the logged decision and
    outcome are joined afterwards (descriptive only)."""
    snapshot = row.get('observation')
    guard = guard_on_snapshot(policy, snapshot, workers) if snapshot is not None else dict(category='no_snapshot', reason='no_snapshot')
    logged = row.get('decision')
    out = dict(index=row['index'], scheduled_offset_s=row['scheduled_offset_s'], guard=guard,
               logged=dict(status=row.get('status'), plan_label=(logged or {}).get('plan_label'), predicted_ms=(logged or {}).get('predicted_ms'),
                           fallback_reason=((logged or {}).get('fallback') or {}).get('reason'), latency_ms=row.get('latency_ms')))
    if 'plan_label' in guard and logged is not None:
        out['agreement'] = guard['plan_label'] == logged['plan_label']
    if replay is not None and snapshot is not None and logged is not None:
        out['replay_fidelity'] = replay_logged(replay, snapshot, workers, logged)
    return out


# ---------------------------------------------------------------------------------------------------- run inputs
def load_json(path, hashes, root):
    path = Path(path)
    data = path.read_bytes()
    hashes[str(path.relative_to(root)) if path.is_relative_to(root) else str(path)] = sha_bytes(data)
    return json.loads(data)


def load_run(run_dir, plan_dir, root):
    """Validates the 36-cell run against its released plan; returns (inputs, hashes). Refuses (ValueError) on any mismatch."""
    run, plan_dir, hashes = Path(run_dir), Path(plan_dir), {}
    plan = load_json(plan_dir/'plan.json', hashes, root)
    release = load_json(run/'release.json', hashes, root)['binding']
    if release.get('plan_sha256') != sha_file(plan_dir/'plan.json'):
        raise ValueError('run was not released for this plan')
    cells = load_json(run/'cells.json', hashes, root)
    if cells != plan['schedule']['cells']:
        raise ValueError('cells.json is not the plan frozen schedule')
    profile_json = load_json(run/'profile-good.json', hashes, root)
    candidates = load_json(run/'live-candidates.json', hashes, root)['candidates']
    if load_json(run/'collection.json', hashes, root).get('candidates') != candidates:
        raise ValueError('collection candidates differ from live-candidates.json')
    budgets = plan['budgets']
    ledgers = []
    for cell in cells:
        label = cell['label']
        request = load_json(run/f'trace-{label}.request.json', hashes, root)
        ledger = load_json(run/f'ledger-{label}.json', hashes, root)
        problems = []
        if ledger.get('label') != label or request.get('label') != label:
            problems.append('label')
        if (ledger.get('policy') or {}).get('name') != cell['policy'] or request['policy'] != ledger['policy']:
            problems.append('policy')
        if ledger.get('state') != 'done' or ledger.get('partial') is not False:
            problems.append('state')
        if ledger.get('profile_source') != profile_json['source']:
            problems.append('profile_source')
        if float(ledger.get('deadline_s')) != float(budgets['deadline_s']) or \
                float(ledger['observation_contract']['stale_after_s']) != float(budgets['obs_stale_s']):
            problems.append('deadline/stale contract')
        rows, arrivals = ledger.get('rows'), request.get('arrivals')
        if not isinstance(rows, list) or [r.get('index') for r in rows] != list(range(len(arrivals))):
            problems.append('row indices')
        elif any(str(r['question_id']) != str(a['question_id']) or float(r['scheduled_offset_s']) != float(a['offset_s'])
                 for r, a in zip(rows, sorted(arrivals, key=lambda a: a['index']))):
            problems.append('rows differ from the request cohort')
        if problems:
            raise ValueError(f'{label}: {problems}')
        ledgers.append((cell, ledger))
    return dict(plan_sha256=sha_file(plan_dir/'plan.json'), run_id=release['run_id'], cells=cells, profile=base.Profile.from_json(profile_json),
                candidates=candidates, ledgers=ledgers, stale_after_s=float(budgets['obs_stale_s']),
                deadline_s=float(budgets['deadline_s'])), hashes


# ---------------------------------------------------------------------------------------------------- aggregation
def count(records, key):
    out = {}
    for r in records:
        k = key(r)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: str(kv[0])))


def summarize(records):
    g = lambda r: r['guard']
    with_plan = [r for r in records if 'plan_label' in g(r)]
    compared = [r for r in records if 'agreement' in r]
    return dict(
        rows=len(records),
        category=count(records, lambda r: g(r)['category']),
        reason=count(records, lambda r: g(r)['reason']),
        guard_plan_known=len(with_plan),
        guard_anchor_kept=sum(1 for r in with_plan if g(r)['anchor_kept']),
        guard_moved_from_anchor=sum(1 for r in with_plan if not g(r)['anchor_kept']),
        compared_with_logged=len(compared),
        agree_with_logged=sum(1 for r in compared if r['agreement']),
        differ_from_logged=sum(1 for r in compared if not r['agreement']),
        logged_plan_is_anchor=None,  # filled by the caller (needs the anchor label)
        missing_or_unsupported=len(records) - len(with_plan),
        replay_fidelity=count([r for r in records if 'replay_fidelity' in r], lambda r: r['replay_fidelity']),
        logged_status_by_guard_category=count(records, lambda r: f"{g(r)['category']}|{r['logged']['status']}"),
        logged_status_by_agreement=count(compared, lambda r: f"{'agree' if r['agreement'] else 'differ'}|{r['logged']['status']}"))


def audit_run(inputs, deadline_ms, anchor_fraction):
    spec = dp.policy_spec(deadline_ms, anchor_fraction)
    gate = dp.ExecutionOnly()
    workers = sorted({w for ws in inputs['candidates'].values() for w in ws})
    build = lambda s: dp.build(s, inputs['candidates'], gate, inputs['profile'], stale_after_s=inputs['stale_after_s'])
    guard = build(spec)
    anchor = base.plan_label(guard.anchor)
    cells, rows_out = [], []
    for cell, ledger in inputs['ledgers']:
        replay = build(ledger['policy']) if cell['policy'] in REPLAYED else None
        records = [audit_row(guard, row, workers, replay) for row in ledger['rows']]
        for r in records:
            rows_out.append(dict(r, label=cell['label'], block=cell['block'], trace=cell['trace'], policy=cell['policy']))
        s = summarize(records)
        s['logged_plan_is_anchor'] = sum(1 for r in records if r['logged']['plan_label'] == anchor)
        cells.append(dict(label=cell['label'], block=cell['block'], trace=cell['trace'], policy=cell['policy'], **s))
    group = lambda key: {k: _merge([c for c in cells if c[key] == k]) for k in sorted({c[key] for c in cells}, key=str)}
    static_rows = [r for r in rows_out if r['policy'] == 'dag_static' and r['logged']['plan_label'] is not None]
    return dict(
        policy_spec=spec, anchor_plan_label=anchor,
        anchor_check=dict(dag_static_logged_rows=len(static_rows),
                          dag_static_logged_equal_anchor=sum(1 for r in static_rows if r['logged']['plan_label'] == anchor)),
        workers=workers, enumerated_plans=len(guard.plans), cells=cells, by_policy=group('policy'), by_trace=group('trace'),
        by_block=group('block'), total=_merge(cells)), rows_out


def _merge(cells):
    out = {}
    for c in cells:
        for k, v in c.items():
            if k in ('label', 'block', 'trace', 'policy'):
                continue
            if isinstance(v, dict):
                d = out.setdefault(k, {})
                for kk, vv in v.items():
                    d[kk] = d.get(kk, 0) + vv
            else:
                out[k] = out.get(k, 0) + v
    out['cells'] = len(cells)
    return out


AGGREGATION = dict(
    unit='one ledger row (scheduled arrival) of one cell; requests inside a cell are not independent replicates',
    category='no_snapshot: row has no observation (admission_drop / not_sent); fallback: guard falls back to the anchor on the '
             'reconstructed poll ages (exact); exact: guard decision fully determined by logged fields; non_reconstructible: '
             'the decision needs the queue estimate of a busy worker whose service_ms was not logged',
    agreement='guard plan == logged plan on the same snapshot, over rows with both a guard plan and a logged decision',
    anchor_kept='guard plan == anchor, over rows with a guard plan (exact + fallback)',
    missing_or_unsupported='rows - (exact + fallback)',
    replay_fidelity='greedy_comm / dag_reopt / dag_reopt_no_network re-decided on their own snapshot vs their logged decision '
                    f'(plan label and predicted_ms within {PREDICTED_TOL_MS} ms, fallback reason)',
    logged_status='the logged policy outcome of that row (its own plan); descriptive join only, never a guard outcome',
    totals='sums of per-cell counts; by_policy/by_trace/by_block are the same sums over subsets')


def integration():
    return dict(
        build='deadline_policies.build(spec, candidates, deadline_policies.ExecutionOnly(), profile, stale_after_s=stale_after_s)',
        decide='policy.decide(observation, now) with the generator Observation (base Observation class) and its monotonic clock',
        boundary='experiments/unstable-network/generator.py imports its own policies.build and QualityGate; it does NOT accept '
                 'dag_deadline_guard. A next runner must import deadline_policies.build (and ExecutionOnly) instead; that '
                 'runner/generator copy is not part of this change.',
        deadline_rule='spec.deadline_ms must equal the trace deadline_s x 1000 of the same run',
        log_for_next_run='per decision: observation service_ms per worker and evidence ages of compute/link estimates (not '
                         'logged by the base generator)')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('spec', 'audit'):
        p = sub.add_parser(name)
        p.add_argument('--deadline-ms', type=float, default=dp.DEFAULTS['deadline_ms'])
        p.add_argument('--anchor-fraction', type=float, default=dp.DEFAULTS['anchor_fraction'])
        if name == 'audit':
            p.add_argument('--run', required=True)
            p.add_argument('--plan', required=True)
            p.add_argument('--out', required=True, help='new directory (refused if it exists)')
            p.add_argument('--root', default=str(HERE.parent.parent))
    args = ap.parse_args(argv)
    try:
        if args.cmd == 'spec':
            print(json.dumps(dict(policy=dp.policy_spec(args.deadline_ms, args.anchor_fraction), integration=integration()), indent=1))
            return 0
        out = Path(args.out)
        if out.exists():
            raise ValueError(f'{out} exists; the audit writes only a new directory')
        root = Path(args.root).resolve()
        inputs, hashes = load_run(Path(args.run).resolve(), Path(args.plan).resolve(), root)
        if dp.policy_spec(args.deadline_ms, args.anchor_fraction)['deadline_ms'] != inputs['deadline_s'] * 1000:
            print(f"warning: deadline_ms {args.deadline_ms} != run deadline {inputs['deadline_s'] * 1000}", file=sys.stderr)
        result, rows = audit_run(inputs, args.deadline_ms, args.anchor_fraction)
        rows_bytes = json.dumps(rows, separators=(',', ':'), sort_keys=True).encode()
        code = {p.name: sha_file(p) for p in (HERE/'deadline_policies.py', HERE/'frozen_unstable_policies.py', Path(__file__).resolve())}
        report = dict(run=inputs['run_id'], plan_sha256=inputs['plan_sha256'], cell_count=len(inputs['cells']),
                      arrivals=sum(len(l['rows']) for _, l in inputs['ledgers']), input_sha256=hashes, code_sha256=code,
                      rows_sha256=sha_bytes(rows_bytes), aggregation=AGGREGATION, claims=NO_CLAIM, integration=integration(), **result)
        out.mkdir(parents=True)
        (out/'rows.json').write_bytes(rows_bytes)
        (out/'audit.json').write_text(json.dumps(report, indent=1, sort_keys=True))
        t = result['total']
        print(json.dumps(dict(out=str(out), cells=report['cell_count'], arrivals=report['arrivals'], category=t['category'],
                              agree=t['agree_with_logged'], differ=t['differ_from_logged'], replay=t['replay_fidelity']), sort_keys=True))
        return 0
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        print(f'{args.cmd} refused: {type(error).__name__}: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
