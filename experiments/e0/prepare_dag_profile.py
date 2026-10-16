"""Freeze the 64-placement calibration and identical workers before collection."""

import argparse
import copy
from datetime import datetime,timezone
import importlib.util
import itertools
import json
from pathlib import Path
import pandas as pd

parser=argparse.ArgumentParser();parser.add_argument('--randomization',required=True,type=Path);args=parser.parse_args()
spec=importlib.util.spec_from_file_location('allocation',args.randomization);allocation=importlib.util.module_from_spec(spec);spec.loader.exec_module(allocation)
base=Path('artifacts/e1-dag');base.mkdir(exist_ok=True);assert not (base/'run.json').exists()
old=Path('artifacts/e0-rpc-profile');previous=json.loads((old/'run.json').read_text())
run_id=datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%S');prefix='e1-dag-'+run_id;namespace='scheduler-research'
labels={'app.kubernetes.io/part-of':'scheduler-research','research.scheduler/stage':'e1-dag','research.scheduler/run':run_id}
meta=lambda name:{'name':name,'namespace':namespace,'labels':labels}
targets=copy.deepcopy(previous['targets'])
for t in targets:t['url']=f"http://{prefix}-{t['alias'].replace('_','-')}.{namespace}.svc.cluster.local:8080"
peers={t['alias']:t for t in targets};cache=previous['auxiliary_services'][0]
asset_base=f'http://{cache}.{namespace}.svc.cluster.local:8080';cachehost=f'{cache}.{namespace}.svc.cluster.local'
cm=copy.deepcopy(json.loads((old/'gpu-cached-attempt.json').read_text())['items'][0]);cm['metadata']=meta(prefix)
cm['data']['dag_profile.py']=Path('experiments/e0/dag_profile.py').read_text()
entries=json.loads((old/'wheel-manifest.json').read_text());extra=json.loads((base/'additional-wheel-manifest.json').read_text())
norm=lambda s:s.lower().replace('_','-')
pure={norm(w['name']):w for w in entries if w['filename'].endswith('any.whl')}
cpu_amd={**pure,**{norm(w['name']):w for w in entries if 'x86_64' in w['filename']},
         **{norm(w['name']):w for w in extra if norm(w['name'])=='protobuf'}}
npu={**pure,**{norm(w['name']):w for w in extra}}
requirement=lambda w:f"{w['name']}=={w['version']} --hash=sha256:{w['sha256']}"
cm['data']['requirements-cpu-amd-cache.txt']='\n'.join(requirement(cpu_amd[norm(line.split('==')[0])]) for line in cm['data']['requirements-cpu.txt'].splitlines())+'\n'
npu_tokens=Path('artifacts/e0-accelerators/intel_npu-installed.txt').read_text().splitlines()
cm['data']['requirements-npu-cache.txt']='\n'.join(requirement(npu[norm(token.rsplit('-',1)[0])]) for token in npu_tokens)+'\n'
templates=json.loads((old/'workers.json').read_text())['items']
items=[cm];pods=[];services=[]
for t in targets:
    if t['alias']=='gpu':pod=copy.deepcopy(json.loads((old/'gpu-cached-attempt.json').read_text())['items'][1])
    else:pod=copy.deepcopy(next(p for p in templates if p['kind']=='Pod' and p['metadata']['name'].endswith('-'+t['alias'].replace('_','-'))))
    name=prefix+'-'+t['alias'].replace('_','-');pods.append(name);services.append(name)
    pod['metadata']=meta(name);pod['metadata']['labels']={**labels,'research.scheduler/worker':t['alias']}
    pod['metadata']['annotations']={'sidecar.istio.io/inject':'false'}
    s=pod['spec'];s['activeDeadlineSeconds']=1200
    for v in s['volumes']:
        if 'configMap' in v:v['configMap']['name']=prefix
    c=s['containers'][0]
    requirements={'arm':'cpu-cache','amd':'cpu-amd-cache','gpu':'gpu-cache','intel_npu':'npu-cache'}[t['alias']]
    c['command']=['python3','-u','-c',"import subprocess,sys,runpy;subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','--no-cache-dir','--no-compile','--retries','0','--timeout','60','--no-index','--find-links','"+asset_base+"','--trusted-host','"+cachehost+"','--require-hashes','--target','/work/site','-r','/config/requirements-"+requirements+".txt'],check=True);sys.path[:0]=['/work/site','/config'];runpy.run_path('/config/dag_profile.py',run_name='__main__')"]
    c['env']=[{'name':'NODE_NAME','valueFrom':{'fieldRef':{'fieldPath':'spec.nodeName'}}},
              *[{'name':k,'value':v} for k,v in {'MODE':'server','ALIAS':t['alias'],'BACKEND':t['backend'],
                'PEERS_JSON':json.dumps(peers),'ASSET_BASE':asset_base,'NO_PROXY':cachehost,'no_proxy':cachehost,
                'XDG_CACHE_HOME':'/work/cache','TORCH_HOME':'/work/torch-cache','PYTHONDONTWRITEBYTECODE':'1'}.items()]]
    items += [pod,{'apiVersion':'v1','kind':'Service','metadata':meta(name),
                   'spec':{'selector':pod['metadata']['labels'],'ports':[{'port':8080,'targetPort':8080}]}}]
plans=[{'id':'-'.join(plan),'plan':list(plan)} for plan in itertools.product(peers,repeat=3)]
schedule=allocation.assign_factorial_runs(pd.DataFrame(plans),seed=20261017).to_dict('records')
client=copy.deepcopy(json.loads((old/'client-0.json').read_text()))
client['metadata']=meta(prefix+'-calibration');client['spec']['activeDeadlineSeconds']=600
client['spec']['template']['metadata']={'labels':labels,'annotations':{'sidecar.istio.io/inject':'false'}}
s=client['spec']['template']['spec'];s['volumes'][0]['configMap']['name']=prefix
c=s['containers'][0];c['command']=['python3','-u','-c',"import sys,runpy;sys.path.insert(0,'/config');runpy.run_path('/config/dag_profile.py',run_name='__main__')"]
c['env']=[{'name':'NODE_NAME','valueFrom':{'fieldRef':{'fieldPath':'spec.nodeName'}}},
          *[{'name':k,'value':v} for k,v in {'MODE':'client','BLOCK':'calibration','RUN_ID':run_id,'PEERS_JSON':json.dumps(peers),
            'ASSET_BASE':asset_base,'NO_PROXY':cachehost,'no_proxy':cachehost,'SCHEDULE_JSON':json.dumps(schedule),'MEASURED':'5','PYTHONDONTWRITEBYTECODE':'1'}.items()]]
(base/'workers.json').write_text(json.dumps({'apiVersion':'v1','kind':'List','items':items},indent=2)+'\n')
(base/'calibration.json').write_text(json.dumps(client,indent=2)+'\n')
(base/'calibration-schedule.json').write_text(json.dumps(schedule,indent=2)+'\n')
run={'run_id':run_id,'namespace':namespace,'configmap':prefix,'worker_pods':pods,'services':services,'targets':targets,
     'client_jobs':[client['metadata']['name']],'shared_cache_run':previous['run_id'],'shared_cache_pod':cache,
     'design':{'calibration_placements':64,'warmup_per_condition':2,'calibration_measured':5,'evaluation_blocks':3,
               'evaluation_measured':10,'cycle_interval_s':0.25,'single_fixture':True,'workers_shared':True,
               'policy_information':'identical per-task and per-hop residual profiles from calibration'}}
(base/'run.json').write_text(json.dumps(run,indent=2)+'\n')
print(json.dumps({'run_id':run_id,'calibration_requests':448,'worker_pods':pods}))
