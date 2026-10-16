"""Three real tasks with direct worker-to-worker forwarding and local calls."""

import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import threading
import time
from urllib.parse import urlparse

from probe import MODEL_URL, MODEL_SHA256, IMAGE_URL, IMAGE_SHA256, fetch, emit
from transport import preprocess, TENSOR_SHA256


def serve():
    import numpy as np
    import onnxruntime as ort

    alias,node,backend=os.environ['ALIAS'],os.environ['NODE_NAME'],os.environ['BACKEND']
    peers=json.loads(os.environ['PEERS_JSON'])
    work=Path(os.environ.get('WORKDIR','/work'))
    asset_base=os.environ.get('ASSET_BASE')
    model_path=work/'model.onnx'
    model_path.write_bytes(fetch(asset_base+'/model.onnx' if asset_base else MODEL_URL,MODEL_SHA256))
    image=fetch(asset_base+'/dog.jpg' if asset_base else IMAGE_URL,IMAGE_SHA256)
    fixture=preprocess(image)
    assert hashlib.sha256(fixture.tobytes()).hexdigest()==TENSOR_SHA256
    opts=ort.SessionOptions();opts.intra_op_num_threads=opts.inter_op_num_threads=1
    session=ort.InferenceSession(str(model_path),opts,providers=['CPUExecutionProvider'])
    reference=session.run(None,{session.get_inputs()[0].name:fixture})[0].reshape(-1)
    corpus={}
    if os.environ.get('CORPUS_FILE'):
        corpus=json.loads(Path(os.environ['CORPUS_FILE']).read_text())
        for sample in corpus.values():
            local=os.environ.get('CORPUS_ASSET_DIR')
            data=(Path(local)/sample['filename']).read_bytes() if local else fetch(
                asset_base+'/corpus/'+sample['filename'],sample['sha256'])
            assert len(data)==sample['bytes'] and hashlib.sha256(data).hexdigest()==sample['sha256']
            assert hashlib.sha256(preprocess(data).tobytes()).hexdigest()==sample['tensor_sha256']
    runtime=None
    if os.environ.get('DYNAMIC_LIFECYCLE')=='1':
        from model_lifecycle import ModelRuntime,factory_for
        runtime=ModelRuntime(factory_for(backend,model_path,opts),fixture)
        infer=runtime.run
    elif backend=='gpu':
        from accelerator_inference import gpu_backend
        infer=gpu_backend(model_path,fixture)
    elif backend=='intel_npu':
        from accelerator_inference import npu_backend
        infer=npu_backend(model_path,fixture)
    else:
        assert backend=='cpu'
        def infer(tensor):
            return session.run(None,{session.get_inputs()[0].name:tensor})[0].reshape(-1),{'execution_device':'CPU'}
    for _ in range(2):
        scores,evidence=infer(fixture)
        assert np.argsort(scores)[-5:][::-1].tolist()==[258,259,157,279,152]
    lock=threading.Lock()

    def execute(stage,data,plan,request_id,connections,input_id='dog'):
        sample=corpus[input_id] if corpus else {'bytes':len(image),'sha256':IMAGE_SHA256,'tensor_sha256':TENSOR_SHA256}
        digest=hashlib.sha256(data).hexdigest()
        if stage==0:
            assert len(data)==sample['bytes'] and digest==sample['sha256']
        elif stage==1:
            assert len(data)==602112 and digest==sample['tensor_sha256']
        else:
            assert stage==2 and len(data)==4000
        started=time.perf_counter()
        if stage==0:
            payload=preprocess(data).tobytes();device={'execution_device':'CPU'}
        elif stage==1:
            tensor=np.frombuffer(data,dtype='<f4').reshape(1,3,224,224).copy()
            with lock:scores,device=infer(tensor)
            payload=np.asarray(scores,dtype='<f4').reshape(-1).tobytes()
        else:
            scores=np.frombuffer(data,dtype='<f4')
            top5=np.argsort(scores)[-5:][::-1].tolist()
            payload=json.dumps(top5).encode();device={'execution_device':'CPU'}
        task_ms=(time.perf_counter()-started)*1000
        if stage==0:assert hashlib.sha256(payload).hexdigest()==sample['tensor_sha256']
        if stage==1:
            assert len(payload)==4000 and np.isfinite(scores).all()
            predicted=np.argsort(scores)[-5:][::-1].tolist()
            if not corpus:assert predicted==[258,259,157,279,152]
            target=np.asarray(sample['reference_scores'],dtype=np.float64) if corpus else reference
            expected=sample['reference_top5'] if corpus else [258,259,157,279,152]
            difference=scores.astype(np.float64)-target
            device={**device,'max_abs_error_vs_cpu':float(np.max(np.abs(difference))),
                    'rmse_vs_cpu':float(np.sqrt(np.mean(difference**2))),
                    'top1_agrees_with_cpu':predicted[0]==expected[0],
                    'top5_overlap_with_cpu':len(set(predicted)&set(expected)),
                    'top5_order_agrees_with_cpu':predicted==expected}
        entry={'stage':stage,'alias':alias,'node':node,'worker_backend':backend,'task_ms':task_ms,
               'input_bytes':len(data),'output_bytes':len(payload),'input_sha256':digest,
               'output_sha256':hashlib.sha256(payload).hexdigest(),'device':device,'rpc_next_ms':0.0}
        if corpus:entry['input_id']=input_id
        if stage==2:
            if not corpus:assert top5==[258,259,157,279,152]
            result={'request_id':request_id,'plan':plan,'top5':top5,'trace':[entry]}
            if corpus:result['input_id']=input_id
        else:
            next_alias=plan[stage+1]
            called=time.perf_counter()
            if next_alias==alias:
                child=execute(stage+1,payload,plan,request_id,connections,input_id)
                entry['next_mode']='local_call'
            else:
                now=time.monotonic()
                cached=connections.get(next_alias)
                if cached is not None and now-cached['last_used']>=10:
                    cached['connection'].close()
                    del connections[next_alias]
                reused=next_alias in connections
                if next_alias not in connections:
                    url=urlparse(peers[next_alias]['url'])
                    connection=http.client.HTTPConnection(url.hostname,url.port,timeout=15)
                    connection.connect();connection.sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
                    connections[next_alias]={'connection':connection,'last_used':now}
                connection=connections[next_alias]['connection']
                assert connection.sock is not None,'Unexpected reconnect'
                connection.request('POST',f'/stage/{stage+1}',payload,
                                   {'X-Plan':json.dumps(plan),'X-Request-ID':request_id,
                                    'X-Payload-SHA256':entry['output_sha256'],'X-Input-ID':input_id})
                response=connection.getresponse()
                assert response.status==200 and int(response.getheader('Content-Length'))<65536
                child=json.loads(response.read())
                assert response.getheader('X-Worker-Alias')==next_alias
                connections[next_alias]['last_used']=time.monotonic()
                entry['next_mode']='http'
                entry['next_connection_reused']=reused
            entry['rpc_next_ms']=(time.perf_counter()-called)*1000
            assert child['request_id']==request_id and child['plan']==plan
            result={**child,'trace':[entry]+child['trace']}
        emit('stage_complete',request_id=request_id,plan=plan,**entry)
        return result

    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        disable_nagle_algorithm=True

        def setup(self):
            super().setup();self.connection.settimeout(15);self.connections={}

        def finish(self):
            for cached in self.connections.values():cached['connection'].close()
            super().finish()

        def log_message(self,*_):pass

        def do_GET(self):
            if self.path!='/healthz':self.send_error(404);return
            self.reply({'alias':alias,'node':node,'backend':backend,'model_sha256':MODEL_SHA256,
                        **(runtime.health() if runtime else {})})

        def reply(self,result):
            body=json.dumps(result,allow_nan=False).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(body)))
            self.send_header('X-Worker-Alias',alias);self.end_headers();self.wfile.write(body)

        def do_POST(self):
            try:
                if self.path=='/evict' and runtime:
                    assert int(self.headers.get('Content-Length','0'))==0
                    result=runtime.evict()
                    emit('model_evicted',alias=alias,**result)
                    self.reply(result)
                    return
                assert self.path in ['/stage/0','/stage/1','/stage/2']
                stage=int(self.path[-1]);plan=json.loads(self.headers['X-Plan'])
                assert len(plan)==3 and all(p in peers for p in plan) and plan[stage]==alias
                input_id=self.headers.get('X-Input-ID','dog')
                assert not corpus or input_id in corpus
                expected=[corpus[input_id]['bytes'] if corpus else len(image),602112,4000][stage]
                assert int(self.headers.get('Content-Length','0'))==expected
                data=self.rfile.read(expected)
                assert len(data)==expected and hashlib.sha256(data).hexdigest()==self.headers['X-Payload-SHA256']
                result=execute(stage,data,plan,self.headers['X-Request-ID'],self.connections,input_id)
                self.reply(result)
            except (AssertionError,ValueError,KeyError,TypeError) as error:
                emit('request_error',error=str(error));self.send_error(400,'Invalid workflow request')

    server=ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','8080'))),Handler)
    emit('server_ready',alias=alias,node=node,backend=backend,evidence=evidence)
    server.serve_forever()


def client():
    peers=json.loads(os.environ['PEERS_JSON']);schedule=json.loads(os.environ['SCHEDULE_JSON'])
    asset_base=os.environ.get('ASSET_BASE')
    image=fetch(asset_base+'/dog.jpg' if asset_base else IMAGE_URL,IMAGE_SHA256)
    measured=int(os.environ['MEASURED']);block=os.environ['BLOCK']
    for condition in schedule:
        plan=condition['plan'];url=urlparse(peers[plan[0]]['url'])
        connection=http.client.HTTPConnection(url.hostname,url.port,timeout=15)
        for iteration in range(2+measured):
            request_id=f"{os.environ['RUN_ID']}-{block}-{condition['id']}-{iteration}"
            started=time.perf_counter()
            if connection.sock is None:
                assert iteration==0,'Unexpected ingress reconnect'
                connection.connect();connection.sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            connection.request('POST','/stage/0',image,{'X-Plan':json.dumps(plan),'X-Request-ID':request_id,
                                                       'X-Payload-SHA256':IMAGE_SHA256})
            response=connection.getresponse()
            assert response.status==200 and int(response.getheader('Content-Length'))<65536
            result=json.loads(response.read())
            finished=time.perf_counter()
            assert result['request_id']==request_id and result['plan']==plan
            assert result['top5']==[258,259,157,279,152]
            assert [t['alias'] for t in result['trace']]==plan and [t['stage'] for t in result['trace']]==[0,1,2]
            trace=result['trace']
            assert trace[0]['output_sha256']==trace[1]['input_sha256'] and trace[1]['output_sha256']==trace[2]['input_sha256']
            emit('workflow',block=block,condition=condition,iteration=iteration,warmup=iteration<2,
                 workflow_ms=(finished-started)*1000,pacing_overrun=(finished-started)>.25,**result)
            time.sleep(max(0,.25-(time.perf_counter()-started)))
        connection.close()
    emit('client_complete',block=block,conditions=len(schedule),requests=len(schedule)*(2+measured))


if __name__=='__main__':
    if os.environ['MODE']=='server':serve()
    elif os.environ['MODE']=='client':client()
    else:raise ValueError('Invalid MODE')
