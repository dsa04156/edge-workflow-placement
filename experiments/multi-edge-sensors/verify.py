"""Read-only audit of one multi-edge-sensors run; prints a JSON summary, nonzero exit on any evidence error.

Rejects: sources not on their frozen edge nodes (e.g. replaced by the server), model or input
hash mismatches, missing/extra attempts (failures are counted, never dropped), and the same
placement observed or counted twice within a workflow. Quality differences against
reference.json are reported as findings with their rows, not removed. Runs whose design
environment is not 'cluster' are marked offline: no cluster or performance claim.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import models  # noqa: E402

TASKS = ('V', 'T', 'F')
REQUIRED = ('design.json', 'prepared-ref.json', 'topology-preflight.json', 'sources-evidence.json', 'frozen-inputs.json',
            'qualification.json', 'profiles-frozen.json', 'plans-frozen.json', 'schedule-frozen.json', 'attempts.jsonl',
            'policy-observation-map.json', 'cleanup.json')


def audit(run, prepared_dir):
    errors, findings = [], []

    def check(ok, message):
        if not ok:
            errors.append(message)
        return ok

    def load(name):
        path = run/name
        if not check(path.is_file(), 'missing '+name):
            return None
        if name.endswith('.jsonl'):
            return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        return json.loads(path.read_text())

    files = {name: load(name) for name in REQUIRED}
    if any(v is None for v in files.values()):
        return dict(status='failed', errors=errors)
    design, sources, frozen = files['design.json'], files['sources-evidence.json'], files['frozen-inputs.json']
    prepared = json.loads((prepared_dir/'prepared.json').read_text())
    check(files['prepared-ref.json']['sha256'] == hashlib.sha256((prepared_dir/'prepared.json').read_bytes()).hexdigest(),
          'prepared.json differs from the run prepared-ref')
    check(not (run/'failure.json').exists(), 'collection recorded failure.json')
    check(files['topology-preflight.json'].get('status') == 'passed', 'topology preflight not passed')
    for start in run.glob('phase-*.start.json'):
        end = start.with_name(start.name.replace('.start.json', '.end.json'))
        if check(end.is_file(), 'phase without end record: '+start.name):
            check(json.loads(end.read_text())['status'] == 'ok', 'phase not ok: '+start.name)
    check(files['cleanup.json'].get('remaining_owned_pods_and_configmaps') == 0, 'owned resource cleanup not verified')
    check(files['cleanup.json'].get('cleanup_verified') is True and not files['cleanup.json'].get('unresolved_creation'),
          'cleanup not verified or creation outcome unresolved (see cleanup.json unresolved_creation)')

    roles = design['topology']['roles']
    sc = prepared['scenario']['content']
    for sid, spec in design['sources'].items():
        entry = sources.get(sid, {})
        check(entry.get('node') == spec['node'] and roles.get(entry.get('node')) == 'edge',
              f'source {sid} not on its frozen edge node {spec["node"]}: {entry.get("node")}')
        ev = entry.get('evidence') or {}
        if spec['kind'] == 'video':
            check((ev.get('mediamtx') or {}).get('alive') and (ev.get('publisher') or {}).get('alive'),
                  f'source {sid} publisher/MediaMTX process evidence missing')
            path = next((p for p in ev.get('mediamtx_paths', []) if p.get('name') == spec['path']), {})
            check(path.get('ready') and (path.get('bytes_received') or 0) > 0, f'source {sid} RTSP path not ready/receiving')
            freeze = ev.get('freeze') or {}
            check((freeze.get('codec') or {}).get('name') == 'h264', f'source {sid} codec is not h264')
            for seq, item in (freeze.get('frozen') or {}).items():
                check(item.get('loop_consistent') is True, f'source {sid} seq {seq} decoded hash not loop-consistent')
                check(frozen[sid][seq]['png_sha256'] == item['png_sha256'], f'frozen input {sid}/{seq} differs from source freeze')
        else:
            for seq, item in prepared['inputs']['text'][sid].items():
                check(frozen[sid][seq]['sha256'] == item['sha256'], f'text input {sid}/{seq} differs from prepared')

    # Candidate gate: calibration-only CPU reference before planning; frozen candidates are quality-valid and shared.
    calref, quality = run/'calibration-reference.json', run/'candidate-quality.json'
    if check(calref.is_file() and quality.is_file(), 'missing calibration-reference.json/candidate-quality.json (candidate quality gate)'):
        seqs = json.loads(calref.read_text())['seqs']
        check(sorted(seqs) == sorted(sc['inputs']['calibration']), f'calibration reference used non-calibration seqs {seqs}')
        gate = json.loads(quality.read_text())['workflows']
        frozen_candidates = files['profiles-frozen.json']['candidates']
        for flow_id, entry in frozen_candidates.items():
            for task in TASKS:
                extra = set(entry['candidates'][task]) - set(gate[flow_id][task]['valid'])
                check(not extra, f'{flow_id}/{task}: frozen candidates not quality-valid: {sorted(extra)}')
            observed = run/f'scheduler-{flow_id}.json'
            if observed.is_file():
                check(json.loads(observed.read_text())['candidates'] == entry['candidates'],
                      f'{flow_id}: kube-default candidates differ from the shared profile candidates')

    # Jobs: one observation per (workflow, placement); every ok policy plan maps to the job with its placement.
    jobs = files['schedule-frozen.json']['jobs']
    keys = [(j['workflow'], tuple(j['placement'][t] for t in TASKS)) for j in jobs]
    check(len(keys) == len(set(keys)), 'duplicate placement observation within a workflow')
    seen_policies = [(j['workflow'], p) for j in jobs for p in j['policies']]
    check(len(seen_policies) == len(set(seen_policies)), 'duplicate policy assignment within a workflow')
    plans = files['plans-frozen.json']
    for flow, policies in plans.items():
        for policy, p in policies.items():
            mapped = [j for j in jobs if j['workflow'] == flow and policy in j['policies']]
            if p['status'] == 'ok':
                check(len(mapped) == 1 and mapped[0]['placement'] == p['placement'], f'plan {flow}/{policy} not mapped to its placement')
            else:
                check(not mapped, f'non-ok plan {flow}/{policy} was evaluated')
    mapping = files['policy-observation-map.json']
    for job in jobs:
        for policy in job['policies']:
            check(mapping.get(job['workflow'], {}).get(policy) == job['label'], f'policy map inconsistent for {job["label"]}/{policy}')

    rows = files['attempts.jsonl']
    seqs = [sc['inputs']['calibration'][0]] + sc['inputs']['evaluation']
    expected = sorted((j['label'], s) for j in jobs for s in seqs)
    check(sorted((r.get('job'), r.get('seq')) for r in rows) == expected, 'attempt count/identity mismatch: failures must be kept, none added')
    flows = {f['id']: f for f in design['workflows']}
    model_sha = dict(V=prepared['models']['V']['sha256'], T=prepared['models']['T']['sha256'], F=prepared['code']['models.py'])
    reference = json.loads((run/'reference.json').read_text()) if (run/'reference.json').is_file() else None
    quality = dict(checked_rows=0, ok_rows=0, violations=[])
    for row in rows:
        if row.get('status') != 'ok':
            continue
        flow, seq, tag = flows[row['workflow']], str(row['seq']), f"{row['job']}/{row['seq']}"
        receipts = {r['task']: r for r in row.get('receipts', [])}
        if not check(set(receipts) == set(TASKS), f'{tag}: receipts incomplete'):
            continue
        for task in TASKS:
            r = receipts[task]
            check(r['node'] == row['placement'][task] and r['pod_uid'] == row['pods'][task]['uid'], f'{tag}: {task} ran outside its placement/Pod')
            check(r['model_sha256'] == model_sha[task], f'{tag}: {task} model hash mismatch')
        for task, sid in (('V', flow['video']), ('T', flow['text'])):
            src = receipts[task]['source']
            check(src.get('x-source-node') == design['sources'][sid]['node'] and src.get('x-source-pod-uid') == sources[sid]['uid']
                  and src.get('x-source-id') == sid, f'{tag}: {task} input source is not the frozen edge source {sid}')
        check(receipts['V']['input_payload_sha256'] == frozen[flow['video']][seq]['png_sha256'], f'{tag}: V input frame hash mismatch')
        check(receipts['T']['input_sha256'] == prepared['inputs']['text'][flow['text']][seq]['sha256'], f'{tag}: T input text hash mismatch')
        check(all(receipts['F']['input_sha256'][t] == receipts[t]['output_sha256'] for t in 'VT'), f'{tag}: F input hash mismatch')
        outputs = row.get('outputs', {})
        for task in TASKS:
            # Malformed/nonfinite/wrong-shape outputs are evidence errors (fail closed), with or without a reference.
            got = outputs.get(f"{row['request_id']}/{task}")
            if check(got is not None, f'{tag}: {task} output missing'):
                for problem in models.validate(task, got, prepared['class_names']):
                    check(False, f'{tag}: {task} {problem}')
        detections, fusion = outputs.get(f"{row['request_id']}/V"), outputs.get(f"{row['request_id']}/F")
        if isinstance(detections, list) and isinstance(fusion, dict):
            check(fusion.get('detections') == len(detections),
                  f"{tag}: F invalid: detections {fusion.get('detections')!r} != {len(detections)} V detections of this row")
        if reference:
            problems = []
            for task, key in (('V', f"{flow['video']}/{seq}"), ('T', f"{flow['text']}/{seq}"), ('F', f"{flow['id']}/{seq}")):
                got = outputs.get(f"{row['request_id']}/{task}")
                expected = reference[task][key]['output'] if task != 'F' else reference['F'][key]
                found = [f'{task}: missing output'] if got is None else [f'{task}: {p}' for p in models.compare(task, got, expected, prepared['class_names'])]
                for problem in found:
                    if 'invalid reference' in problem:
                        check(False, f'{tag}: {problem}')
                problems += found
            quality['checked_rows'] += 1
            if problems:
                quality['violations'].append(dict(row=tag, problems=problems))
            else:
                quality['ok_rows'] += 1
    if reference is None:
        findings.append('quality not checked: reference.json missing (run reference.py)')
    if quality['violations']:
        findings.append(f"{len(quality['violations'])} rows differ from the CPU reference beyond tolerance (kept, not excluded)")

    summary = {}
    for job in jobs:
        evals = [r for r in rows if r.get('job') == job['label'] and not r.get('warmup')]
        ok = [r['e2e_ms'] for r in evals if r.get('status') == 'ok']
        for policy in job['policies']:
            summary.setdefault(job['workflow'], {})[policy] = dict(job=job['label'], placement=job['placement'], attempts=len(evals),
                ok=len(ok), failures=[dict(seq=r.get('seq'), status=r.get('status'), error=r.get('error')) for r in evals if r.get('status') != 'ok'],
                e2e_ms=ok, mean_e2e_ms=statistics.mean(ok) if ok else None)
    rtsp = []
    for row in files['qualification.json'].get('rtsp', []):
        reply = row.get('reply') or {}
        sid = row['tag'].split('/', 1)[1]
        freeze = ((sources.get(sid) or {}).get('evidence') or {}).get('freeze', {}).get('frozen', {})
        same = [reply.get('frame_sha256', {}).get(s, [None])[0] == item['rgb_sha256'] for s, item in freeze.items()
                if s in reply.get('frame_sha256', {})]
        rtsp.append(dict(source=sid, reader_node=row['node'], status=reply.get('status', row.get('status')), setup_ms=reply.get('setup_ms'),
                         decoded_frames=reply.get('decoded_frames'), decode_ms_sum=reply.get('decode_ms_sum'),
                         decoded_equal_frozen=f'{sum(same)}/{len(same)}', error=reply.get('error') or row.get('error')))
    return dict(status='passed' if not errors else 'failed', offline=design.get('environment') != 'cluster',
                environment=design.get('environment'), errors=errors, findings=findings, quality=quality,
                policies=summary, rtsp_protocol=rtsp,
                scope='descriptive per-policy e2e of evaluation rows; warmups excluded from summaries but audited; '
                      'no significance claim with 3 evaluation inputs')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--output', type=Path, help='optional summary path, opened exclusively')
    args = parser.parse_args(argv)
    result = audit(args.run.resolve(), args.prepared)
    text = json.dumps(result, indent=1)
    if args.output:
        with args.output.open('x') as handle:
            handle.write(text+'\n')
    print(text)
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
