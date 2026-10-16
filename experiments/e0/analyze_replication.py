"""Audit per-input receipts and summarize paired deployment-level contrasts."""

from collections import Counter,defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics

base=Path('artifacts/e3-input-replication');run=json.loads((base/'run.json').read_text());corpus=json.loads((base/'corpus.json').read_text())
def records(p):return [json.loads(l) for l in p.read_text().splitlines() if l.startswith('{')]
rows=[];controls=[];receipts={}
for path in sorted(base.glob('evaluation-*.log')):
    rs=records(path);assert rs[-1]['kind']=='client_complete' and rs[-1]['requests']==224
    assert (base/(path.stem+'.stderr')).read_text()==''
    rows.extend(r for r in rs if r['kind']=='workflow');controls.extend(r for r in rs if r['kind']=='control')
assert len(rows)==1344 and len({r['request_id'] for r in rows})==1344
for path in base.glob('worker-*.log'):
    rs=records(path);assert not any(r['kind']=='request_error' for r in rs)
    for r in rs:
        if r['kind']=='stage_complete':
            key=(r['request_id'],r['stage']);assert key not in receipts;receipts[key]=r
assert len(receipts)==4032
for row in rows:
    trace=row['trace'];sample=corpus[row['input_id']];plan=row['plan']
    assert row['input_id']==row['condition']['image_id']
    assert [t['stage'] for t in trace]==[0,1,2] and [t['alias'] for t in trace]==plan
    assert [t['node'] for t in trace]==[run['deployments'][row['block']]['targets'][a]['node'] for a in plan]
    assert all(t['input_id']==row['input_id'] for t in trace)
    assert [t['input_bytes'] for t in trace]==[sample['bytes'],602112,4000]
    assert trace[0]['input_sha256']==sample['sha256']
    assert trace[0]['output_sha256']==trace[1]['input_sha256']==sample['tensor_sha256']
    assert trace[1]['output_sha256']==trace[2]['input_sha256']
    payload=json.dumps(row['top5']).encode()
    assert trace[2]['output_bytes']==len(payload) and trace[2]['output_sha256']==hashlib.sha256(payload).hexdigest()
    for t in trace:
        receipt=receipts[(row['request_id'],t['stage'])]
        assert all(receipt[k]==v for k,v in t.items())
    device=trace[1]['device']
    if plan[1]=='intel_npu':assert device['execution_devices']==['NPU']
    else:assert device['execution_device']=='CPU'
    assert device['top1_agrees_with_cpu']==(row['top5'][0]==sample['reference_top5'][0])
    assert device['top5_overlap_with_cpu']==len(set(row['top5'])&set(sample['reference_top5']))
    assert device['top5_order_agrees_with_cpu']==(row['top5']==sample['reference_top5'])
    assert device['was_cold']==(device['preparation_ms']>0)
    assert abs(row['total_ms']-row['workflow_ms']-row['observation_ms'])<1e-6
identities=json.loads((base/'pod-identities.json').read_text());assert len(identities)==12
assert len({p['uid'] for p in identities})==12
assert len({c for p in identities for c in p['container_ids']})==12
for block in range(3):
    assert Counter(r['condition']['id'] for r in rows if r['block']==block)=={c['id']:7 for schedule in run['deployments'][block]['schedules'].values() for c in schedule}
measured=[r for r in rows if not r['warmup']];assert len(measured)==960
assert len(controls)==96
cold=[r for r in measured if r['trace'][1]['device']['was_cold']];assert len(cold)==48
assert all(r['iteration']==2 and r['condition']['state']=='eviction' and r['condition']['anchor']=='intel_npu' for r in cold)
groups=defaultdict(list)
for r in measured:groups[(r['input_id'],r['source'],r['condition']['state'],r['condition']['anchor'],r['block'])].append(r)
assert len(groups)==192 and all(len(rs)==5 for rs in groups.values())
episodes=[]
for key,rs in sorted(groups.items()):
    values=[r['total_ms'] for r in rs]
    episodes.append({'image_id':key[0],'source':key[1],'state':key[2],'anchor':key[3],'block':key[4],
        'n':len(values),'mean_ms':statistics.mean(values),'median_ms':statistics.median(values),
        'sd_ms':statistics.stdev(values),'min_ms':min(values),'max_ms':max(values),'missing':0})
contrasts=[]
for image in corpus:
    for source in ['arm','intel_npu']:
        for state in ['warm','eviction']:
            arm=[statistics.mean(r['total_ms'] for r in groups[(image,source,state,'arm',b)]) for b in range(3)]
            npu=[statistics.mean(r['total_ms'] for r in groups[(image,source,state,'intel_npu',b)]) for b in range(3)]
            difference=[a-n for a,n in zip(arm,npu)]
            contrasts.append({'image_id':image,'source':source,'state':state,'all_arm_mean_ms_by_deployment':arm,
                'all_npu_mean_ms_by_deployment':npu,'arm_minus_npu_ms_by_deployment':difference,
                'mean_arm_ms':statistics.mean(arm),'mean_npu_ms':statistics.mean(npu),
                'deployment_sd_arm_ms':statistics.stdev(arm),'deployment_sd_npu_ms':statistics.stdev(npu),
                'direction':'npu_lower_all3' if min(difference)>0 else 'arm_lower_all3' if max(difference)<0 else 'mixed'})
quality={}
for anchor in ['arm','intel_npu']:
    rs=[r for r in measured if r['condition']['anchor']==anchor];devices=[r['trace'][1]['device'] for r in rs]
    quality[anchor]={'requests':len(rs),'top1_agreement_count':sum(d['top1_agrees_with_cpu'] for d in devices),
        'top5_set_agreement_count':sum(d['top5_overlap_with_cpu']==5 for d in devices),
        'top5_order_agreement_count':sum(d['top5_order_agrees_with_cpu'] for d in devices),
        'minimum_top5_overlap':min(d['top5_overlap_with_cpu'] for d in devices),
        'max_abs_error':max(d['max_abs_error_vs_cpu'] for d in devices),'max_rmse':max(d['rmse_vs_cpu'] for d in devices)}
summary={}
for state in ['warm','eviction']:
    fixed=[];source_aware=[]
    for block in range(3):
        rs=[r for r in measured if r['block']==block and r['condition']['state']==state]
        fixed.append(statistics.mean(r['total_ms'] for r in rs if r['condition']['anchor']=='arm'))
        source_aware.append(statistics.mean(r['total_ms'] for r in rs if r['condition']['anchor']==r['source']))
    summary[state]={'fixed_arm_mean_ms_by_deployment':fixed,'frozen_E2_source_plan_mean_ms_by_deployment':source_aware,
                    'fixed_arm_mean_ms':statistics.mean(fixed),'frozen_E2_source_plan_mean_ms':statistics.mean(source_aware)}
assert hashlib.sha256((base/'corpus.json').read_bytes()).hexdigest()==run['corpus_sha256']
assert hashlib.sha256(Path('artifacts/e2-dynamic/selection.json').read_bytes()).hexdigest()==run['frozen_E2_selection_sha256']
assert datetime.fromisoformat(run['design_frozen_at']).timestamp()<min(p.stat().st_mtime for p in base.glob('evaluation-*.input.json'))
assert json.loads((base/'live-source-verification.json').read_text())['matches_frozen']
verification={'total_workflows':1344,'measured_workflows':960,'matching_stage_receipts':4032,'new_pod_uids':12,
    'new_container_ids':12,'deployments':3,'measured_NPU_preparations':48,'eviction_controls':96,
    'workflow_failures':0,'measured_pacing_overruns':sum(r['pacing_overrun'] for r in measured),
    'quality_reference':'CPU predictions, not labeled accuracy','same_hosts_driver_OS_cache_retained':True}
prep_values=[r['trace'][1]['device']['preparation_ms'] for r in cold]
preparation={'n':len(prep_values),'mean_ms':statistics.mean(prep_values),'median_ms':statistics.median(prep_values),
             'min_ms':min(prep_values),'max_ms':max(prep_values),'sd_ms':statistics.stdev(prep_values)}
quality_by_input={}
for image in corpus:
    rs=[r for r in measured if r['input_id']==image and r['condition']['anchor']=='intel_npu']
    quality_by_input[image]={'n':len(rs),'reference_top5':corpus[image]['reference_top5'],
        'observed_npu_top5':[list(t) for t in sorted({tuple(r['top5']) for r in rs})],
        'top1_agreement_count':sum(r['trace'][1]['device']['top1_agrees_with_cpu'] for r in rs),
        'top5_set_agreement_count':sum(r['trace'][1]['device']['top5_overlap_with_cpu']==5 for r in rs)}
result={'run_id':run['run_id'],'verification':verification,'summary':summary,'quality':quality,
        'quality_by_input':quality_by_input,'preparation':preparation,
        'paired_contrasts':contrasts,'episode_descriptives':episodes}
(base/'results.json').write_text(json.dumps(result,indent=2)+'\n');(base/'verification.json').write_text(json.dumps(verification,indent=2)+'\n')
print(json.dumps({'verification':verification,'summary':summary,'quality':quality,
    'edge_input_directions':[x for x in contrasts if x['source']=='arm']},indent=2))
