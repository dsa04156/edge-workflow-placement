"""Policy-independent observation and execution of the existing three-task HTTP DAG."""

import hashlib
import http.client
import json
import math
import socket
import time
from types import MappingProxyType
from urllib.parse import urlparse


def readonly(value):
    if isinstance(value,dict):
        return MappingProxyType({k:readonly(v) for k,v in value.items()})
    if isinstance(value,list):
        return tuple(readonly(v) for v in value)
    return value


class PolicyRunner:
    """Use one runner per serial client; connections are reused until close()."""

    def __init__(self,peers):
        self.peers={a:{k:p[k] for k in ['node','backend','url']} for a,p in peers.items()}
        self.connections={}

    def __enter__(self):
        return self

    def __exit__(self,*_):
        self.close()

    def close(self):
        for connection in self.connections.values():
            connection.close()
        self.connections.clear()

    def _request(self,alias,method,path,body=None,headers=None):
        try:
            if alias not in self.connections:
                url=urlparse(self.peers[alias]['url'])
                if url.scheme!='http' or not url.hostname:
                    raise ValueError('Expected an explicit HTTP worker URL')
                self.connections[alias]=http.client.HTTPConnection(url.hostname,url.port,timeout=15)
            connection=self.connections[alias]
            if connection.sock is None:
                connection.connect()
                connection.sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            connection.request(method,path,body,headers or {})
            response=connection.getresponse()
            size=int(response.getheader('Content-Length','-1'))
            if response.status!=200 or not 0<=size<65536:
                raise ValueError(f'Invalid worker response: HTTP {response.status}, bytes {size}')
            data=response.read(size)
            if len(data)!=size or response.getheader('X-Worker-Alias')!=alias:
                raise ValueError('Worker response length or alias mismatch')
            return json.loads(data)
        except Exception:
            connection=self.connections.pop(alias,None)
            if connection is not None:
                connection.close()
            raise

    def _health(self,alias,model_sha256):
        health=self._request(alias,'GET','/healthz')
        peer=self.peers[alias]
        if (health.get('node')!=peer['node'] or health.get('backend')!=peer['backend'] or
                health.get('model_sha256')!=model_sha256 or
                type(health.get('model_ready')) is not bool or
                type(health.get('generation')) is not int):
            raise ValueError(f'Worker identity/model/lifecycle mismatch: {alias}')
        return {'node':health['node'],'backend':health['backend'],
                'service_object_ready':health['model_ready'],'service_generation':health['generation'],
                'weight_memory_resident':'unknown','received_monotonic_ns':time.monotonic_ns()}

    def _profile(self,profile,input_id,source):
        keys={'id','provenance','input_id','source','model_sha256','candidates','cost_ms'}
        if set(profile)!=keys:
            raise ValueError('Profile fields must match the common observation contract')
        detached=json.loads(json.dumps(profile,allow_nan=False))
        if (detached['input_id']!=input_id or detached['source']!=source or source not in self.peers or
                not all(isinstance(detached[k],str) and detached[k] for k in ['id','provenance','model_sha256'])):
            raise ValueError('Profile input/source/provenance does not match this request')
        candidates=detached['candidates']
        if (not isinstance(candidates,list) or len(candidates)!=3 or
                any(not isinstance(group,list) or not group or
                    any(a not in self.peers for a in group) for group in candidates)):
            raise ValueError('Expected nonempty common candidates for all three tasks')
        costs=detached['cost_ms']
        if set(costs)!={'task','ingress','edges','service_prepare'} or len(costs['edges'])!=2:
            raise ValueError('Unexpected fields in common cost profile')
        task_keys={f'{i}|{a}' for i in range(3) for a in self.peers}
        edge_keys={f'{a}|{b}' for a in self.peers for b in self.peers}
        groups=[(costs['task'],task_keys),(costs['ingress'],set(self.peers)),
                (costs['service_prepare'],set(self.peers)),
                *[(edge,edge_keys) for edge in costs['edges']]]
        for group,expected in groups:
            if set(group)!=expected:
                raise ValueError('Cost profile is missing candidates or contains unknown fields')
            if any(v is not None and (type(v) not in [int,float] or not math.isfinite(v) or v<0)
                   for v in group.values()):
                raise ValueError('Costs must be nonnegative milliseconds or null for unknown')
        return detached

    def run(self,*,request_id,input_id,body,source,profile,policy_name,policy):
        """Return a JSON-safe attempt, retaining errors as well as successful receipts."""
        started=time.perf_counter();phase_started=started;phase='validation'
        record={'request_id':request_id,'input_id':input_id,'policy':policy_name,
                'status':'error','timings_ms':{}}

        def advance(next_phase):
            nonlocal phase,phase_started
            now=time.perf_counter()
            record['timings_ms'][phase]=(now-phase_started)*1000
            phase=next_phase;phase_started=now

        try:
            frozen_profile=self._profile(profile,input_id,source)
            advance('observation')
            workers={a:self._health(a,frozen_profile['model_sha256']) for a in sorted(self.peers)}
            snapshot={'schema_version':1,'request':{'request_id':request_id,'input_id':input_id,
                      'source':source,'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()},
                      'profile':frozen_profile,'workers':workers}
            snapshot_id=hashlib.sha256(json.dumps(snapshot,sort_keys=True,allow_nan=False).encode()).hexdigest()
            snapshot['snapshot_id']=snapshot_id
            record.update(snapshot_id=snapshot_id,snapshot=snapshot)
            policy_input=readonly(snapshot)
            advance('decision')
            proposed=policy(policy_input)
            advance('plan_validation')
            if not isinstance(proposed,(list,tuple)) or any(type(a) is not str for a in proposed):
                raise ValueError('Policy must return a list or tuple of worker alias strings')
            plan=list(proposed);record['plan']=plan
            if len(plan)!=3 or any(a not in frozen_profile['candidates'][i] for i,a in enumerate(plan)):
                raise ValueError('Policy plan contains a task candidate outside the common feasible set')
            advance('state_validation')
            selected=plan[1]
            preflight=self._health(selected,frozen_profile['model_sha256'])
            record['preflight']={'alias':selected,**preflight}
            if any(preflight[k]!=workers[selected][k]
                   for k in ['service_object_ready','service_generation']):
                raise ValueError('Selected inference service state changed after observation')
            advance('workflow')
            result=self._request(plan[0],'POST','/stage/0',body,{
                'X-Plan':json.dumps(plan),'X-Request-ID':request_id,'X-Input-ID':input_id,
                'X-Payload-SHA256':snapshot['request']['sha256']})
            record['result']=result
            advance('receipt_validation')
            trace=result['trace']
            if (result['request_id']!=request_id or result.get('input_id','dog')!=input_id or
                    result['plan']!=plan or [t['stage'] for t in trace]!=[0,1,2] or
                    [t['alias'] for t in trace]!=plan or
                    [t['node'] for t in trace]!=[self.peers[a]['node'] for a in plan] or
                    [t['worker_backend'] for t in trace]!=[self.peers[a]['backend'] for a in plan] or
                    trace[0]['input_bytes']!=len(body) or
                    trace[0]['output_bytes']!=trace[1]['input_bytes'] or
                    trace[1]['output_bytes']!=trace[2]['input_bytes'] or
                    trace[0]['input_sha256']!=snapshot['request']['sha256'] or
                    trace[0]['output_sha256']!=trace[1]['input_sha256'] or
                    trace[1]['output_sha256']!=trace[2]['input_sha256']):
                raise ValueError('Workflow receipt identity or payload linkage mismatch')
            record['status']='ok'
        except Exception as error:
            record['error']={'phase':phase,'type':type(error).__name__,'message':str(error)}
        finally:
            finished=time.perf_counter()
            record['timings_ms'][phase]=(finished-phase_started)*1000
            record['total_ms']=(finished-started)*1000
        return record
