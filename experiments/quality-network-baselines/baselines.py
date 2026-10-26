"""Baseline-first heterogeneous DAG comparison: plan (no kubectl) | dry-run (no kubectl) | execute (release-bound live) |
freeze (pilot -> frozen offered loads, offline) | analyze (offline).

  plan     --inventory <capture.py node-inventory.json> --export <qnh export dir> --deps <qnh deps dir>... [--out DIR]
  dry-run  --plan DIR --stage pilot|compare [--frozen FILE] [--run-id qnb-...]  prints cells, budgets, estimates, the exact
           release binding and the execute command. Never calls kubectl.
  execute  --plan DIR --stage pilot|compare --run-id qnb-... --release FILE [--frozen FILE]
           rc 2 BEFORE any kubectl call unless FILE is JSON {"release": "RELEASE", stage, run_id, plan_sha256,
           shipped_sha256, budgets_sha256[, frozen_loads_sha256]} matching this plan/sources/budgets exactly.
  freeze   --plan DIR --pilot-run RUN_DIR [--out FILE]   saturation rule -> equal absolute loads for all policies.
  analyze  --plan DIR --run RUN_DIR [--frozen FILE (compare: the release-bound frozen loads)] [--out FILE]
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

OUT_ROOT = design.ROOT/'artifacts/quality-network-baselines'
PLANNER_SOURCES = ('baselines.py', 'design.py', 'live.py', 'analyze.py', 'capture.py', 'policies.py', 'generator.py', 'dag_worker.py',
                   'gateway.py', 'bounded_http.py')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def utc():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def plan(out_dir, inventory, export_dir, deps_dirs, hash_files=True):
    """Writes plan.json, assets-manifest.json, corpus.json, catalog.{json,csv}. hash_files=False only in unit tests."""
    import live
    qualify, catalog = live.qualify, live.qualify.catalog
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=False)
    audit = catalog.load_audit(inventory)
    workers = design.dag_workers(qualify.workers_for(audit))
    used = {w['recipe'] for w in workers}
    # NPU ASR is not a candidate here, so the derived speech ONNX is not shipped (vision ONNX only).
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
    manifest_sha = sha(out/'assets-manifest.json')
    import live as live_module
    value = dict(created_utc=utc(), experiment=design.EXPERIMENT, namespace=live_module.NS,
                 inventory=dict(path=str(inventory), sha256=sha(inventory)), workers=workers, candidates=cands,
                 coverage=design.coverage(cands), fixed_plan=design.fixed_plan(workers), dag_pairs=design.dag_pairs(cands),
                 networks=design.NETWORKS, pilot=design.PILOT, compare=design.COMPARE, policies=list(design.POLICY_SPECS),
                 prior_adapters={k: dict(paper=v.paper, status='not_implemented', audit=v.audit) for k, v in policies.PRIOR_ADAPTERS.items()},
                 budgets=design.BUDGETS, budgets_sha256=design.digest(design.BUDGETS), load_fractions=design.LOAD_FRACTIONS,
                 saturation_rule=design.SATURATION_RULE,
                 quality=dict(mode='diagnostic', qmin=design.QMIN, evidence={},
                              status='no plan meets Qmin 0.40 (2026-10-23: cal 0.515 / eval 0.355 on the reused 60 pairs); every result '
                                     'is a diagnostic, not a same-quality comparison; parity is a separate diagnostic'),
                 inputs=dict(pairs=str(qualify.LOAD_PAIRS.relative_to(design.ROOT)), pairs_sha256=sha(qualify.LOAD_PAIRS),
                             gt=str(qualify.GT_PAIRS.relative_to(design.ROOT)), gt_sha256=sha(qualify.GT_PAIRS),
                             trace_split='evaluation (40, reused; diagnostic)', profile_split='calibration (first profile_pairs)'),
                 pairs=[dict(question_id=p['question_id'], split=p['split']) for p in pairs],
                 assets_manifest_sha256=manifest_sha, corpus_sha256=sha(out/'corpus.json'), asset_bytes=sum(e['bytes'] for e in entries),
                 model_provenance=dict(expectations=design.model_expectations(workers, assets, export),
                                       export_manifest=str(Path(export_dir)/'manifest.json'),
                                       export_manifest_sha256=sha(Path(export_dir)/'manifest.json'),
                                       rule='receipts and UID-checked /health must equal these plan-bound hashes; artifact identity '
                                            'only (numerical parity and answer quality are separate gates)'),
                 shipped_sources=live_module.shipped_hashes(), shipped_sha256=design.digest(live_module.shipped_hashes()),
                 planner_sources={f: sha(HERE/f) for f in PLANNER_SOURCES},
                 # + the consumed execution (run_baseline_comparison: phase / projected_call) and scoring (vqa_eval) sources
                 reused_frozen_sources={str(p.relative_to(design.ROOT)): sha(p) for p in (design.QNH/'qualify.py', design.QNH/'catalog.py',
                                        live_module.LIVE/'http_runtime.py', live_module.LIVE/'net_control.py',
                                        design.ROOT/'experiments/multi-edge-sensors/collect.py',
                                        Path(live_module.baseline.__file__).resolve(), design.ROOT/analyze_module().VQA_EVAL)},
                 node_image_storage=qualify.node_image_storage(workers),
                 estimates=dict(pilot=design.estimate_seconds(design.pilot_cells(), design.PILOT['window_s'], conditions=design.PILOT['conditions']),
                                compare_per_frozen_loads=design.estimate_seconds(
                                    design.compare_cells(dict(loads_rps={k: 1.0 for k in design.LOAD_FRACTIONS})), design.COMPARE['window_s'],
                                    conditions=design.COMPARE['conditions'])),
                 not_deployed=[dict(node=r['node'], family=r['family'], stage=r['stage'], status=r['status'])
                               for r in cat['rows'] if r['dag_role'] != 'deployed candidate' and r['stage'] == 'vision'])
    with (out/'plan.json').open('x') as handle:
        json.dump(value, handle, indent=1)
    return value, sha(out/'plan.json')


def analyze_module():
    import analyze
    return analyze


def binding(plan_dir, stage, run_id, frozen=None, verify_files=True):
    """-> (release binding, drift). Drift lists every consumed input that is not the plan's hashed input: shipped and
    planner sources, budgets, reused frozen sources, load-pairs/GT/inventory inputs, the assets manifest and (verify_files)
    every asset source the deploy tar is built from, and the corpus question->file mapping (plan-bound hash, pinned
    load-pairs manifest, shipped in the assets manifest), and the plan's per-stage model provenance (recomputed from the
    assets manifest and the frozen export record). Any drift refuses execute before kubectl."""
    import live
    plan_dir = Path(plan_dir)
    value = json.loads((plan_dir/'plan.json').read_text())
    out = dict(stage=stage, run_id=run_id, plan_sha256=sha(plan_dir/'plan.json'), shipped_sha256=design.digest(live.shipped_hashes()),
               budgets_sha256=design.digest(design.BUDGETS))
    if stage == 'compare':
        if not frozen:
            raise ValueError('compare needs --frozen (pilot-derived offered loads)')
        out['frozen_loads_sha256'] = sha(frozen)
    drift = []
    if value['shipped_sha256'] != out['shipped_sha256']:
        drift.append('shipped sources changed since the plan')
    if value['budgets_sha256'] != out['budgets_sha256']:
        drift.append('budgets changed since the plan')
    if any(sha(HERE/f) != h for f, h in value['planner_sources'].items()):
        drift.append('planner sources changed since the plan')
    drift += input_drift(value, plan_dir, live.qualify, verify_files)
    return out, drift


def input_drift(value, plan_dir, qualify, verify_files):
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
    if not value.get('corpus_sha256'):
        drift.append('plan predates corpus binding')
    elif sha(plan_dir/'corpus.json') != value['corpus_sha256']:
        drift.append('corpus mapping changed since the plan')
    corpus = {(str(f['question_id']), f['kind']): (f['path'], f['sha256']) for f in json.loads((plan_dir/'corpus.json').read_text())['files']}
    pinned = {(str(p['question_id']), k): ('corpus/'+Path(p['raw_'+k+'_path']).name, p['raw_'+k+'_sha256'])
              for p in qualify.expected_pairs() for k in ('image', 'audio')}
    if corpus != pinned:
        drift.append('corpus mapping differs from the pinned load-pairs manifest')
    shipped = {(e['path'], e['sha256']) for e in manifest['files'] if e.get('group') == 'corpus'}
    if verify_files and not set(corpus.values()) <= shipped:
        drift.append('corpus entry not shipped by the assets manifest')
    provenance = value.get('model_provenance')
    if not provenance:
        drift.append('plan predates model provenance binding')
        return drift
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
        e.add_argument('--stage', required=True, choices=('pilot', 'compare'))
        e.add_argument('--frozen', default=None)
        e.add_argument('--run-id', required=name == 'execute', default='qnb-RUNID')
        if name == 'execute':
            e.add_argument('--release', required=True)
            e.add_argument('--output-root', default=str(OUT_ROOT))
    f = sub.add_parser('freeze')
    f.add_argument('--plan', required=True)
    f.add_argument('--pilot-run', required=True)
    f.add_argument('--out', default=None)
    a = sub.add_parser('analyze')
    a.add_argument('--plan', required=True)
    a.add_argument('--run', required=True)
    a.add_argument('--frozen', default=None)
    a.add_argument('--out', default=None)
    args = ap.parse_args(argv)
    if args.mode == 'plan':
        value, plan_sha = plan(args.out or OUT_ROOT/f'plan-{utc()}', args.inventory, args.export, args.deps)
        print(json.dumps(dict(plan_sha256=plan_sha, workers=[w['key'] for w in value['workers']], candidates=value['candidates'],
                              coverage=value['coverage'], fixed_plan=value['fixed_plan'], asset_bytes=value['asset_bytes'],
                              assets_manifest_sha256=value['assets_manifest_sha256']), indent=1))
        return 0
    if args.mode in ('dry-run', 'execute'):
        frozen = json.loads(Path(args.frozen).read_text()) if args.frozen else None
        try:
            bind, drift = binding(args.plan, args.stage, args.run_id, args.frozen)
        except ValueError as error:
            print(f'refused: {error}', file=sys.stderr)
            return 2
        plan_value = json.loads((Path(args.plan)/'plan.json').read_text())
        if args.mode == 'dry-run':
            cells = design.pilot_cells() if args.stage == 'pilot' else design.compare_cells(frozen)
            window = design.PILOT['window_s'] if args.stage == 'pilot' else design.COMPARE['window_s']
            conditions = design.PILOT['conditions'] if args.stage == 'pilot' else design.COMPARE['conditions']
            print(json.dumps(dict(stage=args.stage, drift=drift, release_binding=dict(release='RELEASE', **bind),
                                  candidates=plan_value['candidates'], coverage=plan_value['coverage'], fixed_plan=plan_value['fixed_plan'],
                                  conditions={c: design.NETWORKS[c] for c in conditions}, frozen_loads=(frozen or {}).get('loads_rps'),
                                  cells=len(cells), first_cells=[c['label'] for c in cells[:8]], estimate=design.estimate_seconds(cells, window, conditions=conditions),
                                  budgets=design.BUDGETS, quality=plan_value['quality']['status'],
                                  command=f"rtk proxy {sys.executable} experiments/quality-network-baselines/baselines.py execute --plan {args.plan} "
                                          f"--stage {args.stage} --run-id {args.run_id} --release <coordinator-release.json>"
                                          + (f' --frozen {args.frozen}' if args.frozen else '')), indent=1, default=str))
            return 0
        ok, reason = design.release_ok(args.release, bind)
        if drift or not ok:
            print(f'refused: {"; ".join(drift) or reason}', file=sys.stderr)
            return 2
        import live
        from live import ownership
        session = live.BaselineSession(args.output_root, args.run_id, plan_value)
        status, error = 'error', None
        session.save('release.json', dict(release=str(args.release), binding=bind, release_sha256=sha(args.release)))
        session.save('protected-before.json', ownership.protected_snapshot(session, 'protected-before'))
        try:
            live.Live(session, plan_value, args.plan, args.stage, frozen).run()
            status = 'collected'
        except BaseException as exc:
            error = repr(exc)
            raise
        finally:
            remaining, errors = ownership.cleanup(session)
            diff = session.out/'protected-diff.json'
            changes = json.loads(diff.read_text())['changes'] if diff.exists() else None
            session.save('run-status.json', dict(status=status, error=error, cleanup_remaining=remaining, cleanup_errors=errors,
                                                 protected_changes=changes, audit='complete' if changes == [] and remaining == 0 else 'partial'))
        return 0
    import analyze
    if args.mode == 'freeze':
        result = analyze.analyze(args.pilot_run, args.plan)
        if result['stage'] != 'pilot':
            print('refused: not a pilot run', file=sys.stderr)
            return 2
        frozen = dict(design.freeze_loads(result['pilot_rows']), pilot_run=str(args.pilot_run), plan_sha256=sha(Path(args.plan)/'plan.json'))
        out = Path(args.out or Path(args.pilot_run).parent/f'frozen-loads-{utc()}.json')
        with out.open('x') as handle:
            json.dump(frozen, handle, indent=1)
        print(json.dumps(dict(frozen, file=str(out), sha256=sha(out)), indent=1))
        return 0
    result = analyze.analyze(args.run, args.plan, frozen=args.frozen)
    out = Path(args.out) if args.out else None
    if out:
        with out.open('x') as handle:
            json.dump(result, handle, indent=1)
    print(json.dumps([{k: t[k] for k in ('label', 'goodput_rps', 'counts', 'latency_ms', 'quality')} for t in result['traces']], indent=1)[:6000])
    return 0


if __name__ == '__main__':
    sys.exit(main())
