"""Validate all three stage receipts and describe fresh fixed-policy evaluation."""

from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
import statistics

base=Path('artifacts/e1-dag');run=json.loads((base/'run.json').read_text());selection=json.loads((base/'selection.json').read_text())
def records(path):return [json.loads(line) for line in path.read_text().splitlines() if line.startswith('{')]
rows=[]
for filename in ['calibration.log',*[f'evaluation-{i}.log' for i in range(3)]]:
    rs=records(base/filename);assert rs[-1]['kind']=='client_complete'
    subset=[r for r in rs if r['kind']=='workflow'];assert len(subset)==rs[-1]['requests']
    rows.extend(subset)
assert len({r['request_id'] for r in rows})==len(rows)
calibration=[r for r in rows if r['block']=='calibration'];assert len(calibration)==448
assert len([r for r in calibration if not r['warmup']])==320
assert hashlib.sha256((base/'calibration.log').read_bytes()).hexdigest()==selection['calibration_log_sha256']
targets={t['alias']:t for t in run['targets']}
receipts={}
for alias in targets:
    rs=records(base/('worker-'+alias+'.log'));assert any(r['kind']=='server_ready' for r in rs)
    assert not any(r['kind']=='request_error' for r in rs)
    for r in rs:
        if r['kind']=='stage_complete':
            key=(r['request_id'],r['stage']);assert key not in receipts;receipts[key]=r
assert len(receipts)==3*len(rows)
groups=defaultdict(list)
for row in rows:
    assert row['plan']==row['condition']['plan'] and row['top5']==[258,259,157,279,152]
    trace=row['trace'];assert [t['stage'] for t in trace]==[0,1,2]
    assert [t['alias'] for t in trace]==row['plan']
    assert trace[0]['input_bytes']==661378 and trace[0]['input_sha256']=='f3f87bb8ab3c26c7ecfd3ac60421d7f32b0503d1d6c5baf8bac42ed93d86351a'
    assert trace[0]['output_bytes']==trace[1]['input_bytes']==602112
    assert trace[0]['output_sha256']==trace[1]['input_sha256']=='791a0f0caae0b9e4a81d7ad9fe3db67514bc2f67fbff71fb4d29f6cd66ff011d'
    assert trace[1]['output_bytes']==trace[2]['input_bytes']==4000
    assert trace[1]['output_sha256']==trace[2]['input_sha256']
    for i,t in enumerate(trace):
        r=receipts[(row['request_id'],i)]
        assert r['plan']==row['plan'] and all(r[k]==v for k,v in t.items())
        assert t['node']==targets[t['alias']]['node'] and t['worker_backend']==targets[t['alias']]['backend']
        assert t['task_ms']>=0
        if i<2:assert t['next_mode']==('local_call' if row['plan'][i]==row['plan'][i+1] else 'http')
        else:assert t['rpc_next_ms']==0
    device=trace[1]['device'];backend=trace[1]['worker_backend']
    if backend=='gpu':assert device['output_device']=='cuda:0' and device['cuda_event_ms']>0
    elif backend=='intel_npu':assert device['execution_devices']==['NPU']
    else:assert device['execution_device']=='CPU'
    assert trace[0]['device']['execution_device']==trace[2]['device']['execution_device']=='CPU'
    groups[(row['block'],row['condition']['id'])].append(row)
for (block,_),group in groups.items():
    expected=7 if block=='calibration' else 12
    assert len(group)==expected and [r['iteration'] for r in group]==list(range(expected))
    assert all(r['warmup']==(r['iteration']<2) for r in group)
assert len({tuple(r['plan']) for r in calibration})==64
pods=json.loads((base/'pods-final.json').read_text())['items']
for pod in pods:
    name=pod['metadata'].get('labels',{}).get('job-name','')
    if '-eval-' in name:
        started=pod['status']['containerStatuses'][0]['state']['terminated']['startedAt']
        assert datetime.fromisoformat(selection['frozen_at'])<datetime.fromisoformat(started.replace('Z','+00:00'))

def describe(values):
    values=list(values)
    return {'n':len(values),'median':statistics.median(values),'mean':statistics.mean(values),
            'sd':statistics.stdev(values),'min':min(values),'max':max(values)}
evaluated=[r for r in rows if r['block']!='calibration' and not r['warmup']]
summaries={}
for policy,plan in selection['policies'].items():
    subset=[r for r in evaluated if r['plan']==plan]
    assert len(subset)==30
    summaries[policy]={'plan':plan,'workflow_ms':describe(r['workflow_ms'] for r in subset),
                        'block_median_ms':{str(b):statistics.median(r['workflow_ms'] for r in subset if r['block']==f'eval-{b}') for b in range(3)},
                        'predicted_ms':selection['predicted_ms'][policy],
                        'same_plan_policies':[p for p,v in selection['policies'].items() if v==plan]}
result={'run_id':run['run_id'],'workflow_requests':len(rows),'stage_receipts':len(receipts),
        'calibration_requests':448,'calibration_measured':320,'evaluation_measured':len(evaluated),
        'unique_evaluation_plans':len({tuple(r['plan']) for r in evaluated}),
        'pacing_overruns':sum(r['pacing_overrun'] for r in rows),'policies':summaries,
        'B2_minus_B3_median_ms':summaries['B2_fastest']['workflow_ms']['median']-summaries['B3_joint_enumeration']['workflow_ms']['median'],
        'C1_minus_B3_median_ms':summaries['C1_data_greedy']['workflow_ms']['median']-summaries['B3_joint_enumeration']['workflow_ms']['median'],
        'limitations':['Single image, same warm worker instance per backend, three sequential client evaluation blocks.',
                       'No CI, hypothesis tests, p95 or independent deployment replication.',
                       'Communication residual includes protocol, response trace, validation and executor costs; not pure network.',
                       'Diagnostic SDK preparation and model warmup are outside request timing; no dynamic transition net-benefit claim.',
                       'Exact small-DAG enumeration is not JellyBean/Atlas implementation or a new algorithm.',
                       'GPU support warning/kernel trace gap and cross-runtime dataset-quality validation remain unresolved.']}
(base/'results.json').write_text(json.dumps(result,indent=2)+'\n')
(base/'workflows.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
(base/'stage-receipts.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in receipts.values()))
print(json.dumps(result))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
fig,axes=plt.subplots(1,2,figsize=(13,4.5))
aliases=sorted(targets);cost=selection['task_ms'];incoming=selection['ingress_residual_ms'];edge=selection['edge_residual_ms']
score=lambda p:incoming[p[0]]+sum(cost[f'{i}|{p[i]}'] for i in range(3))+sum(edge[i][f'{p[i]}|{p[i+1]}'] for i in range(2))
plans=sorted({tuple(r['plan']) for r in calibration})
observed=[statistics.median(r['workflow_ms'] for r in calibration if tuple(r['plan'])==p and not r['warmup']) for p in plans]
predicted=[score(p) for p in plans]
axes[0].scatter(predicted,observed,s=18,alpha=.7)
lo=min(predicted+observed);hi=max(predicted+observed);axes[0].plot([lo,hi],[lo,hi],'--',color='gray')
axes[0].set(xlabel='Common component model (ms)',ylabel='Calibration workflow median (ms)',title='64 calibration assignments (training fit)')
unique={tuple(plan):[] for plan in selection['policies'].values()}
short={'B2_fastest':'B2','C1_data_greedy':'C1','B3_joint_enumeration':'B3','anchor_all_arm':'ARM','anchor_all_gpu':'GPU'}
for policy,plan in selection['policies'].items():unique[tuple(plan)].append(short[policy])
rng=random.Random(3);colors=['#2563eb','#d97706','#059669']
for i,(plan,names) in enumerate(unique.items()):
    for b in range(3):
        values=[r['workflow_ms'] for r in evaluated if tuple(r['plan'])==plan and r['block']==f'eval-{b}']
        axes[1].scatter([i+rng.uniform(-.12,.12) for _ in values],values,s=14,alpha=.6,color=colors[b],label=f'block {b}' if i==0 else None)
    values=[r['workflow_ms'] for r in evaluated if tuple(r['plan'])==plan]
    axes[1].plot([i-.2,i+.2],[statistics.median(values)]*2,color='black',lw=2)
axes[1].set_xticks(range(len(unique)),['/'.join(v)+'\n'+'→'.join(p) for p,v in unique.items()],fontsize=8)
axes[1].set(ylabel='Fresh evaluation workflow wall (ms)',title='Frozen plans, shared warm workers')
axes[1].legend(fontsize=8)
for ax in axes:ax.grid(alpha=.2)
fig.tight_layout();fig.savefig(base/'joint-placement.png',dpi=180);fig.savefig(base/'joint-placement.svg')
