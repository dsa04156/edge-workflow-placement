"""Validate historical E2 decisions on live KubeEdge workers; clean only this run."""

from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time
import urllib.request

from measured_policy_bridge import load_e2_contract


def main():
    base=Path('artifacts/measured-policy-bridge');base.mkdir(exist_ok=True)
    assert not (base/'run.json').exists(),'Preserve the existing run; use a fresh work copy'
    namespace='scheduler-research'
    run_id=datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%S%f')
    prefix='meas-bridge-'+run_id
    labels={'app.kubernetes.io/part-of':'scheduler-research','research.scheduler/stage':'measured-bridge',
            'research.scheduler/run':run_id}
    meta=lambda name:{'name':name,'namespace':namespace,'labels':copy.deepcopy(labels)}
    cmd=['rtk','proxy','kubectl','--request-timeout=20s','-n',namespace]
    def call(*args):return subprocess.check_output([*cmd,*args],text=True)
    def write(name,data):
        path=base/name;assert not path.exists(),f'Preserve attempt {name}'
        path.write_text(data if isinstance(data,str) else json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    def records(raw):return [json.loads(line) for line in raw.splitlines() if line.startswith('{')]
    def protected_snapshot(pods=None):
        if pods is None:
            pods=json.loads(subprocess.check_output(['rtk','proxy','kubectl','--request-timeout=20s','get','pods','-A','-o','json'],text=True))['items']
        names={p['name'] for p in json.loads(Path('artifacts/e3-input-replication/preexisting-after-cleanup.json').read_text())}
        return [{'name':p['metadata']['name'],'namespace':p['metadata']['namespace'],'phase':p['status']['phase'],
                 'node':p['spec'].get('nodeName'),'ready':next((c['status'] for c in p['status'].get('conditions',[]) if c['type']=='Ready'),None)}
                for p in pods if p['metadata']['name'] in names]

    contract=load_e2_contract(Path('artifacts/e2-dynamic'))
    e3=Path('artifacts/e3-input-replication')
    old_run=json.loads((e3/'run.json').read_text())
    assert contract['selection_sha256']==old_run['frozen_E2_selection_sha256']
    write('bridge-contract.json',contract)
    cache=json.loads((e3/'cache.json').read_text())['items']
    cache_name=prefix+'-cache';host=cache_name+'.'+namespace+'.svc.cluster.local';endpoint='http://'+host+':8080'
    old_endpoint=next(e['value'] for item in json.loads((e3/'deployment-0.json').read_text())['items']
                      if item['kind']=='Pod' for e in item['spec']['containers'][0]['env'] if e['name']=='ASSET_BASE')
    old_host=old_endpoint.split('://')[1].split(':')[0]
    for item in cache:
        item['metadata']=meta(cache_name);item['metadata']['labels']['research.scheduler/worker']='cache'
        if item['kind']=='Pod':item['spec']['activeDeadlineSeconds']=900
        else:item['spec']['selector']=copy.deepcopy(cache[0]['metadata']['labels'])
    cm=json.loads((e3/'configmap.json').read_text());cm['metadata']=meta(prefix)
    for name in ['policy_runner.py','measured_policy_bridge.py','bridge_client.py']:
        cm['data'][name]=Path('experiments/e0',name).read_text()
    cm['data']['bridge-contract.json']=json.dumps(contract)
    peers=copy.deepcopy(old_run['deployments'][0]['targets'])
    for alias,peer in peers.items():
        peer['url']=f'http://{prefix}-{alias.replace("_","-")}.{namespace}.svc.cluster.local:8080'
        assert {k:peer[k] for k in ['node','backend']}==contract['historical_targets'][alias]
    deployment=json.loads((e3/'deployment-0.json').read_text())['items']
    workers={};clients={}
    for item in deployment:
        if item['kind']=='Service':continue
        old_labels=item['metadata']['labels'];alias=old_labels['research.scheduler/alias'];role=old_labels['research.scheduler/role']
        name=prefix+'-'+alias.replace('_','-')+('-client' if role=='client' else '')
        item['metadata']=meta(name);item['metadata']['labels'].update({'research.scheduler/alias':alias,'research.scheduler/role':role})
        item['spec']['activeDeadlineSeconds']=900
        for volume in item['spec']['volumes']:
            if 'configMap' in volume:volume['configMap']['name']=prefix
        container=item['spec']['containers'][0]
        if role=='worker':
            container['command'][-1]=container['command'][-1].replace(old_endpoint,endpoint).replace(old_host,host)
            workers[alias]=name
        else:clients[alias]=name
        for env in container['env']:
            if env['name']=='PEERS_JSON':env['value']=json.dumps(peers)
            elif env['name']=='ASSET_BASE':env['value']=endpoint
            elif env['name'] in ['NO_PROXY','no_proxy']:env['value']=host
    deployment=[item for item in deployment if item['kind']=='Pod']
    for alias,name in workers.items():
        pod=next(p for p in deployment if p['metadata']['name']==name)
        deployment.append({'apiVersion':'v1','kind':'Service','metadata':meta(name),
                           'spec':{'selector':pod['metadata']['labels'],'ports':[{'port':8080,'targetPort':8080}]}})
    edge=json.loads((e3/'postcheck.json').read_text())['items'][0];edge['metadata']=meta(prefix+'-edge-check')
    objects=[*cache,cm,*deployment,edge]
    assert all(item['metadata']['name'].startswith(prefix) and item['metadata']['namespace']==namespace for item in objects)
    write('cache.json',{'apiVersion':'v1','kind':'List','items':cache})
    write('configmap.json',cm)
    write('deployment.json',{'apiVersion':'v1','kind':'List','items':deployment})
    write('postcheck.json',edge)
    manifest=json.loads(Path('artifacts/e2-dynamic/wheel-manifest.json').read_text())
    write('wheel-manifest.json',manifest)
    write('run.json',{'run_id':run_id,'namespace':namespace,'prefix':prefix,'targets':peers,'workers':workers,'clients':clients,
        'design_frozen_at':datetime.now(timezone.utc).isoformat(),'application_workflows':10,'setup_workflows':2,'policy_decisions':60,
        'source_sha256':{n:hashlib.sha256(data.encode()).hexdigest() for n,data in cm['data'].items() if n.endswith('.py')},
        'original_selection_sha256':contract['selection_sha256']})
    wheelhouse=base/'wheelhouse';wheelhouse.mkdir(exist_ok=True)
    mutations=False
    try:
        existing=json.loads(call('get','pods,services,configmaps','-o','json'))['items']
        identities={(item['kind'],item['metadata']['name']) for item in objects}
        assert not any((item['kind'],item['metadata']['name']) in identities for item in existing),'Owned name collision'
        pods=json.loads(subprocess.check_output(['rtk','proxy','kubectl','--request-timeout=20s','get','pods','-A','-o','json'],text=True))['items']
        claims=[p['metadata']['name'] for p in pods if p['spec'].get('nodeName')==peers['intel_npu']['node'] and
                p['status']['phase'] not in ['Succeeded','Failed'] and any(
                any('npu' in key for key in container.get('resources',{}).get(group,{}))
                for container in p['spec']['containers'] for group in ['requests','limits'])]
        write('preflight.json',{'npu_claims':claims,'owned_name_collisions':0})
        assert not claims,'Intel NPU is in use; do not interfere'
        before=protected_snapshot(pods);write('preexisting-before.json',before)
        assert len(before)==3 and all(p['phase']=='Running' and p['ready']=='True' for p in before)
        def download(wheel):
            path=wheelhouse/wheel['filename']
            with urllib.request.urlopen(wheel['url'],timeout=60) as response:path.write_bytes(response.read())
            assert path.stat().st_size==wheel['size'] and hashlib.sha256(path.read_bytes()).hexdigest()==wheel['sha256']
        with ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(download,manifest))
        mutations=True
        subprocess.run([*cmd,'create','-f',str(base/'cache.json')],check=True)
        subprocess.run([*cmd,'wait','--for=condition=Ready','pod/'+cache_name,'--timeout=60s'],check=True)
        process=subprocess.Popen([*cmd,'exec','-i',cache_name,'--','python3','-c',
            "import sys,tarfile;tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall('/work',filter='data')"],stdin=subprocess.PIPE)
        with tarfile.open(fileobj=process.stdin,mode='w|') as archive:
            for path in sorted(wheelhouse.iterdir()):archive.add(path,arcname=path.name)
            for path in (e3/'assets').iterdir():archive.add(path,arcname=path.name)
        process.stdin.close();assert process.wait(timeout=60)==0
        subprocess.run([*cmd,'create','-f',str(base/'configmap.json')],check=True)
        subprocess.run([*cmd,'create','-f',str(base/'deployment.json')],check=True)
        ready=set();deadline=time.monotonic()+240
        while len(ready)!=2:
            for alias,name in workers.items():
                pod=json.loads(call('get','pod',name,'-o','json'))
                if pod['status']['phase']=='Pending':continue
                raw=call('logs',name);(base/f'worker-{alias}.log').write_text(raw)
                assert pod['status']['phase']=='Running',alias
                if any(r['kind']=='server_ready' for r in records(raw)):ready.add(alias)
            print(json.dumps({'kind':'workers_preparing','ready':sorted(ready)}),flush=True)
            assert time.monotonic()<deadline,'Worker model preparation timeout'
            if len(ready)!=2:time.sleep(5)
        all_rows=[]
        for source,pod in clients.items():
            subprocess.run([*cmd,'wait','--for=condition=Ready','pod/'+pod,'--timeout=60s'],check=True)
            spec={'source':source,'run_id':run_id};write(source+'.input.json',spec)
            result=subprocess.run([*cmd,'exec','-i',pod,'--','python3','-u','-c',
                "import sys,runpy;sys.path.insert(0,'/config');runpy.run_path('/config/bridge_client.py',run_name='__main__')"],
                input=json.dumps(spec),capture_output=True,text=True,timeout=90)
            write(source+'.log',result.stdout);write(source+'.stderr',result.stderr)
            assert result.returncode==0,f'Client failure preserved: {source}'
            rows=records(result.stdout);assert rows[-1]['kind']=='client_complete'
            all_rows.extend(rows)
        receipts=[]
        for alias,name in workers.items():
            raw=call('logs',name);(base/f'worker-{alias}.log').write_text(raw)
            receipts.extend(r for r in records(raw) if r['kind']=='stage_complete')
        workflows=[r for r in all_rows if r['kind'] in ['bridge_attempt','setup_workflow']]
        attempts=[r for r in workflows if r['kind']=='bridge_attempt']
        assert len(workflows)==12 and len(attempts)==10
        for row in workflows:
            assert row['status']=='ok' and row['result']['top5']==[258,259,157,279,152]
            rr=[r for r in receipts if r['request_id']==row['request_id']];assert len(rr)==3
            for actual,returned in zip(sorted(rr,key=lambda r:r['stage']),row['result']['trace']):
                assert all(actual[k]==returned[k] for k in ['stage','alias','node','input_sha256','output_sha256','task_ms'])
                assert actual['node']==peers[actual['alias']]['node']
                device=actual['device']
                if actual['stage']==1 and actual['alias']=='intel_npu':assert device['execution_devices']==['NPU']
                else:assert device['execution_device']=='CPU'
            if row['kind']=='bridge_attempt':
                assert len(row['decisions'])==6 and all(d['snapshot_id']==row['snapshot_id'] for d in row['decisions'].values())
        write('attempts.json',workflows)
        status=json.loads(call('get','pods','-l','research.scheduler/run='+run_id,'-o','json'))
        write('pods-after.json',status)
        assert all(p['status']['phase']=='Running' and all(c['ready'] and c['restartCount']==0 for c in p['status']['containerStatuses']) for p in status['items'])
        live=json.loads(call('get','configmap',prefix,'-o','json'))
        assert live['data']==cm['data'];write('live-source-verification.json',{'matches_frozen':True})
        write('verification.json',{'application_workflows':10,'setup_workflows':2,'same_snapshot_policy_decisions':60,
            'matched_stage_receipts':36,'workflow_failures':0,'source_sha_verified':True,
            'npu_inferences':sum(r['result']['trace'][1]['alias']=='intel_npu' for r in workflows),
            'cold_npu_preparations':sum(r['result']['trace'][1]['alias']=='intel_npu' and r['result']['trace'][1]['device']['was_cold'] for r in workflows),
            'scope':'Historical calibration/decision compatibility and live HTTP integration; not a policy performance comparison.'})
        subprocess.run([*cmd,'create','-f',str(base/'postcheck.json')],check=True)
        deadline=time.monotonic()+60
        while True:
            status=json.loads(call('get','pod',edge['metadata']['name'],'-o','json'))['status']['phase']
            if status=='Succeeded':break
            assert status!='Failed' and time.monotonic()<deadline,'Read-only EdgeCore snapshot failed'
            time.sleep(2)
        write('edge-snapshot.json',json.loads(call('logs',edge['metadata']['name'])))
    except BaseException as error:
        write('failure.json',{'type':type(error).__name__,'message':str(error),'at':datetime.now(timezone.utc).isoformat()})
        raise
    finally:
        if mutations:
            for item in reversed(objects):
                name=item['metadata']['name'];kind=item['kind'].lower()
                raw=call('get',kind,name,'--ignore-not-found','-o','json')
                if not raw.strip():continue
                actual=json.loads(raw)
                assert actual['metadata']['labels'].get('research.scheduler/run')==run_id,'Cleanup ownership mismatch'
                subprocess.run([*cmd,'delete',kind,name,'--wait=false'],check=True,stdout=subprocess.DEVNULL)
            deadline=time.monotonic()+90
            while json.loads(call('get','pods,services,configmaps','-l','research.scheduler/run='+run_id,'-o','json'))['items']:
                assert time.monotonic()<deadline,'Cleanup pending; never force-delete'
                time.sleep(2)
        removed=0
        for wheel in manifest:
            path=wheelhouse/wheel['filename']
            if path.exists():path.unlink();removed+=1
        after=protected_snapshot();write('preexisting-after-cleanup.json',after)
        write('cleanup.json',{'completed_at':datetime.now(timezone.utc).isoformat(),'remaining_owned_resources':0,
                              'removed_wheels':removed,'production_configuration_changed':False})
        assert len(after)==3 and all(p['phase']=='Running' and p['ready']=='True' for p in after)
    print(json.dumps({'kind':'complete','workflows':12,'policy_decisions':60,'owned_resources_remaining':0}),flush=True)


if __name__=='__main__':main()
