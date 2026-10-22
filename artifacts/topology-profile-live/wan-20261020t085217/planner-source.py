"""Offline measured-profile planner and Kubernetes manifest generator; never applies resources."""
import argparse, hashlib, itertools, json, math
from pathlib import Path

POLICIES=('resource','topology_only','profile_only','topology_profile')

def read(path): return json.loads(Path(path).read_text())
def digest(obj): return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def number(x): return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) and x>=0

def validate(c,fixture=False):
    if c.get('provenance')!='measured' and not fixture:
        raise ValueError('Measured calibration required; --allow-fixture is only for local interface tests')
    if not c.get('evidence_id'): raise ValueError('evidence_id required')
    nodes=c['nodes'];tasks=c['tasks'];seen=set();ids=[t['id'] for t in tasks]
    if len(set(ids))!=len(ids) or not ids: raise ValueError('Unique nonempty task ids required')
    for n,v in nodes.items():
        if not n or not all(number(v[k]) for k in ['cpu_free_m','memory_free_mib']): raise ValueError('Invalid node capacity')
    for t in tasks:
        if not t['candidates'] or len(set(t['candidates']))!=len(t['candidates']): raise ValueError('Invalid candidates')
        if not t.get('image') or ':latest' in t['image']: raise ValueError('Use versioned image, preferably digest')
        if not number(t['cpu_m']) or not number(t['memory_mib']): raise ValueError('Invalid requests')
        for n in t['candidates']:
            if n not in nodes or not number(t['compute_ms'][n]): raise ValueError('Missing measured compute')
        for edge in t['inputs']:
            if ('task' in edge)==('source_node' in edge): raise ValueError('Exactly one predecessor or source required')
            if 'task' in edge and edge['task'] not in seen: raise ValueError('Tasks must be in topological order')
            origins=next(x['candidates'] for x in tasks if x['id']==edge['task']) if 'task' in edge else [edge['source_node']]
            if any(n not in nodes for n in origins) or not number(edge['bytes']): raise ValueError('Invalid input source/bytes')
            for a,b in itertools.product(origins,t['candidates']):
                if not number(edge['transfer_ms'].get(a+'|'+b)): raise ValueError('Missing measured transfer, including colocated path')
        seen.add(t['id'])
    for out in c['outputs']:
        if out['task'] not in seen or out['destination_node'] not in nodes: raise ValueError('Invalid output')
        for n in next(t['candidates'] for t in tasks if t['id']==out['task']):
            if not number(out['transfer_ms'].get(n+'|'+out['destination_node'])): raise ValueError('Missing return cost')
    if not c['outputs']: raise ValueError('Terminal outputs required')

def feasible(c,p):
    used={n:[0,0] for n in c['nodes']}
    for t in c['tasks']:
        n=p[t['id']];used[n][0]+=t['cpu_m'];used[n][1]+=t['memory_mib']
    return all(v[0]<=c['nodes'][n]['cpu_free_m'] and v[1]<=c['nodes'][n]['memory_free_mib'] for n,v in used.items())

def cost(c,p,policy):
    finish={}
    for t in c['tasks']:
        n=p[t['id']];comp=t['compute_ms'][n]
        if policy=='topology_only': comp=sum(t['compute_ms'][a] for a in t['candidates'])/len(t['candidates'])
        ready=[]
        for edge in t['inputs']:
            source=p[edge['task']] if 'task' in edge else edge['source_node']
            start=finish[edge['task']] if 'task' in edge else 0
            tx=0 if policy=='profile_only' else edge['transfer_ms'][source+'|'+n]
            ready.append(start+tx)
        finish[t['id']]=max(ready,default=0)+comp
    return max(finish[e['task']]+(0 if policy=='profile_only' else e['transfer_ms'][p[e['task']]+'|'+e['destination_node']]) for e in c['outputs'])

def plan(c,policy,limit=100000):
    count=math.prod(len(t['candidates']) for t in c['tasks'])
    if count>limit: raise ValueError(f'{count} combinations exceeds exact-search guard {limit}')
    best=None
    for seq in itertools.product(*(sorted(t['candidates']) for t in c['tasks'])):
        p=dict(zip((t['id'] for t in c['tasks']),seq))
        if feasible(c,p):
            key=(cost(c,p,policy),seq)
            if best is None or key<best[0]:best=(key,p)
    if best is None: raise ValueError('No feasible placement')
    return {'policy':policy,'profile_sha256':digest(c),'placement':best[1],'predicted_critical_path_ms':best[0][0],
            'cost_model':'no-queue critical path; ignores concurrent link/node contention','combinations':count}

def manifests(c,policy,run_id,result=None):
    # Resource baseline is genuinely left to default-scheduler; no replica spreading/Service group.
    docs=[{'apiVersion':'v1','kind':'Namespace','metadata':{'name':c['namespace']}}]
    for t in c['tasks']:
        spec={'schedulerName':'default-scheduler','restartPolicy':'Never','containers':[{'name':'task','image':t['image'],
            'resources':{'requests':{'cpu':str(t['cpu_m'])+'m','memory':str(t['memory_mib'])+'Mi'}},
            'env':[{'name':'TASK_ID','value':t['id']},{'name':'RUN_ID','value':run_id}]}],
            'affinity':{'nodeAffinity':{'requiredDuringSchedulingIgnoredDuringExecution':{'nodeSelectorTerms':[{'matchFields':[{
                'key':'metadata.name','operator':'In','values':[node]}]} for node in t['candidates']]}}}}
        if t.get('command'): spec['containers'][0]['command']=t['command']
        if t.get('args'): spec['containers'][0]['args']=t['args']
        if policy!='resource':
            # Keep normal scheduler resource admission; require the node selected by planner.
            spec['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']=[{'matchFields':[{
                'key':'metadata.name','operator':'In','values':[result['placement'][t['id']]]}]}]
        labels={'experiment':'topology-profile','run-id':run_id,'task-id':t['id']}
        docs.append({'apiVersion':'v1','kind':'Pod','metadata':{'name':run_id+'-'+t['id'],'namespace':c['namespace'],'labels':labels},'spec':spec})
    return {'apiVersion':'v1','kind':'List','items':docs}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--config',required=True);ap.add_argument('--policy',choices=POLICIES,required=True)
    ap.add_argument('--run-id',required=True);ap.add_argument('--out',required=True);ap.add_argument('--allow-fixture',action='store_true');a=ap.parse_args()
    import re
    if not re.fullmatch('[a-z][a-z0-9-]{0,29}',a.run_id): raise ValueError('run-id must be a short DNS label')
    c=read(a.config);validate(c,a.allow_fixture);out=Path(a.out);out.mkdir(parents=True,exist_ok=False)
    r={'policy':'resource','profile_sha256':digest(c),'placement':None} if a.policy=='resource' else plan(c,a.policy)
    (out/'profile-frozen.json').write_text(json.dumps(c,indent=2));(out/'plan.json').write_text(json.dumps(r,indent=2))
    (out/'pods.json').write_text(json.dumps(manifests(c,a.policy,a.run_id,r),indent=2));print(json.dumps(r,indent=2))
if __name__=='__main__': main()
