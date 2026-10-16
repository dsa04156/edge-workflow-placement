"""Collect paired inputs on three genuinely new sets of worker/client Pods."""

from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time

base=Path('artifacts/e3-input-replication');run=json.loads((base/'run.json').read_text())
cmd=['rtk','proxy','kubectl','--request-timeout=20s','-n',run['namespace']]
def call(*args):return subprocess.check_output([*cmd,*args],text=True)
def write(name,data):
    p=base/name;assert not p.exists(),f'Preserve attempt {name}'
    p.write_text(data if isinstance(data,str) else json.dumps(data,indent=2)+'\n')
def records(raw):return [json.loads(l) for l in raw.splitlines() if l.startswith('{')]
def snapshot():
    pods=json.loads(subprocess.check_output(['rtk','proxy','kubectl','get','pods','-A','-o','json'],text=True))['items']
    names={p['name'] for p in json.loads(Path('artifacts/e2-dynamic/preexisting-after.json').read_text())}
    return [{'name':p['metadata']['name'],'namespace':p['metadata']['namespace'],'node':p['spec'].get('nodeName'),
             'phase':p['status']['phase'],'ready':next((c['status'] for c in p['status'].get('conditions',[]) if c['type']=='Ready'),None)}
            for p in pods if p['metadata']['name'] in names]
write('preexisting-before.json',snapshot())
subprocess.run([*cmd,'apply','-f',str(base/'cache.json')],check=True)
subprocess.run([*cmd,'wait','--for=condition=Ready','pod/'+run['cache'],'--timeout=90s'],check=True)
process=subprocess.Popen([*cmd,'exec','-i',run['cache'],'--','python3','-c',
    "import sys,tarfile;tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall('/work',filter='data')"],stdin=subprocess.PIPE)
with tarfile.open(fileobj=process.stdin,mode='w|') as archive:
    for path in (base/'wheelhouse').iterdir():archive.add(path,arcname=path.name)
    for path in (base/'assets').iterdir():archive.add(path,arcname=path.name)
process.stdin.close();assert process.wait(timeout=60)==0
subprocess.run([*cmd,'create','-f',str(base/'configmap.json')],check=True)
identities=[]
for deployment in run['deployments']:
    block=deployment['block'];items=json.loads((base/deployment['manifest']).read_text())['items']
    subprocess.run([*cmd,'apply','-f',str(base/deployment['manifest'])],check=True)
    deadline=time.monotonic()+240;ready=set()
    while len(ready)!=2:
        for alias,name in zip(deployment['targets'],deployment['worker_pods']):
            pod=json.loads(call('get','pod',name,'-o','json'))
            if pod['status']['phase']=='Pending':continue
            raw=call('logs',name);(base/f'worker-b{block}-{alias}.log').write_text(raw)
            assert pod['status']['phase']=='Running',f'Worker exited: {name}'
            if any(r['kind']=='server_ready' for r in records(raw)):ready.add(alias)
        print(json.dumps({'kind':'preparation','block':block,'ready':sorted(ready)}),flush=True)
        if len(ready)!=2:
            assert time.monotonic()<deadline,'Model preparation timeout'
            time.sleep(5)
    for name in deployment['client_pods']:
        subprocess.run([*cmd,'wait','--for=condition=Ready','pod/'+name,'--timeout=60s'],check=True)
    for source in deployment['source_order']:
        spec={'block':block,'run_id':run['run_id'],'source':source,'conditions':deployment['schedules'][source]}
        stem=f'evaluation-b{block}-{source}';write(stem+'.input.json',spec)
        pod=deployment['client_pods'][list(deployment['targets']).index(source)]
        p=subprocess.run([*cmd,'exec','-i',pod,'--','python3','-u','-c',
            "import sys,runpy;sys.path.insert(0,'/config');runpy.run_path('/config/corpus_client.py',run_name='__main__')"],
            input=json.dumps(spec),capture_output=True,text=True,timeout=180)
        write(stem+'.log',p.stdout);write(stem+'.stderr',p.stderr)
        assert p.returncode==0,f'Failed attempt: {stem}'
        rows=records(p.stdout);assert rows[-1]['kind']=='client_complete' and rows[-1]['requests']==224
        print(json.dumps({'kind':'source_complete','block':block,'source':source,'requests':224}),flush=True)
    for alias,name in zip(deployment['targets'],deployment['worker_pods']):
        (base/f'worker-b{block}-{alias}.log').write_text(call('logs',name))
    status=json.loads(call('get','pods','-l','research.scheduler/run='+run['run_id']+',research.scheduler/deployment='+str(block),'-o','json'))
    write(f'pods-b{block}.json',status)
    for pod in status['items']:
        assert pod['status']['phase']=='Running'
        assert all(s['ready'] and s['restartCount']==0 for s in pod['status']['containerStatuses'])
        identities.append({'block':block,'name':pod['metadata']['name'],'uid':pod['metadata']['uid'],
                           'node':pod['spec']['nodeName'],'container_ids':[s['containerID'] for s in pod['status']['containerStatuses']]})
    for item in items:
        subprocess.run([*cmd,'delete',item['kind'].lower(),item['metadata']['name'],'--wait=false'],check=True,stdout=subprocess.DEVNULL)
    deadline=time.monotonic()+90
    while json.loads(call('get','pods','-l','research.scheduler/run='+run['run_id']+',research.scheduler/deployment='+str(block),'-o','json'))['items']:
        assert time.monotonic()<deadline,'Previous deployment still terminating; no force deletion'
        time.sleep(2)
    print(json.dumps({'kind':'deployment_complete_and_removed','block':block}),flush=True)
write('pod-identities.json',identities)
cm=json.loads(call('get','configmap',run['configmap'],'-o','json'))
assert {n:hashlib.sha256(cm['data'][n].encode()).hexdigest() for n in run['source_sha256']}==run['source_sha256']
write('live-source-verification.json',{'matches_frozen':True,'resource_version':cm['metadata']['resourceVersion']})
write('preexisting-after.json',snapshot())
write('collection-complete.json',{'completed_at':datetime.now(timezone.utc).isoformat(),'total_workflows':1344,'measured_workflows':960})
print(json.dumps({'kind':'collection_complete','workflows':1344,'measured':960}),flush=True)
