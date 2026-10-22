"""Descriptive single-deployment report; no request-level significance tests."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics


def describe(values):
    if not values:
        return dict(n=0, mean=None, sd=None, median=None, p95=None, min=None, max=None)
    values = sorted(values)
    index = .95 * (len(values)-1)
    lo, fraction = int(index), index-int(index)
    p95 = values[lo] if lo == len(values)-1 else values[lo]*(1-fraction)+values[lo+1]*fraction
    return dict(n=len(values), mean=statistics.mean(values), sd=statistics.stdev(values) if len(values)>1 else None,
                median=statistics.median(values), p95=p95, min=min(values), max=max(values))


def analyze(run):
    out = run / datetime.now(timezone.utc).strftime('analysis-%Y%m%dt%H%M%S')
    out.mkdir(exist_ok=False)
    phases = []
    all_rows = []
    sources = {}
    references = {r['question_id']:r for r in map(json.loads,(run.parent/'reference-v1/results.jsonl').read_text().splitlines())}
    for path in sorted(run.glob('*.rows.json')):
        name = path.name.removesuffix('.rows.json')
        config = json.loads((run/(name+'.config.json')).read_text())
        rows = json.loads(path.read_text())
        sources[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        valid = [r for r in rows if r['status']=='ok']
        outputs = [r for r in rows if 'output' in r]
        score_errors = [max(abs(a-b) for a,b in zip(r['output']['scores'],references[r['question_id']]['scores'])) for r in outputs]
        for r in valid:
            all_rows.append(dict(r, phase=name))
        item = dict(phase=name, mbps=config['mbps'], delay_ms=config['oneway_delay_ms'], rate=config['rate_rps'],
            representation=config['representation'], placement=config['placement'], attempts=len(rows),
            successes=len(valid), failures=len(rows)-len(valid), statuses={status:sum(r['status']==status for r in rows) for status in sorted(set(r['status'] for r in rows))},
            e2e_ms=describe([r['e2e_ms'] for r in valid]), source_total_ms=describe([r['source_total_ms'] for r in valid]),
            scheduled_terminal_ms=describe([(r['finished_s']-r['scheduled_s'])*1000 for r in rows]),
            release_lag_ms=describe([(r['actual_release_s']-r['scheduled_s'])*1000 for r in rows]),
            driver_queue_ms=describe([r['driver_queue_ms'] for r in rows if 'driver_queue_ms' in r]),
            deadline_exceeded=sum(r['finished_s']-r['scheduled_s']>config['deadline_s'] for r in rows),
            outputs_received=len(outputs),
            numeric_quality_failures=sum(r['output']['answer_ids'][0]!=references[r['question_id']]['answer_ids'][0] or error>1e-4 for r,error in zip(outputs,score_errors)),
            top5_order_matches=sum(r['output']['answer_ids']==references[r['question_id']]['answer_ids'] for r in outputs),
            top5_set_matches=sum(set(r['output']['answer_ids'])==set(references[r['question_id']]['answer_ids']) for r in outputs),
            top5_min_overlap=min((len(set(r['output']['answer_ids'])&set(references[r['question_id']]['answer_ids'])) for r in outputs),default=None),
            max_score_error=max(score_errors, default=None),
            model_compute_ms={}, model_queue_ms={})
        for task in ('vision','speech','fusion'):
            receipts=[next(q for q in r['receipts'] if q['task']==task) for r in valid]
            item['model_compute_ms'][task]=describe([q['compute_ms'] for q in receipts])
            item['model_queue_ms'][task]=describe([q['queue_ms'] for q in receipts])
        phases.append(item)
    summary = dict(scope='descriptive repeated requests within one deployment; no independent-replication inference',
        run_id=run.name, source_sha256=sources, phases=phases, phase_count=len(phases),
        attempts=sum(p['attempts'] for p in phases), successes=sum(p['successes'] for p in phases),
        failures=sum(p['failures'] for p in phases), observed_successful_workflow_receipts=sum(len(r['receipts']) for r in all_rows),
        completed=(run/'completed.json').exists(),
        note='success latency excludes failed requests; failure counts and all terminal times reported separately; queue is per operator Pod')
    (out/'summary.json').write_text(json.dumps(summary, indent=2))
    with (out/'phases.csv').open('w') as handle:
        columns=['phase','mbps','delay_ms','rate','representation','attempts','successes','failures','mean_ms','median_ms','p95_ms','driver_lag_max_ms','speech_queue_mean_ms']
        writer=csv.DictWriter(handle,fieldnames=columns);writer.writeheader()
        for p in phases:
            writer.writerow(dict({k:p[k] for k in columns if k in p}, mean_ms=p['e2e_ms']['mean'],median_ms=p['e2e_ms']['median'],p95_ms=p['e2e_ms']['p95'],driver_lag_max_ms=p['release_lag_ms']['max'],speech_queue_mean_ms=p['model_queue_ms']['speech']['mean']))
    plot(out, phases, run)
    print(json.dumps(dict(output=str(out),attempts=summary['attempts'],failures=summary['failures'],phases=len(phases))))
    return out


def plot(out, phases, run):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    colors={'cloud':'#c55c35','edge':'#348573','mixed':'#4d6eac'}
    fig, axes=plt.subplots(2,2,figsize=(11,8),layout='constrained')
    ax=axes[0,0]
    for placement,color in colors.items():
        selected=sorted([p for p in phases if p['phase'].startswith('low-') and p['phase'].endswith('-'+placement)],key=lambda p:p['mbps'])
        ax.plot([p['mbps'] for p in selected],[p['e2e_ms']['mean']/1000 if p['e2e_ms']['mean'] is not None else float('nan') for p in selected],marker='o',color=color,label=placement)
    ax.set(xscale='log',xticks=[2,10,100],xticklabels=['2','10','100'],xlabel='Shared WAN cap (Mbps)',ylabel='Mean E2E (s)',title='Low arrival rate: 0.1 requests/s')
    ax.legend(frameon=False)
    ax=axes[0,1]
    for placement,color in colors.items():
        selected=sorted([p for p in phases if p['phase'].startswith('load-2-') and p['phase'].endswith('-'+placement)],key=lambda p:p['rate'])
        ax.plot([p['rate'] for p in selected],[p['e2e_ms']['mean']/1000 if p['e2e_ms']['mean'] is not None else float('nan') for p in selected],marker='o',color=color,label=placement)
        for p in selected:
            if p['failures'] and p['e2e_ms']['mean'] is not None:
                ax.annotate(f"{p['successes']}/{p['attempts']} succeeded",
                    (p['rate'],p['e2e_ms']['mean']/1000),xytext=(0,-15),
                    textcoords='offset points',ha='center',fontsize=8,color=color)
    ax.set(xlabel='Arrival rate (requests/s)',ylabel='Mean successful E2E (s)',title='2 Mbps: finite-window open-loop load')
    ax=axes[1,0]
    for placement,color in colors.items():
        path=run/f'load-2-1-{placement}.rows.json'
        if path.exists():
            rows=json.loads(path.read_text());ok=[r for r in rows if r['status']=='ok']
            ax.plot([r['index'] for r in sorted(ok,key=lambda r:r['index'])],[r['e2e_ms']/1000 for r in sorted(ok,key=lambda r:r['index'])],'.-',color=color,label=placement)
    ax.set(xlabel='Planned request index',ylabel='Successful E2E (s)',title='2 Mbps, 1 request/s: latency by request')
    ax=axes[1,1]
    groups=['native','relay-unlimited','low-2','compressed-2']
    for i,placement in enumerate(colors):
        values=[]
        for prefix in groups:
            selected=next((p for p in phases if p['phase']==prefix+'-'+placement),None)
            values.append(selected['e2e_ms']['mean']/1000 if selected and selected['e2e_ms']['mean'] is not None else float('nan'))
        ax.bar([j+i*.24 for j in range(4)],values,width=.23,color=colors[placement],label=placement)
    ax.set(xticks=[j+.24 for j in range(4)],xticklabels=['Native','Relay\nunlimited','Raw\n2 Mbps','Compressed\n2 Mbps'],ylabel='Mean E2E (s)',title='Path and input representation controls')
    fig.suptitle('Real ResNet18 + Wav2Vec2 + MUTAN; fixed edge sources\nSingle deployment diagnostic, not policy superiority',fontsize=12)
    fig.savefig(out/'load-diagnostic.png',dpi=180)
    fig.savefig(out/'load-diagnostic.svg')
    plt.close(fig)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('run',type=Path);args=ap.parse_args();analyze(args.run)
