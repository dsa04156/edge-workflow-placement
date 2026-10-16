"""Descriptive paired summaries; shared placement observations are counted once."""

from collections import defaultdict
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import statistics


def describe(values):
    return {'n':len(values),'mean':statistics.mean(values),'median':statistics.median(values),
            'sd':statistics.stdev(values) if len(values)>1 else None,'min':min(values),'max':max(values)} if values else None


def main():
    base=Path('artifacts/fresh-placement');assert not (base/'results.json').exists()
    run=json.loads((base/'run.json').read_text());verification=json.loads((base/'verification.json').read_text())
    corpus=json.loads((base/'corpus.json').read_text());episodes=[];comparisons=[];policy_cases=[];quality=[]
    all_rows=[];frozen_all=[];phase_time_order=[]
    for design in run['deployments']:
        block=design['block'];frozen=json.loads((base/f'frozen-b{block}.json').read_text());frozen_all.append(frozen)
        evaluation_design=json.loads((base/f'evaluation-design-b{block}.json').read_text())
        assert hashlib.sha256((base/f'frozen-b{block}.json').read_bytes()).hexdigest()==evaluation_design['frozen_sha256']
        for source,digest in frozen['calibration_log_sha256'].items():
            assert hashlib.sha256((base/f'calibration-b{block}-{source}.log').read_bytes()).hexdigest()==digest
        rows=[]
        for mode in ['calibration','evaluation']:
            for source in ['arm','intel_npu']:
                path=base/f'{mode}-b{block}-{source}.log'
                rows.extend(json.loads(line) for line in path.read_text().splitlines() if line.startswith('{') and json.loads(line).get('kind')=='workflow')
        all_rows+=rows
        calibration=[r for r in rows if r['mode']=='calibration'];evaluation=[r for r in rows if r['mode']=='evaluation']
        assert len(calibration)==512
        for row in rows:
            if row['status']!='ok':continue
            sample=corpus[row['input_id']];trace=row['result']['trace']
            assert trace[0]['input_sha256']==sample['sha256']
            assert trace[0]['output_sha256']==trace[1]['input_sha256']==sample['tensor_sha256']
            assert trace[1]['output_sha256']==trace[2]['input_sha256']
            assert not trace[1]['device']['was_cold']
            assert abs(sum(row['timings_ms'].values())-row['total_ms'])<1e-6
            if row['mode']=='evaluation':
                p=frozen['profiles'][row['source']+'|'+row['input_id']]
                assert row['snapshot']['profile']==p
                assert row['plan'][1] in p['candidates'][1]
        for key,devices in frozen['quality'].items():
            for alias,entry in devices.items():quality.append({'block':block,'input_id':key,'alias':alias,**entry})
        grouped=defaultdict(list)
        for row in evaluation:
            if not row['warmup']:grouped[(row['source'],row['input_id'],tuple(row['condition']['plan']))].append(row)
        indexed={}
        for (source,key,plan),rr in grouped.items():
            assert len(rr)==5
            members=rr[0]['condition']['members'];assert all(r['condition']['members']==members for r in rr)
            identifier=f"b{block}|{source}|{key}|{','.join(plan)}"
            episode={'id':identifier,'block':block,'source':source,'input_id':key,'plan':list(plan),'members':members,
                     'attempts':len(rr),'valid_complete':sum(r['valid_complete'] for r in rr),
                     'workflow_failures':sum(r['status']!='ok' for r in rr),
                     'quality_failures':sum(r['status']=='ok' and not r['valid_complete'] for r in rr),
                     'total_ms':describe([r['total_ms'] for r in rr]),
                     'workflow_ms':describe([r['timings_ms']['workflow'] for r in rr if r['status']=='ok']),
                     'request_ids':[r['request_id'] for r in rr]}
            episodes.append(episode)
            for member in members:indexed[(source,key,member)]=episode
        for source,key in ((s,k) for s in ['arm','intel_npu'] for k in corpus):
            for name in ['task_fastest','data_greedy','dag_enumeration']:
                episode=indexed[(source,key,name)]
                assert episode['plan']==frozen['selections'][source+'|'+key][name]['plan']
                policy_cases.append({'block':block,'source':source,'input_id':key,'policy':name,'episode_id':episode['id'],
                                     'plan':episode['plan'],'mean_total_ms':episode['total_ms']['mean'],
                                     'valid_complete':episode['valid_complete'],'attempts':episode['attempts']})
            dag=indexed[(source,key,'dag_enumeration')]
            for baseline in ['task_fastest','data_greedy']:
                other=indexed[(source,key,baseline)]
                valid=dag['valid_complete']==5 and other['valid_complete']==5
                delta=other['total_ms']['mean']-dag['total_ms']['mean'] if valid else None
                comparisons.append({'block':block,'source':source,'input_id':key,'baseline':baseline,
                    'same_plan':other['id']==dag['id'],'quality_controlled':valid,'baseline_plan':other['plan'],'dag_plan':dag['plan'],
                    'baseline_minus_dag_ms':delta,'relative_reduction_percent':100*delta/other['total_ms']['mean'] if valid else None})
        phase_time_order.append({'block':block,'calibration_last_mtime_ns':max((base/f'calibration-b{block}-{s}.log').stat().st_mtime_ns for s in ['arm','intel_npu']),
            'freeze_mtime_ns':(base/f'frozen-b{block}.json').stat().st_mtime_ns,
            'evaluation_first_spec_mtime_ns':min((base/f'evaluation-b{block}-{s}.input.json').stat().st_mtime_ns for s in ['arm','intel_npu'])})
    for times in phase_time_order:
        assert times['calibration_last_mtime_ns']<times['freeze_mtime_ns']<times['evaluation_first_spec_mtime_ns']
    summary=[]
    for source in ['arm','intel_npu','both']:
        for name in ['task_fastest','data_greedy','dag_enumeration']:
            per_block=[]
            for block in range(3):
                rows=[r for r in policy_cases if r['block']==block and r['policy']==name and (source=='both' or r['source']==source)]
                per_block.append({'block':block,'case_mean_total_ms':statistics.mean(r['mean_total_ms'] for r in rows),
                                  'case_count':len(rows),'valid_complete':sum(r['valid_complete'] for r in rows),
                                  'shared_observation_attempts':sum(r['attempts'] for r in rows)})
            summary.append({'source':source,'policy':name,'blocks':per_block,
                            'block_mean_summary':describe([r['case_mean_total_ms'] for r in per_block])})
    measured=[r for r in all_rows if r['mode']=='evaluation' and not r['warmup']]
    result={'analyzed_at':datetime.now(timezone.utc).isoformat(),'scope':'Descriptive warm static placement; three fresh deployments on same hosts/date; temporal holdout of the same eight inputs.',
            'all_workflows':len(all_rows),'calibration_workflows':sum(r['mode']=='calibration' for r in all_rows),
            'evaluation_workflows':sum(r['mode']=='evaluation' for r in all_rows),'evaluation_measured_workflows':len(measured),
            'evaluation_valid_complete':sum(r['valid_complete'] for r in measured),
            'protocol_failures':sum(r['status']!='ok' for r in all_rows),'pacing_overruns':sum(r['pacing_overrun'] for r in all_rows),
            'calibration_quality_disagreements':sum(r['status']=='ok' and not r['valid_complete'] for r in all_rows if r['mode']=='calibration'),
            'episodes':episodes,'policy_cases':policy_cases,'paired_comparisons':comparisons,'policy_case_summaries':summary,
            'quality_gate':quality,'phase_order':phase_time_order,
            'planning_ms':{name:describe([f['selections'][key][name]['planning_ms'] for f in frozen_all for key in f['selections']]) for name in ['task_fastest','data_greedy','dag_enumeration']},
            'phase_durations':verification,'interpretation_limits':['No independent hardware/day samples','No unseen-image holdout','CPU-reference agreement is not accuracy',
              'Shared placement observations are not independent policy samples','Planning is frozen before evaluation; no online reoptimization cost claim',
              'No p-values/confidence intervals/p95/SLO superiority or equivalence claims','Small DAG enumeration is not AnchorDL reproduction']}
    (base/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ['all_workflows','calibration_workflows','evaluation_workflows','evaluation_measured_workflows','evaluation_valid_complete','protocol_failures','pacing_overruns','calibration_quality_disagreements']},ensure_ascii=False))


if __name__=='__main__':main()
