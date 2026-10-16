"""Retain observed reversals and cost boundaries without refitting any plans."""

from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys


def main():
    base=Path('artifacts/fresh-placement')
    result=json.loads((base/'results.json').read_text())
    run=json.loads((base/'run.json').read_text())
    rows=[]
    for path in sorted(base.glob('*.log')):
        if path.name.startswith(('calibration-','evaluation-')):
            rows.extend(json.loads(line) for line in path.read_text().splitlines()
                        if line.startswith('{') and json.loads(line).get('kind')=='workflow')
    negative=[]
    for comparison in result['paired_comparisons']:
        if comparison['baseline_minus_dag_ms'] is None or comparison['baseline_minus_dag_ms']>=0:
            continue
        frozen=json.loads((base/f"frozen-b{comparison['block']}.json").read_text())
        key=comparison['source']+'|'+comparison['input_id'];details=[]
        for policy in [comparison['baseline'],'dag_enumeration']:
            episode=next(e for e in result['episodes'] if e['block']==comparison['block'] and
                         e['source']==comparison['source'] and e['input_id']==comparison['input_id'] and policy in e['members'])
            rr=[row for row in rows if row['request_id'] in episode['request_ids']]
            details.append({'policy':policy,'episode':episode,
                'predicted_workflow_ms':frozen['selections'][key][policy]['estimated_workflow_ms'],
                'mean_components_ms':{k:statistics.mean(row['timings_ms'][k] for row in rr) for k in rr[0]['timings_ms']}})
        negative.append({'comparison':comparison,'details':details})
    stability={}
    for policy in ['task_fastest','data_greedy','dag_enumeration']:
        groups=defaultdict(set)
        for case in result['policy_cases']:
            if case['policy']==policy:groups[(case['source'],case['input_id'])].add(tuple(case['plan']))
        stability[policy]={'stable_across_three_deployments':sum(len(v)==1 for v in groups.values()),
            'total_source_input_cases':len(groups),'varying_cases':[{'source':k[0],'input_id':k[1],
            'plans':[list(p) for p in sorted(v)]} for k,v in groups.items() if len(v)>1]}
    cleanup=json.loads((base/'cleanup.json').read_text())
    diagnostic={'scope':'Exploratory descriptive diagnostics of retained observations; no refitting or rerunning evaluation.',
        'negative_observed_comparisons':negative,'selection_stability':stability,
        'actual_npu_workflows':sum(row['result']['trace'][1]['alias']=='intel_npu' for row in rows if row['status']=='ok'),
        'actual_arm_cpu_workflows':sum(row['result']['trace'][1]['alias']=='arm' for row in rows if row['status']=='ok'),
        'pacing_overruns':[{'request_id':row['request_id'],'mode':row['mode'],'warmup':row['warmup'],
            'total_ms':row['total_ms']} for row in rows if row['pacing_overrun']],
        'design_freeze_to_cleanup_seconds':(datetime.fromisoformat(cleanup['completed_at'])-
            datetime.fromisoformat(run['design_frozen_at'])).total_seconds(),
        'analysis_environment':{'python':platform.python_version(),'system':platform.system(),'machine':platform.machine()},
        'analysis_source_sha256':{name:hashlib.sha256((Path('experiments/e0')/name).read_bytes()).hexdigest()
            for name in ['analyze_fresh_placement.py','plot_fresh_placement.py']}}
    path=base/'diagnostics.json'
    if sys.argv[1:]==['--check']:
        assert diagnostic==json.loads(path.read_text()),'Diagnostics differ from retained observations'
    else:
        assert not sys.argv[1:] and not path.exists()
        path.write_text(json.dumps(diagnostic,indent=2)+'\n')
    print(json.dumps({'negative_observed_comparisons':len(negative),'selection_stability':stability}))


if __name__=='__main__':main()
