from pathlib import Path
import sys,itertools,json,csv,hashlib,time,platform
import numpy as np
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'profiled-placement-20261019'))
import run_cloud_required as base
Engine,Resource=base.Engine,base.Resource
NODES=[0,2,3]
CAP=np.array([[4000,8192],[8000,16384],[16000,32768]],dtype=np.int64)
REQ=np.array([1000,1024],dtype=np.int64)
SEEDS=range(45001,45013)
KEYS=['workload','sensors','bw','rate','layout','sink']
POLICIES=['k8s_resource_proxy','location','compute','network_greedy','dag_joint']

def profiles(workload):
 im,sp=('resnet18','wav2vec2-base-960h') if workload=='light' else ('resnet50','wav2vec2-large-960h-lv60-self')
 stages=[('ExtractImageFeature',im),('SpeechRecognition',sp),('VQA','default')]
 def get(q):return np.array([[base.TABLE[q][s,base.W[n],m] for n in NODES] for s,m in stages])
 med=get('p50');sigma=np.maximum(0,np.log(get('p90')/med)/1.2815515655446004)
 audio=float(next(r[4] for r in base.MSG if r[0]=='ReadSpeech'))*1024
 speech=float(next(r[4] for r in base.MSG if r[0]=='SpeechRecognition'))*1024
 return med,sigma,get('p75'),[base.RAW,audio,base.feat(im),speech,1024.]

def background(layout):
 f={'balanced':[.25,.25,.25],'cloud-free':[.75,.5,0],'edge-free':[0,.5,.75]}[layout]
 return (CAP*np.array(f)[:,None]).astype(np.int64)

def feasible(p,bg,sink):
 if sink=='cloud' and p[2]!=2:return False
 usage=bg.copy()
 for n in p:usage[n]+=REQ
 return bool(np.all(usage<=CAP))

def score(usage,n):
 r=usage[n]+REQ
 if np.any(r>CAP[n]):return -1
 least=int(sum(((CAP[n]-r)*100)//CAP[n])//2)
 frac=r/CAP[n]
 balanced=int((1-abs(float(frac[0]-frac[1]))/2)*100)
 return least+balanced

def resource_plan(bg,sink,sensors,seed,location=False):
 rng=np.random.default_rng(seed+300000);order=rng.permutation(3);usage=bg.copy();p=[None]*3
 preferred=[0,0 if sensors=='same' else 1,0]
 for i in order:
  nodes=[n for n in range(3) if (i!=2 or sink!='cloud' or n==2) and score(usage,n)>=0]
  if location and preferred[i] in nodes:n=preferred[i]
  else:
   best=max(score(usage,n) for n in nodes);ties=[n for n in nodes if score(usage,n)==best];n=int(rng.choice(ties))
  p[i]=n;usage[n]+=REQ
 assert feasible(p,bg,sink)
 return tuple(p),list(map(int,order))

def wire(a,b,size,bw):return base.wire(NODES[a],NODES[b],size,bw)

def greedy(bg,sink,sensors,pr,sizes,bw):
 p=[];finish=[];usage=bg.copy();src=[0,0 if sensors=='same' else 1]
 for i in range(3):
  candidates=[n for n in range(3) if score(usage,n)>=0 and (i!=2 or sink!='cloud' or n==2)]
  def ready(n):
   if i<2:return wire(src[i],n,sizes[i],bw)
   return max(finish[j]+wire(p[j],n,sizes[2+j],bw) for j in [0,1])
  n=min(candidates,key=lambda n:ready(n)+pr[i,n]);finish.append(ready(n)+pr[i,n]);p.append(n);usage[n]+=REQ
 assert feasible(p,bg,sink)
 return tuple(p)

def simulate(p,services,arrivals,sizes,bw,sensors):
 e=Engine();cpu=[Resource(e) for _ in range(3)];links={(n,d):Resource(e) for n in range(3) for d in ['up','down']}
 lat=[];decomp=[];joins=[];wan=0;wan_dirs=[0.,0.];cpu_total=np.zeros(3)
 def send(a,b,size,parts,cb):
  nonlocal wan
  path=[] if a==b else [(a,'up'),(b,'down')]
  for n,d in path:
   if n==2:wan+=size;wan_dirs[0 if d=='down' else 1]+=size
  def hop(k,part):
   if k==len(path):cb(part);return
   n,d=path[k];duration=size*8/(bw*1e6 if n==2 else 1e9);delay=.02 if n==2 else .0005
   def done(w,s):
    updated=(part[0],part[1]+s+delay,part[2]+w)
    e.at(e.now+delay,lambda:hop(k+1,updated))
   links[n,d].submit(duration,'app',done)
  hop(0,parts)
 def start(rid,t):
  ready={}
  def execute(i,part,cb):
   duration=services[rid,i,p[i]];cpu_total[p[i]]+=duration
   cpu[p[i]].submit(duration,'app',lambda w,s:cb((part[0]+s,part[1],part[2]+w)))
  def finish(part):
   delta=e.now-t;assert abs(sum(part)-delta)<1e-7
   lat.append(delta);decomp.append(part)
  def arrive(i,part):
   ready[i]=(e.now,part)
   if len(ready)==2:
    joins.append(abs(ready[0][0]-ready[1][0]));critical=max(ready.values(),key=lambda x:x[0])[1]
    execute(2,critical,lambda done:send(p[2],0,sizes[4],done,finish))
  def branch(i,src):
   send(src,p[i],sizes[i],(0.,0.,0.),lambda part:execute(i,part,lambda done:send(p[i],p[2],sizes[i+2],done,lambda arrived:arrive(i,arrived))))
  branch(0,0);branch(1,0 if sensors=='same' else 1)
 for rid,t in enumerate(arrivals):e.at(float(t),lambda rid=rid,t=t:start(rid,t))
 e.run();assert len(lat)==len(arrivals)
 dc=np.mean(decomp,axis=0)*1000
 return dict(mean_ms=float(np.mean(lat)*1000),p95_ms=float(np.quantile(lat,.95)*1000),wan_bytes=wan/len(lat),critical_compute_ms=float(dc[0]),critical_network_ms=float(dc[1]),critical_queue_ms=float(dc[2]),join_wait_ms=float(np.mean(joins)*1000),wan_max_direction_bytes=max(wan_dirs)/len(lat),cpu_seconds_per_request=list(cpu_total/len(lat)))

def verify():
 assert score(np.zeros((3,2),dtype=np.int64),0)==174
 assert score(CAP.copy(),0)==-1
 # Independent branches 1s and 2s, fusion3s: 5s, not6s. Ignore links by colocating or zero data; wire propagation removed via subtract known40? use all same => shared lane6s.
 sv=np.array([[[1.,1.,1.],[2.,2.,2.],[3.,3.,3.]]])
 a=simulate((0,0,0),sv,np.array([0.]),[0]*5,10,'same');assert abs(a['mean_ms']-6000)<1e-8
 # Two edge lanes, same-site sources. Speech transfers to E1 and back to E0: two1ms paths. Fusion starts after slower branch arrives.
 b=simulate((0,1,0),sv,np.array([0.]),[0]*5,10,'same');assert abs(b['mean_ms']-5002)<1e-8
 assert abs(b['join_wait_ms']-1002)<1e-8
 return {'resource_score_fixtures':True,'parallel_branches_and_join':True,'serial_colocation':True}

def main():
 verification=verify();rows=[];predictions=[];started=time.perf_counter();conditions=list(itertools.product(['light','heavy'],['same','split'],[2,10,100],[.1,1],['balanced','cloud-free','edge-free'],['free','cloud']))
 for ci,values in enumerate(conditions):
  workload,sensors,bw,rate,layout,sink=values;meta=dict(zip(KEYS,values));med,sig,pr,sizes=profiles(workload);bg=background(layout)
  plans=[p for p in itertools.product(range(3),repeat=3) if feasible(p,bg,sink)]
  st=time.perf_counter();pred={p:simulate(p,pr[None,:,:],np.array([0.]),sizes,bw,sensors)['mean_ms'] for p in plans};joint=min(plans,key=pred.get);planning_ms=(time.perf_counter()-st)*1000
  compute=min(plans,key=lambda p:sum(pr[i,n] for i,n in enumerate(p)))
  ng=greedy(bg,sink,sensors,pr,sizes,bw)
  predictions.append(dict(**meta,plans=[dict(plan=p,predicted_ms=pred[p]) for p in plans],joint_plan=joint,planning_ms=planning_ms))
  for seed in SEEDS:
   arrivals=base.arrivals(seed,rate,1,120/rate);services=np.random.default_rng(seed+200000).lognormal(np.log(med),sig,(len(arrivals),3,3))
   kp,order=resource_plan(bg,sink,sensors,seed);lp,_=resource_plan(bg,sink,sensors,seed,True)
   placements=dict(k8s_resource_proxy=kp,location=lp,compute=compute,network_greedy=ng,dag_joint=joint)
   cache={p:simulate(p,services,arrivals,sizes,bw,sensors) for p in set(placements.values())}
   for policy,p in placements.items():
    assert feasible(p,bg,sink)
    v=cache[p];rows.append(dict(**meta,seed=seed,policy=policy,plan=p,deployment_order=order,n=len(arrivals),trace_hash=hashlib.sha256(arrivals.tobytes()).hexdigest(),predicted_ms=pred[p],planning_ms=planning_ms if policy=='dag_joint' else None,wan_rho=rate*v['wan_max_direction_bytes']*8/(bw*1e6),node_rho=rate*max(v['cpu_seconds_per_request']),**v))
  if (ci+1)%12==0:print('conditions',ci+1,'/',len(conditions),flush=True)
 summary=[];rng=np.random.default_rng(20261020)
 for vals in conditions:
  rs=[r for r in rows if tuple(r[k] for k in KEYS)==vals];block={(r['seed'],r['policy']):r for r in rs}
  for comp in POLICIES[:-1]:
   effects=np.array([100*(1-block[s,'dag_joint']['mean_ms']/block[s,comp]['mean_ms']) for s in SEEDS]);ci=np.quantile(rng.choice(effects,(5000,len(SEEDS))).mean(axis=1),[.025,.975])
   rec=dict(zip(KEYS,vals));rec.update(comparator=comp,improvement_pct=float(effects.mean()),ci_low=float(ci[0]),ci_high=float(ci[1]))
   for prefix,pol in [('baseline',comp),('joint','dag_joint')]:
    for metric in ['mean_ms','p95_ms','wan_bytes','critical_compute_ms','critical_network_ms','critical_queue_ms','join_wait_ms','wan_rho','node_rho']:
     rec[prefix+'_'+metric]=float(np.mean([block[s,pol][metric] for s in SEEDS]))
   summary.append(rec)
 (ROOT/'results.json').write_text(json.dumps(dict(runs=rows,summary=summary,predictions=predictions),indent=2))
 with (ROOT/'summary.csv').open('w') as f:
  w=csv.DictWriter(f,fieldnames=summary[0]);w.writeheader();w.writerows(summary)
 verification.update(conditions=len(conditions),seeds=len(SEEDS),policy_rows=len(rows),resource_fit_all=True,critical_path_decomposition_all=True,identical_plan_reuse=True,wall_seconds=time.perf_counter()-started,python=platform.python_version(),numpy=np.__version__,script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),profile_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in base.P.glob('*.csv')})
 (ROOT/'verification.json').write_text(json.dumps(verification,indent=2));print(verification)
if __name__=='__main__':main()
