"""Verify actual cross-node HTTP shaping and restore/delete only this run's Pods."""

from datetime import datetime,timezone
import hashlib,json,subprocess,time,urllib.request,sys
from pathlib import Path


def main():
    base=Path(sys.argv[1] if len(sys.argv)>1 else 'artifacts/edge-link-preflight');base.mkdir(exist_ok=True);assert not (base/'run.json').exists()
    run=datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%S%f');prefix='edge-link-check-'+run
    command=['rtk','proxy','kubectl','--request-timeout=20s','-n','scheduler-research']
    template=json.loads(Path('artifacts/measured-policy-bridge/deployment.json').read_text())
    image=next(p['spec']['containers'][0]['image'] for p in template['items'] if p['kind']=='Pod' and p['metadata']['labels']['research.scheduler/alias']=='arm')
    server="""from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import hashlib,json
class H(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_POST(self):
  data=self.rfile.read(int(self.headers['Content-Length']))
  body=json.dumps({'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}).encode()
  self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
ThreadingHTTPServer(('0.0.0.0',8080),H).serve_forever()
"""
    pods=[]
    for alias,node in [('arm','etri-dev0003-raspi5'),('server','etri-ser0002-cgnmsb')]:
        pods.append({'apiVersion':'v1','kind':'Pod','metadata':{'name':prefix+'-'+alias,'namespace':'scheduler-research','labels':{'research.scheduler/run':run,'research.scheduler/stage':'edge-link-preflight'}},
          'spec':{'nodeName':node,'restartPolicy':'Never','activeDeadlineSeconds':300,'automountServiceAccountToken':False,
                  'securityContext':{'runAsUser':0,'seccompProfile':{'type':'RuntimeDefault'}},
                  'containers':[{'name':'probe','image':image,'command':['python3','-u','-c',server],
                                 'env':[{'name':'RESEARCH_LINK_CONTROL','value':'owned-pod-netns'},{'name':'PYTHONPATH','value':'/work/sdk'}],
                                 'securityContext':{'readOnlyRootFilesystem':True,'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL'],'add':['NET_ADMIN']}},
                                 'resources':{'requests':{'cpu':'20m','memory':'32Mi'},'limits':{'cpu':'500m','memory':'128Mi'}},
                                 'volumeMounts':[{'name':'work','mountPath':'/work'}]}],'volumes':[{'name':'work','emptyDir':{}}]}})
    path=base/'pods.json';path.write_text(json.dumps({'apiVersion':'v1','kind':'List','items':pods}))
    (base/'run.json').write_text(json.dumps({'run_id':run,'prefix':prefix,'scope':'Only research Pod network namespaces; no host networking or production configuration','frozen_at':datetime.now(timezone.utc).isoformat()},indent=2))
    def call(*args,**kwargs):return subprocess.check_output([*command,*args],**kwargs)
    try:
        subprocess.run([*command,'create','-f',str(path)],check=True)
        addresses={}
        wheel=json.loads(Path('artifacts/edge-link-preflight/wheel.json').read_text());(base/'wheel.json').write_text(json.dumps(wheel));data=urllib.request.urlopen(wheel['url'],timeout=30).read()
        assert len(data)==wheel['size'] and hashlib.sha256(data).hexdigest()==wheel['sha256']
        for pod in pods:
            name=pod['metadata']['name'];subprocess.run([*command,'wait','--for=condition=Ready','pod/'+name,'--timeout=90s'],check=True)
            addresses[name]=json.loads(call('get','pod',name,'-o','json'))['status']['podIP']
            call('exec','-i',name,'--','python3','-c',"import io,sys,zipfile;zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())).extractall('/work/sdk')",input=data)
        (base/'addresses.json').write_text(json.dumps(addresses))
        arm=pods[0]['metadata']['name'];target=addresses[pods[1]['metadata']['name']]
        control=Path('experiments/e0/pod_link_control.py').read_text()
        call('exec','-i',arm,'--','python3','-c',"import sys;open('/work/control.py','w').write(sys.stdin.read())",input=control.encode())
        rows=[]
        for label,mbps,delay in [('native',None,0),('cap50',50,0),('cap20',20,0),('restored',None,0)]:
            setting=json.loads(call('exec','-i',arm,'--','python3','/work/control.py',input=json.dumps({'destinations':[target],'mbps':mbps,'delay_ms':delay}).encode()))
            script="""import http.client,hashlib,json,time
for size in [4000,240512,602112]:
 for i in range(4):
  data=b'x'*size;c=http.client.HTTPConnection(%r,8080,timeout=10)
  start=time.perf_counter();c.request('POST','/',data);r=c.getresponse();got=json.loads(r.read());elapsed=(time.perf_counter()-start)*1000;c.close()
  assert got=={'bytes':size,'sha256':hashlib.sha256(data).hexdigest()}
  print(json.dumps({'bytes':size,'iteration':i,'ms':elapsed}))
""" % target
            raw=call('exec',arm,'--','python3','-c',script,text=True)
            row={'regime':label,'control':setting,'requests':[json.loads(s) for s in raw.splitlines()]};rows.append(row)
            (base/(label+'.json')).write_text(json.dumps(row,indent=2));print(label,[(x['bytes'],round(x['ms'],2)) for x in row['requests'] if x['iteration']==3],flush=True)
        (base/'complete.json').write_text(json.dumps({'regimes':len(rows),'requests':sum(len(x['requests']) for x in rows),'completed_at':datetime.now(timezone.utc).isoformat()},indent=2))
    except BaseException as error:
        (base/'failure.json').write_text(json.dumps({'type':type(error).__name__,'message':str(error)},indent=2));raise
    finally:
        for pod in pods:
            name=pod['metadata']['name'];raw=call('get','pod',name,'--ignore-not-found','-o','json')
            if raw.strip():
                assert json.loads(raw)['metadata']['labels']['research.scheduler/run']==run
                subprocess.run([*command,'delete','pod',name,'--wait=true','--timeout=60s'],check=True)
        (base/'cleanup.json').write_text(json.dumps({'remaining_owned_resources':len(json.loads(call('get','pods','-l','research.scheduler/run='+run,'-o','json'))['items'])}))


if __name__=='__main__':main()
