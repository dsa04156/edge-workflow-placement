"""Descriptive crossover analysis with frozen-policy and receipt integrity checks."""

from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import sys
import statistics

from analyze_fresh_placement import describe
from fresh_placement import freeze_profiles, plan_cost, data_greedy, dag_enumeration
from placement_cost_diagnostic import dag_with_state, state_costs


BASE=Path('artifacts/placement-cost-diagnostic')


def records(path):
    return [json.loads(s) for s in path.read_text().splitlines() if s.startswith('{')]


def metrics(row):
    timings=row['timings_ms'];trace=row['result']['trace']
    result={'total':row['total_ms'],**timings}
    result['other']=row['total_ms']-sum(timings[k] for k in ['workflow','state_validation','observation'])
    result.update({f'task{i}':t['task_ms'] for i,t in enumerate(trace)})
    result['ingress']=timings['workflow']-trace[0]['task_ms']-trace[0]['rpc_next_ms']
    result['edge0']=trace[0]['rpc_next_ms']-trace[1]['task_ms']-trace[1]['rpc_next_ms']
    result['edge1']=trace[1]['rpc_next_ms']-trace[2]['task_ms']
    return result


def summarize(rows):
    valid=[r for r in rows if r['valid_complete']]
    return {'attempts':len(rows),'valid_complete':len(valid),
            'metrics_ms':{k:describe([metrics(r)[k] for r in valid]) for k in metrics(valid[0])} if valid else {}}


def main():
    check='--check' in sys.argv
    output=BASE/'results.json';assert check or not output.exists(),'Preserve existing analysis'
    evidence_order=json.loads((BASE/'phase-order-evidence.json').read_text())
    for name,evidence in evidence_order['files'].items():
        assert hashlib.sha256((BASE/name).read_bytes()).hexdigest()==evidence['sha256']
    run=json.loads((BASE/'run.json').read_text());corpus=json.loads((BASE/'corpus.json').read_text())
    verification=json.loads((BASE/'verification.json').read_text())
    aliases=['arm','intel_npu'];all_rows=[];cases=[];pairs=[];policy_cases=[];plans=[];ordering=[]
    for deployment in run['deployments']:
        b=deployment['block'];frozen=json.loads((BASE/f'frozen-b{b}.json').read_text())
        ed=json.loads((BASE/f'evaluation-design-b{b}.json').read_text())
        assert ed['frozen_sha256']==hashlib.sha256((BASE/f'frozen-b{b}.json').read_bytes()).hexdigest()
        phases={mode:[r for r in records(BASE/f'{mode}-b{b}-arm.log') if r['kind']=='workflow'] for mode in ['calibration','evaluation']}
        assert len(phases['calibration'])==128 and len(phases['evaluation'])==192
        assert frozen['calibration_log_sha256']['arm']==hashlib.sha256((BASE/f'calibration-b{b}-arm.log').read_bytes()).hexdigest()
        evidence=hashlib.sha256(json.dumps(frozen['calibration_log_sha256'],sort_keys=True).encode()).hexdigest()
        profiles,quality=freeze_profiles(phases['calibration'],corpus,aliases,evidence)
        assert profiles==frozen['profiles'] and quality==frozen['quality']
        receipts=[r for a in aliases for r in records(BASE/f'worker-b{b}-{a}.log') if r['kind']=='stage_complete']
        seen=[]
        for mode,rows in phases.items():
            expected=deployment['calibration']['arm'] if mode=='calibration' else ed['schedules']['arm']
            assert [r['condition'] for r in rows]==expected
            for row in rows:
                assert row['warmup']==row['condition']['warmup']
                assert row['iteration']==row['condition']['sweep']
                if row['status']!='ok':continue
                trace=row['result']['trace'];sample=corpus[row['input_id']]
                assert row['plan']==row['condition']['plan']
                assert trace[0]['input_sha256']==sample['sha256']
                assert trace[0]['output_sha256']==trace[1]['input_sha256']==sample['tensor_sha256']
                assert trace[1]['output_sha256']==trace[2]['input_sha256']
                assert row['valid_complete']==(row['result']['top5']==sample['reference_top5'])
                assert not trace[1]['device']['was_cold']
                assert abs(sum(row['timings_ms'].values())-row['total_ms'])<1e-6
                rr=sorted([r for r in receipts if r['request_id']==row['request_id']],key=lambda r:r['stage'])
                assert len(rr)==3
                for server,client in zip(rr,trace):
                    assert all(server[k]==client[k] for k in ['stage','alias','node','input_sha256','output_sha256','task_ms'])
                    assert server['node']==deployment['targets'][server['alias']]['node']
                    if server['stage']==1 and server['alias']=='intel_npu':assert server['device']['execution_devices']==['NPU']
                    else:assert server['device']['execution_device']=='CPU'
                seen+=rr
                if mode=='evaluation':
                    assert row['snapshot']['profile']==profiles['arm|'+row['input_id']]
                    assert row['completed_at']>=frozen['frozen_at']
            all_rows+=rows
        assert len(receipts)==len(seen)
        times=[evidence_order['files'][name]['original_host_mtime_ns'] for name in [f'calibration-b{b}-arm.log',f'frozen-b{b}.json',f'evaluation-b{b}-arm.input.json']]
        assert times[0]<times[1]<times[2];ordering.append({'block':b,'calibration_freeze_evaluation_mtime_ns':times})
        for key in corpus:
            profile=profiles['arm|'+key];state=state_costs(phases['calibration'],key)
            assert state==frozen['state_ms'][key]
            functions={'data_greedy':data_greedy,'dag_enumeration':dag_enumeration,'dag_with_state':lambda s:dag_with_state(s,state)}
            eval_by_plan={};cal_by_plan={}
            for plan in itertools.product(aliases,repeat=3):
                eval_by_plan[plan]=[r for r in phases['evaluation'] if not r['warmup'] and r['input_id']==key and tuple(r['condition']['plan'])==plan]
                cal_by_plan[plan]=[r for r in phases['calibration'] if not r['warmup'] and r['input_id']==key and tuple(r['condition']['plan'])==plan]
                assert len(eval_by_plan[plan])==10 and len(cal_by_plan[plan])==6
                plans.append({'block':b,'input_id':key,'plan':list(plan),
                              'predicted_workflow_ms':plan_cost(profile,plan),'predicted_workflow_plus_state_ms':plan_cost(profile,plan)+state[plan[1]],
                              'calibration':summarize(cal_by_plan[plan]),'evaluation':summarize(eval_by_plan[plan])})
            for name,function in functions.items():
                selection=frozen['selections']['arm|'+key][name]
                assert function({'profile':profile})==selection['plan']
                policy_cases.append({'block':b,'input_id':key,'policy':name,**selection,**summarize(eval_by_plan[tuple(selection['plan'])])})
            for mode,n in [('calibration',6),('evaluation',10)]:
                rr=[r for r in phases[mode] if r['input_id']==key and not r['warmup']]
                first=Counter(r['condition']['plan'][0] for r in rr if r['condition']['position']==0)
                assert first=={'arm':n//2,'intel_npu':n//2}
                for sweep in range(2,2+n):
                    anchors=[r for r in rr if r['condition']['sweep']==sweep and r['condition']['position']<2]
                    assert len(anchors)==2 and all(len(set(r['condition']['plan']))==1 for r in anchors)
                    by_alias={r['condition']['plan'][0]:r for r in anchors}
                    valid=all(r['valid_complete'] for r in anchors)
                    delta={k:metrics(by_alias['intel_npu'])[k]-metrics(by_alias['arm'])[k] for k in metrics(anchors[0])} if valid else None
                    pairs.append({'block':b,'input_id':key,'mode':mode,'sweep':sweep,'first':anchors[0]['condition']['plan'][0],
                                  'valid':valid,'npu_minus_arm_ms':delta,'request_ids':[r['request_id'] for r in anchors]})
            group=[r for r in pairs if r['block']==b and r['input_id']==key and r['mode']=='evaluation' and r['valid']]
            cases.append({'block':b,'input_id':key,'state_ms':state,
                          'anchor_predicted_workflow_npu_minus_arm_ms':plan_cost(profile,('intel_npu',)*3)-plan_cost(profile,('arm',)*3),
                          'anchor_calibration_workflow_median_npu_minus_arm_ms':statistics.median(r['timings_ms']['workflow'] for r in cal_by_plan[('intel_npu',)*3])-statistics.median(r['timings_ms']['workflow'] for r in cal_by_plan[('arm',)*3]),
                          'anchor_evaluation_pair_delta_ms':{k:describe([r['npu_minus_arm_ms'][k] for r in group]) for k in group[0]['npu_minus_arm_ms']} if group else None,
                          'workflow_npu_faster_pairs':sum(r['npu_minus_arm_ms']['workflow']<0 for r in group),
                          'total_npu_faster_pairs':sum(r['npu_minus_arm_ms']['total']<0 for r in group)})
    ids=[r['request_id'] for r in all_rows];assert len(ids)==len(set(ids))==960
    identities=json.loads((BASE/'pod-identities.json').read_text());assert len({p['uid'] for p in identities})==12
    assert len({c for p in identities for c in p['container_ids']})==12
    cm=json.loads((BASE/'configmap.json').read_text())
    for name,digest in run['source_sha256'].items():
        assert hashlib.sha256(cm['data'][name].encode()).hexdigest()==digest
        assert hashlib.sha256(Path('experiments/e0',name).read_bytes()).hexdigest()==digest
    result={'run_id':run['run_id'],'all_workflows':len(all_rows),'calibration_workflows':384,'evaluation_workflows':576,'evaluation_measured':480,
            'valid_complete':sum(r['valid_complete'] for r in all_rows),'protocol_failures':sum(r['status']!='ok' for r in all_rows),
            'pacing_overruns':sum(r['pacing_overrun'] for r in all_rows),'receipt_count':sum(v['stage_receipts'] for v in verification['blocks']),
            'cases':cases,'plans':plans,'policy_cases':policy_cases,'paired_anchors':pairs,'phase_order':ordering,
            'phase_durations':verification,'runtime_source_count':len(run['source_sha256']),
            'scope':'Targeted descriptive follow-up; same hosts/date, two selected images, no dynamic intervention or inferential test.',
            'analysis_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if check:
        previous=json.loads((BASE/'results.json').read_text())
        assert {k:v for k,v in previous.items() if k!='analysis_sha256'}=={k:v for k,v in result.items() if k!='analysis_sha256'}
        print('Archived analysis reproduced (analysis source revision tracked separately)')
    else:
        (BASE/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ['cases','plans','policy_cases','paired_anchors','phase_order','phase_durations']},ensure_ascii=False))


if __name__=='__main__':main()
