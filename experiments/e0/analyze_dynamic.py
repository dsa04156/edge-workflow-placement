"""Receipt audit and descriptive, trajectory-level E2 summaries."""

from collections import Counter,defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics

base=Path('artifacts/e2-dynamic');run=json.loads((base/'run.json').read_text())
selection=json.loads((base/'selection.json').read_text())
def records(path):return [json.loads(line) for line in path.read_text().splitlines() if line.startswith('{')]
files=[*sorted(base.glob('calibration-*.log')),*sorted(base.glob('preparation-*.log')),*sorted(base.glob('evaluation-*.log'))]
rows=[];setups=[];controls=[]
for path in files:
    rs=records(path)
    assert rs[-1]['kind']=='phase_complete',path
    assert (base/(path.stem+'.stderr')).read_text()==''
    for r in rs:
        if r['kind']=='workflow':rows.append(r)
        elif r['kind']=='setup_workflow':setups.append(r)
        elif r['kind']=='control':controls.append(r)
assert len(rows)==1497 and len(setups)==17
evaluation=[r for r in rows if r['mode']=='evaluation']
assert len(evaluation)==1380
assert len({r['request_id'] for r in rows+setups})==1514
receipts={}
for alias in run['targets']:
    worker_records=records(base/f'worker-{alias}.log')
    assert not any(r['kind']=='request_error' for r in worker_records)
    for r in worker_records:
        if r['kind']=='stage_complete':
            key=(r['request_id'],r['stage']);assert key not in receipts
            receipts[key]=r
assert len(receipts)==4542
for row in rows+setups:
    trace=row['trace'];plan=row['plan']
    assert row['top5']==[258,259,157,279,152]
    assert [t['stage'] for t in trace]==[0,1,2]
    assert [t['alias'] for t in trace]==plan
    assert [t['node'] for t in trace]==[run['targets'][a]['node'] for a in plan]
    assert [t['input_bytes'] for t in trace]==[661378,602112,4000]
    assert [t['output_bytes'] for t in trace]==[602112,4000,25]
    assert trace[0]['input_sha256']=='f3f87bb8ab3c26c7ecfd3ac60421d7f32b0503d1d6c5baf8bac42ed93d86351a'
    assert trace[1]['input_sha256']==trace[0]['output_sha256']=='791a0f0caae0b9e4a81d7ad9fe3db67514bc2f67fbff71fb4d29f6cd66ff011d'
    assert trace[2]['input_sha256']==trace[1]['output_sha256']
    assert trace[2]['output_sha256']=='ed198ada143eaee4642ea977b55a61368be1a67bc0cb5fadd195c6f53d340bcb'
    for t in trace:
        receipt=receipts[(row['request_id'],t['stage'])]
        for key,value in t.items():assert receipt[key]==value,(row['request_id'],key)
    device=trace[1]['device']
    if plan[1]=='intel_npu':assert device['execution_devices']==['NPU']
    else:assert device['execution_device']=='CPU'
    assert device['was_cold']==(device['preparation_ms']>0)
    assert trace[1]['task_ms']>=device['preparation_ms']
for filename,digest in selection['calibration_log_sha256'].items():
    assert hashlib.sha256((base/filename).read_bytes()).hexdigest()==digest
frozen=datetime.fromisoformat(selection['frozen_at']).timestamp()
assert frozen<min(p.stat().st_mtime for p in base.glob('evaluation-*.input.json'))
groups=defaultdict(list);trajectories=defaultdict(list)
for r in evaluation:
    groups[(r['scenario'],r['policy'])].append(r)
    trajectories[(r['scenario'],r['policy'],r['block'])].append(r)
assert len(trajectories)==30 and all(len(rs)==46 for rs in trajectories.values())
for (scenario,policy,block),rs in trajectories.items():
    assert Counter(r['phase'] for r in rs)==dict(enumerate([8,1,4,12,4,1,4,12]))
    for r in rs:
        assert r['source']==run['design']['source_phases'][r['phase']][0]
        assert abs(r['total_ms']-r['workflow_ms']-r['observation_ms']-r['decision_ms'])<1e-6

def describe(values):
    return {'n':len(values),'mean':statistics.mean(values),'median':statistics.median(values),
            'min':min(values),'max':max(values),'sum':sum(values)}

summary=[]
for (scenario,policy),rs in sorted(groups.items()):
    assert len(rs)==138
    changed=[];totals=[]
    for block in range(3):
        trajectory=sorted(trajectories[(scenario,policy,block)],key=lambda r:(r['phase'],r['iteration']))
        changed.append(sum(a['plan']!=b['plan'] for a,b in zip(trajectory,trajectory[1:])))
        totals.append(sum(r['total_ms'] for r in trajectory))
    cold=[r['trace'][1]['device']['preparation_ms'] for r in rs if r['trace'][1]['device']['was_cold']]
    summary.append({'scenario':scenario,'policy':policy,'total_ms':describe([r['total_ms'] for r in rs]),
                    'workflow_ms':describe([r['workflow_ms'] for r in rs]),
                    'observation_ms':describe([r['observation_ms'] for r in rs]),
                    'decision_ms':describe([r['decision_ms'] for r in rs]),
                    'trajectory_total_ms_by_block':totals,'plan_changes_by_block':changed,
                    'cold_preparations':len(cold),'preparation_ms':describe(cold) if cold else None,
                    'plans':dict(Counter(','.join(r['plan']) for r in rs)),
                    'pacing_overruns':sum(r['pacing_overrun'] for r in rs)})
audit={'all_workflows':1514,'evaluation_workflows':1380,'calibration_workflows':112,'preparation_calibration_workflows':5,
       'setup_workflows':17,'matching_stage_receipts':4542,'failed_attempts':0,'eviction_controls':len(controls),
       'evaluation_pacing_overruns':sum(r['pacing_overrun'] for r in evaluation),
       'selection_frozen_before_evaluation':True,'source_hashes_match_configmap':True}
cm=json.loads((base/'workers.json').read_text())['items'][0]
for name,digest in run['source_sha256'].items():assert hashlib.sha256(cm['data'][name].encode()).hexdigest()==digest
pods=json.loads((base/'pods-final.json').read_text())['items']
for pod in pods:
    assert pod['status']['phase']=='Running'
    assert all(c['ready'] and c['restartCount']==0 for c in pod['status']['containerStatuses'])
result={'run_id':run['run_id'],'verification':audit,'summary':summary,
        'scope':'single fixture; 3 repeated shared-worker trajectories per policy/scenario; descriptive only'}
(base/'results.json').write_text(json.dumps(result,indent=2)+'\n')
(base/'verification.json').write_text(json.dumps(audit,indent=2)+'\n')
print(json.dumps(result,indent=2))
