"""Published-profile-driven simulation, not a reproduction of either paper's optimizer."""
from pathlib import Path
import csv, json, math, hashlib, sys, time, itertools, platform
import numpy as np
ROOT=Path(__file__).resolve().parent
OLD=ROOT.parent/'simulation-20261019'
sys.path.insert(0,str(OLD))
from legacy import Engine, Resource
P=OLD/'data/jellybean/VQA/examples/optimizer_inputs'
W=['workflow-compute-cpu-1','workflow-compute-cpu-3','workflow-compute-cpu-5','workflow-compute-gpu-1']
TABLE={q:{(s,w,m):float(v)/1000 for s,w,m,v in (row[:4] for row in csv.reader(open(P/f'execution_profile_{q}.csv')))} for q in ['p50','p75','p90']}
MSG=list(csv.reader(open(P/'message_sizes.csv')))
RAW=float(MSG[0][4])*1024
PLANS=[(n,3) for n in range(4)]
def profile(model,q):return np.array([[TABLE[q][s,w,m] for w in W] for s,m in [('ExtractImageFeature',model),('VQA','default')]])
def feat(model):return float(next(r[4] for r in MSG if r[:3]==['ExtractImageFeature','VQA',model]))*1024

def route(a,b):
    if a==b:return []
    return [(a,'up'),(b,'down')]
def wire(a,b,size,bw):return sum(size*8/(bw*1e6 if n==3 else 1e9)+(.02 if n==3 else .0005) for n,d in route(a,b))
def estimate(plan,pr,model,bw):
    a,b=plan
    return wire(0,a,RAW,bw)+pr[0,a]+wire(a,b,feat(model),bw)+pr[1,b]+wire(b,0,1024,bw)
def arrivals(seed,rate,cv,duration):
    rng=np.random.default_rng(seed);t=0;out=[]
    while True:
        t+=rng.gamma(1/cv**2,cv**2/rate)
        if t>=duration:return np.array(out)
        out.append(t)
def evaluate(plan,services,trace,model,bw):
    e=Engine();cpu=[Resource(e) for _ in W];links={(n,d):Resource(e) for n in range(4) for d in ['up','down']}
    lat=[];wan=0
    def send(a,b,size,fn):
        nonlocal wan
        path=route(a,b);wan+=sum(n==3 for n,d in path)*size
        def hop(i):
            if i==len(path):fn();return
            k=path[i];n,d=k;bps=bw*1e6 if n==3 else 1e9;delay=.02 if n==3 else .0005
            links[k].submit(size*8/bps,'app',lambda w,s:e.at(e.now+delay,lambda:hop(i+1)))
        hop(0)
    def start(rid,t):
        a,b=plan
        def finish():lat.append(e.now-t)
        def second():cpu[b].submit(services[rid,1,b],'app',lambda w,s:send(b,0,1024,finish))
        def first():cpu[a].submit(services[rid,0,a],'app',lambda w,s:send(a,b,feat(model),second))
        send(0,a,RAW,first)
    for rid,t in enumerate(trace):e.at(t,lambda rid=rid,t=t:start(rid,t))
    e.run();assert len(lat)==len(trace) and min(lat)>0
    return dict(mean_ms=float(np.mean(lat)*1000),p95_ms=float(np.quantile(lat,.95)*1000),wan_bytes_per_request=wan/len(lat),n=len(lat),last_completion=e.now)
def main():
    out=ROOT/'results_cloud_required';out.mkdir(exist_ok=True);rows=[];cache_runs=0;t0=time.perf_counter()
    for model,bw,rate,cv in itertools.product(['resnet18','resnet50'],[10,100,1000],[1,8],[1,3]):
        pr=profile(model,'p75');p50=profile(model,'p50');p90=profile(model,'p90');sigma=np.maximum(0,np.log(p90/p50)/1.2815515655446004)
        compute=(int(pr[0].argmin()),3)
        a=min(range(4),key=lambda n:wire(0,n,RAW,bw)+pr[0,n]);greedy=(a,3)
        joint=min(PLANS,key=lambda p:estimate(p,pr,model,bw));j50=min(PLANS,key=lambda p:estimate(p,p50,model,bw))
        for seed in range(12001,12013):
            planning=arrivals(seed+100000,rate,cv,60)
            training_services=np.broadcast_to(pr,(len(planning),2,4))
            began=time.perf_counter();trace_plan=min(PLANS,key=lambda p:evaluate(p,training_services,planning,model,bw)['mean_ms']);plan_wall=time.perf_counter()-began
            trace=arrivals(seed,rate,cv,120);rng=np.random.default_rng(seed+200000);services=rng.lognormal(np.log(p50),sigma,(len(trace),2,4))
            policies={'compute_p75':compute,'greedy_network_p75':greedy,'joint_p75':joint,'joint_p50':j50,'trace_p75':trace_plan}
            cache={p:evaluate(p,services,trace,model,bw) for p in set(policies.values())};cache_runs+=len(cache)
            for policy,plan in policies.items():rows.append(dict(model=model,bw=bw,rate=rate,cv=cv,seed=seed,policy=policy,plan=list(plan),planning_wall_s=plan_wall if policy=='trace_p75' else None,trace_sha256=hashlib.sha256(trace.tobytes()).hexdigest(),**cache[plan]))
        print(model,bw,rate,cv,flush=True)
    (out/'runs.json').write_text(json.dumps(rows,indent=2))
    groups={}
    for r in rows:groups.setdefault((r['model'],r['bw'],r['rate'],r['cv']),[]).append(r)
    summary=[];rng=np.random.default_rng(19102026)
    for key,rs in groups.items():
        by={(r['seed'],r['policy']):r for r in rs}
        for comparator in ['compute_p75','greedy_network_p75','joint_p50','joint_p75']:
            target='trace_p75' if comparator=='joint_p75' else 'joint_p75'
            effects=np.array([100*(1-by[s,target]['mean_ms']/by[s,comparator]['mean_ms']) for s in range(12001,12013)])
            boot=rng.choice(effects,(5000,12),replace=True).mean(axis=1)
            summary.append(dict(model=key[0],bw=key[1],rate=key[2],cv=key[3],target=target,comparator=comparator,improvement_pct=float(effects.mean()),ci_low=float(np.quantile(boot,.025)),ci_high=float(np.quantile(boot,.975)),target_mean_ms=float(np.mean([by[s,target]['mean_ms'] for s in range(12001,12013)])),baseline_mean_ms=float(np.mean([by[s,comparator]['mean_ms'] for s in range(12001,12013)]))))
    (out/'summary.json').write_text(json.dumps(summary,indent=2))
    with open(out/'summary.csv','w') as f:
        writer=csv.DictWriter(f,fieldnames=summary[0]);writer.writeheader();writer.writerows(summary)
    (out/'manifest.json').write_text(json.dumps(dict(python=platform.python_version(),numpy=np.__version__,evaluations=len(rows),distinct_plan_evaluations=cache_runs,wall_seconds=time.perf_counter()-t0,profiles={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in P.glob('*.csv')},script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),indent=2))
    for c in ['compute_p75','greedy_network_p75','joint_p50','joint_p75']:
        vals=[s['improvement_pct'] for s in summary if s['comparator']==c];print(c,'scenario_equal_mean',np.mean(vals),'range',min(vals),max(vals))
if __name__=='__main__':main()
