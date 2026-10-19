"""Owned shared-WAN real-AI diagnostic, with raw-ready edge replay and open loop.

This is a placement/load diagnostic, not a six-policy online benchmark.
All collector phases, failed attempts, source snapshots and cleanup are retained.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import struct
import subprocess
import sys
import time

from run_smoke import ASSETS, HERE, IMAGE, NODES, NS


class Session:
    def __init__(self):
        self.run = datetime.now(timezone.utc).strftime('wan-%Y%m%dt%H%M%S')
        self.out = ASSETS / self.run
        self.out.mkdir(exist_ok=False)
        (self.out / 'collector-source').mkdir()
        for path in HERE.glob('*.py'):
            (self.out / 'collector-source' / path.name).write_bytes(path.read_bytes())
        self.counter, self.resources, self.actual = 0, [], {}
        self.protected = self.snapshot()
        self.save('protected-before.json', self.protected)

    def save(self, name, value):
        with (self.out / name).open('x') as handle:
            json.dump(value, handle, indent=2, default=str)
            handle.write('\n')

    @staticmethod
    def snapshot():
        raw = subprocess.check_output(['rtk', 'proxy', 'kubectl', '--request-timeout=20s', 'get', 'pods', '-A', '-o', 'json'], timeout=30)
        return {p['metadata']['uid']: dict(name=p['metadata']['name'], namespace=p['metadata']['namespace'],
            phase=p['status'].get('phase'), ready=next((c['status'] for c in p['status'].get('conditions', []) if c['type']=='Ready'), None))
            for p in json.loads(raw)['items'] if p['status'].get('phase') == 'Running'}

    def call(self, stem, args, stdin=None, timeout=40):
        self.counter += 1
        tag = f'{self.counter:03d}-{stem}'
        command = ['rtk', 'proxy', 'kubectl', '--request-timeout=20s', *args]
        event = dict(started_at=datetime.now(timezone.utc).isoformat(), command=command, timeout_s=timeout)
        try:
            with (self.out / (tag+'.stdout')).open('wb') as so, (self.out/(tag+'.stderr')).open('wb') as se:
                result = subprocess.run(command, input=stdin.encode() if isinstance(stdin,str) else stdin, stdout=so, stderr=se, timeout=timeout)
            event.update(returncode=result.returncode, status='completed')
            if result.returncode:
                raise RuntimeError(tag+' failed; inspect phase stderr')
            return (self.out/(tag+'.stdout')).read_text()
        except BaseException as error:
            event.update(status='timeout' if isinstance(error,subprocess.TimeoutExpired) else 'error', error=repr(error))
            raise
        finally:
            event['finished_at'] = datetime.now(timezone.utc).isoformat()
            self.save(tag+'.event.json', event)

    def apply(self, stem, items):
        manifest = dict(apiVersion='v1',kind='List',items=items)
        self.save(stem+'.manifest.json',manifest)
        self.call(stem+'-dryrun',['apply','--dry-run=server','-f','-'],json.dumps(manifest))
        self.resources.extend(items)
        self.call(stem,['apply','-f','-'],json.dumps(manifest))

    def meta(self,name):
        return dict(name=name,namespace=NS,labels={'experiment':'topology-profile','run-id':self.run})

    def pod(self,key,zone,task,cache_url=None):
        helper = task in ('source','cache','gateway')
        mounts = [dict(name='work',mountPath='/work')]
        env = [dict(name='NODE_NAME',valueFrom={'fieldRef':{'fieldPath':'spec.nodeName'}}),
               dict(name='POD_UID',valueFrom={'fieldRef':{'fieldPath':'metadata.uid'}}),
               dict(name='TASK_ID',value=task),dict(name='TMPDIR',value='/work/tmp'),
               dict(name='PYTHONDONTWRITEBYTECODE',value='1'),dict(name='HF_HUB_OFFLINE',value='1'),
               dict(name='TRANSFORMERS_OFFLINE',value='1')]
        if task == 'cache':
            command = ['python3','-u','-m','http.server','8080','--directory','/work']
        else:
            mounts.append(dict(name='config',mountPath='/config',readOnly=True))
            env.append(dict(name='ASSET_URL',value=cache_url))
            script = "import os,runpy;os.makedirs('/work/tmp',exist_ok=True);runpy.run_path('/config/"+('wan_gateway.py' if task=='gateway' else 'bootstrap.py')+"',run_name='__main__')"
            command = ['python3','-u','-c',script]
        worker = dict(name='worker',image=IMAGE,imagePullPolicy='IfNotPresent',command=command,env=env,volumeMounts=mounts,
            resources={'requests':{'cpu':'100m' if helper else '1','memory':'256Mi' if helper else '2Gi','ephemeral-storage':'128Mi'},
                       'limits':{'cpu':'1','memory':'512Mi' if helper else '2Gi','ephemeral-storage':'8Gi'}},
            readinessProbe={'httpGet':{'path':'/' if task=='cache' else '/health','port':8080},'periodSeconds':5,'timeoutSeconds':3},
            securityContext={'runAsUser':65532,'runAsGroup':65532,'runAsNonRoot':True,'allowPrivilegeEscalation':False,
                             'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}})
        containers=[worker]
        if task != 'cache':
            wheel='pyroute2-0.9.6-py3-none-any.whl'
            wheelhash=json.loads((ASSETS/'load-bundles.json').read_text())[wheel]['sha256']
            setup="import urllib.request,io,zipfile,hashlib,time;op=urllib.request.build_opener(urllib.request.ProxyHandler({}));b=op.open("+repr(cache_url+'/'+wheel)+").read();assert hashlib.sha256(b).hexdigest()=="+repr(wheelhash)+";zipfile.ZipFile(io.BytesIO(b)).extractall('/work/net');print('netctl-ready',flush=True);time.sleep(10800)"
            containers.append(dict(name='netctl',image=IMAGE,command=['python3','-u','-c',setup],
                env=[dict(name='RESEARCH_LINK_CONTROL',value='owned-pod-netns'),dict(name='PYTHONPATH',value='/work/net:/config')],
                volumeMounts=mounts,resources={'requests':{'cpu':'10m','memory':'32Mi'},'limits':{'cpu':'1','memory':'96Mi'}},
                securityContext={'runAsUser':0,'runAsNonRoot':False,'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,
                                 'capabilities':{'drop':['ALL'],'add':['NET_ADMIN']}}))
        spec=dict(schedulerName='default-scheduler',automountServiceAccountToken=False,restartPolicy='Never',
            activeDeadlineSeconds=10800,terminationGracePeriodSeconds=3,securityContext={'fsGroup':65532},
            affinity={'nodeAffinity':{'requiredDuringSchedulingIgnoredDuringExecution':{'nodeSelectorTerms':[
                {'matchFields':[{'key':'metadata.name','operator':'In','values':[NODES[zone]]}]}]}}},containers=containers,
            volumes=[{'name':'work','emptyDir':{'sizeLimit':'8Gi'}}])
        if task!='cache':spec['volumes'].append({'name':'config','configMap':{'name':self.run}})
        return dict(apiVersion='v1',kind='Pod',metadata=self.meta(self.run+'-'+key),spec=spec)

    def execute(self,key,code,stem='operation',timeout=60,container='worker',stdin=None):
        return self.call(stem,['exec','-i','-n',NS,self.run+'-'+key,'-c',container,'--','python3','-u','-c',code],stdin,timeout)

    def net(self,key,spec,stem):
        return json.loads(self.execute(key,"import runpy;runpy.run_path('/config/net_control.py',run_name='__main__')",stem,container='netctl',stdin=json.dumps(spec)))

    def rpc(self,url,body,stem='rpc',timeout=150,retain=True):
        # Control only: input payloads are read/transmitted by owned edge sources.
        from http_runtime import request
        import urllib.error
        for attempt in range(3):
            self.counter+=1
            tag=f'{self.counter:03d}-{stem}-http-{attempt}'
            event=dict(started_at=datetime.now(timezone.utc).isoformat(),method='POST',url=url,timeout_s=timeout,control=body)
            try:
                result=request(url,body,timeout=timeout)
                if retain:self.save(tag+'.response.json',result)
                event.update(status='completed',response_json_sha256=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest())
                return result
            except urllib.error.HTTPError as error:
                event.update(status='http_error',code=error.code,error=str(error),body=error.read().decode(errors='replace'))
                raise
            except (OSError, TimeoutError) as error:
                event.update(status='transport_error',error=repr(error))
                if attempt==2:raise
                time.sleep(1)
            finally:
                event['finished_at']=datetime.now(timezone.utc).isoformat()
                self.save(tag+'.event.json',event)

    def pod_rpc(self,key,url,body,stem,timeout=20):
        # Bypass/restore proof must originate in each guarded Pod, not collector.
        code="import sys,json;sys.path.insert(0,'/config');from http_runtime import request;print(json.dumps(request("+repr(url)+','+repr(body)+',timeout='+str(timeout-5)+')))'
        return json.loads(self.execute(key,code,stem,timeout))

    def deploy(self):
        self.apply('cache',[self.pod('cache','c','cache')])
        self.call('cache-ready',['wait','pod','-n',NS,self.run+'-cache','--for=condition=Ready','--timeout=90s'],timeout=100)
        cache=json.loads(self.call('cache-state',['get','pod','-n',NS,self.run+'-cache','-o','json']))
        cache_url='http://'+cache['status']['podIP']+':8080'
        event=dict(started_at=datetime.now(timezone.utc).isoformat(),scope='asset deployment before measurement')
        try:
            with (ASSETS/'load-cache-upload.tar').open('rb') as source, (self.out/'upload.stdout').open('wb') as so,(self.out/'upload.stderr').open('wb') as se:
                result=subprocess.run(['rtk','proxy','kubectl','--request-timeout=20s','exec','-i','-n',NS,self.run+'-cache','--','python3','-c',"import tarfile,sys;tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall('/work',filter='data')"],stdin=source,stdout=so,stderr=se,timeout=300)
                event['returncode']=result.returncode
                if result.returncode:raise RuntimeError('cache upload failed')
        finally:
            event['finished_at']=datetime.now(timezone.utc).isoformat();self.save('upload.event.json',event)
        files=['http_runtime.py','edge_client.py','model_adapter.py','bootstrap.py','load_trace.py','net_control.py','wan_gateway.py']
        cm=dict(apiVersion='v1',kind='ConfigMap',metadata=self.meta(self.run),data={f:(HERE/f).read_text() for f in files})
        cm['data']['bundles.json']=(ASSETS/'load-bundles.json').read_text()
        self.zones={'source-e0':'edge','source-e1':'edge','v-edge':'edge','s-edge':'edge','v-cloud':'cloud','s-cloud':'cloud','fusion':'cloud'}
        items=[cm,self.pod('gateway','c','gateway',cache_url)]
        for key,zone,task in [('source-e0','e0','source'),('source-e1','e1','source'),('v-edge','e0','vision'),
                              ('s-edge','e1','speech'),('v-cloud','c','vision'),('s-cloud','c','speech'),('fusion','c','fusion')]:
            items.append(self.pod(key,zone,task,cache_url))
        self.apply('workers',items)
        self.call('workers-ready',['wait','pods','-n',NS,'-l','run-id='+self.run,'--for=condition=Ready','--timeout=600s'],timeout=610)
        for key in [*self.zones,'gateway']:
            self.actual[key]=json.loads(self.call('state-'+key,['get','pod','-n',NS,self.run+'-'+key,'-o','json']))
        self.save('actual-pods.json',self.actual)
        self.urls={k:'http://'+v['status']['podIP']+':8080' for k,v in self.actual.items()}
        peers={k:dict(url=self.urls[k],zone=z) for k,z in self.zones.items()}
        self.execute('gateway',"from pathlib import Path;Path('/work/peers.json').write_text("+repr(json.dumps(peers))+")",'freeze-peers')
        self.cloud=[self.actual[k]['status']['podIP'] for k,z in self.zones.items() if z=='cloud']
        self.edge=[self.actual[k]['status']['podIP'] for k,z in self.zones.items() if z=='edge']
        self.save('peers.json',peers)
        self.shape(None,0,'initial')  # Probe kernel API and preserve actual qdiscs.

    def shape(self,mbps,delay,label):
        self.save('net-'+label+'.json',self.net('gateway',dict(action='shape',mbps=mbps,delay_ms=delay,cloud=self.cloud,edge=self.edge),'shape-'+label))

    def endpoints(self,placement,relay=True):
        vision,speech=placement
        def route(origin,target):
            if relay and origin!=self.zones[target]:return self.urls['gateway']+'/to/'+target
            return self.urls[target]
        return dict(vision=route('edge',vision),speech=route('edge',speech),fusion=route('edge','fusion'),
                    vision_fusion=route(self.zones[vision],'fusion'),speech_fusion=route(self.zones[speech],'fusion'))

    def guard(self,enabled):
        for key,zone in self.zones.items():
            destinations=self.cloud if zone=='edge' else self.edge
            self.save(('block-' if enabled else 'unblock-')+key+'.json',self.net(key,dict(action='block' if enabled else 'unblock',destinations=destinations),('block-' if enabled else 'unblock-')+key))
        # Direct cross-zone health must fail for every owned non-gateway endpoint.
        if enabled:
            for key,zone in self.zones.items():
                target='fusion' if zone=='edge' else 'source-e0'
                code="import urllib.request,json;op=urllib.request.build_opener(urllib.request.ProxyHandler({}));\ntry:\n op.open("+repr(self.urls[target]+'/health')+",timeout=2);print(json.dumps({'blocked':False}))\nexcept Exception as e:print(json.dumps({'blocked':True,'error':repr(e)}))"
                result=json.loads(self.execute(key,code,'bypass-'+key,timeout=10))
                self.save('bypass-'+key+'.json',result)
                if not result['blocked']:raise RuntimeError('Direct route bypass remains')
                self.pod_rpc(key,self.urls['gateway']+'/to/'+target+'/health',{},'relay-probe-'+key)
        else:
            for key,zone in self.zones.items():
                target='fusion' if zone=='edge' else 'source-e0'
                restored=self.pod_rpc(key,self.urls[target]+'/health',{},'restored-probe-'+key)
                if restored['status']!='ready':raise RuntimeError('Direct connectivity not restored')
                self.save('restored-'+key+'.json',restored)

    def network_preflight(self):
        """Actual corpus upload probes; application payload counts plus kernel dumps."""
        records=[]
        pair=self.pairs[0]
        path=pair['raw_image_path']
        for rate in (100,10,2):
            self.shape(rate,0,'capacity-'+str(rate))
            code="import sys,json,time;sys.path.insert(0,'/config');from pathlib import Path;from http_runtime import request,digest;b=Path("+repr(path)+").read_bytes();start=time.monotonic();rows=[];"
            code+="\nfor i in range(4):\n r=request("+repr(self.urls['gateway']+'/to/fusion/sink')+",{'data':b},timeout=60);assert r['sha256']==digest(b) and r['bytes']==len(b);rows.append(r)\nprint(json.dumps({'bytes':len(b)*4,'elapsed_s':time.monotonic()-start,'hashes_ok':True}))"
            started=time.monotonic()
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs=[pool.submit(self.execute,key,code,'capacity-'+str(rate)+'-'+key,timeout=150) for key in ('source-e0','source-e1')]
                results=[json.loads(job.result()) for job in jobs]
            elapsed=time.monotonic()-started
            throughput=sum(r['bytes'] for r in results)*8/1e6/elapsed
            record=dict(mbps=rate,aggregate_payload_mbps=throughput,collector_elapsed_s=elapsed,sources=results)
            records.append(record)
            self.save('capacity-'+str(rate)+'.json',record)
            # 100 Mbps can be limited by the physical path; 10/2 must realize the cap.
            if throughput>rate*1.12 or (rate in (2,10) and throughput<rate*.75):
                raise RuntimeError('Aggregate cap validation failed: '+repr(record))
            self.save('capacity-'+str(rate)+'-qdisc.json',self.net('gateway',dict(action='snapshot'),'capacity-qdisc-'+str(rate)))
        # Reverse direction: cache sends actual raw corpus data to both edge sources.
        self.shape(2,0,'reverse-capacity')
        blob=Path(ASSETS/'load-cache/corpus'/Path(path).name).read_bytes()
        # Cache already holds source.tar; extract one real fixture before probing.
        extract="import tarfile;tarfile.open('/work/source.tar').extract("+repr('corpus/'+Path(path).name)+",'/work',filter='data')"
        self.execute('cache',extract,'reverse-extract')
        code="import urllib.request,json,time,hashlib;from concurrent.futures import ThreadPoolExecutor;from pathlib import Path;b=Path("+repr(path)+").read_bytes();op=urllib.request.build_opener(urllib.request.ProxyHandler({}));start=time.monotonic();\ndef upload(url):\n for _ in range(4):\n  r=json.loads(op.open(urllib.request.Request(url,json.dumps({'data':__import__('base64').b64encode(b).decode()}).encode(),{'Content-Type':'application/json'}),timeout=60).read());assert r['sha256']==hashlib.sha256(b).hexdigest()\nwith ThreadPoolExecutor(2) as pool:list(pool.map(upload,"+repr([self.urls['gateway']+'/to/'+key+'/sink' for key in ('source-e0','source-e1')])+"))\nprint(json.dumps({'payload_bytes':len(b)*8,'wire_base64_bytes':((len(b)+2)//3*4)*8,'elapsed_s':time.monotonic()-start,'hashes_ok':True}))"
        reverse=json.loads(self.execute('cache',code,'capacity-reverse',timeout=150))
        reverse['wire_base64_mbps']=reverse['wire_base64_bytes']*8/1e6/reverse['elapsed_s']
        self.save('reverse-capacity.json',reverse)
        if not 1.5<reverse['wire_base64_mbps']<2.24:raise RuntimeError('Reverse aggregate cap validation failed')
        # Same tiny binary control roundtrip probes at delay0/20 on the same relay.
        latency=[]
        for delay in (0,20):
            self.shape(100,delay,'latency-'+str(delay))
            code="import sys,time,json;sys.path.insert(0,'/config');from http_runtime import request;values=[];\nfor _ in range(10):\n t=time.monotonic();request("+repr(self.urls['gateway']+'/to/fusion/sink')+",{'data':b'probe'});values.append((time.monotonic()-t)*1000)\nprint(json.dumps({'application_roundtrip_ms':values}))"
            latency.append(dict(oneway_delay_ms=delay,**json.loads(self.execute('source-e0',code,'latency-'+str(delay)))))
        self.save('latency-probes.json',latency)
        means=[sum(r['application_roundtrip_ms'])/10 for r in latency]
        if means[1]-means[0]<30:raise RuntimeError('Two-direction delay not observed')
        self.shape(None,0,'post-preflight')
        self.save('network-preflight.json',dict(status='passed',capacity=records,reverse=reverse,
            latency=latency,semantics='split TCP relay; application RTT includes multiple handshakes, not direct ICMP RTT'))

    def qualify(self):
        """Raw decoding must preserve reference quality for all60 inputs on each branch candidate."""
        refs={r['question_id']:r for r in map(json.loads,(ASSETS/'reference-v1/results.jsonl').read_text().splitlines())}
        records=[]
        self.save('source-input-readiness.json',{key:self.rpc(self.urls[key]+'/health',{},'source-prepared-'+key) for key in ('source-e0','source-e1')})
        for pair in self.pairs:
            qid=pair['question_id']
            expected=(ASSETS/'reference-v1'/f'{qid}.npy').read_bytes()
            values=struct.unpack('<512f',expected[-2048:])
            for key,kind,task in [('v-edge','image','vision'),('v-cloud','image','vision'),('s-edge','audio','speech'),('s-cloud','audio','speech')]:
                source='source-e0' if kind=='image' else 'source-e1'
                reply=self.rpc(self.urls[source]+'/profile',dict(run_id=self.run,request_id='quality-'+str(qid)+'-'+key,
                    question_id=qid,kind=kind,operator_url=self.urls[key],representation='raw',remaining_s=60),
                    'quality-'+str(qid)+'-'+key,timeout=65)
                body=__import__('base64').b64decode(reply['data'])
                if task=='vision':
                    observed=struct.unpack('<512f',body[-2048:])
                    ok=all(abs(a-b)<=1e-5+1e-4*abs(b) for a,b in zip(observed,values))
                    error=max(abs(a-b) for a,b in zip(observed,values))
                else:ok=body.decode()==refs[qid]['transcript'];error=None
                row=dict(question_id=qid,split=pair['split'],candidate=key,quality_ok=ok,feature_max_abs_error=error,receipt=reply['receipt'],output_sha256=hashlib.sha256(body).hexdigest())
                if reply['receipt'].get('serving_torch_threads')!=1:raise RuntimeError('Worker thread contract unverified')
                records.append(row)
                if not ok:
                    self.save('qualification-failed.json',records)
                    raise RuntimeError('Raw reference quality gate failed; no exclusions')
        self.save('qualification.json',records)
        # Only calibration rows may define costs; evaluation quality is a predeclared gate.
        calibration=[r for r in records if r['split']=='calibration']
        self.save('fresh-calibration.json',dict(scope='isolated raw-input service times; no fitted policy comparison',rows=calibration))

    def trace(self,label,placement,mbps,delay,rate,count,relay=True,representation='raw',split='evaluation'):
        pairs=[p for p in self.pairs if p['split']==split]
        config=dict(run_id=self.run,phase=label,question_ids=[pairs[i%len(pairs)]['question_id'] for i in range(count)],
            offsets_s=[i/rate for i in range(count)],endpoints=self.endpoints(placement,relay),
            remote_source=self.urls['source-e1'],representation=representation,deadline_s=120,max_inflight=32,
            placement=placement,mbps=mbps,oneway_delay_ms=delay,rate_rps=rate)
        self.save(label+'.config.json',config)
        print('phase',label,flush=True)
        self.rpc(self.urls['source-e0']+'/start_trace',config,'start-'+label,timeout=15)
        stop=time.monotonic()+(count-1)/rate+270
        events=[]
        with (self.out/(label+'.events.jsonl')).open('x') as handle:
            while True:
                status=self.rpc(self.urls['source-e0']+'/trace_status',{'phase':label},'poll-'+label,timeout=15,retain=False)
                new=status['events'][len(events):]
                for row in new:handle.write(json.dumps(row)+'\n')
                handle.flush()
                events=status['events']
                if status['status']=='complete':break
                if status['status']=='failed':raise RuntimeError('Source trace failed: '+str(status.get('error')))
                if time.monotonic()>stop:raise TimeoutError('Source trace control deadline exceeded')
                time.sleep(2)
        rows=[e for e in events if e['event']=='terminal']
        if len(rows)!=count or not any(e['event']=='phase_end' for e in events):raise RuntimeError('Missing terminal ledger')
        self.save(label+'.rows.json',rows)
        self.verify_rows(rows,placement,representation)
        self.drain(label)
        self.save(label+'.qdisc.json',self.net('gateway',dict(action='snapshot'),'snapshot-'+label))
        self.save(label+'.relay-counters.json',self.rpc(self.urls['gateway']+'/health',{},'relay-counters-'+label))
        return rows

    def verify_rows(self,rows,placement,representation):
        byid={p['question_id']:p for p in self.pairs}
        expected_models=json.loads((ASSETS/'reference-v1/metadata.json').read_text())['stages']
        for row in rows:
            if row['status']!='ok':continue
            receipt={r['task']:r for r in row['receipts']}
            for task,key in [('vision',placement[0]),('speech',placement[1]),('fusion','fusion')]:
                r=receipt[task];p=self.actual[key]
                if r['pod_uid']!=p['metadata']['uid'] or r['node']!=p['spec']['nodeName'] or r['model_sha256']!=expected_models[task]['model_sha256']:
                    raise RuntimeError('Runtime identity/model hash mismatch')
                if r.get('serving_torch_threads')!=1:raise RuntimeError('Worker thread contract unverified')
            prefix='raw_' if representation=='raw' else ''
            pair=byid[row['question_id']]
            if receipt['vision']['input_sha256']['source']!=pair[prefix+'image_sha256'] or receipt['speech']['input_sha256']['source']!=pair[prefix+'audio_sha256']:
                raise RuntimeError('Input transfer hash mismatch')
            if row['source_node']!=NODES['e0'] or row['speech_source_node']!=NODES['e1']:
                raise RuntimeError('Input source moved')

    def drain(self,label):
        # Entire data path must be stably idle before a qdisc can be replaced.
        stable=0
        ledger=[]
        for attempt in range(100):
            states={key:self.rpc(self.urls[key]+'/health',{},'drain-'+label+'-'+key,timeout=20)
                for key in ('v-edge','s-edge','v-cloud','s-cloud','fusion','source-e0','source-e1','gateway')}
            network=self.net('gateway',dict(action='snapshot'),'drain-qdisc-'+label)
            idle=all(states[k]['serving_queue']==0 and not states[k]['serving_active'] and states[k]['http_active']==0 and states[k]['pending_joins']==0
                for k in ('v-edge','s-edge','v-cloud','s-cloud','fusion'))
            idle=idle and all(states[k]['active_requests']==0 for k in ('source-e0','source-e1','gateway')) and network['backlog_bytes']==0
            ledger.append(dict(attempt=attempt,idle=idle,states=states,backlog_bytes=network['backlog_bytes']))
            stable=stable+1 if idle else 0
            if stable>=2:
                self.save(label+'.drain.json',ledger)
                return
            time.sleep(2)
        self.save(label+'.drain-failed.json',ledger)
        raise RuntimeError('Data path did not drain; abort before changing network condition')

    def close(self):
        errors=[]
        for item in self.resources:
            if item['kind']=='Pod':
                for container in item['spec']['containers']:
                    try:self.call('logs-'+item['metadata']['name']+'-'+container['name'],['logs','-n',NS,item['metadata']['name'],'-c',container['name']])
                    except Exception as error:errors.append(repr(error))
        try:self.call('events',['get','events','-n',NS,'-o','json'])
        except Exception as error:errors.append(repr(error))
        if self.resources:
            try:
                self.call('cleanup',['delete','-f','-','--ignore-not-found=true','--wait=false'],json.dumps(dict(apiVersion='v1',kind='List',items=self.resources)))
                self.call('deleted',['wait','--for=delete','pod','-n',NS,'-l','run-id='+self.run,'--timeout=90s'],timeout=100)
            except Exception as error:errors.append(repr(error))
        remaining=json.loads(self.call('remaining',['get','pods,configmaps','-n',NS,'-l','run-id='+self.run,'-o','json']))['items']
        after=self.snapshot()
        changes=[dict(uid=uid,before=before,after=after.get(uid)) for uid,before in self.protected.items() if uid not in after or before['phase']!=after[uid]['phase'] or before['ready']!=after[uid]['ready']]
        self.save('protected-after.json',dict(protected_count=len(self.protected),changes=changes))
        self.save('cleanup.json',dict(remaining_owned=len(remaining),errors=errors))
        print('evidence',self.out,flush=True)
        if remaining:raise RuntimeError('Owned resources remain')


def main():
    session=Session()
    session.pairs=json.loads((ASSETS/'load-pairs.json').read_text())['pairs']
    # Freeze diagnostic design before creating workers, without selecting on results.
    placements={'cloud':('v-cloud','s-cloud'),'edge':('v-edge','s-edge'),'mixed':('v-edge','s-cloud')}
    conditions=[(b,20,p) for b in (100,10,2) for p in placements]
    random.Random(20261019).shuffle(conditions)
    session.save('design.json',dict(scope='single deployment shared-WAN placement/load diagnostic; not six-policy benchmark',
        baseline_commit='46fd850',placements=placements,low_rate_rps=.1,low_count=6,load_rates=[.5,1],load_count=40,
        deadline_s=120,serving='one FIFO per operator Pod, not node-wide FIFO',qdisc='aggregate per direction, netem64 packet queue,4096B TBF burst',
        low_order=conditions,seed=20261019,calibration_inputs=20,evaluation_inputs=40,
        replay='offline decoded fixture, input-ready clock, actual edge Pod emission',
        quality={'feature_rtol':1e-4,'feature_atol':1e-5,'transcript':'exact','answer_id':'exact','score_atol':1e-4}))
    try:
        session.deploy()
        session.network_preflight()
        session.qualify()
        for label,placement in placements.items():
            session.trace('native-'+label,placement,None,0,.1,6,relay=False)
        session.guard(True)
        session.shape(None,0,'relay-unlimited')
        for label,placement in placements.items():session.trace('relay-unlimited-'+label,placement,None,0,.1,6)
        for i,(mbps,delay,label) in enumerate(conditions):
            session.shape(mbps,delay,'low-'+str(i))
            session.trace(f'low-{mbps}-{label}',placements[label],mbps,delay,.1,6)
        session.shape(2,0,'delay-zero')
        for label,placement in placements.items():session.trace('delay-zero-'+label,placement,2,0,.1,6)
        session.shape(2,20,'compressed-control')
        for label,placement in placements.items():
            session.trace('compressed-2-'+label,placement,2,20,.1,6,representation='compressed')
        # Higher arrival rates are a finite-window stress experiment, with stop/drain.
        high=[(b,r,p) for b in (10,2) for r in (.5,1) for p in ('cloud','edge','mixed')]
        random.Random(20261020).shuffle(high)
        session.save('high-order.json',high)
        for i,(mbps,rate,label) in enumerate(high):
            session.shape(mbps,20,'high-'+str(i))
            session.trace(f'load-{mbps}-{rate}-{label}',placements[label],mbps,20,rate,40)
        session.shape(None,0,'restored')
        session.guard(False)
        session.save('completed.json',dict(status='all diagnostic phases completed'))
    finally:
        session.close()


if __name__=='__main__':main()
