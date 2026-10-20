from run_smoke import KUBECTL
"""Measured-profile automatic placement pilot on three owned candidate nodes.

Run from the repository root. Uses immutable offline assets and existing HTTP
runtime. Nonselected model Pods are removed before each evaluation deployment.
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import statistics
import struct
import subprocess
import time

from run_load import Session
from run_smoke import ASSETS, HERE, IMAGE, NODES, NS
from placement_policies import prepare, select_policies

TASKS = ('vision', 'speech', 'fusion')
HELPERS = ('source-e0', 'source-e1', 'source-c', 'sink-e0', 'sink-e1', 'sink-c')


def quantile(values, q=.75):
    ordered = sorted(values)
    pos = (len(ordered)-1)*q
    low = int(pos)
    return ordered[low] + (ordered[min(low+1, len(ordered)-1)]-ordered[low])*(pos-low)


class Pilot(Session):
    def __init__(self,spec_path=None):
        super().__init__()
        self.pairs = json.loads((ASSETS/'load-pairs.json').read_text())['pairs']
        self.refs = {r['question_id']: r for r in map(json.loads,
            (ASSETS/'reference-v1/results.jsonl').read_text().splitlines())}
        self.models = json.loads((ASSETS/'reference-v1/metadata.json').read_text())['stages']
        self.operators = []
        self.generation = 0
        self.guarded = False
        self.compute = {task: {} for task in TASKS}
        self.cal_outputs = {}
        self.spec = (spec_path or HERE.parents[1]/'.scratch/owned-hardware-pilot/spec.md').read_text()
        (self.out/'spec-frozen.md').write_text(self.spec)
        planner = Path(prepare.__file__)
        (self.out/'planner-source.py').write_bytes(planner.read_bytes())
        self.save('planner-source.json', dict(path=str(planner), sha256=hashlib.sha256(planner.read_bytes()).hexdigest()))
        self.save('git-source.json', dict(commit=json.loads((HERE.parents[1]/'release.json').read_text())['collector_id'],
            files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.glob('*.py')}))

    def pod(self,key,zone,task,cache_url=None):
        pod=super().pod(key,zone,task,cache_url)
        if task=='cache':return pod
        names=['http_runtime.py','edge_client.py','model_adapter.py','bootstrap.py','load_trace.py','net_control.py','wan_gateway.py']
        expected={name:hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in names}
        expected['bundles.json']=hashlib.sha256((ASSETS/'load-bundles.json').read_bytes()).hexdigest()
        freezer=(HERE/'freeze_config.py').read_text()
        for container in pod['spec']['containers']:
            destination='/work/config' if container['name']=='worker' else '/work/net-config'
            prefix="exec(compile("+repr(freezer)+",'frozen-config-copier','exec'));freeze_config('/config',"+repr(destination)+","+repr(expected)+");"
            container['command'][-1]=prefix+container['command'][-1].replace("/config/",destination+'/')
            if container['name']=='worker':
                container['env'].extend([dict(name='CONFIG_ROOT',value=destination),dict(name='PYTHONPATH',value=destination)])
            else:
                container['command'][-1]=container['command'][-1].replace("print('netctl-ready',flush=True)", "__import__('pathlib').Path('/work/netctl-ready').write_text('ready');print('netctl-ready',flush=True)")
                container['readinessProbe']={'exec':{'command':['python3','-c',"from pathlib import Path;assert Path('/work/netctl-ready').is_file()"]},'periodSeconds':2,'timeoutSeconds':3}
                container['env'].extend([dict(name='POD_UID',valueFrom={'fieldRef':{'fieldPath':'metadata.uid'}}),
                    dict(name='NODE_NAME',valueFrom={'fieldRef':{'fieldPath':'spec.nodeName'}})])
                for entry in container['env']:
                    if entry['name']=='PYTHONPATH':entry['value']='/work/net:'+destination
        return pod

    def execute(self,key,code,stem='operation',timeout=60,container='worker',stdin=None):
        if container=='worker':
            code=code.replace("sys.path.insert(0,'/config')", "sys.path.insert(0,'/work/config')")
        return super().execute(key,code,stem,timeout,container,stdin)

    def net(self,key,spec,stem):
        expected=hashlib.sha256((HERE/'net_control.py').read_bytes()).hexdigest()
        code="import runpy,hashlib,os;from pathlib import Path;assert os.environ['POD_UID']=="+repr(self.actual[key]['metadata']['uid'])+";p=Path('/work/net-config/net_control.py');assert hashlib.sha256(p.read_bytes()).hexdigest()=="+repr(expected)+";runpy.run_path(str(p),run_name='__main__')"
        return json.loads(self.execute(key,code,stem,container='netctl',stdin=json.dumps(spec)))

    def deploy(self):
        self.apply('cache', [self.pod('cache','c','cache')])
        self.call('cache-ready',['wait','pod','-n',NS,self.run+'-cache','--for=condition=Ready','--timeout=90s'],timeout=100)
        cache=json.loads(self.call('cache-state',['get','pod','-n',NS,self.run+'-cache','-o','json']))
        self.cache_url='http://'+cache['status']['podIP']+':8080'
        event=dict(started_at=datetime.now(timezone.utc).isoformat())
        try:
            with (ASSETS/'load-cache-upload.tar').open('rb') as source, (self.out/'upload.stdout').open('wb') as so, (self.out/'upload.stderr').open('wb') as se:
                result=subprocess.run([*KUBECTL,'--request-timeout=20s','exec','-i','-n',NS,self.run+'-cache','--','python3','-c',
                    "import tarfile,sys;tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall('/work',filter='data')"],
                    stdin=source,stdout=so,stderr=se,timeout=300)
                event['returncode']=result.returncode
                if result.returncode: raise RuntimeError('Asset upload failed')
        finally:
            event['finished_at']=datetime.now(timezone.utc).isoformat()
            self.save('upload.event.json',event)
        files=['http_runtime.py','edge_client.py','model_adapter.py','bootstrap.py','load_trace.py','net_control.py','wan_gateway.py']
        cm=dict(apiVersion='v1',kind='ConfigMap',metadata=self.meta(self.run),data={f:(HERE/f).read_text() for f in files})
        cm['data']['bundles.json']=(ASSETS/'load-bundles.json').read_text()
        items=[cm,self.pod('gateway','c','gateway',self.cache_url)]
        items += [self.pod('source-'+node,node,'source',self.cache_url) for node in NODES]
        for node in NODES:
            sink=self.pod('sink-'+node,node,'source',self.cache_url)
            sink['spec']['containers'][0]['resources']['requests'].update(cpu='5m',memory='32Mi')
            items.append(sink)
        self.apply('helpers',items)
        self.call('helpers-ready',['wait','pods','-n',NS,'-l','run-id='+self.run,'--for=condition=Ready','--timeout=240s'],timeout=250)
        self.actual={key:json.loads(self.call('helper-'+key,['get','pod','-n',NS,self.run+'-'+key,'-o','json'])) for key in (*HELPERS,'gateway')}
        self.urls={k:'http://'+p['status']['podIP']+':8080' for k,p in self.actual.items()}
        self.zones={key:'cloud' if key.endswith('-c') else 'edge' for key in HELPERS}
        self.refresh_network('helpers')
        # Preflight's sink is a helper, not an AI operator; unchanged probe bytes.
        self.urls['fusion']=self.urls['source-c']
        self.execute('gateway',"from pathlib import Path;import json;p=Path('/work/peers.json');d=json.loads(p.read_text());d['fusion']=d['source-c'];p.write_text(json.dumps(d))",'probe-alias')

    def refresh_network(self,label):
        peers={k:dict(url=self.urls[k],zone=z) for k,z in self.zones.items()}
        self.execute('gateway',"from pathlib import Path;Path('/work/peers.json').write_text("+repr(json.dumps(peers))+")",'peers-'+label)
        self.cloud=[self.actual[k]['status']['podIP'] for k,z in self.zones.items() if z=='cloud']
        self.edge=[self.actual[k]['status']['podIP'] for k,z in self.zones.items() if z=='edge']
        self.save('peers-'+label+'.json',peers)
        self.shape(None,0,label)

    def guard(self,enabled):
        for key,zone in self.zones.items():
            targets=self.cloud if zone=='edge' else self.edge
            self.net(key,dict(action='block' if enabled else 'unblock',destinations=targets),'guard-'+str(enabled)+'-'+key)
        # Restore both directions on every participant before connectivity probes.
        for key,zone in self.zones.items():
            target='source-c' if zone=='edge' else 'source-e0'
            if enabled:
                code="import urllib.request,json;op=urllib.request.build_opener(urllib.request.ProxyHandler({}));\ntry:\n op.open("+repr(self.urls[target]+'/health')+",timeout=2);print(json.dumps({'blocked':False}))\nexcept Exception as e:print(json.dumps({'blocked':True,'error':repr(e)}))"
                result=json.loads(self.execute(key,code,'bypass-'+key,timeout=10))
                if not result['blocked']: raise RuntimeError('Cross-zone direct bypass exists')
                self.pod_rpc(key,self.urls['gateway']+'/to/'+target+'/health',{},'relay-'+key)
            else:
                result=self.pod_rpc(key,self.urls[target]+'/health',{},'restore-'+key)
                if result['status']!='ready': raise RuntimeError('Direct connectivity not restored')
        self.guarded=enabled

    def route(self,origin,target,relay=True):
        if relay and self.zones[origin]!=self.zones[target]:
            return self.urls['gateway']+'/to/'+target
        return self.urls[target]

    def endpoints(self,placement,relay=True):
        return dict(vision=self.route('source-e0','vision',relay),speech=self.route('source-e1','speech',relay),
            fusion=self.route('source-e0','fusion',relay),vision_fusion=self.route('vision','fusion',relay),
            speech_fusion=self.route('speech','fusion',relay))

    def deploy_operators(self,placement,label):
        if self.operators: raise RuntimeError('Nonselected operators still exist')
        self.generation+=1
        start=time.monotonic()
        items=[]
        reverse={name:node for node,name in NODES.items()}
        for task,node in placement.items():
            pod=self.pod(task,reverse[node] if node else 'c',task,self.cache_url)
            if node is None:
                pod['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']=[
                    {'matchFields':[{'key':'metadata.name','operator':'In','values':[candidate]}]} for candidate in NODES.values()]
            items.append(pod)
        self.apply(label,items)
        self.operators=list(placement)
        for task in self.operators:
            self.call(label+'-ready-'+task,['wait','pod','-n',NS,self.run+'-'+task,'--for=condition=Ready','--timeout=600s'],timeout=610)
            self.actual[task]=json.loads(self.call(label+'-actual-'+task,['get','pod','-n',NS,self.run+'-'+task,'-o','json']))
            self.urls[task]='http://'+self.actual[task]['status']['podIP']+':8080'
            actual=self.actual[task]['spec']['nodeName']
            if placement[task] is not None and actual!=placement[task]:
                raise RuntimeError('Planner pin did not match actual node')
            self.zones[task]='cloud' if actual==NODES['c'] else 'edge'
        actual={t:self.actual[t]['spec']['nodeName'] for t in self.operators}
        self.save(label+'-deployment.json',dict(elapsed_ms=(time.monotonic()-start)*1000,placement=actual,
            pods={t:self.actual[t] for t in self.operators}))
        self.refresh_network(label)
        return actual

    def remove_operators(self,label):
        if self.guarded: self.guard(False)
        items=[p for p in self.resources if p['kind']=='Pod' and p['metadata']['name'] in [self.run+'-'+t for t in self.operators]]
        for item in items:
            for container in item['spec']['containers']:
                self.call(label+'-log-'+item['metadata']['name']+'-'+container['name'],['logs','-n',NS,item['metadata']['name'],'-c',container['name']])
        if items:
            self.call(label+'-delete',['delete','-f','-','--wait=true','--timeout=90s'],json.dumps(dict(apiVersion='v1',kind='List',items=items)),timeout=100)
        self.resources=[r for r in self.resources if r not in items]
        for task in self.operators:
            self.actual.pop(task,None);self.urls.pop(task,None);self.zones.pop(task,None)
        self.operators=[]
        self.refresh_network(label+'-removed')

    def qualify_all(self):
        rows=[]
        for task in TASKS:
            for node in NODES:
                label='qualify-'+task+'-'+node
                print(label,flush=True)
                self.deploy_operators({task:NODES[node]},label)
                health=self.rpc(self.urls[task]+'/health',{},label+'-health')
                if health.get('serving_torch_threads')!=1: raise RuntimeError('Worker thread setting invalid')
                for index,pair in enumerate([self.pairs[0]]*5+self.pairs):
                    q=pair['question_id'];warmup=index<5
                    identity=dict(run_id=self.run,request_id=label+'-'+str(index),remaining_s=120)
                    if task=='fusion':
                        data=dict(identity,inputs={
                            'vision':base64.b64encode((ASSETS/'reference-v1'/f'{q}.npy').read_bytes()).decode(),
                            'speech':base64.b64encode(self.refs[q]['transcript'].encode()).decode()})
                        reply=self.rpc(self.urls[task]+'/infer',data,label+'-'+str(index))
                    else:
                        kind='image' if task=='vision' else 'audio'
                        source='source-e0' if task=='vision' else 'source-e1'
                        reply=self.rpc(self.urls[source]+'/profile',dict(identity,question_id=q,kind=kind,
                            operator_url=self.urls[task],representation='compressed'),label+'-'+str(index))
                    body=base64.b64decode(reply['data']);receipt=reply['receipt']
                    if receipt['model_sha256']!=self.models[task]['model_sha256'] or receipt['pod_uid']!=self.actual[task]['metadata']['uid']:
                        raise RuntimeError('Qualification identity mismatch')
                    if receipt.get('serving_torch_threads')!=1: raise RuntimeError('Serving thread count invalid')
                    if task=='vision':
                        expected=struct.unpack('<512f',(ASSETS/'reference-v1'/f'{q}.npy').read_bytes()[-2048:])
                        observed=struct.unpack('<512f',body[-2048:])
                        ok=all(abs(a-b)<=1e-5+1e-4*abs(b) for a,b in zip(observed,expected))
                    elif task=='speech': ok=body.decode()==self.refs[q]['transcript']
                    else:
                        out=json.loads(body);ref=self.refs[q]
                        ok=out['answer_ids']==ref['answer_ids'] and max(abs(a-b) for a,b in zip(out['scores'],ref['scores']))<=1e-4
                    row=dict(task=task,node=NODES[node],question_id=q,split=pair['split'],warmup=warmup,quality_ok=ok,receipt=receipt)
                    rows.append(row)
                    if not ok:
                        self.save('qualification-failed.json',rows)
                        raise RuntimeError('Quality mismatch; no input excluded')
                    if pair['split']=='calibration' and not warmup:
                        self.cal_outputs[(task,q)]=body
                samples=[r['receipt']['compute_ms'] for r in rows if r['task']==task and r['node']==NODES[node] and r['split']=='calibration' and not r['warmup']]
                self.compute[task][NODES[node]]=quantile(samples)
                self.save(label+'-qualification.json',[r for r in rows if r['task']==task and r['node']==NODES[node]])
                self.remove_operators(label)
        self.save('qualification-all.json',rows)
        self.save('compute-p75.json',self.compute)

    def capacity(self):
        nodes=json.loads(self.call('nodes',['get','nodes','-o','json']))['items']
        pods=json.loads(self.call('pods',['get','pods','-A','-o','json']))['items']
        def amount(raw,cpu=False):
            raw=str(raw)
            if cpu: return float(raw[:-1]) if raw.endswith('m') else float(raw)*1000
            for unit,factor in [('Ki',1/1024),('Mi',1),('Gi',1024),('Ti',1024**2)]:
                if raw.endswith(unit):return float(raw[:-len(unit)])*factor
            return float(raw)/1024**2
        result={}
        for node in nodes:
            name=node['metadata']['name']
            if name not in NODES.values():continue
            if not any(c['type']=='Ready' and c['status']=='True' for c in node['status']['conditions']):raise RuntimeError('Candidate node not Ready')
            alloc=node['status']['allocatable'];cpu=amount(alloc['cpu'],True);mem=amount(alloc['memory'])
            for pod in pods:
                if pod['spec'].get('nodeName')!=name or pod['status'].get('phase') in ('Succeeded','Failed'):continue
                regular=[c.get('resources',{}).get('requests',{}) for c in pod['spec']['containers']]
                init=[c.get('resources',{}).get('requests',{}) for c in pod['spec'].get('initContainers',[])]
                # Refuse sidecar-init accounting ambiguity instead of undercounting.
                if any(c.get('restartPolicy')=='Always' for c in pod['spec'].get('initContainers',[])):
                    raise RuntimeError('Restartable init containers need explicit resource accounting')
                overhead=pod['spec'].get('overhead',{})
                cpu-=max(sum(amount(r.get('cpu',0),True) for r in regular),max((amount(r.get('cpu',0),True) for r in init),default=0))+amount(overhead.get('cpu',0),True)
                mem-=max(sum(amount(r.get('memory',0)) for r in regular),max((amount(r.get('memory',0)) for r in init),default=0))+amount(overhead.get('memory',0))
            result[name]=dict(cpu_free_m=cpu,memory_free_mib=mem)
        return result

    def transfer_profiles(self,condition,mbps,delay,representation,capacity):
        self.shape(mbps,delay,'cal-'+condition)
        table={kind:{} for kind in ('image','audio','vision','speech','return','control')}
        sizes={kind:[] for kind in table}
        calibration=[p for p in self.pairs if p['split']=='calibration']
        for kind in table:
            origins=['e0'] if kind=='image' else ['e1'] if kind=='audio' else list(NODES)
            destinations=['e0'] if kind=='return' else list(NODES)
            if kind=='control': origins=['e0']
            for origin in origins:
                for dest in destinations:
                    observations=[]
                    for index,pair in enumerate(calibration):
                        q=pair['question_id']
                        if kind in ('image','audio'):
                            prefix='raw_' if representation=='raw' else ''
                            filename=Path(pair[prefix+kind+'_path']).name
                            content=(ASSETS/'load-cache/corpus'/filename).read_bytes()
                        elif kind in ('vision','speech'):content=self.cal_outputs[(kind,q)]
                        elif kind=='return':content=self.cal_outputs[('fusion',q)]
                        else:content=b'{}'
                        label=f'{condition}-{kind}-{origin}-{dest}-{index}'
                        row=self.rpc(self.urls['source-'+origin]+'/transfer_profile',dict(run_id=self.run,request_id=label,
                            target_url=self.route('source-'+origin,'sink-'+dest),data=base64.b64encode(content).decode(),count=1),label,timeout=125)
                        if row['source_node']!=NODES[origin] or row['sha256']!=hashlib.sha256(content).hexdigest():raise RuntimeError('Transfer observation identity mismatch')
                        observations.extend(row['samples_ms']);sizes[kind].append(len(content))
                    table[kind][NODES[origin]+'|'+NODES[dest]]=quantile(observations)
        tasks=[]
        for task in TASKS:
            if task in ('vision','speech'):
                kind='image' if task=='vision' else 'audio';source=NODES['e0' if task=='vision' else 'e1']
                inputs=[dict(source_node=source,bytes=statistics.mean(sizes[kind]),transfer_ms=table[kind])]
            else:inputs=[dict(task=t,bytes=statistics.mean(sizes[t]),transfer_ms=table[t]) for t in ('vision','speech')]
            tasks.append(dict(id=task,candidates=list(NODES.values()),compute_ms=self.compute[task],
                image=IMAGE,cpu_m=1010,memory_mib=2080,inputs=inputs))
        output={node+'|'+NODES['e0']:table['return'][node+'|'+NODES['e0']]+table['control'][NODES['e0']+'|'+node] for node in NODES.values()}
        config=dict(provenance='measured',evidence_id=str(self.out),namespace=NS,nodes=capacity,tasks=tasks,
            outputs=[dict(task='fusion',destination_node=NODES['e0'],transfer_ms=output)],
            representation=representation,condition=condition,
            model='p75 isolated compute + payload echo RPC proxies + one open-control RPC proxy plus final-payload echo proxy; no queue/contention prediction')
        prepare.validate(config)
        self.save('profile-'+condition+'.json',config)
        self.save('transfer-'+condition+'.json',dict(table=table,payload_bytes=sizes))
        return config

    def verify_rows(self,rows,placement,representation):
        pairs={p['question_id']:p for p in self.pairs}
        for row in rows:
            if row['status']!='ok':continue
            if not row['top5_order_matches']:raise RuntimeError('Top5 order mismatch')
            for receipt in row['receipts']:
                task=receipt['task'];pod=self.actual[task]
                if receipt['pod_uid']!=pod['metadata']['uid'] or receipt['node']!=placement[task] or receipt['model_sha256']!=self.models[task]['model_sha256']:
                    raise RuntimeError('Evaluation identity mismatch')
                if receipt.get('serving_torch_threads')!=1:raise RuntimeError('Worker thread count invalid')
            if row['source_node']!=NODES['e0'] or row['speech_source_node']!=NODES['e1']:raise RuntimeError('Sensor source moved')
            prefix='raw_' if representation=='raw' else ''
            receipts={r['task']:r for r in row['receipts']};pair=pairs[row['question_id']]
            for task,kind in [('vision','image'),('speech','audio')]:
                if receipts[task]['input_sha256']['source']!=pair[prefix+kind+'_sha256']:raise RuntimeError('Payload hash mismatch')

    def drain(self,label):
        ledger=[];stable=0
        for attempt in range(100):
            states={key:self.rpc(self.urls[key]+'/health',{},'drain-'+label+'-'+key,timeout=20) for key in [*self.zones,'gateway']}
            network=self.net('gateway',dict(action='snapshot'),'drain-qdisc-'+label)
            self.save(label+f'-drain-network-{attempt}.json',network)
            idle=all(states[k]['serving_queue']==0 and not states[k]['serving_active'] and states[k]['http_active']==0 and states[k]['pending_joins']==0 for k in self.operators)
            idle=idle and all(states[k]['active_requests']==0 for k in (*HELPERS,'gateway')) and network['backlog_bytes']==0
            ledger.append(dict(attempt=attempt,idle=idle,states=states,backlog_bytes=network['backlog_bytes']))
            stable=stable+1 if idle else 0
            if stable>=2:
                self.save(label+'.drain.json',ledger);return
            time.sleep(2)
        self.save(label+'.drain-failed.json',ledger)
        raise RuntimeError('Path did not drain')


def main():
    session=Pilot()
    conditions=[('unlimited',None,0,'compressed')]+[(f'{representation}-{rate}',rate,20,representation) for representation in ('compressed','raw') for rate in (100,10,2)]
    random.Random(20261020).shuffle(conditions)
    session.save('design.json',dict(scope='automatic placement execution pilot, one time block',conditions=conditions,
        rate_rps=.1,warmup=2,evaluation_count=6,seed=20261020,quality_inputs=60,calibration_inputs=20,
        policies=['kube-default','server-only','greedy','heft','ours','no-network','no-node-profile'],
        serving='independent persistent stage FIFO; no node-exclusive processor lane',
        compute_representation='compressed; raw recomputation required before raw condition'))
    try:
        scheduler=json.loads(session.call('scheduler-snapshot',['get','pods','-n','kube-system','-l','component=kube-scheduler','-o','json']))
        session.save('scheduler-provenance.json',dict(snapshot=scheduler,
            configuration='Pod command/images captured; no explicit --config found in preflight; plugin weights inferred from version defaults, not live introspection'))
        session.deploy()
        session.network_preflight()
        session.qualify_all()
        capacity=session.capacity();session.save('capacity-frozen.json',capacity)
        session.guard(True)
        profiles={};plans={}
        # This first executable pilot deliberately runs compressed S1/S2 first.
        # Raw conditions require their own compute calibration and are not silently
        # evaluated with compressed service times.
        active=[c for c in conditions if c[3]=='compressed']
        session.save('active-conditions.json',dict(active=active,deferred=[c for c in conditions if c[3]=='raw'],reason='raw needs separate compute calibration'))
        for condition,rate,delay,representation in active:
            print('profile',condition,flush=True)
            profiles[condition]=session.transfer_profiles(condition,rate,delay,representation,capacity)
            plans[condition]=select_policies(profiles[condition],NODES['c'])
            session.drain('cal-'+condition)
        session.save('plans-frozen.json',plans)
        session.guard(False)
        # Observe the actual scheduler's choice once, then remove its probe Pods.
        # All policy proposals, including that observed placement, are evaluated
        # with identical fresh deployments in randomized unique-placement order.
        baseline=session.deploy_operators({t:None for t in TASKS},'kube-default')
        if not prepare.feasible(profiles[active[0][0]],baseline):raise RuntimeError('Baseline placement violates frozen capacity')
        session.save('baseline-placement.json',baseline)
        session.remove_operators('default-selection-complete')
        observations={};mapping={condition:{} for condition,_,_,_ in active}
        jobs=[]
        for condition,rate,delay,representation in active:
            groups={tuple(baseline[t] for t in TASKS):['kube-default']}
            for policy,plan in plans[condition].items():
                if plan['status']!='ok':continue
                key=tuple(plan['placement'][t] for t in TASKS)
                groups.setdefault(key,[]).append(policy)
            for key,policies in groups.items():jobs.append((condition,rate,delay,representation,key,policies))
        random.Random(20261021).shuffle(jobs)
        session.save('execution-order.json',jobs)
        for index,(condition,rate,delay,representation,key,policies) in enumerate(jobs):
            label=f'eval-{index}-{condition}'
            placement=dict(zip(TASKS,key));print('deploy',label,policies,placement,flush=True)
            actual=session.deploy_operators(placement,label)
            session.guard(True);session.shape(rate,delay,label+'-shape')
            session.trace(label+'-warm',actual,rate,delay,.1,2,representation=representation,split='calibration')
            observations[label]=session.trace(label,actual,rate,delay,.1,6,representation=representation)
            for policy in policies:mapping[condition][policy]=label
            session.remove_operators(label)
        session.save('policy-observation-map.json',mapping)
        summaries={label:dict(attempts=len(rows),success=sum(r['status']=='ok' for r in rows),
            mean_ms=statistics.mean([r['e2e_ms'] for r in rows if r['status']=='ok']) if any(r['status']=='ok' for r in rows) else None,
            failures=[r for r in rows if r['status']!='ok']) for label,rows in observations.items()}
        session.save('pilot-summary.json',dict(mapping=mapping,observations=summaries,plans=plans))
        session.shape(None,0,'restored-final')
        session.save('completed.json',dict(status='compressed S1/S2 automatic placement pilot completed',
            remaining='raw compute sweep, repeated blocks, all-placement audit, S3/S4/S5'))
    finally:
        session.close()


if __name__=='__main__': main()
