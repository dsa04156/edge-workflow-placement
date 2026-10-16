"""Audit and summarize fixed-edge-source placement under controlled Pod TBF."""

import ast
from collections import defaultdict
import hashlib,json,itertools,statistics
from pathlib import Path
import sys

from analyze_fresh_placement import describe
from fresh_placement import freeze_profiles,plan_cost,data_greedy,dag_enumeration
from measured_policy_bridge import task_fastest


BASE=Path('artifacts/edge-network-placement')


def read(name):return json.loads((BASE/name).read_text())


def records(name):return [json.loads(s) for s in (BASE/name).read_text().splitlines() if s.startswith('{')]


def attrs(record):
    # pyroute2 nla_slot objects were retained with their literal representations.
    return dict(ast.literal_eval(x) if isinstance(x,str) else x for x in record['attrs'])


def metrics(row):
    t=row['result']['trace'];p=row['plan'];m={'total':row['total_ms'],**row['timings_ms']}
    m.update({f'task{i}':x['task_ms'] for i,x in enumerate(t)})
    m['ingress']=m['workflow']-t[0]['task_ms']-t[0]['rpc_next_ms']
    m['edge0']=t[0]['rpc_next_ms']-t[1]['task_ms']-t[1]['rpc_next_ms']
    m['edge1']=t[1]['rpc_next_ms']-t[2]['task_ms']
    m['cross_node_payload_bytes']=(t[0]['input_bytes'] if p[0]!='arm' else 0)+sum(t[i]['output_bytes'] for i in [0,1] if p[i]!=p[i+1])
    return m


def main():
    check='--check' in sys.argv
    assert check or not (BASE/'results.json').exists()
    run=read('run.json');corpus=read('corpus.json');aliases=['arm','intel_npu'];regimes=list(run['regimes'])
    ordering=read('phase-order-evidence.json');assert ordering['run_id']==run['run_id']
    for name, evidence in ordering['files'].items():
        assert hashlib.sha256((BASE/name).read_bytes()).hexdigest()==evidence['sha256']
    all_rows=[];episodes=[];cases=[];comparisons=[];calibration_plans=[];network_audits=[];phase_order=[]
    for deployment in run['deployments']:
        b=deployment['block'];f=read(f'frozen-b{b}.json');ed=read(f'evaluation-design-b{b}.json')
        assert ed['frozen_sha256']==hashlib.sha256((BASE/f'frozen-b{b}.json').read_bytes()).hexdigest()
        cr=[];er=[]
        for mode in ['calibration','evaluation']:
            for regime in regimes:
                rr=[r for r in records(f'{mode}-b{b}-{regime}.log') if r['kind']=='workflow']
                assert [r['condition'] for r in rr]==(deployment['calibration'][regime] if mode=='calibration' else ed['schedules'][regime])
                for r in rr:
                    assert r['source']=='arm' and r['regime']==regime and r['warmup']==r['condition']['warmup']
                    if r['status']!='ok':continue
                    assert r['plan']==r['condition']['plan']
                    t=r['result']['trace'];sample=corpus[r['input_id']]
                    assert t[0]['input_sha256']==sample['sha256']
                    assert t[0]['output_sha256']==t[1]['input_sha256']==sample['tensor_sha256']
                    assert t[1]['output_sha256']==t[2]['input_sha256'] and not t[1]['device']['was_cold']
                    assert abs(sum(r['timings_ms'].values())-r['total_ms'])<1e-6
                    assert r['valid_complete']==(r['result']['top5']==sample['reference_top5'])
                    if mode=='evaluation':
                        assert r['snapshot']['profile']==f['profiles'][regime]['arm|'+r['input_id']]
                        assert r['completed_at']>=f['frozen_at']
                (cr if mode=='calibration' else er).extend(rr)
                n=read(f'network-b{b}-{mode}-{regime}.json')
                assert len(n['settings'])==3
                for pod,state in n['settings'].items():
                    qdiscs=[(q,attrs(q)) for q in state['after']]
                    if regime=='native':assert all(a['TCA_KIND']=='noqueue' for q,a in qdiscs)
                    else:
                        shaped=[a for q,a in qdiscs if a['TCA_KIND']=='tbf'];assert len(shaped)==1
                        parameters=dict(shaped[0]['TCA_OPTIONS']['attrs'])['TCA_TBF_PARMS']
                        assert parameters['rate']==run['regimes'][regime]*1_000_000//8 and parameters['limit']==2_000_000
                        assert len(state['filters'])>=len(state['destinations'])
                    before=[attrs(q) for q in state['before'] if attrs(q)['TCA_KIND']=='tbf']
                    network_audits.append({'block':b,'mode':mode,'regime':regime,'pod':pod,'netns':state['netns'],
                                          'destinations':state['destinations'],'previous_tbf_stats':[a['TCA_STATS'] for a in before]})
        assert len(cr)==288
        for regime,digest in f['calibration_log_sha256'].items():assert hashlib.sha256((BASE/f'calibration-b{b}-{regime}.log').read_bytes()).hexdigest()==digest
        evidence=hashlib.sha256(json.dumps(f['calibration_log_sha256'],sort_keys=True).encode()).hexdigest()
        _,quality=freeze_profiles(cr,corpus,aliases,evidence);assert quality==f['quality']
        for regime in regimes:
            rr=[r for r in cr if r['regime']==regime]
            profiles,_=freeze_profiles(rr,corpus,aliases,evidence+'|'+regime);assert profiles==f['profiles'][regime]
            for key in corpus:
                profile=profiles['arm|'+key]
                for plan in itertools.product(aliases,repeat=3):
                    rows=[r for r in rr if r['input_id']==key and tuple(r['plan'])==plan and not r['warmup']]
                    assert len(rows)==3
                    calibration_plans.append({'block':b,'regime':regime,'input_id':key,'plan':list(plan),'predicted_workflow_ms':plan_cost(profile,plan),
                        'metrics_ms_or_bytes':{k:describe([metrics(r)[k] for r in rows]) for k in metrics(rows[0])}})
                selected=f['selections'][regime]['arm|'+key]
                functions={'task_fastest':task_fastest,'data_greedy':data_greedy,'current_dag':dag_enumeration,
                           'fixed_native_dag':lambda s:dag_enumeration({'profile':f['profiles']['native']['arm|'+key]})}
                grouped=defaultdict(list)
                for r in er:
                    if r['regime']==regime and r['input_id']==key and not r['warmup']:grouped[tuple(r['condition']['plan'])].append(r)
                indexed={}
                for plan,rows in grouped.items():
                    assert len(rows)==5
                    good=[r for r in rows if r['valid_complete']]
                    episode={'block':b,'regime':regime,'input_id':key,'plan':list(plan),'members':rows[0]['condition']['members'],
                        'attempts':len(rows),'valid':len(good),'request_ids':[r['request_id'] for r in rows],
                        'metrics_ms_or_bytes':{k:describe([metrics(r)[k] for r in good]) for k in metrics(good[0])} if good else {}}
                    episodes.append(episode)
                    for name in episode['members']:indexed[name]=episode
                for name,function in functions.items():
                    assert function({'profile':profile})==selected[name]['plan']==indexed[name]['plan']
                    cases.append({'block':b,'regime':regime,'input_id':key,'policy':name,**selected[name],
                                  'valid':indexed[name]['valid'],'metrics_ms_or_bytes':indexed[name]['metrics_ms_or_bytes']})
                for name in ['task_fastest','data_greedy','fixed_native_dag']:
                    baseline=indexed[name];dag=indexed['current_dag'];valid=baseline['valid']==dag['valid']==5
                    comparisons.append({'block':b,'regime':regime,'input_id':key,'baseline':name,'same_plan':baseline['plan']==dag['plan'],
                        'baseline_plan':baseline['plan'],'dag_plan':dag['plan'],'valid':valid,
                        'baseline_minus_current_ms':baseline['metrics_ms_or_bytes']['total']['mean']-dag['metrics_ms_or_bytes']['total']['mean'] if valid else None})
        receipts=[r for alias in aliases for r in records(f'worker-b{b}-{alias}.log') if r['kind']=='stage_complete']
        assert len(receipts)==3*sum(r['status']=='ok' for r in cr+er)
        for r in cr+er:
            if r['status']!='ok':continue
            server=sorted([t for t in receipts if t['request_id']==r['request_id']],key=lambda t:t['stage']);assert len(server)==3
            for a,c in zip(server,r['result']['trace']):
                assert all(a[k]==c[k] for k in ['stage','node','alias','task_ms','input_sha256','output_sha256'])
                assert a['node']==deployment['targets'][a['alias']]['node']
                if a['stage']==1 and a['alias']=='intel_npu':assert a['device']['execution_devices']==['NPU']
                else:assert a['device']['execution_device']=='CPU'
        stamps=ordering['files']
        times=[max(stamps[f'calibration-b{b}-{x}.log']['original_host_mtime_ns'] for x in regimes),
               stamps[f'frozen-b{b}.json']['original_host_mtime_ns'],
               min(stamps[f'evaluation-b{b}-{x}.input.json']['original_host_mtime_ns'] for x in regimes)]
        assert times[0]<times[1]<times[2];phase_order.append(times);all_rows+=cr+er
        restored=read(f'network-b{b}-restore-native.json')
        assert len(restored['settings'])==3
        for pod,state in restored['settings'].items():
            assert all(attrs(q)['TCA_KIND']=='noqueue' for q in state['after'])
            network_audits.append({'block':b,'mode':'restore','regime':'native','pod':pod,'netns':state['netns'],
                'destinations':state['destinations'], 'previous_tbf_stats':[attrs(q)['TCA_STATS'] for q in state['before'] if attrs(q)['TCA_KIND']=='tbf']})
    assert len({r['request_id'] for r in all_rows})==len(all_rows)
    cm=read('configmap.json')
    for name,digest in run['source_sha256'].items():
        assert hashlib.sha256(cm['data'][name].encode()).hexdigest()==digest
        assert hashlib.sha256(Path('experiments/e0',name).read_bytes()).hexdigest()==digest
    identities=read('pod-identities.json');assert len({p['uid'] for p in identities})==12
    assert len({c for p in identities for c in p['container_ids']})==21
    for path in BASE.glob('deployment-b*.json'):
        for pod in json.loads(path.read_text())['items']:
            if pod['kind']=='Pod':assert not pod['spec'].get('hostNetwork',False) and not pod['spec'].get('hostPID',False)
    result={'run_id':run['run_id'],'all_workflows':len(all_rows),'calibration':sum(r['mode']=='calibration' for r in all_rows),
            'evaluation':sum(r['mode']=='evaluation' for r in all_rows),'evaluation_measured':sum(r['mode']=='evaluation' and not r['warmup'] for r in all_rows),
            'valid_complete':sum(r['valid_complete'] for r in all_rows),'protocol_failures':sum(r['status']!='ok' for r in all_rows),
            'pacing_overruns':[{'request_id':r['request_id'],'mode':r['mode'],'total_ms':r['total_ms'],'condition':r['condition']} for r in all_rows if r['pacing_overrun']],
            'episodes':episodes,'policy_cases':cases,'comparisons':comparisons,'calibration_plans':calibration_plans,
            'network_audits':network_audits,'phase_order':phase_order,'source_sha256':run['source_sha256'],
            'analysis_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'limits':['Fixed edge source file replay, not live sensors','Three Pod deployments share hosts/date/cache',
                      'TBF per Pod/direction, not aggregate WAN contention','Current profile is pre-calibrated regime information, not online estimation',
                      'Small DAG enumeration is not a published-policy reproduction','No inferential or p95/SLO claim']}
    if check:
        previous=json.loads((BASE/'results.json').read_text())
        assert {k:v for k,v in previous.items() if k!='analysis_sha256'}=={k:v for k,v in result.items() if k!='analysis_sha256'}
        print('Archived analysis reproduced (analysis source revision tracked separately)')
    else:
        (BASE/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ['run_id','all_workflows','calibration','evaluation','evaluation_measured','valid_complete','protocol_failures']}))


if __name__=='__main__':main()
