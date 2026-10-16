"""Freeze E2 artifacts and restore only checksum-pinned CPU/NPU SDK wheels."""

from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import urllib.request
import pandas as pd

base=Path('artifacts/e2-dynamic');base.mkdir(exist_ok=True)
assert not (base/'run.json').exists(),'Never overwrite a run'
spec=importlib.util.spec_from_file_location('allocation','/home/jinuk/.codex/skills/experimental-design/scripts/randomization.py')
allocation=importlib.util.module_from_spec(spec);spec.loader.exec_module(allocation)
old=json.loads(Path('artifacts/e1-dag/workers.json').read_text())['items']
run_id=datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%S');prefix='e2-dynamic-'+run_id
namespace='scheduler-research'
labels={'app.kubernetes.io/part-of':'scheduler-research','research.scheduler/stage':'e2-dynamic','research.scheduler/run':run_id}
meta=lambda name:{'name':name,'namespace':namespace,'labels':copy.deepcopy(labels)}
cache_name=prefix+'-cache';cache_host=cache_name+'.'+namespace+'.svc.cluster.local';asset_base='http://'+cache_host+':8080'
cache=json.loads(Path('artifacts/e0-rpc-profile/wheel-cache.json').read_text())['items']
for item in cache:
    item['metadata']=meta(cache_name)
    item['metadata']['labels']['research.scheduler/worker']='cache'
    if item['kind']=='Pod':
        item['spec']['activeDeadlineSeconds']=3600
        c=item['spec']['containers'][0]
        c['resources']['requests']['ephemeral-storage']='256Mi'
        c['resources']['limits']['ephemeral-storage']='1Gi'
        item['spec']['volumes'][0]['emptyDir']['sizeLimit']='768Mi'
    else:item['spec']['selector']=copy.deepcopy(cache[0]['metadata']['labels'])
cm=copy.deepcopy(old[0]);cm['metadata']=meta(prefix)
for name in ['probe.py','transport.py','dag_profile.py','model_lifecycle.py','dynamic_client.py']:
    cm['data'][name]=Path('experiments/e0',name).read_text()
peers={}
for alias in ['arm','intel_npu']:
    template=next(x for x in old if x['kind']=='Pod' and x['metadata']['name'].endswith('-'+alias.replace('_','-')))
    peers[alias]={'alias':alias,'backend':'cpu' if alias=='arm' else alias,
                  'node':template['spec']['nodeSelector']['kubernetes.io/hostname'],
                  'url':f'http://{prefix}-{alias.replace("_","-")}.{namespace}.svc.cluster.local:8080'}
items=[cm];workers=[];clients=[]
for alias,peer in peers.items():
    pod=copy.deepcopy(next(x for x in old if x['kind']=='Pod' and x['metadata']['name'].endswith('-'+alias.replace('_','-'))))
    name=prefix+'-'+alias.replace('_','-');workers.append(name)
    pod['metadata']=meta(name);pod['metadata']['labels']['research.scheduler/worker']=alias
    pod['metadata']['annotations']={'sidecar.istio.io/inject':'false'}
    s=pod['spec'];s['activeDeadlineSeconds']=3600
    for volume in s['volumes']:
        if 'configMap' in volume:volume['configMap']['name']=prefix
    c=s['containers'][0]
    original_code=c['command'][-1]
    old_base=next(v['value'] for v in c['env'] if v['name']=='ASSET_BASE')
    c['command'][-1]=original_code.replace(old_base,asset_base).replace(old_base.split('://')[1].split(':')[0],cache_host)
    for env in c['env']:
        if env['name']=='PEERS_JSON':env['value']=json.dumps(peers)
        if env['name']=='ASSET_BASE':env['value']=asset_base
        if env['name'] in ['NO_PROXY','no_proxy']:env['value']=cache_host
    c['env'].append({'name':'DYNAMIC_LIFECYCLE','value':'1'})
    items+=[pod,{'apiVersion':'v1','kind':'Service','metadata':meta(name),
                  'spec':{'selector':pod['metadata']['labels'],'ports':[{'port':8080,'targetPort':8080}]}}]
    client=copy.deepcopy(pod);client_name=name+'-client';clients.append(client_name)
    client['metadata']=meta(client_name);client['metadata']['labels']['research.scheduler/client']=alias
    s=client['spec'];s['securityContext']={'runAsNonRoot':True,'runAsUser':65532,'runAsGroup':65532,'fsGroup':65532,'seccompProfile':{'type':'RuntimeDefault'}}
    c=s['containers'][0]
    c['image']=cache[0]['spec']['containers'][0]['image']
    c['command']=['python3','-u','-c','import time;time.sleep(3500)']
    c['resources']={'requests':{'cpu':'100m','memory':'64Mi','ephemeral-storage':'64Mi'},
                    'limits':{'cpu':'1','memory':'256Mi','ephemeral-storage':'256Mi'}}
    s['volumes'][1]['emptyDir']['sizeLimit']='128Mi'
    s['volumes'][2]['emptyDir']['sizeLimit']='64Mi'
    items.append(client)
entries=json.loads(Path('artifacts/e0-rpc-profile/wheel-manifest.json').read_text())+json.loads(Path('artifacts/e1-dag/additional-wheel-manifest.json').read_text())
needed=set()
for key in ['requirements-cpu-cache.txt','requirements-npu-cache.txt']:
    for line in cm['data'][key].splitlines():needed.add(line.split('--hash=sha256:')[1])
wheels=[e for e in entries if e['sha256'] in needed]
assert {e['sha256'] for e in wheels}==needed
(base/'wheelhouse').mkdir()
def download(w):
    path=base/'wheelhouse'/w['filename']
    with urllib.request.urlopen(w['url'],timeout=90) as response,path.open('wb') as out:shutil.copyfileobj(response,out)
    assert path.stat().st_size==w['size']
    with path.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==w['sha256']
    return w['filename']
with ThreadPoolExecutor(max_workers=6) as pool:
    for name in pool.map(download,wheels):print(json.dumps({'verified_wheel':name}),flush=True)
for name in ['model.onnx','dog.jpg']:shutil.copyfile(Path('artifacts/e1-dag/additional-assets')/name,base/'wheelhouse'/name)
conditions=[{'id':','.join(p),'plan':list(p)} for p in __import__('itertools').product(peers,repeat=3)]
calibration={source:allocation.assign_factorial_runs(pd.DataFrame(conditions),seed=20261018+i).to_dict('records') for i,source in enumerate(peers)}
trials=[{'scenario':scenario,'policy':policy} for scenario in ['warm','eviction']
        for policy in ['fixed','warm_dag','ready_dag','ready_greedy','credit_hysteresis']]
design={'source_phases':[['arm',8],['intel_npu',1],['arm',4],['intel_npu',12],['arm',4],['intel_npu',1],['arm',4],['intel_npu',12]],
        'blocks':[allocation.assign_factorial_runs(pd.DataFrame(trials),seed=20261019+b).to_dict('records') for b in range(3)],
        'calibration':calibration,'horizon_requests':4,'cycle_s':0.25,'preparation_calibration_requests':5,
        'evaluation_requests':1380,'spec':'.scratch/e2-dynamic/spec.md'}
run={'run_id':run_id,'namespace':namespace,'configmap':prefix,'cache':cache_name,'worker_pods':workers,'client_pods':clients,
     'targets':peers,'design':design,'frozen_at':datetime.now(timezone.utc).isoformat(),
     'source_sha256':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in cm['data'].items() if k.endswith('.py')}}
for filename,data in [('run.json',run),('design.json',design),('cache.json',{'apiVersion':'v1','kind':'List','items':cache}),
                       ('workers.json',{'apiVersion':'v1','kind':'List','items':items}),('wheel-manifest.json',wheels)]:
    (base/filename).write_text(json.dumps(data,indent=2)+'\n')
print(json.dumps({'kind':'design_frozen','run_id':run_id,'wheels':len(wheels),'evaluation_requests':1380}),flush=True)
