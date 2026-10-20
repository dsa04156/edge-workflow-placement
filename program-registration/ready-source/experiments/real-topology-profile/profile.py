"""Run a user-provided real operator adapter on the target node; preserve samples."""
import argparse,hashlib,importlib,json,os,platform,socket,time
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--adapter',required=True,help='module:function, bytes -> bytes; synchronize GPU before returning')
    p.add_argument('--input',required=True);p.add_argument('--node',required=True);p.add_argument('--task',required=True)
    p.add_argument('--model-sha256',required=True);p.add_argument('--out',required=True);p.add_argument('--warmup',type=int,default=5);p.add_argument('--repeat',type=int,default=100)
    a=p.parse_args()
    if a.repeat<2 or a.warmup<0: raise ValueError('repeat>=2,warmup>=0 required')
    output=Path(a.out)
    if output.exists(): raise FileExistsError(output)
    mod,fn=a.adapter.split(':');call=getattr(importlib.import_module(mod),fn);data=Path(a.input).read_bytes();rows=[]
    metadata={'task':a.task,'declared_node':a.node,'hostname':socket.gethostname(),'pod_node':os.getenv('NODE_NAME'),
        'adapter':a.adapter,'model_sha256':a.model_sha256,'input_sha256':hashlib.sha256(data).hexdigest(),
        'input_bytes':len(data),'python':platform.python_version(),'started_unix_ns':time.time_ns(),
        'cost_boundary':'adapter call including its serialization; excludes import/model load; accelerator synchronization is adapter responsibility'}
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as f:
        f.write(json.dumps({'metadata':metadata})+'\n');f.flush()
        for i in range(a.warmup+a.repeat):
            st=time.perf_counter_ns()
            try:
                result=call(data)
                if not isinstance(result,bytes): raise TypeError('Adapter must return bytes')
                row={'sample':i,'warmup':i<a.warmup,'status':'ok','elapsed_ms':(time.perf_counter_ns()-st)/1e6,
                    'output_bytes':len(result),'output_sha256':hashlib.sha256(result).hexdigest()}
            except Exception as ex:
                row={'sample':i,'warmup':i<a.warmup,'status':'error','elapsed_ms':(time.perf_counter_ns()-st)/1e6,'error':repr(ex)}
                f.write(json.dumps(row)+'\n');f.flush();raise
            f.write(json.dumps(row)+'\n');f.flush();rows.append(row)
    values=sorted(r['elapsed_ms'] for r in rows if not r['warmup'])
    def q(p):
        pos=(len(values)-1)*p;i=int(pos);return values[i]+(values[min(i+1,len(values)-1)]-values[i])*(pos-i)
    print(json.dumps({'n':len(values),'p50_ms':q(.5),'p75_ms':q(.75),'p95_ms':q(.95),'quality_validated':False}))
if __name__=='__main__':main()
