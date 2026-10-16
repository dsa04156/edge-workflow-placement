"""One phase of an actual source-local workload; no access to future phases."""

import hashlib
import http.client
import itertools
import json
import os
from pathlib import Path
import socket
import sys
import time
from urllib.parse import urlparse

from probe import IMAGE_SHA256,fetch,emit


def warm_score(profile,plan):
    return (profile['ingress'][plan[0]]+
            sum(profile['task'][f'{i}|{a}'] for i,a in enumerate(plan))+
            sum(profile['edges'][i][f'{plan[i]}|{plan[i+1]}'] for i in range(2)))


def choose(policy,profile,selection,ready,state,source):
    aliases=sorted(profile['ingress'])
    plans=list(itertools.product(aliases,repeat=3))
    prep=0 if ready else selection['preparation_ms']
    score=lambda p:warm_score(profile,p)
    best=list(min(plans,key=lambda p:(score(p),p)))
    information={'cold_preparation_estimate_ms':prep}
    if policy=='fixed':return selection['initial_plan'],state,information
    if policy=='warm_dag':return best,state,information
    if policy=='ready_dag':
        plan=min(plans,key=lambda p:(score(p)+(prep/4 if p[1]=='intel_npu' else 0),p))
        return list(plan),state,information
    if policy=='ready_greedy':
        plan=[min(aliases,key=lambda a:(profile['ingress'][a]+profile['task'][f'0|{a}'],a))]
        for i in [1,2]:
            plan.append(min(aliases,key=lambda a:(profile['edges'][i-1][f'{plan[-1]}|{a}']+
                            profile['task'][f'{i}|{a}']+(prep/4 if i==1 and a=='intel_npu' else 0),a)))
        return plan,state,information
    assert policy=='credit_hysteresis'
    if ready or best[1]!='intel_npu':return best,{},information
    safe=list(min((p for p in plans if p[1]!='intel_npu'),key=lambda p:(score(p),p)))
    key=source+'|'+','.join(best)
    credit=state.get('credit_ms',0) if state.get('key')==key else 0
    credit+=max(0,score(safe)-score(best))
    information['credit_ms']=credit
    return (best if credit>=prep else safe),{'key':key,'credit_ms':credit},information


def main():
    spec=json.load(sys.stdin)
    peers=json.loads(os.environ['PEERS_JSON'])
    image_path=Path('/work/dog.jpg')
    if not image_path.exists():
        image_path.write_bytes(fetch(os.environ['ASSET_BASE']+'/dog.jpg',IMAGE_SHA256))
    image=image_path.read_bytes();assert hashlib.sha256(image).hexdigest()==IMAGE_SHA256
    connections={}

    def request(alias,method,path,body=None,headers=None):
        if alias not in connections:
            url=urlparse(peers[alias]['url'])
            connections[alias]=http.client.HTTPConnection(url.hostname,url.port,timeout=15)
        c=connections[alias]
        if c.sock is None:
            c.connect();c.sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        c.request(method,path,body,headers or {})
        response=c.getresponse()
        assert response.status==200,(alias,path,response.status,response.read())
        assert int(response.getheader('Content-Length'))<65536
        result=json.loads(response.read())
        assert response.getheader('X-Worker-Alias')==alias
        return result

    def workflow(plan,request_id):
        result=request(plan[0],'POST','/stage/0',image,{
            'X-Plan':json.dumps(plan),'X-Request-ID':request_id,'X-Payload-SHA256':IMAGE_SHA256})
        assert result['request_id']==request_id and result['plan']==plan
        assert result['top5']==[258,259,157,279,152]
        trace=result['trace']
        assert [t['stage'] for t in trace]==[0,1,2]
        assert [t['alias'] for t in trace]==plan
        assert [t['node'] for t in trace]==[peers[a]['node'] for a in plan]
        assert trace[0]['output_sha256']==trace[1]['input_sha256']
        assert trace[1]['output_sha256']==trace[2]['input_sha256']
        return result

    prefix=spec['prefix']
    if spec.get('reset'):
        emit('control',prefix=prefix,operation='evict',**request('intel_npu','POST','/evict',b''))
    if spec.get('prewarm'):
        result=workflow(['intel_npu']*3,prefix+'-setup')
        emit('setup_workflow',prefix=prefix,**result)
    state=spec.get('state',{})
    if spec['mode']=='calibration':
        conditions=spec['conditions']
        tasks=[(c['plan'],i,i<2) for c in conditions for i in range(7)]
    elif spec['mode']=='preparation':
        tasks=[(['intel_npu']*3,0,False)]
    else:
        assert spec['mode']=='evaluation'
        tasks=[(None,i,False) for i in range(spec['requests'])]
    for fixed_plan,index,warmup in tasks:
        request_id=prefix+f'-{index}' if fixed_plan is None else prefix+'-'+','.join(fixed_plan)+f'-{index}'
        started=time.perf_counter()
        health=request('intel_npu','GET','/healthz')
        assert health['node']==peers['intel_npu']['node'] and health['backend']=='intel_npu'
        observed=time.perf_counter()
        if fixed_plan is not None:
            plan=fixed_plan;information={}
        else:
            plan,state,information=choose(spec['policy'],spec['selection']['profiles'][spec['source']],
                                          spec['selection'],health['model_ready'],state,spec['source'])
        decided=time.perf_counter()
        result=workflow(plan,request_id)
        finished=time.perf_counter()
        emit('workflow',mode=spec['mode'],prefix=prefix,source=spec['source'],policy=spec.get('policy'),
             scenario=spec.get('scenario'),block=spec.get('block'),phase=spec.get('phase'),
             iteration=index,warmup=warmup,health=health,decision=information,
             observation_ms=(observed-started)*1000,decision_ms=(decided-observed)*1000,
             workflow_ms=(finished-decided)*1000,total_ms=(finished-started)*1000,
             pacing_overrun=(finished-started)>.25,**result)
        time.sleep(max(0,.25-(time.perf_counter()-started)))
    for connection in connections.values():connection.close()
    emit('phase_complete',prefix=prefix,state=state,requests=len(tasks))


if __name__=='__main__':main()
