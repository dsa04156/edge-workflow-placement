"""Unstable-edge-network heterogeneous DAG re-experiment: plan (no kubectl) | dry-run (no kubectl) | execute (release-bound
live) | analyze (offline). Adapted copy of experiments/quality-network-baselines/baselines.py (no pilot/freeze stage: the
offered load 1.6 rps, traces and schedule are fixed by the spec).

  plan     --inventory <node-inventory.json> --export <qnh export dir> --deps <qnh deps dir>... [--out DIR]
           needs numpy+pandas for the experimental-design helper (run under the uv command in the experiment doc).
  dry-run  --plan DIR [--run-id un-...]   prints drift, cells, budgets, estimates, the exact release binding and the
           execute command. Never calls kubectl.
  execute  --plan DIR --run-id un-... --release FILE
           rc 2 BEFORE any kubectl call unless FILE is JSON {"release": "RELEASE", stage, run_id, plan_sha256,
           shipped_sha256, budgets_sha256, traces_sha256, schedule_sha256} matching this plan/sources exactly.
  analyze  --plan DIR --run RUN_DIR [--out FILE]
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import design  # noqa: E402
import policies  # noqa: E402
import traces  # noqa: E402

OUT_ROOT = design.ROOT/'artifacts/unstable-network'
STAGE = 'collect'
PLANNER_SOURCES = ('unstable.py', 'design.py', 'live.py', 'analyze.py', 'policies.py', 'generator.py', 'netctl.py', 'traces.py')
# copy -> origin (frozen; never modified). Relation: the copy is the imported/shipped file; the origin is only hashed.
COPIED = {'policies.py': design.QNB/'policies.py', 'generator.py': design.QNB/'generator.py', 'live.py': design.QNB/'live.py',
          'analyze.py': design.QNB/'analyze.py', 'design.py': design.QNB/'design.py', 'unstable.py': design.QNB/'baselines.py'}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def utc():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def shipped_hashes():
    import live
    return live.shipped_hashes()


def planner_hashes():
    return {f: sha(HERE/f) for f in PLANNER_SOURCES}


def copied_sources():
    return {name: dict(origin=str(origin.relative_to(design.ROOT)), origin_sha256=sha(origin), copy_sha256=sha(HERE/name),
                       relation='adapted copy in experiments/unstable-network (imported/shipped); origin unchanged, hashed only')
            for name, origin in COPIED.items()}


def traces_table():
    return {k: traces.trace_sha256(k) for k in traces.TRACES}


def source_drift(value):
    drift = []
    if value.get('shipped_sha256') != design.digest(shipped_hashes()):
        drift.append('shipped sources changed since the plan')
    if value.get('budgets_sha256') != design.digest(design.BUDGETS):
        drift.append('budgets changed since the plan')
    if value.get('planner_sources') != planner_hashes():
        drift.append('planner sources changed since the plan')
    if value.get('traces') != traces_table():
        drift.append('network trace table changed since the plan')
    for name, rec in (value.get('copied_sources') or {}).items():
        if sha(design.ROOT/rec['origin']) != rec['origin_sha256']:
            drift.append(f'copy origin changed: {rec["origin"]}')
    if set(value.get('copied_sources') or {}) != set(COPIED):
        drift.append('copied-source record incomplete')
    helper = (value.get('schedule') or {}).get('helper') or {}
    if not helper or sha(design.ROOT/helper['path']) != helper['sha256']:
        drift.append('experimental-design helper changed since the plan')
    return drift


def plan(out_dir, inventory, export_dir, deps_dirs, hash_files=True):
    """Writes plan.json, assets-manifest.json, corpus.json, catalog.{json,csv} (baselines plan rules, unchanged assets)."""
    import analyze
    import live
    qualify, catalog = live.qualify, live.qualify.catalog
    schedule = design.schedule()  # numpy/pandas; generated once and frozen here
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=False)
    audit = catalog.load_audit(inventory)
    workers = design.dag_workers(qualify.workers_for(audit))
    used = {w['recipe'] for w in workers}
    files = [f for f in qualify.asset_files(export_dir, deps_dirs, recipes=used, calibration_inputs=False) if f[1] != 'onnx/speech-dynamic.onnx']
    jb, fusion_sha = qualify.jellybean_tar(out)
    digest = (lambda p: sha(p)) if hash_files else (lambda p: None)
    entries = [dict(group='common', path='jellybean.tar', source=str(jb), bytes=jb.stat().st_size, sha256=digest(jb))]
    entries += [dict(group=g, path=p, source=str(s), bytes=s.stat().st_size, sha256=digest(s),
                     **({'unpacked_bytes': qualify.unpacked_bytes(s)} if p.endswith('.whl') else {})) for g, p, s in files]
    pairs = qualify.expected_pairs()
    corpus = []
    for p in pairs:
        for kind in ('image', 'audio'):
            src = qualify.CORPUS/Path(p['raw_'+kind+'_path']).name
            entry = dict(group='corpus', path='corpus/'+src.name, source=str(src), bytes=src.stat().st_size, sha256=digest(src))
            if hash_files and entry['sha256'] != p['raw_'+kind+'_sha256']:
                raise ValueError(f'{src}: sha256 differs from the pinned load-pairs manifest')
            entries.append(entry)
            corpus.append(dict(question_id=p['question_id'], kind=kind, path=entry['path'], sha256=entry['sha256'] or p['raw_'+kind+'_sha256']))
    entries.append(dict(group='netctl', path='netctl/'+live.PYROUTE2.name, source=str(live.PYROUTE2), bytes=live.PYROUTE2.stat().st_size,
                        sha256=digest(live.PYROUTE2)))
    if hash_files and entries[-1]['sha256'] != live.PYROUTE2_SHA256:
        raise ValueError('pyroute2 wheel sha256 differs from the pinned value')
    for w in workers:
        budget = qualify.storage_budget(w, entries)
        if budget:
            w['storage'] = budget
    export = json.loads((Path(export_dir)/'manifest.json').read_text())
    recipes = {k: dict(requirements=v, no_deps=k in qualify.NO_DEPS_RECIPES) for k, v in qualify.REQUIREMENTS.items() if k in used}
    assets = dict(files=entries, recipes=recipes,
                  onnx=dict(vision=dict(path='onnx/vision-static-448.onnx', sha256=export['stages']['vision']['export']['sha256'])),
                  fusion_tar_sha256=fusion_sha, export_dir=str(export_dir), deps_dirs=[str(d) for d in deps_dirs])
    with (out/'assets-manifest.json').open('x') as handle:
        json.dump(assets, handle, indent=1)
    with (out/'corpus.json').open('x') as handle:
        json.dump(dict(files=corpus, note='raw decoded .npy fixtures of the pinned 60 pairs (reused diagnostic set, not a holdout)'), handle, indent=1)
    cands = design.candidates(workers)
    cat = catalog.build(audit)
    dag_keys = {(w['node'], w['backend']) for w in workers}
    for row in cat['rows']:
        row['dag_role'] = 'deployed candidate' if (row['node'], row['backend']) in dag_keys else 'not deployed (status recorded)'
    catalog.write(cat, out)
    value = dict(created_utc=utc(), experiment=design.EXPERIMENT, namespace=live.NS,
                 inventory=dict(path=str(inventory), sha256=sha(inventory)), workers=workers, candidates=cands,
                 coverage=design.coverage(cands), dag_pairs=design.dag_pairs(cands),
                 design=design.DESIGN, traces=traces_table(), trace_table=traces.TRACES, approved_values=traces.APPROVED,
                 network_scope='egress of the owned gateway Pod netns only: gateway->cloud and gateway->edge Pod IPs (edge<->server '
                               'path); edge<->edge LAN, host, physical links and other workloads untouched',
                 schedule=schedule, policies=list(design.POLICY_SPECS), recovery=design.RECOVERY,
                 prior_adapters={k: dict(paper=v.paper, status='not_implemented', audit=v.audit) for k, v in policies.PRIOR_ADAPTERS.items()},
                 budgets=design.BUDGETS, budgets_sha256=design.digest(design.BUDGETS),
                 quality=dict(mode=design.QUALITY_MODE, qmin=design.QMIN, evidence={},
                              status='no plan meets Qmin 0.40 (2026-10-23: cal 0.515 / eval 0.355 on the reused 60 pairs); every result '
                                     'is a diagnostic, not a same-quality comparison; a quality miss does not block the network run'),
                 inputs=dict(pairs=str(qualify.LOAD_PAIRS.relative_to(design.ROOT)), pairs_sha256=sha(qualify.LOAD_PAIRS),
                             gt=str(qualify.GT_PAIRS.relative_to(design.ROOT)), gt_sha256=sha(qualify.GT_PAIRS),
                             trace_split='evaluation (40, reused; diagnostic)', profile_split='calibration (first profile_pairs)'),
                 pairs=[dict(question_id=p['question_id'], split=p['split']) for p in pairs],
                 assets_manifest_sha256=sha(out/'assets-manifest.json'), corpus_sha256=sha(out/'corpus.json'),
                 asset_bytes=sum(e['bytes'] for e in entries),
                 model_provenance=dict(expectations=design.model_expectations(workers, assets, export),
                                       export_manifest=str(Path(export_dir)/'manifest.json'),
                                       export_manifest_sha256=sha(Path(export_dir)/'manifest.json'),
                                       rule='receipts and UID-checked /health must equal these plan-bound hashes; artifact identity only'),
                 shipped_sources=live.shipped_hashes(), shipped_sha256=design.digest(live.shipped_hashes()),
                 planner_sources=planner_hashes(), copied_sources=copied_sources(),
                 reused_frozen_sources={str(p.relative_to(design.ROOT)): sha(p) for p in (
                     design.QNH/'qualify.py', design.QNH/'catalog.py', live.LIVE/'http_runtime.py', design.ROOT/'experiments/multi-edge-sensors/collect.py',
                     Path(live.baseline.__file__).resolve(), design.ROOT/analyze.VQA_EVAL, design.QNB/'capture.py')},
                 node_image_storage=qualify.node_image_storage(workers),
                 estimate=design.cells_estimate(schedule['cells']),
                 not_deployed=[dict(node=r['node'], family=r['family'], stage=r['stage'], status=r['status'])
                               for r in cat['rows'] if r['dag_role'] != 'deployed candidate' and r['stage'] == 'vision'])
    with (out/'plan.json').open('x') as handle:
        json.dump(value, handle, indent=1)
    return value, sha(out/'plan.json')


def binding(plan_dir, run_id, verify_files=True):
    """-> (release binding, drift). Any drift (sources, budgets, trace table, copy origins, helper, schedule, consumed inputs,
    assets, corpus mapping, model provenance) refuses execute before kubectl; an unreadable plan is drift, not a crash."""
    plan_dir = Path(plan_dir)
    value = json.loads((plan_dir/'plan.json').read_text())
    out = dict(stage=STAGE, run_id=run_id, plan_sha256=sha(plan_dir/'plan.json'), shipped_sha256=design.digest(shipped_hashes()),
               budgets_sha256=design.digest(design.BUDGETS), traces_sha256=design.digest(traces_table()),
               schedule_sha256=design.digest((value.get('schedule') or {}).get('cells')))
    drift = source_drift(value)
    try:
        cells = value['schedule']['cells']
        if design.check_schedule(cells):
            drift.append('plan schedule fails the frozen balance check')
        import live
        drift += input_drift(value, plan_dir, live.qualify, verify_files)
    except (KeyError, TypeError, ValueError, OSError) as error:
        drift.append(f'plan inputs unreadable: {error!r}'[:300])
    return out, drift


def input_drift(value, plan_dir, qualify, verify_files):
    """Unchanged rule of baselines.input_drift (reused frozen sources, inputs, inventory, assets, corpus, provenance)."""
    drift = []
    for path, h in value['reused_frozen_sources'].items():
        if sha(design.ROOT/path) != h:
            drift.append(f'reused frozen source changed: {path}')
    inputs = value['inputs']
    for key in ('pairs', 'gt'):
        if sha(design.ROOT/inputs[key]) != inputs[f'{key}_sha256']:
            drift.append(f'input changed: {inputs[key]}')
    if sha(value['inventory']['path']) != value['inventory']['sha256']:
        drift.append('planning inventory changed')
    manifest_path = plan_dir/'assets-manifest.json'
    if sha(manifest_path) != value['assets_manifest_sha256']:
        drift.append('assets manifest changed since the plan')
    manifest = json.loads(manifest_path.read_text())
    if verify_files:
        for e in manifest['files']:
            if not e.get('sha256'):
                drift.append(f"asset entry without sha256: {e['path']}")
            elif not Path(e['source']).is_file() or Path(e['source']).stat().st_size != e['bytes'] or sha(e['source']) != e['sha256']:
                drift.append(f"asset source changed: {e['path']}")
    if sha(plan_dir/'corpus.json') != value['corpus_sha256']:
        drift.append('corpus mapping changed since the plan')
    corpus = {(str(f['question_id']), f['kind']): (f['path'], f['sha256']) for f in json.loads((plan_dir/'corpus.json').read_text())['files']}
    pinned = {(str(p['question_id']), k): ('corpus/'+Path(p['raw_'+k+'_path']).name, p['raw_'+k+'_sha256'])
              for p in qualify.expected_pairs() for k in ('image', 'audio')}
    if corpus != pinned:
        drift.append('corpus mapping differs from the pinned load-pairs manifest')
    shipped = {(e['path'], e['sha256']) for e in manifest['files'] if e.get('group') == 'corpus'}
    if verify_files and not set(corpus.values()) <= shipped:
        drift.append('corpus entry not shipped by the assets manifest')
    provenance = value['model_provenance']
    export_path = design.ROOT/provenance['export_manifest']
    if sha(export_path) != provenance['export_manifest_sha256']:
        drift.append('export record changed since the plan')
    try:
        expected = design.model_expectations(value['workers'], manifest, json.loads(export_path.read_text()))
    except ValueError as error:
        drift.append(f'model provenance: {error}')
    else:
        if expected != provenance['expectations']:
            drift.append('model provenance differs from the frozen assets')
    return drift


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest='mode', required=True)
    p = sub.add_parser('plan')
    p.add_argument('--inventory', required=True)
    p.add_argument('--export', required=True)
    p.add_argument('--deps', action='append', required=True)
    p.add_argument('--out', default=None)
    for name in ('dry-run', 'execute'):
        e = sub.add_parser(name)
        e.add_argument('--plan', required=True)
        e.add_argument('--run-id', required=name == 'execute', default='un-RUNID')
        if name == 'execute':
            e.add_argument('--release', required=True)
            e.add_argument('--output-root', default=str(OUT_ROOT))
    a = sub.add_parser('analyze')
    a.add_argument('--plan', required=True)
    a.add_argument('--run', required=True)
    a.add_argument('--out', default=None)
    args = ap.parse_args(argv)
    if args.mode == 'plan':
        value, plan_sha = plan(args.out or OUT_ROOT/f'plan-{utc()}', args.inventory, args.export, args.deps)
        print(json.dumps(dict(plan_sha256=plan_sha, workers=[w['key'] for w in value['workers']], candidates=value['candidates'],
                              coverage=value['coverage'], asset_bytes=value['asset_bytes'], cells=len(value['schedule']['cells']),
                              assets_manifest_sha256=value['assets_manifest_sha256']), indent=1))
        return 0
    if args.mode in ('dry-run', 'execute'):
        budget = None
        if args.mode == 'execute':
            import live
            budget = live.WallBudget()  # the one wall budget starts here, before release verification and preparation
        bind, drift = binding(args.plan, args.run_id)
        if args.mode == 'dry-run':
            plan_value = json.loads((Path(args.plan)/'plan.json').read_text())
            cells = plan_value['schedule']['cells']
            print(json.dumps(dict(stage=STAGE, drift=drift, release_binding=dict(release='RELEASE', **bind),
                                  candidates=plan_value['candidates'], coverage=plan_value['coverage'], traces=traces.TRACES,
                                  policies=[s['name'] for s in design.POLICY_SPECS], cells=len(cells),
                                  order=[c['label'] for c in cells], estimate=design.cells_estimate(cells), budgets=design.BUDGETS,
                                  quality=plan_value['quality']['status'],
                                  command=f"rtk proxy {sys.executable} experiments/unstable-network/unstable.py execute --plan {args.plan} "
                                          f"--run-id {args.run_id} --release <coordinator-release.json>"), indent=1, default=str))
            return 0
        ok, reason = design.release_ok(args.release, bind)
        if drift or not ok:
            print(f'refused: {"; ".join(drift) or reason}', file=sys.stderr)
            return 2
        plan_value = json.loads((Path(args.plan)/'plan.json').read_text())
        import live
        from live import ownership
        session = live.UnstableSession(args.output_root, args.run_id, plan_value, budget=budget)
        status, error = 'error', None
        session.save('release.json', dict(release=str(args.release), binding=bind, release_sha256=sha(args.release)))
        session.require(live.PROTECTED_SNAPSHOT_S, 'protected-before')
        session.save('protected-before.json', ownership.protected_snapshot(session, 'protected-before'))
        try:
            live.Live(session, plan_value, args.plan, budget=budget).run()
            status = 'collected'
        except BaseException as exc:
            error = repr(exc)
            raise
        finally:
            session.begin_cleanup()  # UID-owned deletion may use the cleanup reserve; unverifiable cleanup stays reported
            remaining, errors = live.cleanup(session)  # collect.cleanup unless its protected snapshot cannot fit
            diff = session.out/'protected-diff.json'
            changes = json.loads(diff.read_text())['changes'] if diff.exists() else None
            session.save('run-status.json', dict(status=status, error=error, cleanup_remaining=remaining, cleanup_errors=errors,
                                                 cleanup_verified=remaining == 0, wall_budget=budget.record(), protected_changes=changes,
                                                 audit='complete' if changes == [] and remaining == 0 else 'partial'))
        return 0
    import analyze
    result = analyze.analyze(args.run, args.plan)
    if args.out:
        with Path(args.out).open('x') as handle:
            json.dump(result, handle, indent=1)
    print(json.dumps([{k: t[k] for k in ('label', 'goodput_rps', 'counts', 'comparable', 'latency_ms', 'fallbacks', 'recovery')} for t in result['cells']],
                     indent=1)[:6000])
    return 0


if __name__ == '__main__':
    sys.exit(main())
