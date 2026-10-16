"""Fixed edge sensor placement under verified Pod-local destination-filtered TBF regimes."""

from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime,timezone
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import subprocess
import tarfile
import time
import urllib.request

import pandas as pd

from fresh_placement import freeze_profiles,plan_cost, data_greedy, dag_enumeration
from measured_policy_bridge import task_fastest
from edge_network_design import schedule
from policy_runner import readonly
from phase_capture import capture_phase


def main():
    base=Path('artifacts/edge-network-placement');base.mkdir(exist_ok=True)
    assert not (base/'run.json').exists(),'Preserve all prior attempts'
    began=time.perf_counter();namespace='scheduler-research'
    run_id=datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%S%f');prefix='edge-network-'+run_id
    labels={'app.kubernetes.io/part-of':'scheduler-research','research.scheduler/stage':'edge-network-placement','research.scheduler/run':run_id}
    meta=lambda name:{'name':name,'namespace':namespace,'labels':copy.deepcopy(labels)}
    cmd=['rtk','proxy','kubectl','--request-timeout=20s','-n',namespace]
    def call(*args):return subprocess.check_output([*cmd,*args],text=True)
    def write(name,data):
        path=base/name;assert not path.exists(),f'Preserve attempt {name}'
        path.write_text(data if isinstance(data,str) else json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    def records(raw):return [json.loads(line) for line in raw.splitlines() if line.startswith('{')]
    def protected():
        pods=json.loads(subprocess.check_output(['rtk','proxy','kubectl','--request-timeout=20s','get','pods','-A','-o','json'],text=True))['items']
        names={p['name'] for p in json.loads(Path('artifacts/measured-policy-bridge/preexisting-after-cleanup.json').read_text())}
        return [{'name':p['metadata']['name'],'namespace':p['metadata']['namespace'],'node':p['spec'].get('nodeName'),
                 'phase':p['status']['phase'],'ready':next((c['status'] for c in p['status'].get('conditions',[]) if c['type']=='Ready'),None)}
                for p in pods if p['metadata']['name'] in names]
    loader=importlib.util.spec_from_file_location('allocation','/home/jinuk/.codex/skills/experimental-design/scripts/randomization.py')
    allocation=importlib.util.module_from_spec(loader);loader.loader.exec_module(allocation)
    def assigned(rows,seed):return allocation.assign_factorial_runs(pd.DataFrame(rows),seed=seed).to_dict('records')
    old=Path('artifacts/measured-policy-bridge');e3=Path('artifacts/e3-input-replication')
    corpus={k:v for k,v in json.loads((e3/'corpus.json').read_text()).items() if k in ['hubble_deep_field','chelsea','rocket']};write('corpus.json',corpus)
    old_run=json.loads((old/'run.json').read_text());old_items=json.loads((old/'deployment.json').read_text())['items']
    cache=json.loads((old/'cache.json').read_text())['items']
    old_endpoint=next(e['value'] for p in old_items if p['kind']=='Pod' for e in p['spec']['containers'][0]['env'] if e['name']=='ASSET_BASE')
    old_host=old_endpoint.split('://')[1].split(':')[0]
    cache_name=prefix+'-cache';host=cache_name+'.'+namespace+'.svc.cluster.local';endpoint='http://'+host+':8080'
    for item in cache:
        item['metadata']=meta(cache_name);item['metadata']['labels']['research.scheduler/worker']='cache'
        if item['kind']=='Pod':item['spec']['activeDeadlineSeconds']=3600
        else:item['spec']['selector']=copy.deepcopy(cache[0]['metadata']['labels'])
    cm=json.loads((old/'configmap.json').read_text());cm['metadata']=meta(prefix)
    cm['data'].pop('bridge-contract.json',None)
    for name in ['dag_profile.py','fresh_placement.py','edge_network_client.py','pod_link_control.py']:
        cm['data'][name]=Path('experiments/e0',name).read_text()
    deployments=[];objects=[*cache,cm]
    regimes={'native':None,'cap50':50,'cap20':20}
    cpu_image=next(i['spec']['containers'][0]['image'] for i in old_items if i['kind']=='Pod' and i['metadata']['labels']['research.scheduler/alias']=='arm')
    aliases=['arm','intel_npu']
    for block in range(3):
        peers=copy.deepcopy(old_run['targets']);block_prefix=prefix+f'-b{block}'
        for alias,peer in peers.items():peer['url']=f'http://{block_prefix}-{alias.replace("_","-")}.{namespace}.svc.cluster.local:8080'
        items=[];workers={};clients={}
        for template in old_items:
            if template['kind']!='Pod':continue
            item=copy.deepcopy(template);tag=item['metadata']['labels'];alias=tag['research.scheduler/alias'];role=tag['research.scheduler/role']
            name=block_prefix+'-'+alias.replace('_','-')+('-client' if role=='client' else '')
            item['metadata']=meta(name);item['metadata']['labels'].update({'research.scheduler/block':str(block),'research.scheduler/alias':alias,'research.scheduler/role':role})
            item['spec']['activeDeadlineSeconds']=900
            for volume in item['spec']['volumes']:
                if 'configMap' in volume:volume['configMap']['name']=prefix
            c=item['spec']['containers'][0]
            if role=='worker':
                c['command'][-1]=c['command'][-1].replace(old_endpoint,endpoint).replace(old_host,host)
                workers[alias]=name
            else:clients[alias]=name
            for env in c['env']:
                if env['name']=='PEERS_JSON':env['value']=json.dumps(peers)
                elif env['name']=='ASSET_BASE':env['value']=endpoint
                elif env['name'] in ['NO_PROXY','no_proxy']:env['value']=host
            if role=='worker' or alias=='arm':
                item['metadata']['annotations']={'kubectl.kubernetes.io/default-container':c['name']}
                item['spec']['containers'].append({'name':'link-control','image':cpu_image,
                    'command':['python3','-c','import time;time.sleep(1800)'],
                    'env':[{'name':'PYTHONPATH','value':'/work/link-sdk'},{'name':'RESEARCH_LINK_CONTROL','value':'owned-pod-netns'}],
                    'securityContext':{'runAsUser':0,'runAsNonRoot':False,'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,
                                       'capabilities':{'drop':['ALL'],'add':['NET_ADMIN']}},
                    'resources':{'requests':{'cpu':'10m','memory':'24Mi'},'limits':{'cpu':'200m','memory':'96Mi'}},
                    'volumeMounts':[copy.deepcopy(v) for v in c['volumeMounts'] if v['mountPath'] in ['/work','/config']]})
            items.append(item)
        for alias,name in workers.items():
            pod=next(p for p in items if p['metadata']['name']==name)
            items.append({'apiVersion':'v1','kind':'Service','metadata':meta(name),
                          'spec':{'selector':pod['metadata']['labels'],'ports':[{'port':8080,'targetPort':8080}]}})
        full=[{'image_id':key,'plan':list(plan)} for key,plan in itertools.product(corpus,itertools.product(aliases,repeat=3))]
        calibration={regime:schedule(full,1,3,20261301+block*1000+i*100,assigned,regime) for i,regime in enumerate(regimes)}
        design={'block':block,'workers':workers,'clients':clients,'targets':peers,
                'calibration_order':[r['regime'] for r in assigned([{'regime':r} for r in regimes],20261301+block*1000+900)],
                'evaluation_order':[r['regime'] for r in assigned([{'regime':r} for r in regimes],20261351+block*1000+900)],
                'calibration':calibration}
        deployments.append(design);objects.extend(items)
        write(f'deployment-b{block}.json',{'apiVersion':'v1','kind':'List','items':items})
    edge=json.loads((old/'postcheck.json').read_text());edge['metadata']=meta(prefix+'-edge-check');objects.append(edge)
    for block in range(3):objects.append({'kind':'ConfigMap','metadata':meta(prefix+f'-profile-b{block}')})
    assert all(o['metadata']['name'].startswith(prefix) and o['metadata']['namespace']==namespace for o in objects)
    write('cache.json',{'apiVersion':'v1','kind':'List','items':cache});write('configmap.json',cm);write('postcheck.json',edge)
    manifest=json.loads((old/'wheel-manifest.json').read_text());manifest.append(json.loads(Path('artifacts/edge-link-preflight/wheel.json').read_text()));write('wheel-manifest.json',manifest)
    source_hashes={name:hashlib.sha256(data.encode()).hexdigest() for name,data in cm['data'].items() if name.endswith('.py')}
    write('run.json',{'run_id':run_id,'namespace':namespace,'prefix':prefix,'design_frozen_at':datetime.now(timezone.utc).isoformat(),
         'deployments':deployments,'source_sha256':source_hashes,'calibration_workflows_per_block':288,'regimes':regimes,
         'corpus_sha256':hashlib.sha256((e3/'corpus.json').read_bytes()).hexdigest(),
         'quality_rule':'exact CPU-reference Top5 order in every calibration observation',
         'scope':'Fixed ARM sensor source; three payloads; destination-filtered Pod TBF; calibrated current-regime versus native-fixed placement.',
         'spec_sha256':hashlib.sha256(Path('.scratch/edge-network-placement/spec.md').read_bytes()).hexdigest()})
    write('implementation.json',{str(path):{'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'source':path.read_text()}
          for path in [Path(__file__),Path('experiments/e0/phase_capture.py'),Path('experiments/e0/edge_network_design.py'),Path('experiments/e0/edge_network_client.py'),Path('experiments/e0/pod_link_control.py')]})
    wheelhouse=base/'wheelhouse';wheelhouse.mkdir(exist_ok=True);mutations=False;identities=[];totals=[]

    def remove(items):
        for item in reversed(items):
            name=item['metadata']['name'];kind=item['kind'].lower()
            raw=call('get',kind,name,'--ignore-not-found','-o','json')
            if not raw.strip():continue
            actual=json.loads(raw)
            assert actual['metadata']['labels'].get('research.scheduler/run')==run_id,'Ownership mismatch'
            subprocess.run([*cmd,'delete',kind,name,'--wait=false'],check=True,stdout=subprocess.DEVNULL)

    try:
        existing=json.loads(call('get','pods,services,configmaps','-o','json'))['items']
        names={(o['kind'],o['metadata']['name']) for o in objects}
        assert not any((o['kind'],o['metadata']['name']) in names for o in existing),'Owned name collision'
        allpods=json.loads(subprocess.check_output(['rtk','proxy','kubectl','--request-timeout=20s','get','pods','-A','-o','json'],text=True))['items']
        claims=[p['metadata']['name'] for p in allpods if p['spec'].get('nodeName')==old_run['targets']['intel_npu']['node'] and
            p['status']['phase'] not in ['Succeeded','Failed'] and any(any('npu' in key for key in c.get('resources',{}).get(g,{}))
            for c in p['spec']['containers'] for g in ['requests','limits'])]
        write('preflight.json',{'npu_claims':claims,'owned_collisions':0});assert not claims,'NPU already in use'
        before=protected();write('preexisting-before.json',before)
        assert len(before)==3 and all(p['phase']=='Running' and p['ready']=='True' for p in before)
        def download(w):
            p=wheelhouse/w['filename']
            with urllib.request.urlopen(w['url'],timeout=60) as response:p.write_bytes(response.read())
            assert p.stat().st_size==w['size'] and hashlib.sha256(p.read_bytes()).hexdigest()==w['sha256']
        with ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(download,manifest))
        mutations=True;subprocess.run([*cmd,'create','-f',str(base/'cache.json')],check=True)
        subprocess.run([*cmd,'wait','--for=condition=Ready','pod/'+cache_name,'--timeout=60s'],check=True)
        upload=subprocess.Popen([*cmd,'exec','-i',cache_name,'--','python3','-c',
            "import sys,tarfile;tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall('/work',filter='data')"],stdin=subprocess.PIPE)
        with tarfile.open(fileobj=upload.stdin,mode='w|') as archive:
            for p in wheelhouse.iterdir():archive.add(p,arcname=p.name)
            for p in (e3/'assets').iterdir():archive.add(p,arcname=p.name)
        upload.stdin.close();assert upload.wait(timeout=60)==0
        subprocess.run([*cmd,'create','-f',str(base/'configmap.json')],check=True)
        for design in deployments:
            block=design['block'];prep_started=time.perf_counter()
            subprocess.run([*cmd,'create','-f',str(base/f'deployment-b{block}.json')],check=True)
            ready=set();deadline=time.monotonic()+240
            while len(ready)!=2:
                for alias,name in design['workers'].items():
                    p=json.loads(call('get','pod',name,'-o','json'))
                    if p['status']['phase']=='Pending':continue
                    raw=call('logs',name);(base/f'worker-b{block}-{alias}.log').write_text(raw)
                    assert p['status']['phase']=='Running',f'Worker failed: {alias}'
                    if any(r['kind']=='server_ready' for r in records(raw)):ready.add(alias)
                print(json.dumps({'kind':'preparing','block':block,'ready':sorted(ready)}),flush=True)
                assert time.monotonic()<deadline,'Hard worker preparation timeout'
                if len(ready)!=2:time.sleep(5)
            prep_seconds=time.perf_counter()-prep_started
            for name in design['clients'].values():
                subprocess.run([*cmd,'wait','--for=condition=Ready','pod/'+name,'--timeout=60s'],check=True)

            def phase(mode,regime,conditions,profiles=None):
                source='arm'
                spec={'run_id':run_id,'block':block,'source':source,'mode':mode,'conditions':conditions,'regime':regime}
                if profiles is not None:spec['profiles']=profiles
                stem=f'{mode}-b{block}-{regime}';write(stem+'.input.json',spec)
                started=time.perf_counter()
                result=capture_phase([*cmd,'exec','-i',design['clients'][source],'--','python3','-u','-c',
                    "import sys,runpy;sys.path.insert(0,'/config');runpy.run_path('/config/edge_network_client.py',run_name='__main__')"],
                    json.dumps(spec),base,stem,timeout=180)
                assert result.returncode==0,f'Failed phase preserved: {stem}'
                result_rows=records(result.stdout);assert result_rows[-1]['kind']=='client_complete'
                print(json.dumps({'kind':'phase_complete','block':block,'mode':mode,'regime':regime,
                                  'requests':result_rows[-1]['requests']}),flush=True)
                return [r for r in result_rows if r['kind']=='workflow'],time.perf_counter()-started

            state=json.loads(call('get','pods,services','-l','research.scheduler/run='+run_id,'-o','json'))['items']
            pod_ips={x['metadata']['name']:x['status']['podIP'] for x in state if x['kind']=='Pod' and x['metadata']['name'] in [*design['workers'].values(),*design['clients'].values()]}
            service_ips={x['metadata']['name']:x['spec']['clusterIP'] for x in state if x['kind']=='Service' and x['metadata']['name'] in design['workers'].values()}
            controlled=[design['workers']['arm'],design['clients']['arm'],design['workers']['intel_npu']]
            destinations={name:([pod_ips[design['workers']['intel_npu']],service_ips[design['workers']['intel_npu']]] if name!=design['workers']['intel_npu'] else
                [pod_ips[design['workers']['arm']],pod_ips[design['clients']['arm']],service_ips[design['workers']['arm']]]) for name in controlled}
            wheel=manifest[-1]
            for name in controlled:
                install="import urllib.request,hashlib,io,zipfile;data=urllib.request.urlopen(%r).read();assert hashlib.sha256(data).hexdigest()==%r;zipfile.ZipFile(io.BytesIO(data)).extractall('/work/link-sdk')" % (endpoint+'/'+wheel['filename'],wheel['sha256'])
                call('exec',name,'-c','link-control','--','python3','-c',install)
            def set_regime(mode,regime):
                evidence={};started=time.perf_counter()
                for name in controlled:
                    spec={'destinations':destinations[name],'mbps':regimes[regime]}
                    proc=subprocess.run([*cmd,'exec','-i',name,'-c','link-control','--','python3','/config/pod_link_control.py'],
                        input=json.dumps(spec),capture_output=True,text=True,timeout=30)
                    assert proc.returncode==0,proc.stderr
                    evidence[name]=json.loads(proc.stdout)
                write(f'network-b{block}-{mode}-{regime}.json',{'settings':evidence,'duration_seconds':time.perf_counter()-started})
            calibration=[];calibration_seconds=0;by_regime={}
            for regime in design['calibration_order']:
                set_regime('calibration',regime)
                rr,elapsed=phase('calibration',regime,design['calibration'][regime]);calibration+=rr;calibration_seconds+=elapsed;by_regime[regime]=rr
            assert len(calibration)==288
            hashes={regime:hashlib.sha256((base/f'calibration-b{block}-{regime}.log').read_bytes()).hexdigest() for regime in regimes}
            evidence_id=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
            _,quality=freeze_profiles(calibration,corpus,aliases,evidence_id)
            assert all(q[a]['eligible'] for q in quality.values() for a in aliases),'Shared quality candidate excluded; preserve and stop'
            profiles={regime:freeze_profiles(rows,corpus,aliases,evidence_id+'|'+regime)[0] for regime,rows in by_regime.items()}
            selections={};groups={}
            for regime in regimes:
                selections[regime]={};groups[regime]=[]
                for key,profile in profiles[regime].items():
                    planning_input=readonly({'profile':profile});chosen={};grouped={}
                    policies={'task_fastest':task_fastest,'data_greedy':data_greedy,'current_dag':dag_enumeration,
                              'fixed_native_dag':lambda s:dag_enumeration({'profile':profiles['native'][key]})}
                    for name,policy in policies.items():
                        t=time.perf_counter();plan=policy(planning_input);duration=(time.perf_counter()-t)*1000
                        chosen[name]={'plan':plan,'planning_ms':duration,'estimated_current_workflow_ms':plan_cost(profile,plan)}
                        grouped.setdefault(tuple(plan),[]).append(name)
                    selections[regime][key]=chosen
                    for plan,members in sorted(grouped.items()):
                        groups[regime].append({'image_id':profile['input_id'],'source':'arm','plan':list(plan),'members':members})
            frozen={'profiles':profiles,'quality':quality,'selections':selections,'calibration_log_sha256':hashes,
                    'frozen_at':datetime.now(timezone.utc).isoformat(),'preparation_seconds':prep_seconds,
                    'calibration_seconds':calibration_seconds,'source_sha256':source_hashes}
            write(f'frozen-b{block}.json',frozen)
            profile_cm={'apiVersion':'v1','kind':'ConfigMap','metadata':meta(prefix+f'-profile-b{block}'),'immutable':True,
                        'data':{'frozen.json':json.dumps(frozen)}}
            write(f'profile-configmap-b{block}.json',profile_cm)
            subprocess.run([*cmd,'create','-f',str(base/f'profile-configmap-b{block}.json')],check=True)
            frozen_digest=hashlib.sha256((base/f'frozen-b{block}.json').read_bytes()).hexdigest()
            schedules={regime:schedule(groups[regime],2,5,20261351+block*1000+i*100,assigned,regime) for i,regime in enumerate(regimes)}
            write(f'evaluation-design-b{block}.json',{'groups':groups,'schedules':schedules,'frozen_sha256':frozen_digest})
            print(json.dumps({'kind':'profiles_frozen','block':block,'unique_plans':sum(map(len,groups.values()))}),flush=True)
            evaluation=[];evaluation_seconds=0
            for regime in design['evaluation_order']:
                set_regime('evaluation',regime)
                rr,elapsed=phase('evaluation',regime,schedules[regime],profiles[regime]);evaluation+=rr;evaluation_seconds+=elapsed
            # Capture counters before restoring the default qdisc on owned Pod interfaces.
            set_regime('restore','native')
            assert len(evaluation)==7*sum(map(len,groups.values()))
            assert hashlib.sha256((base/f'frozen-b{block}.json').read_bytes()).hexdigest()==frozen_digest
            live_profile=json.loads(call('get','configmap',profile_cm['metadata']['name'],'-o','json'));assert live_profile['data']==profile_cm['data']
            receipts=[]
            for alias,name in design['workers'].items():
                raw=call('logs',name);(base/f'worker-b{block}-{alias}.log').write_text(raw)
                receipts.extend(r for r in records(raw) if r['kind']=='stage_complete')
            for row in [*calibration,*evaluation]:
                if row['status']!='ok':continue
                returned=row['result']['trace'];rr=[r for r in receipts if r['request_id']==row['request_id']]
                assert len(rr)==3
                for actual,client in zip(sorted(rr,key=lambda r:r['stage']),returned):
                    assert all(actual[k]==client[k] for k in ['stage','node','alias','input_sha256','output_sha256','task_ms'])
                    assert actual['node']==design['targets'][actual['alias']]['node']
                    if actual['stage']==1 and actual['alias']=='intel_npu':assert actual['device']['execution_devices']==['NPU']
                    else:assert actual['device']['execution_device']=='CPU'
                if row['mode']=='evaluation':
                    assert row['completed_at']>=frozen['frozen_at']
                    assert row['plan'][1] in profiles[row['regime']]['arm|'+row['input_id']]['candidates'][1]
            pods=json.loads(call('get','pods','-l','research.scheduler/run='+run_id+',research.scheduler/block='+str(block),'-o','json'))
            write(f'pods-b{block}.json',pods)
            for pod in pods['items']:
                assert pod['status']['phase']=='Running' and all(c['ready'] and c['restartCount']==0 for c in pod['status']['containerStatuses'])
                identities.append({'block':block,'name':pod['metadata']['name'],'uid':pod['metadata']['uid'],
                    'node':pod['spec']['nodeName'],'container_ids':[c['containerID'] for c in pod['status']['containerStatuses']]})
            totals.append({'block':block,'calibration':len(calibration),'evaluation':len(evaluation),
                           'evaluation_measured':sum(not r['warmup'] for r in evaluation),'unique_plans':sum(map(len,groups.values())),
                           'workflow_failures':sum(r['status']!='ok' for r in evaluation),
                           'invalid_quality':sum(r['status']=='ok' and not r['valid_complete'] for r in evaluation),
                           'stage_receipts':3*sum(r['status']=='ok' for r in [*calibration,*evaluation]),
                           'evaluation_seconds':evaluation_seconds})
            write(f'block-verification-b{block}.json',totals[-1])
            remove(json.loads((base/f'deployment-b{block}.json').read_text())['items'])
            deadline=time.monotonic()+90
            while json.loads(call('get','pods','-l','research.scheduler/run='+run_id+',research.scheduler/block='+str(block),'-o','json'))['items']:
                assert time.monotonic()<deadline,'Previous block cleanup pending; never force delete'
                time.sleep(2)
            print(json.dumps({'kind':'block_complete','block':block,**totals[-1]}),flush=True)
        write('pod-identities.json',identities);assert len({p['uid'] for p in identities})==12
        live=json.loads(call('get','configmap',prefix,'-o','json'));assert live['data']==cm['data']
        write('live-source-verification.json',{'matches_frozen':True})
        write('verification.json',{'blocks':totals,'completed_at':datetime.now(timezone.utc).isoformat(),
                                 'duration_seconds':time.perf_counter()-began,'source_matches_frozen':True})
        subprocess.run([*cmd,'create','-f',str(base/'postcheck.json')],check=True)
        deadline=time.monotonic()+60
        while True:
            state=json.loads(call('get','pod',edge['metadata']['name'],'-o','json'))['status']['phase']
            if state=='Succeeded':break
            assert state!='Failed' and time.monotonic()<deadline,'Read-only EdgeCore check failed'
            time.sleep(2)
        write('edge-snapshot.json',json.loads(call('logs',edge['metadata']['name'])))
    except BaseException as error:
        # Preserve the initiating failure before optional diagnostics can fail.
        write('failure.json',{'type':type(error).__name__,'message':str(error),'at':datetime.now(timezone.utc).isoformat()})
        for design in deployments:
            for alias,name in design['workers'].items():
                try:
                    diagnostic=capture_phase([*cmd,'logs',name],'',base,
                        f"failure-worker-b{design['block']}-{alias}",timeout=25)
                except Exception:
                    # capture_phase retained timeout/error metadata; never mask original error.
                    pass
        raise
    finally:
        if mutations:
            remove(objects);deadline=time.monotonic()+90
            while json.loads(call('get','pods,services,configmaps','-l','research.scheduler/run='+run_id,'-o','json'))['items']:
                assert time.monotonic()<deadline,'Final cleanup pending; never force delete'
                time.sleep(2)
        removed=0
        for w in manifest:
            p=wheelhouse/w['filename']
            if p.exists():p.unlink();removed+=1
        after=protected();write('preexisting-after-cleanup.json',after)
        write('cleanup.json',{'completed_at':datetime.now(timezone.utc).isoformat(),'remaining_owned_resources':0,
                              'removed_wheels':removed,'namespace_retained':True,'production_configuration_changed':False})
        assert len(after)==3 and all(p['phase']=='Running' and p['ready']=='True' for p in after)
    print(json.dumps({'kind':'complete','blocks':len(totals),'owned_resources_remaining':0}),flush=True)


if __name__=='__main__':main()
