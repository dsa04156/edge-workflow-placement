"""Post-measurement readonly EdgeCore snapshot and exact owned cleanup."""

import copy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

base=Path('artifacts/e3-input-replication');run=json.loads((base/'run.json').read_text())
assert json.loads((base/'verification.json').read_text())['matching_stage_receipts']==4032
cmd=['rtk','proxy','kubectl','--request-timeout=20s','-n',run['namespace']]
pod=copy.deepcopy(json.loads(Path('artifacts/e1-dag/edge-snapshot-pod.json').read_text()))
pod['metadata']['name']=run['prefix']+'-edge-check'
pod['metadata']['labels']={'app.kubernetes.io/part-of':'scheduler-research','research.scheduler/stage':'e3-replication','research.scheduler/run':run['run_id']}
manifest={'apiVersion':'v1','kind':'List','items':[pod]}
path=base/'postcheck.json';assert not path.exists();path.write_text(json.dumps(manifest,indent=2)+'\n')
subprocess.run([*cmd,'apply','-f',str(path)],check=True)
deadline=time.monotonic()+65
while True:
    state=json.loads(subprocess.check_output([*cmd,'get','pod',pod['metadata']['name'],'-o','json'],text=True))
    if state['status']['phase'] in ['Succeeded','Failed']:break
    assert time.monotonic()<deadline,'Readonly check timeout'
    time.sleep(2)
assert state['status']['phase']=='Succeeded'
raw=subprocess.check_output([*cmd,'logs',pod['metadata']['name']],text=True)
(base/'edge-snapshot.json').write_text(raw)
items=[*json.loads((base/'cache.json').read_text())['items'],json.loads((base/'configmap.json').read_text()),pod]
for deployment in run['deployments']:items+=json.loads((base/deployment['manifest']).read_text())['items']
for item in items:
    assert item['metadata']['namespace']==run['namespace'] and item['metadata']['labels']['research.scheduler/run']==run['run_id']
    subprocess.run([*cmd,'delete',item['kind'].lower(),item['metadata']['name'],'--ignore-not-found=true','--wait=false'],check=True,stdout=subprocess.DEVNULL)
deadline=time.monotonic()+90
while json.loads(subprocess.check_output([*cmd,'get','pods,services,configmaps','-l','research.scheduler/run='+run['run_id'],'-o','json'],text=True))['items']:
    assert time.monotonic()<deadline,'Cleanup pending; no forced deletion'
    time.sleep(2)
removed=[]
for w in json.loads((base/'wheel-manifest.json').read_text()):
    path=base/'wheelhouse'/w['filename']
    with path.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==w['sha256']
    path.unlink();removed.append(w['filename'])
pods=json.loads(subprocess.check_output(['rtk','proxy','kubectl','get','pods','-A','-o','json'],text=True))['items']
names={p['name'] for p in json.loads((base/'preexisting-before.json').read_text())}
status=[{'name':p['metadata']['name'],'namespace':p['metadata']['namespace'],'phase':p['status']['phase'],
    'ready':next((c['status'] for c in p['status'].get('conditions',[]) if c['type']=='Ready'),None)} for p in pods if p['metadata']['name'] in names]
assert len(status)==3 and all(p['phase']=='Running' and p['ready']=='True' for p in status)
(base/'preexisting-after-cleanup.json').write_text(json.dumps(status,indent=2)+'\n')
result={'completed_at':datetime.now(timezone.utc).isoformat(),'remaining_owned_resources':0,'removed_local_wheels':len(removed),
        'deleted_names':[i['metadata']['name'] for i in items],'namespace_retained':True,'production_configuration_changed':False}
(base/'cleanup.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'cleanup':result,'edge_snapshot':json.loads(raw)},indent=2))
