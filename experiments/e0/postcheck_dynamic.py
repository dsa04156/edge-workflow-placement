"""After measurement: runtime versions, existing workloads and EdgeCore/NIC."""

import copy
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import time

base=Path('artifacts/e2-dynamic');run=json.loads((base/'run.json').read_text())
assert (base/'collection-complete.json').exists()
cmd=['rtk','proxy','kubectl','--request-timeout=20s','-n',run['namespace']]
import hashlib
cm=json.loads(subprocess.check_output([*cmd,'get','configmap',run['configmap'],'-o','json'],text=True))
hashes={name:hashlib.sha256(cm['data'][name].encode()).hexdigest() for name in run['source_sha256']}
assert hashes==run['source_sha256']
(base/'live-configmap-verification.json').write_text(json.dumps({'uid':cm['metadata']['uid'],
    'resource_version':cm['metadata']['resourceVersion'],'source_sha256':hashes,'matches_frozen':True},indent=2)+'\n')
versions={}
code="""import sys,json,platform,importlib.metadata as m;sys.path.insert(0,'/work/site');print(json.dumps({'python':platform.python_version(),'architecture':platform.machine(),'packages':{n:m.version(n) for n in ['numpy','onnxruntime','Pillow']}}))"""
for alias,name in zip(run['targets'],run['worker_pods']):
    versions[alias]=json.loads(subprocess.check_output([*cmd,'exec',name,'--','python3','-c',code],text=True))
extra="""import sys,json;sys.path.insert(0,'/work/site');import openvino as ov;c=ov.Core();names=[str(p) for p in c.get_property('NPU','SUPPORTED_PROPERTIES')];print(json.dumps({'openvino':ov.__version__,'available_devices':c.available_devices,'npu_name':str(c.get_property('NPU','FULL_DEVICE_NAME')),'version_properties':{n:str(c.get_property('NPU',n)) for n in names if 'DRIVER_VERSION' in n or 'COMPILER_VERSION' in n}}))"""
versions['npu_details']=json.loads(subprocess.check_output([*cmd,'exec',run['worker_pods'][1],'--','python3','-c',extra],text=True))
(base/'runtime-versions.json').write_text(json.dumps(versions,indent=2)+'\n')
pod=copy.deepcopy(json.loads(Path('artifacts/e1-dag/edge-snapshot-pod.json').read_text()))
pod['metadata']['name']=run['configmap']+'-edge-check'
pod['metadata']['labels']={'app.kubernetes.io/part-of':'scheduler-research','research.scheduler/stage':'e2-dynamic','research.scheduler/run':run['run_id']}
pod['spec']['containers'][0]['command'][-1]=Path('experiments/e0/edge_snapshot.py').read_text()
manifest={'apiVersion':'v1','kind':'List','items':[pod]}
path=base/'postcheck.json';assert not path.exists();path.write_text(json.dumps(manifest,indent=2)+'\n')
subprocess.run([*cmd,'apply','-f',str(path)],check=True)
deadline=time.monotonic()+65
while True:
    state=json.loads(subprocess.check_output([*cmd,'get','pod',pod['metadata']['name'],'-o','json'],text=True))
    if state['status']['phase'] in ['Succeeded','Failed']:break
    assert time.monotonic()<deadline,'Readonly EdgeCore snapshot timeout'
    time.sleep(2)
(base/'edge-snapshot-status.json').write_text(json.dumps(state,indent=2)+'\n')
raw=subprocess.check_output([*cmd,'logs',pod['metadata']['name']],text=True)
(base/'edge-snapshot.json').write_text(raw)
assert state['status']['phase']=='Succeeded'
print(json.dumps({'runtime_versions':versions,'edge_snapshot':json.loads(raw),'checked_at':datetime.now(timezone.utc).isoformat()},indent=2))
