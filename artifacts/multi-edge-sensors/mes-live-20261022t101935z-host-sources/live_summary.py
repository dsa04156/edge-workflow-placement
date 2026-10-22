"""Descriptive summary of one live run (n=3 evaluation inputs per placement; no hypothesis tests)."""
import json
from pathlib import Path
import statistics
import sys

run = Path(sys.argv[1])
read = lambda n: json.loads((run/n).read_text())
rows = [json.loads(l) for l in (run/'attempts.jsonl').read_text().splitlines()]
jobs = read('schedule-frozen.json')['jobs']
plans = read('plans-frozen.json')


def stats(values):
    if not values:
        return dict(n=0)
    return dict(n=len(values), median=statistics.median(values), mean=statistics.mean(values),
                sd=statistics.stdev(values) if len(values) > 1 else None, min=min(values), max=max(values))


def stage(evals):
    out = {}
    for r in evals:
        rc = {x['task']: x for x in r.get('receipts', [])}
        for k, v in (('V_fetch_ms', rc['V'].get('fetch_ms')), ('V_decode_ms', rc['V'].get('decode_ms')), ('V_compute_ms', rc['V'].get('compute_ms')),
                     ('T_fetch_ms', rc['T'].get('fetch_ms')), ('T_compute_ms', rc['T'].get('compute_ms')),
                     ('F_call_V_ms', rc['F']['call_ms']['V']), ('F_call_T_ms', rc['F']['call_ms']['T']), ('F_compute_ms', rc['F'].get('compute_ms'))):
            out.setdefault(k, []).append(v)
    return {k: statistics.median(v) for k, v in out.items()}


phases = {}
for end in run.glob('phase-*.end.json'):
    e = json.loads(end.read_text())
    phases[e['name']] = dict(status=e['status'], duration_ms=e['duration_ms'])

summary = dict(run=run.name, scope='descriptive n=3 per unique placement; warmup excluded; E2E = destination-Pod monotonic F request->response '
               '(frozen decoded frames, not live RTSP E2E); no hypothesis test', workflows={})
for flow_id in sorted(plans):
    w = dict(policies={}, failed_or_missing=[])
    for job in [j for j in jobs if j['workflow'] == flow_id]:
        evals = [r for r in rows if r['job'] == job['label'] and not r['warmup']]
        ok = [r['e2e_ms'] for r in evals if r.get('status') == 'ok']
        w['failed_or_missing'] += [dict(job=job['label'], seq=r.get('seq'), status=r.get('status'), error=r.get('error')) for r in
                                   [r for r in rows if r['job'] == job['label']] if r.get('status') != 'ok']
        for policy in job['policies']:
            w['policies'][policy] = dict(job=job['label'], shared_with=[p for p in job['policies'] if p != policy], placement=job['placement'],
                                         predicted_ms=plans[flow_id][policy].get('predicted_ms'), decision_ms=plans[flow_id][policy].get('decision_ms'),
                                         e2e_ms=ok, stats=stats(ok), stage_medians_ms=stage([r for r in evals if r.get('status') == 'ok']),
                                         deploy_and_run_phase_ms=phases.get('evaluation-'+job['label'], {}).get('duration_ms'))
    w['not_evaluated'] = {p: v for p, v in plans[flow_id].items() if v['status'] != 'ok'}
    for ref in ('task-fastest', 'kube-default'):
        base = w['policies'].get(ref, {}).get('stats', {}).get('mean')
        if base:
            for p, v in w['policies'].items():
                if v['stats'].get('mean') is not None:
                    v.setdefault('pct_mean_diff_vs', {})[ref] = (v['stats']['mean']-base)/base*100
    summary['workflows'][flow_id] = w
summary['pct_formula'] = '(mean_policy - mean_reference) / mean_reference * 100, evaluation rows only'
summary['phases_ms'] = phases
print(json.dumps(summary, indent=1))
