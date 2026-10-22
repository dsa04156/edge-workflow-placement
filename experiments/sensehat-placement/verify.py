"""Read-only verifier for a Sense HAT placement smoke run. Partial runs never pass.

Rebuilds split/model/windows/targets/reference from the copied original histories,
then checks every attempt's receipt chain, numeric reference, placement coverage,
Pod safety, cleanup and protected-workload evidence. Writes nothing.
"""
import argparse
import json
import math
from pathlib import Path
import sys

import forecast
import stage_runtime as sr

TOLERANCE = 1e-9
ROLES = ('t1', 't2', 't3')
CONFIG_KEYS = ('model.json', 'stage_runtime.py', 'windows.json')


def close(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(close(a[k], b[k]) for k in a)
    if isinstance(a, float) or isinstance(b, float):
        return (isinstance(a, (int, float)) and isinstance(b, (int, float))
                and math.isclose(a, b, rel_tol=TOLERANCE, abs_tol=TOLERANCE))
    return a == b


def check_attempt(row, ctx):
    """Problems with one recorded workflow; empty list means protocol + numeric reference ok."""
    reply = row.get('reply')
    if row.get('status') != 'ok' or not isinstance(reply, dict) or reply.get('status') != 'ok':
        return ['attempt not ok: ' + str(row.get('error') or (reply or {}).get('error'))]
    problems = []
    wid = row['window_id']
    window, reference = ctx['windows'][wid], ctx['reference'][wid]
    if reply.get('window_id') != wid or reply.get('request_id') != row['request_id'] or reply.get('attempts') != 1:
        problems.append('reply identity/attempts mismatch')
    receipts = reply.get('receipts', [])
    if [r.get('task') for r in receipts] != list(ROLES):
        return problems + ['receipt tasks are not t1,t2,t3']
    payload_raw = sr.canonical(window['payload'])
    expected_input, expected_bytes = sr.digest(payload_raw), len(payload_raw)
    if (window['payload_sha256'], window['payload_bytes']) != (expected_input, expected_bytes):
        problems.append('frozen window sha256/bytes differ from its canonical payload')
    for role, receipt in zip(ROLES, receipts):
        pod = ctx['pods'][(role, row['sites'][role])]
        if (receipt.get('node'), receipt.get('pod_uid')) != (pod['node'], pod['uid']):
            problems.append(f'{role} receipt node/pod differs from placement Pod')
        if (receipt.get('run_id'), receipt.get('request_id')) != (ctx['run_id'], row['request_id']):
            problems.append(f'{role} receipt run_id/request_id differs')
        if receipt.get('model_sha256') != ctx['model_sha256']:
            problems.append(f'{role} model_sha256 differs')
        if (receipt.get('input_sha256'), receipt.get('input_bytes')) != (expected_input, expected_bytes):
            problems.append(f'{role} input sha256/bytes break the chain')
        output_raw = sr.canonical(receipt.get('output'))
        if (receipt.get('output_sha256'), receipt.get('output_bytes')) != (sr.digest(output_raw), len(output_raw)):
            problems.append(f'{role} output sha256/bytes do not match its recorded output')
        if not close(receipt.get('output'), reference[role]['output']):
            problems.append(f'{role} output differs from local CPU reference beyond 1e-9')
        if receipt.get('source_sha256') != reference['t1']['output']['source_sha256']:
            problems.append(f'{role} source_sha256 differs')
        if not all(isinstance(receipt.get(k), (int, float)) for k in ('compute_ms', 'downstream_rpc_ms', 'handler_total_ms')):
            problems.append(f'{role} timing fields missing')
        elif receipt['handler_total_ms'] < receipt['compute_ms'] + receipt['downstream_rpc_ms']:
            problems.append(f'{role} handler total smaller than compute + nested RPC')
        expected_input, expected_bytes = receipt.get('output_sha256'), receipt.get('output_bytes')
    result = reply.get('result')
    if sr.digest(sr.canonical(result)) != receipts[-1].get('output_sha256'):
        problems.append('result does not hash to t3 output_sha256')
    if not close(result, reference['t3']['output']):
        problems.append('result differs from local CPU reference beyond 1e-9')
    source = reply.get('source', {})
    if (source.get('node'), source.get('pod_uid')) != (ctx['source_pod']['node'], ctx['source_pod']['uid']):
        problems.append('source receipt is not the pinned edge source Pod')
    if (source.get('payload_sha256'), source.get('payload_bytes')) != (window['payload_sha256'], len(payload_raw)):
        problems.append('source payload differs from frozen window')
    if not isinstance(source.get('e2e_ms'), (int, float)) or source['e2e_ms'] < receipts[0].get('handler_total_ms', 0):
        problems.append('source e2e smaller than T1 handler total')
    return problems


def scored(result, target):
    return {'truth': target['value'], 'forecast': result['forecast'], 'persistence': result['persistence'],
            'model_error': result['forecast'] - target['value'],
            'persistence_error': result['persistence'] - target['value']}


def observed_pod_problems(pod, run):
    """Contract check of an admitted Pod's safe projection (run.project_pod); admission may add or change fields."""
    security = pod.get('security')
    if not isinstance(security, dict):
        return ['no observed security projection']
    problems = [f'host namespace {k}' for k in ('hostNetwork', 'hostPID', 'hostIPC') if security.get(k)]
    if (security.get('securityContext') or {}).get('runAsNonRoot') is not True:
        problems.append('Pod securityContext is not runAsNonRoot')
    containers = security.get('containers', [])
    if [(c.get('kind'), c.get('name')) for c in containers] != [('containers', 'worker')]:
        problems.append('admitted containers differ from contract: ' + str([(c.get('kind'), c.get('name'))
                                                                             for c in containers]))
    for c in containers:
        context = c.get('securityContext') or {}
        if (context.get('readOnlyRootFilesystem') is not True or context.get('allowPrivilegeEscalation') is not False
                or context.get('privileged') or (context.get('capabilities') or {}).get('add')
                or (context.get('capabilities') or {}).get('drop') != ['ALL']):
            problems.append(f'container {c.get("name")} securityContext violates contract')
    if security.get('volumes') != [{'name': 'config', 'sources': ['configMap'], 'configMap': run}]:
        problems.append('admitted volumes differ from contract: ' + str(security.get('volumes')))
    return problems


def load_context(prepared):
    model_raw = (prepared / 'model.json').read_bytes()
    return {'model_doc': json.loads(model_raw), 'model_sha256': sr.digest(model_raw),
            'windows': json.loads((prepared / 'windows.json').read_text())['windows'],
            'targets': json.loads((prepared / 'targets.json').read_text())['targets'],
            'reference': json.loads((prepared / 'reference.json').read_text())['windows'],
            'plan': json.loads((prepared / 'plan.json').read_text())}


def same_prepared(prepared, run_dir):
    original = forecast.check_prepared(prepared)['files']
    assert forecast.check_prepared(run_dir / 'prepared')['files'] == original, 'run copy differs from prepared'
    return f'{len(original)} frozen files identical'


def rebuild_from_source(prepared, ctx):
    history = prepared / 'source' / 'history'
    sums = (prepared / 'source' / 'SHA256SUMS').read_text().split('\n')
    listed = {line.split()[1].lstrip('./'): line.split()[0] for line in sums if line.strip()}
    for name in ('manifest.json', *(c + '.json' for c in forecast.CHANNELS)):
        assert listed['history/' + name] == sr.digest((history / name).read_bytes()), 'SHA256SUMS mismatch ' + name
    channels = [forecast.build_channel(forecast.load_channel(history, c)) for c in forecast.CHANNELS]
    models = json.loads(json.dumps({c['channel']: c['model'] for c in channels}))
    assert models == ctx['model_doc']['channels'], 'model differs from rebuild'
    warmups, evaluations = [], []
    for channel in channels:
        for request in forecast.select_requests(channel):
            wid = request['window_id']
            assert ctx['windows'][wid]['payload'] == {'rows': request['rows']}, 'window differs ' + wid
            assert ctx['targets'][wid]['value'] == request['target']['value'], 'target differs ' + wid
            assert ctx['targets'][wid]['sequence'] == request['target']['sequence'], 'target differs ' + wid
            assert forecast.reference_chain(ctx['windows'][wid]['payload'], ctx['model_doc'],
                                            ctx['model_sha256']) == ctx['reference'][wid], 'reference differs ' + wid
            (warmups if request['kind'] == 'warmup' else evaluations).append(wid)
    assert set(ctx['windows']) == set(warmups + evaluations), 'extra frozen windows'
    assert json.loads(json.dumps(forecast.make_plan(warmups, evaluations))) == ctx['plan'], 'plan differs'
    return f'{len(channels)} channels rebuilt from byte copies'


def keys_named(word, value):
    if isinstance(value, dict):
        return [k for k in value if word in k.lower()] + [x for v in value.values() for x in keys_named(word, v)]
    if isinstance(value, list):
        return [x for v in value for x in keys_named(word, v)]
    return []


def no_leakage(prepared, ctx):
    split = json.loads((prepared / 'source-manifest.json').read_text())['channels']
    for name, model in ctx['model_doc']['channels'].items():
        seq = split[name]['split_sequences']
        assert model['training_last_sequence'] < seq['calibration'][0] <= seq['calibration'][1] < seq['evaluation'][0]
    assert not keys_named('target', json.loads((prepared / 'windows.json').read_text())), 'target key in shipped windows'
    by_channel = {}
    for wid, window in ctx['windows'].items():
        rows = [r['sequence'] for r in window['payload']['rows']]
        target = ctx['targets'][wid]['sequence']
        assert target == rows[-1] + 1, wid
        lo, hi = split[window['channel']]['split_sequences'][window['split']]
        assert lo <= rows[0] and target <= hi, 'window crosses split ' + wid
        rows_seen, targets_seen = by_channel.setdefault(window['channel'], (set(), set()))
        rows_seen.update(rows)
        targets_seen.add(target)
    for name, (rows_seen, targets_seen) in by_channel.items():
        assert not rows_seen & targets_seen, 'a target row is shipped as input in ' + name
    return 'targets are local-only and never inside a shipped window'


def manifests(run_dir):
    return {p.name: json.loads(p.read_text()) for p in sorted(run_dir.glob('*.manifest.json'))}


def configmap_matches(prepared, run_dir):
    items = [i for m in manifests(run_dir).values() for i in m['items'] if i['kind'] == 'ConfigMap']
    assert len(items) == 1, 'expected one owned ConfigMap'
    data = items[0]['data']
    assert tuple(sorted(data)) == CONFIG_KEYS, 'ConfigMap keys ' + str(sorted(data))
    assert data['model.json'] == (prepared / 'model.json').read_text()
    assert data['windows.json'] == (prepared / 'windows.json').read_text()
    assert data['stage_runtime.py'] == (run_dir / 'collector-source' / 'stage_runtime.py').read_text()
    assert items[0].get('immutable') is True
    return 'ConfigMap carries runtime/model/target-free windows only'


def pod_safety(run_dir):
    pods = [i for m in manifests(run_dir).values() for i in m['items'] if i['kind'] == 'Pod']
    assert pods, 'no Pod manifests'
    for pod in pods:
        spec = pod['spec']
        assert pod['metadata']['namespace'] == 'topology-profile-lab'
        assert not any(spec.get(k) for k in ('hostNetwork', 'hostPID', 'hostIPC')), 'host namespace'
        assert all('hostPath' not in v for v in spec['volumes']), 'hostPath volume'
        for container in spec['containers']:
            ctx = container['securityContext']
            assert ctx['readOnlyRootFilesystem'] is True and ctx['allowPrivilegeEscalation'] is False
            assert not ctx.get('privileged') and ctx['capabilities'] == {'drop': ['ALL']}
    run = json.loads((run_dir / 'design.json').read_text())['run_id']
    observed = {p['name']: p for p in [*json.loads((run_dir / 'scheduler-observation.json').read_text())['pods'].values(),
                                       *json.loads((run_dir / 'pool-pods.json').read_text()).values()]}
    assert len(observed) == len(pods), f'{len(observed)} observed Pods vs {len(pods)} manifests'
    bad = {name: problems for name, pod in observed.items() if (problems := observed_pod_problems(pod, run))}
    assert not bad, json.dumps(bad)
    return f'{len(pods)} Pods requested and observed: native network, no host access, no capabilities'


def code_provenance(prepared, run_dir):
    """Frozen preparation code == executed collector copy == design record == shipped runtime."""
    manifest = forecast.check_prepared(prepared)
    forecast.check_code(manifest, run_dir / 'collector-source')
    design = json.loads((run_dir / 'design.json').read_text())
    assert design['code_sha256'] == manifest['code_sha256'], 'design code_sha256 differs from preparation'
    shipped = [i for m in manifests(run_dir).values() for i in m['items'] if i['kind'] == 'ConfigMap']
    assert [sr.digest(i['data']['stage_runtime.py'].encode()) for i in shipped] == [
        manifest['code_sha256']['stage_runtime.py']], 'shipped runtime differs from frozen code'
    return f'{len(forecast.CODE_FILES)} code files match preparation'


def placements_complete(run_dir, ctx, rows):
    placements = json.loads((run_dir / 'placements.json').read_text())
    for label, sites in forecast.FIXED_PLACEMENTS.items():
        assert placements['fixed'][label] == sites, label
    observation = json.loads((run_dir / 'scheduler-observation.json').read_text())
    observed = {role: observation['pods'][role]['node'] for role in ROLES}
    default = placements['default_scheduler']
    site_of = {node: site for site, node in forecast.NODES.items()}
    assert default['nodes'] == observed and default['sites'] == {r: site_of[n] for r, n in observed.items()}
    shares = next((label for label, sites in forecast.FIXED_PLACEMENTS.items() if sites == default['sites']), None)
    assert default['shares_with'] == shares, 'default sharing decision differs'
    expected_labels = [b['label'] for b in ctx['plan']['blocks']
                       if not (b['label'] == 'default-scheduler' and shares)]
    seen = [r['label'] for r in rows]
    assert sorted(set(seen)) == sorted(expected_labels), f'placements {sorted(set(seen))} vs {expected_labels}'
    for block in ctx['plan']['blocks']:
        if block['label'] not in expected_labels:
            continue
        got = [(r['kind'], r['window_id']) for r in rows if r['label'] == block['label']]
        want = [('warmup', w) for w in block['warmup']] + [('evaluation', w) for w in block['evaluation']]
        assert got == want, 'request ids/order differ for ' + block['label']
        sites = default['sites'] if block['label'] == 'default-scheduler' else forecast.FIXED_PLACEMENTS[block['label']]
        assert all(r['sites'] == sites for r in rows if r['label'] == block['label'])
    return f'{len(expected_labels)} unique placements x {ctx["plan"]["requests_per_unique_placement"]} requests'


def attempts_ok(run_dir, ctx, rows):
    pods = json.loads((run_dir / 'pool-pods.json').read_text())
    ctx = dict(ctx, run_id=json.loads((run_dir / 'design.json').read_text())['run_id'],
               pods={(p['role'], p['site']): p for p in pods.values() if p['role'] in ROLES},
               source_pod=next(p for p in pods.values() if p['role'] == 'source'))
    assert ctx['source_pod']['node'] == forecast.NODES['edge'], 'source not on edge node'
    bad = []
    for row in rows:
        problems = check_attempt(row, ctx)
        if not problems:
            truth = scored(row['reply']['result'], ctx['targets'][row['window_id']])
            if any(row.get(k) != v for k, v in truth.items()):
                problems.append('recorded error metrics differ from target recomputation')
        if problems:
            bad.append({'request_id': row.get('request_id'), 'problems': problems})
    assert not bad, json.dumps(bad[:5])
    return f'{len(rows)} attempts: receipts, hash chain and numeric reference ok'


def phases_closed(run_dir, ctx):
    events = [json.loads(l) for l in (run_dir / 'phases.jsonl').read_text().splitlines()]
    started = [e['phase'] for e in events if e['event'] == 'start']
    ended = {e['phase']: e for e in events if e['event'] == 'end'}
    assert len(set(started)) == len(started), 'phase started twice'
    assert started and all(p in ended and ended[p]['status'] == 'ok' for p in started), 'phase not closed ok'
    shares = json.loads((run_dir / 'placements.json').read_text())['default_scheduler']['shares_with']
    labels = [b['label'] for b in ctx['plan']['blocks'] if not (b['label'] == 'default-scheduler' and shares)]
    required = ['protected-before', 'configmap', 'scheduler-probe', 'pool-deploy', 'health', 'measure',
                *(f'{kind}:{label}' for label in labels for kind in ('warmup', 'evaluation')), 'teardown']
    missing = [p for p in required if p not in started]
    assert not missing, 'required phases missing: ' + str(missing)
    return ', '.join(started)


def cleanup_ok(run_dir):
    cleanup = json.loads((run_dir / 'cleanup.json').read_text())
    assert cleanup['remaining_owned_pods_and_configmaps'] == 0, 'owned resources remain'
    assert cleanup['diagnostic_or_cleanup_errors'] == [], cleanup['diagnostic_or_cleanup_errors']
    owned = sorted([i['kind'], i['metadata']['name']] for m in manifests(run_dir).values() for i in m['items'])
    assert sorted(cleanup['deleted_resources']) == owned, 'delete set differs from run manifests'
    protected = json.loads((run_dir / 'protected-after.json').read_text())
    assert protected['changes'] == [], 'protected workload changed during run (observation)'
    return f'{len(owned)} owned resources deleted; {protected["protected_count"]} protected Pods unchanged'


def verify(prepared, run_dir):
    prepared, run_dir = Path(prepared), Path(run_dir)
    checks = []

    def check(name, fn, *args):
        try:
            checks.append({'name': name, 'ok': True, 'detail': fn(*args)})
        except Exception as error:  # every failure is reported, never skipped
            checks.append({'name': name, 'ok': False, 'detail': f'{type(error).__name__}: {error}'})

    check('prepared_hashes', forecast.check_prepared, prepared)
    check('code_provenance', code_provenance, prepared, run_dir)
    check('run_copy_matches_prepared', same_prepared, prepared, run_dir)
    ctx = load_context(prepared)
    rows = [json.loads(l) for l in (run_dir / 'attempts.jsonl').read_text().splitlines()] \
        if (run_dir / 'attempts.jsonl').exists() else []
    check('rebuild_from_source', rebuild_from_source, prepared, ctx)
    check('no_future_target_leakage', no_leakage, prepared, ctx)
    check('configmap', configmap_matches, prepared, run_dir)
    check('pod_safety', pod_safety, run_dir)
    check('placements', placements_complete, run_dir, ctx, rows)
    check('attempts', attempts_ok, run_dir, ctx, rows)
    check('phases', phases_closed, run_dir, ctx)
    check('cleanup', cleanup_ok, run_dir)
    return {'passed': all(c['ok'] for c in checks), 'run_dir': str(run_dir), 'checks': checks}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args(argv)
    report = verify(args.prepared, args.run)
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
