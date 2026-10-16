"""Collect calibration, freeze all policies, then run fresh evaluation blocks."""

import argparse
from collections import defaultdict
import copy
from datetime import datetime,timezone
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import statistics
import subprocess
import time
import pandas as pd

parser=argparse.ArgumentParser();parser.add_argument('--randomization',required=True,type=Path);args=parser.parse_args()
spec=importlib.util.spec_from_file_location('allocation',args.randomization);allocation=importlib.util.module_from_spec(spec);spec.loader.exec_module(allocation)
base=Path('artifacts/e1-dag');run=json.loads((base/'run.json').read_text())
cmd=['rtk','proxy','kubectl','-n',run['namespace']]

def call(*args):return subprocess.check_output([*cmd,*args,'--request-timeout=15s'],text=True)
def records(raw):return [json.loads(line) for line in raw.splitlines() if line.startswith('{')]

deadline=time.monotonic()+400;ready=set()
while len(ready)!=4:
    assert time.monotonic()<deadline,'Model preparation timeout'
    for target,name in zip(run['targets'],run['worker_pods']):
        pod=json.loads(call('get','pod',name,'-o','json'))
        if pod['status']['phase']=='Pending':continue
        raw=call('logs',name);(base/('worker-'+target['alias']+'.log')).write_text(raw)
        if any(r['kind']=='server_ready' for r in records(raw)):ready.add(target['alias'])
        assert pod['status']['phase']=='Running',f'Worker exited: {name}'
    print(json.dumps({'kind':'model_preparation','ready':sorted(ready)}),flush=True)
    if len(ready)!=4:time.sleep(10)

def collect(filename,name,output,expected):
    assert not (base/output).exists(),'Do not overwrite an attempted measurement'
    subprocess.run([*cmd,'apply','-f',str(base/filename)],check=True)
    deadline=time.monotonic()+660
    while time.monotonic()<deadline:
        job=json.loads(call('get','job',name,'-o','json'))
        if job['status'].get('failed') or job['status'].get('succeeded'):
            raw=call('logs','job/'+name);(base/output).write_text(raw)
            assert job['status'].get('succeeded')==1,f'Measurement failed; see {output}'
            rs=records(raw);assert rs[-1]['kind']=='client_complete' and rs[-1]['requests']==expected
            print(json.dumps({'kind':'measurement_complete','file':output,'requests':expected}),flush=True)
            return rs
        time.sleep(10)
    raise TimeoutError(name)

calibration=collect('calibration.json',run['client_jobs'][0],'calibration.log',448)
rows=[r for r in calibration if r['kind']=='workflow' and not r['warmup']]
assert len(rows)==320
aliases=sorted(t['alias'] for t in run['targets'])
computes=defaultdict(list);ingress=defaultdict(list);edges=[defaultdict(list),defaultdict(list)]
started=time.perf_counter()
for row in rows:
    trace=row['trace'];plan=row['plan']
    for i,t in enumerate(trace):computes[(i,plan[i])].append(t['task_ms'])
    ingress[plan[0]].append(row['workflow_ms']-trace[0]['task_ms']-trace[0]['rpc_next_ms'])
    edges[0][(plan[0],plan[1])].append(trace[0]['rpc_next_ms']-trace[1]['task_ms']-trace[1]['rpc_next_ms'])
    edges[1][(plan[1],plan[2])].append(trace[1]['rpc_next_ms']-trace[2]['task_ms'])
compute={key:statistics.median(v) for key,v in computes.items()}
incoming={key:statistics.median(v) for key,v in ingress.items()}
edge=[{key:statistics.median(v) for key,v in group.items()} for group in edges]
assert all(v>=0 for v in [*compute.values(),*incoming.values(),*edge[0].values(),*edge[1].values()])
def score(plan):return incoming[plan[0]]+sum(compute[(i,plan[i])] for i in range(3))+sum(edge[i][(plan[i],plan[i+1])] for i in range(2))
fastest=[min(aliases,key=lambda a:(compute[(i,a)],a)) for i in range(3)]
greedy=[min(aliases,key=lambda a:(incoming[a]+compute[(0,a)],a))]
for i in [1,2]:greedy.append(min(aliases,key=lambda a:(edge[i-1][(greedy[-1],a)]+compute[(i,a)],a)))
all_plans=list(itertools.product(aliases,repeat=3))
joint=list(min(all_plans,key=lambda plan:(score(plan),plan)))
policies={'B2_fastest':fastest,'C1_data_greedy':greedy,'B3_joint_enumeration':joint,
          'anchor_all_arm':['arm']*3,'anchor_all_gpu':['gpu']*3}
selection={'frozen_at':datetime.now(timezone.utc).isoformat(),'calibration_log_sha256':hashlib.sha256((base/'calibration.log').read_bytes()).hexdigest(),
           'policies':policies,'predicted_ms':{name:score(plan) for name,plan in policies.items()},
           'profile_aggregation_and_selection_ms':(time.perf_counter()-started)*1000,
           'task_ms':{f'{i}|{a}':v for (i,a),v in compute.items()},'ingress_residual_ms':incoming,
           'edge_residual_ms':[{f'{a}|{b}':v for (a,b),v in e.items()} for e in edge],
           'scope':'common median component table; exact enumeration of 64 static assignments; not a published DAG policy reproduction'}
assert not (base/'selection.json').exists()
(base/'selection.json').write_text(json.dumps(selection,indent=2)+'\n')
print(json.dumps({'kind':'selection_frozen','policies':policies}),flush=True)
unique={}
for policy,plan in policies.items():
    key=tuple(plan)
    unique.setdefault(key,[]).append(policy)
conditions=[{'id':'-'.join(plan),'plan':list(plan),'policies':names} for plan,names in unique.items()]
template=json.loads((base/'calibration.json').read_text())
for block in range(3):
    schedule=allocation.assign_factorial_runs(pd.DataFrame(conditions),seed=20261117+block).to_dict('records')
    job=copy.deepcopy(template);name=run['configmap']+f'-eval-{block}';job['metadata']['name']=name
    c=job['spec']['template']['spec']['containers'][0]
    values={'BLOCK':f'eval-{block}','SCHEDULE_JSON':json.dumps(schedule),'MEASURED':'10'}
    for env in c['env']:
        if env['name'] in values:env['value']=values[env['name']]
    filename=f'evaluation-{block}.json'
    (base/filename).write_text(json.dumps(job,indent=2)+'\n')
    run['client_jobs'].append(name);(base/'run.json').write_text(json.dumps(run,indent=2)+'\n')
    collect(filename,name,f'evaluation-{block}.log',len(schedule)*12)
for target,name in zip(run['targets'],run['worker_pods']):
    (base/('worker-'+target['alias']+'.log')).write_text(call('logs',name))
for kind in ['pods','jobs','services','endpointslices']:
    (base/(kind+'-final.json')).write_text(call('get',kind,'-l','research.scheduler/run='+run['run_id'],'-o','json'))
print(json.dumps({'kind':'all_dag_measurements_complete','unique_evaluation_plans':len(unique)}),flush=True)
