"""Freeze 3 fresh worker deployments and 8-input paired anchor schedules."""

from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime,timezone
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import shutil
import urllib.request
import pandas as pd

base=Path('artifacts/e3-input-replication');assert not (base/'run.json').exists()
corpus=json.loads((base/'corpus.json').read_text());assert len(corpus)==8
loader=importlib.util.spec_from_file_location('allocation','/home/jinuk/.codex/skills/experimental-design/scripts/randomization.py')
allocation=importlib.util.module_from_spec(loader);loader.loader.exec_module(allocation)
old_run=json.loads(Path('artifacts/e2-dynamic/run.json').read_text())
old_items=json.loads(Path('artifacts/e2-dynamic/workers.json').read_text())['items']
run_id=datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%S');prefix='e3-repl-'+run_id;namespace='scheduler-research'
labels={'app.kubernetes.io/part-of':'scheduler-research','research.scheduler/stage':'e3-replication','research.scheduler/run':run_id}
meta=lambda name:{'name':name,'namespace':namespace,'labels':copy.deepcopy(labels)}
cache_name=prefix+'-cache';host=cache_name+'.'+namespace+'.svc.cluster.local';endpoint='http://'+host+':8080'
cache=json.loads(Path('artifacts/e2-dynamic/cache.json').read_text())['items']
for item in cache:
    item['metadata']=meta(cache_name);item['metadata']['labels']['research.scheduler/worker']='cache'
    if item['kind']=='Pod':item['spec']['activeDeadlineSeconds']=3600
    else:item['spec']['selector']=copy.deepcopy(cache[0]['metadata']['labels'])
cm=copy.deepcopy(old_items[0]);cm['metadata']=meta(prefix)
cm['immutable']=True
for filename in ['dag_profile.py','corpus_client.py']:
    cm['data'][filename]=Path('experiments/e0',filename).read_text()
cm['data']['corpus.json']=(base/'corpus.json').read_text()
deployments=[]
old_endpoint=next(e['value'] for i in old_items if i['kind']=='Pod' for e in i['spec']['containers'][0]['env'] if e['name']=='ASSET_BASE')
old_host=old_endpoint.split('://')[1].split(':')[0]
conditions=[{'id':f'{image}-{source}-{anchor}-{state}','source':source,'image_id':image,'anchor':anchor,'state':state,'plan':[anchor]*3}
            for image,source,anchor,state in itertools.product(corpus,['arm','intel_npu'],['arm','intel_npu'],['warm','eviction'])]
for block in range(3):
    block_prefix=prefix+f'-b{block}'
    peers=copy.deepcopy(old_run['targets'])
    for alias,p in peers.items():p['url']=f'http://{block_prefix}-{alias.replace("_","-")}.{namespace}.svc.cluster.local:8080'
    items=[];worker_pods=[];client_pods=[]
    for alias in peers:
        for role in ['worker','client']:
            template_name=old_run['worker_pods'][list(peers).index(alias)] if role=='worker' else old_run['client_pods'][list(peers).index(alias)]
            pod=copy.deepcopy(next(i for i in old_items if i['kind']=='Pod' and i['metadata']['name']==template_name))
            name=block_prefix+'-'+alias.replace('_','-')+('-client' if role=='client' else '')
            pod['metadata']=meta(name);pod['metadata']['labels'].update({'research.scheduler/deployment':str(block),'research.scheduler/role':role,'research.scheduler/alias':alias})
            s=pod['spec'];s['activeDeadlineSeconds']=900
            for volume in s['volumes']:
                if 'configMap' in volume:volume['configMap']['name']=prefix
            c=s['containers'][0]
            if role=='worker':
                c['command'][-1]=c['command'][-1].replace(old_endpoint,endpoint).replace(old_host,host)
                worker_pods.append(name)
            else:client_pods.append(name)
            for env in c['env']:
                if env['name']=='PEERS_JSON':env['value']=json.dumps(peers)
                elif env['name']=='ASSET_BASE':env['value']=endpoint
                elif env['name'] in ['NO_PROXY','no_proxy']:env['value']=host
            c['env'].append({'name':'CORPUS_FILE','value':'/config/corpus.json'})
            items.append(pod)
            if role=='worker':items.append({'apiVersion':'v1','kind':'Service','metadata':meta(name),
                'spec':{'selector':pod['metadata']['labels'],'ports':[{'port':8080,'targetPort':8080}]}})
    schedule=allocation.assign_factorial_runs(pd.DataFrame(conditions),seed=20261021+block).to_dict('records')
    sources=allocation.assign_factorial_runs(pd.DataFrame([{'source':'arm'},{'source':'intel_npu'}]),seed=20261025+block)['source'].tolist()
    filename=f'deployment-{block}.json';(base/filename).write_text(json.dumps({'apiVersion':'v1','kind':'List','items':items},indent=2)+'\n')
    deployments.append({'block':block,'manifest':filename,'worker_pods':worker_pods,'client_pods':client_pods,'targets':peers,'source_order':sources,
                        'schedules':{source:[c for c in schedule if c['source']==source] for source in sources}})
manifest=json.loads(Path('artifacts/e2-dynamic/wheel-manifest.json').read_text());(base/'wheelhouse').mkdir(exist_ok=True)
def download(w):
    path=base/'wheelhouse'/w['filename']
    with urllib.request.urlopen(w['url'],timeout=60) as response,path.open('wb') as out:shutil.copyfileobj(response,out)
    with path.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==w['sha256']
    assert path.stat().st_size==w['size']
with ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(download,manifest))
run={'run_id':run_id,'namespace':namespace,'prefix':prefix,'cache':cache_name,'configmap':prefix,'deployments':deployments,
     'design_frozen_at':datetime.now(timezone.utc).isoformat(),'total_workflows':1344,'measured_workflows':960,
     'corpus_sha256':hashlib.sha256((base/'corpus.json').read_bytes()).hexdigest(),
     'source_sha256':{name:hashlib.sha256(data.encode()).hexdigest() for name,data in cm['data'].items() if name.endswith('.py')},
     'frozen_E2_selection_sha256':hashlib.sha256(Path('artifacts/e2-dynamic/selection.json').read_bytes()).hexdigest()}
for filename,data in [('cache.json',{'apiVersion':'v1','kind':'List','items':cache}),('configmap.json',cm),('wheel-manifest.json',manifest),('run.json',run)]:
    (base/filename).write_text(json.dumps(data,indent=2)+'\n')
print(json.dumps({'run_id':run_id,'total_workflows':1344,'measured_workflows':960,'wheel_hashes_verified':len(manifest)}))
