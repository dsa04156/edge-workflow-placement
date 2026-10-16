"""Paired anchors on real inputs; preserve all quality disagreements."""

import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import sys
import time
from urllib.parse import urlparse

from probe import fetch,emit


def main():
    spec=json.load(sys.stdin);peers=json.loads(os.environ['PEERS_JSON'])
    corpus=json.loads(Path(os.environ['CORPUS_FILE']).read_text());connections={}
    images={}
    for key,sample in corpus.items():
        path=Path('/work')/sample['filename']
        if not path.exists():path.write_bytes(fetch(os.environ['ASSET_BASE']+'/corpus/'+sample['filename'],sample['sha256']))
        images[key]=path.read_bytes();assert hashlib.sha256(images[key]).hexdigest()==sample['sha256']

    def request(alias,method,path,body=None,headers=None):
        if alias not in connections:
            u=urlparse(peers[alias]['url']);connections[alias]=http.client.HTTPConnection(u.hostname,u.port,timeout=15)
        c=connections[alias]
        if c.sock is None:c.connect();c.sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        c.request(method,path,body,headers or {});response=c.getresponse()
        assert response.status==200,(alias,path,response.status,response.read())
        assert int(response.getheader('Content-Length'))<65536
        data=json.loads(response.read());assert response.getheader('X-Worker-Alias')==alias
        return data

    for condition in spec['conditions']:
        key=condition['image_id'];sample=corpus[key];plan=condition['plan']
        for iteration in range(7):
            if iteration==2 and condition['state']=='eviction':
                emit('control',condition=condition,**request('intel_npu','POST','/evict',b''))
            request_id=f"{spec['run_id']}-b{spec['block']}-{condition['id']}-{iteration}"
            started=time.perf_counter();health=request('intel_npu','GET','/healthz');observed=time.perf_counter()
            result=request(plan[0],'POST','/stage/0',images[key],{'X-Plan':json.dumps(plan),
                'X-Input-ID':key,'X-Request-ID':request_id,'X-Payload-SHA256':sample['sha256']})
            finished=time.perf_counter()
            assert result['request_id']==request_id and result['plan']==plan and result['input_id']==key
            trace=result['trace'];assert [t['stage'] for t in trace]==[0,1,2]
            assert [t['alias'] for t in trace]==plan
            assert [t['node'] for t in trace]==[peers[a]['node'] for a in plan]
            assert trace[0]['input_sha256']==sample['sha256']
            assert trace[0]['output_sha256']==trace[1]['input_sha256']==sample['tensor_sha256']
            assert trace[1]['output_sha256']==trace[2]['input_sha256']
            assert len(result['top5'])==5 and len(set(result['top5']))==5 and all(0<=c<1000 for c in result['top5'])
            emit('workflow',condition=condition,source=spec['source'],block=spec['block'],iteration=iteration,
                 warmup=iteration<2,observation_ms=(observed-started)*1000,workflow_ms=(finished-observed)*1000,
                 total_ms=(finished-started)*1000,pacing_overrun=(finished-started)>.25,health=health,**result)
            time.sleep(max(0,.25-(time.perf_counter()-started)))
    for c in connections.values():c.close()
    emit('client_complete',block=spec['block'],source=spec['source'],requests=len(spec['conditions'])*7)


if __name__=='__main__':main()
