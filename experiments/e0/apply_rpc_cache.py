"""Use checksum-verified wheels for diagnostic preparation; preserve attempts."""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile


base=Path('artifacts/e0-rpc-profile');run=json.loads((base/'run.json').read_text())
entries=json.loads((base/'wheel-manifest.json').read_text())
for item in entries:
    with (base/'wheelhouse'/item['filename']).open('rb') as f:
        assert hashlib.file_digest(f,'sha256').hexdigest()==item['sha256'],item['filename']
cache=run['auxiliary_pods'][0]
command=['rtk','proxy','kubectl','-n',run['namespace']]
process=subprocess.Popen([*command,'exec','-i',cache,'--','python3','-c',
                          "import sys,tarfile; tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall('/work',filter='data')"],
                         stdin=subprocess.PIPE)
with tarfile.open(fileobj=process.stdin,mode='w|') as archive:
    for item in entries:
        archive.add(base/'wheelhouse'/item['filename'],arcname=item['filename'])
        print(json.dumps({'uploaded':item['filename']}),flush=True)
process.stdin.close()
assert process.wait(timeout=30)==0
print(json.dumps({'kind':'wheel_upload_complete','wheels':len(entries)}),flush=True)

original=json.loads((base/'workers.json').read_text())
cm=copy.deepcopy(original['items'][0]);cm['metadata']['name']+='-cache'
normalize=lambda n:n.lower().replace('_','-')
arm={normalize(w['name']):w for w in entries if 'aarch64' in w['filename'] or w['filename'].endswith('any.whl')}
requirement=lambda w:f"{w['name']}=={w['version']} --hash=sha256:{w['sha256']}"
cm['data']['requirements-gpu-cache.txt']='\n'.join(requirement(w) for w in arm.values())+'\n'
cpu=[]
for line in cm['data']['requirements-cpu.txt'].splitlines():
    name,version=line.split('==');w=arm[normalize(name)];assert w['version']==version
    cpu.append(requirement(w))
cm['data']['requirements-cpu-cache.txt']='\n'.join(cpu)+'\n'
old_name=run['worker_pods'][2]
pod=copy.deepcopy(next(x for x in original['items'] if x['kind']=='Pod' and x['metadata']['name']==old_name))
pod['metadata']['name']=old_name+'-a2'
endpoint=f'http://{cache}.{run["namespace"]}.svc.cluster.local:8080'
hostname=f'{cache}.{run["namespace"]}.svc.cluster.local'


def use_cache(spec,backend):
    for v in spec['volumes']:
        if 'configMap' in v:v['configMap']['name']=cm['metadata']['name']
    c=spec['containers'][0]
    c['command'][-1]=c['command'][-1].replace("'--no-cache-dir',",f"'--no-cache-dir','--no-index','--find-links','{endpoint}','--trusted-host','{hostname}','--require-hashes',")
    c['command'][-1]=c['command'][-1].replace(f'/config/requirements-{backend}.txt',f'/config/requirements-{backend}-cache.txt')
    c['env'] += [{'name':'NO_PROXY','value':hostname},{'name':'no_proxy','value':hostname}]


use_cache(pod['spec'],'gpu')
(base/'gpu-cached-attempt.json').write_text(json.dumps({'apiVersion':'v1','kind':'List','items':[cm,pod]},indent=2)+'\n')
for block in range(3):
    path=base/f'client-{block}.json';initial=path.read_text()
    (base/f'client-{block}-initial.json').write_text(initial)
    client=json.loads(initial);use_cache(client['spec']['template']['spec'],'cpu')
    path.write_text(json.dumps(client,indent=2)+'\n')
for kind,args in [('log',['logs']),('pod.json',['get','pod'])]:
    extra=['-o','json'] if kind.endswith('json') else []
    data=subprocess.check_output([*command,*args,old_name,*extra],text=True)
    (base/('worker-gpu-aborted-attempt1.'+kind)).write_text(data)
subprocess.run([*command,'delete','pod',old_name,'--wait=true','--timeout=30s'],check=True)
subprocess.run([*command,'apply','-f',str(base/'gpu-cached-attempt.json')],check=True)
run['aborted_worker_pods']=[old_name]
run['worker_pods'][2]=pod['metadata']['name']
run['configmaps']=[run['configmap'],cm['metadata']['name']]
run['artifact_cache']={'pod':cache,'service':cache,'manifest':'wheel-manifest.json','upstream_sha256_verified':True,
                       'runtime_and_measured_source_unchanged':True,'reason':'slow public package downloads before measured requests'}
(base/'run.json').write_text(json.dumps(run,indent=2)+'\n')
print(json.dumps({'kind':'cached_gpu_attempt_created','pod':pod['metadata']['name']}),flush=True)
