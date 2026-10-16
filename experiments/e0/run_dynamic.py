"""Execute frozen E2 design, preserving every attempted phase and receipt."""

from collections import defaultdict
from datetime import datetime,timezone
import hashlib
import itertools
import json
from pathlib import Path
import statistics
import subprocess
import tarfile
import time

from dynamic_client import warm_score

base=Path('artifacts/e2-dynamic');run=json.loads((base/'run.json').read_text())
cmd=['rtk','proxy','kubectl','--request-timeout=20s','-n',run['namespace']]
def call(*args):return subprocess.check_output([*cmd,*args],text=True)
def records(raw):return [json.loads(line) for line in raw.splitlines() if line.startswith('{')]
def write(name,data):
    path=base/name;assert not path.exists(),f'Preserve existing attempt: {name}'
    path.write_text(data if isinstance(data,str) else json.dumps(data,indent=2)+'\n')

def snapshot():
    pods=json.loads(subprocess.check_output(['rtk','proxy','kubectl','get','pods','-A','-o','json'],text=True))['items']
    names={'ai-scientist-955f7d5f7-6hfcl','ra-infer-cuda-v1-68b55f98fb-t7cg5','ra-infer-hailo-v1-5cb5f6685d-24brl'}
    return [{'namespace':p['metadata']['namespace'],'name':p['metadata']['name'],'node':p['spec'].get('nodeName'),
             'phase':p['status']['phase'],'ready':next((c['status'] for c in p['status'].get('conditions',[]) if c['type']=='Ready'),None)}
            for p in pods if p['metadata']['name'] in names]

write('preexisting-before.json',snapshot())
subprocess.run([*cmd,'apply','-f',str(base/'cache.json')],check=True)
cache=run['cache'];deadline=time.monotonic()+90
while True:
    pod=json.loads(call('get','pod',cache,'-o','json'))
    if pod['status']['phase']=='Running':break
    assert time.monotonic()<deadline,'Cache startup timeout'
    time.sleep(3)
process=subprocess.Popen([*cmd,'exec','-i',cache,'--','python3','-c',
                         "import sys,tarfile;tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall('/work',filter='data')"],stdin=subprocess.PIPE)
with tarfile.open(fileobj=process.stdin,mode='w|') as archive:
    for path in sorted((base/'wheelhouse').iterdir()):archive.add(path,arcname=path.name)
process.stdin.close();assert process.wait(timeout=60)==0
subprocess.run([*cmd,'apply','-f',str(base/'workers.json')],check=True)
deadline=time.monotonic()+240;ready=set()
while len(ready)!=2:
    for alias,name in zip(run['targets'],run['worker_pods']):
        pod=json.loads(call('get','pod',name,'-o','json'))
        if pod['status']['phase']=='Pending':continue
        raw=call('logs',name);(base/f'worker-{alias}.log').write_text(raw)
        assert pod['status']['phase']=='Running',f'Worker exited: {alias}'
        if any(r['kind']=='server_ready' for r in records(raw)):ready.add(alias)
    print(json.dumps({'kind':'model_preparation','ready':sorted(ready)}),flush=True)
    if len(ready)!=2:
        assert time.monotonic()<deadline,'Worker model startup timeout'
        time.sleep(5)
for name in run['client_pods']:
    subprocess.run([*cmd,'wait','--for=condition=Ready','pod/'+name,'--timeout=60s'],check=True)

def phase(filename,source,data):
    write(filename+'.input.json',data)
    pod=run['client_pods'][list(run['targets']).index(source)]
    p=subprocess.run([*cmd,'exec','-i',pod,'--','python3','-u','-c',
                      "import sys,runpy;sys.path.insert(0,'/config');runpy.run_path('/config/dynamic_client.py',run_name='__main__')"],
                     input=json.dumps(data),capture_output=True,text=True,timeout=60)
    write(filename+'.log',p.stdout);write(filename+'.stderr',p.stderr)
    assert p.returncode==0,f'Failed attempt preserved: {filename}'
    rs=records(p.stdout)
    assert rs[-1]['kind']=='phase_complete',filename
    return rs

calibration=[]
for source,conditions in run['design']['calibration'].items():
    rs=phase('calibration-'+source,source,{'mode':'calibration','prefix':run['run_id']+'-cal-'+source,
            'source':source,'conditions':conditions,'prewarm':True})
    calibration.extend(r for r in rs if r['kind']=='workflow' and not r['warmup'])
preparation=[]
for i in range(5):
    rs=phase('preparation-'+str(i),'intel_npu',{'mode':'preparation','prefix':run['run_id']+'-prep-'+str(i),
             'source':'intel_npu','reset':True})
    row=next(r for r in rs if r['kind']=='workflow')
    assert row['trace'][1]['device']['was_cold']
    preparation.append(row['trace'][1]['device']['preparation_ms'])

profiles={}
for source in run['targets']:
    rows=[r for r in calibration if r['source']==source]
    tasks=defaultdict(list);incoming=defaultdict(list);edges=[defaultdict(list),defaultdict(list)]
    for row in rows:
        trace=row['trace'];plan=row['plan']
        for i,t in enumerate(trace):tasks[f'{i}|{plan[i]}'].append(t['task_ms'])
        incoming[plan[0]].append(row['workflow_ms']-trace[0]['task_ms']-trace[0]['rpc_next_ms'])
        edges[0][f'{plan[0]}|{plan[1]}'].append(trace[0]['rpc_next_ms']-trace[1]['task_ms']-trace[1]['rpc_next_ms'])
        edges[1][f'{plan[1]}|{plan[2]}'].append(trace[1]['rpc_next_ms']-trace[2]['task_ms'])
    median=lambda group:{k:statistics.median(v) for k,v in group.items()}
    profiles[source]={'task':median(tasks),'ingress':median(incoming),'edges':[median(e) for e in edges]}
    assert all(v>=0 for group in [profiles[source]['task'],profiles[source]['ingress'],*profiles[source]['edges']] for v in group.values())
plans=list(itertools.product(sorted(run['targets']),repeat=3))
selection={'frozen_at':datetime.now(timezone.utc).isoformat(),'profiles':profiles,
           'preparation_ms':statistics.median(preparation),'preparation_calibration_ms':preparation,
           'initial_plan':list(min(plans,key=lambda p:(warm_score(profiles['arm'],p),p))),
           'best_warm':{source:list(min(plans,key=lambda p:(warm_score(prof,p),p))) for source,prof in profiles.items()},
           'calibration_log_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in base.glob('calibration-*.log')}}
write('selection.json',selection)
print(json.dumps({'kind':'selection_frozen','initial_plan':selection['initial_plan'],'best_warm':selection['best_warm'],
                  'preparation_ms':selection['preparation_ms']}),flush=True)
for block,trials in enumerate(run['design']['blocks']):
    for trial_index,trial in enumerate(trials):
        state={}
        for index,(source,count) in enumerate(run['design']['source_phases']):
            data={'mode':'evaluation','prefix':f"{run['run_id']}-b{block}-t{trial_index}-p{index}",
                  'source':source,'requests':count,'policy':trial['policy'],'scenario':trial['scenario'],
                  'block':block,'phase':index,'state':state,'selection':selection,
                  'reset':trial['scenario']=='eviction' and (index==0 or source=='intel_npu'),
                  'prewarm':trial['scenario']=='warm' and index==0}
            rs=phase(f'evaluation-b{block}-t{trial_index}-p{index}',source,data)
            state=rs[-1]['state']
        print(json.dumps({'kind':'trajectory_complete','block':block,**trial,'requests':46}),flush=True)
for alias,name in zip(run['targets'],run['worker_pods']):
    (base/f'worker-{alias}.log').write_text(call('logs',name))
for kind in ['pods','services','endpointslices']:
    write(kind+'-final.json',json.loads(call('get',kind,'-l','research.scheduler/run='+run['run_id'],'-o','json')))
write('preexisting-after.json',snapshot())
write('collection-complete.json',{'completed_at':datetime.now(timezone.utc).isoformat(),'evaluation_requests':1380})
print(json.dumps({'kind':'collection_complete','evaluation_requests':1380}),flush=True)
