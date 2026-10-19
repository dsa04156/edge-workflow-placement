from pathlib import Path
import sys,itertools,json,csv,hashlib,time,heapq,platform
import numpy as np
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'dag-placement-20261019'))
import run as dag
Engine,Resource=dag.Engine,dag.Resource
KEYS=['workload','sensors','topology','bw','rate','sink']
POLICIES=['resource_proxy','topology_only','profile_only','topology_profile']
SEEDS=range(56001,56013)

def graph(topology,bw):
 if topology=='cloud_hub':edges=[(0,2,bw,.02),(1,2,bw,.02)]
 else:edges=[(0,3,1000,.0005),(1,3,5 if topology=='edge1_slow' else 1000,.005 if topology=='edge1_slow' else .0005),(3,2,bw,.02)]
 return {(u,v):(mbps*1e6,delay) for a,b,mbps,delay in edges for u,v in [(a,b),(b,a)]}

def route(a,b,size,links):
 q=[(0.,a,())];best={a:0.}
 while q:
  cost,n,path=heapq.heappop(q)
  if n==b:return path
  if cost>best[n]:continue
  for (u,v),(bps,delay) in links.items():
   if u!=n:continue
   nc=cost+size*8/bps+delay
   if nc<best.get(v,float('inf')):best[v]=nc;heapq.heappush(q,(nc,v,path+((u,v),)))
 raise ValueError('unreachable')

def simulate(p,services,arrivals,sizes,links,sensors,zero_network=False):
 e=Engine();cpu=[Resource(e) for _ in range(3)];net={k:Resource(e) for k in links};lat=[];parts_out=[];joins=[];traffic={k:0. for k in links};busy=np.zeros(3)
 paths={(a,b,float(sz)):route(a,b,sz,links) for a in range(3) for b in range(3) for sz in sizes}
 def send(a,b,size,part,cb):
  path=() if zero_network else paths[a,b,float(size)]
  def hop(i,pp):
   if i==len(path):cb(pp);return
   key=path[i];bps,delay=links[key];traffic[key]+=size
   def done(w,s):e.at(e.now+delay,lambda:hop(i+1,(pp[0],pp[1]+s+delay,pp[2]+w)))
   net[key].submit(size*8/bps,'app',done)
  hop(0,part)
 def start(rid,t):
  ready={}
  def execute(i,part,cb):
   duration=services[rid,i,p[i]];busy[p[i]]+=duration
   cpu[p[i]].submit(duration,'app',lambda w,s:cb((part[0]+s,part[1],part[2]+w)))
  def finish(part):
   dt=e.now-t;assert abs(sum(part)-dt)<1e-7;lat.append(dt);parts_out.append(part)
  def arrive(i,part):
   ready[i]=(e.now,part)
   if len(ready)==2:
    joins.append(abs(ready[0][0]-ready[1][0]));critical=max(ready.values(),key=lambda x:x[0])[1]
    execute(2,critical,lambda pp:send(p[2],0,sizes[4],pp,finish))
  def branch(i,src):
   send(src,p[i],sizes[i],(0.,0.,0.),lambda pp:execute(i,pp,lambda qq:send(p[i],p[2],sizes[i+2],qq,lambda rr:arrive(i,rr))))
  branch(0,0);branch(1,0 if sensors=='same' else 1)
 for rid,t in enumerate(arrivals):e.at(float(t),lambda rid=rid,t=t:start(rid,t))
 e.run();assert len(lat)==len(arrivals)
 dc=np.mean(parts_out,axis=0)*1000;n=len(lat)
 return dict(mean_ms=float(np.mean(lat)*1000),p95_ms=float(np.quantile(lat,.95)*1000),wan_bytes=sum(v for k,v in traffic.items() if 2 in k)/n,critical_compute_ms=float(dc[0]),critical_network_ms=float(dc[1]),critical_queue_ms=float(dc[2]),join_wait_ms=float(np.mean(joins)*1000),link_seconds_per_request=max(v*8/links[k][0] for k,v in traffic.items())/n,node_seconds_per_request=float(max(busy)/n))

def verify():
 dag.verify()
 assert route(0,1,100,graph('cloud_hub',10))==((0,2),(2,1))
 assert route(0,1,100,graph('edge_fast',10))==((0,3),(3,1))
 services=np.array([[[1.]*3,[2.]*3,[3.]*3]])
 a=simulate((0,1,0),services,np.array([0.]),[0]*5,graph('edge_fast',10),'same')
 assert abs(a['mean_ms']-5002)<1e-8
 b=simulate((0,1,0),services,np.array([0.]),[0]*5,graph('cloud_hub',10),'same')
 assert abs(b['mean_ms']-5080)<1e-8
 return dict(branch_join=True,resource_score_fixtures=True,routing=True)

def main():
 checks=verify();rows=[];planning=[];t0=time.perf_counter();bg=dag.background('balanced')
 conditions=list(itertools.product(['light','heavy'],['same','split'],['edge_fast','edge1_slow','cloud_hub'],[2,10,100],[.1,1],['free','cloud']))
 for ci,vals in enumerate(conditions):
  workload,sensors,topology,bw,rate,sink=vals;meta=dict(zip(KEYS,vals));med,sig,pr,sizes=dag.profiles(workload);links=graph(topology,bw)
  plans=[p for p in itertools.product(range(3),repeat=3) if dag.feasible(p,bg,sink)]
  pred={};elapsed={};candidates={}
  for policy in POLICIES[1:]:
   pp=np.repeat(pr.mean(axis=1)[:,None],3,axis=1) if policy=='topology_only' else pr
   st=time.perf_counter();pred[policy]={p:simulate(p,pp[None,:,:],np.array([0.]),sizes,links,sensors,policy=='profile_only')['mean_ms'] for p in plans};elapsed[policy]=(time.perf_counter()-st)*1000
   best=min(pred[policy].values());candidates[policy]=[p for p in plans if abs(pred[policy][p]-best)<1e-9]
  planning.append(dict(**meta,candidates=candidates,predictions={pol:[dict(plan=p,cost_ms=v) for p,v in vmap.items()] for pol,vmap in pred.items()},planning_ms=elapsed))
  for seed in SEEDS:
   arrivals=dag.base.arrivals(seed,rate,1,120/rate);services=np.random.default_rng(seed+200000).lognormal(np.log(med),sig,(len(arrivals),3,3))
   rp,order=dag.resource_plan(bg,sink,sensors,seed)
   selected={'resource_proxy':rp}
   for policy in POLICIES[1:]:selected[policy]=candidates[policy][int(np.random.default_rng(seed+900000).integers(len(candidates[policy])))]
   cache={p:simulate(p,services,arrivals,sizes,links,sensors) for p in set(selected.values())}
   for policy,p in selected.items():
    assert dag.feasible(p,bg,sink);v=cache[p]
    rows.append(dict(**meta,seed=seed,policy=policy,plan=p,deployment_order=order,n=len(arrivals),trace_hash=hashlib.sha256(arrivals.tobytes()).hexdigest(),planning_ms=elapsed.get(policy),link_rho=rate*v['link_seconds_per_request'],node_rho=rate*v['node_seconds_per_request'],**v))
  if (ci+1)%12==0:print('conditions',ci+1,'/',len(conditions),flush=True)
 summary=[];rng=np.random.default_rng(20261021)
 for vals in conditions:
  rs=[r for r in rows if tuple(r[k] for k in KEYS)==vals];block={(r['seed'],r['policy']):r for r in rs}
  for comp in POLICIES[:-1]:
   effects=np.array([100*(1-block[s,'topology_profile']['mean_ms']/block[s,comp]['mean_ms']) for s in SEEDS]);ci=np.quantile(rng.choice(effects,(5000,len(SEEDS))).mean(axis=1),[.025,.975])
   rec=dict(zip(KEYS,vals));rec.update(comparator=comp,paired_improvement_pct=float(effects.mean()),ci_low=float(ci[0]),ci_high=float(ci[1]))
   for prefix,pol in [('baseline',comp),('full','topology_profile')]:
    for metric in ['mean_ms','p95_ms','wan_bytes','critical_compute_ms','critical_network_ms','critical_queue_ms','link_rho','node_rho']:
     rec[prefix+'_'+metric]=float(np.mean([block[s,pol][metric] for s in SEEDS]))
   rec['ratio_of_means_pct']=100*(1-rec['full_mean_ms']/rec['baseline_mean_ms']);summary.append(rec)
 (ROOT/'results.json').write_text(json.dumps(dict(runs=rows,summary=summary,planning=planning),indent=2))
 with (ROOT/'summary.csv').open('w') as f:
  w=csv.DictWriter(f,fieldnames=summary[0]);w.writeheader();w.writerows(summary)
 checks.update(conditions=len(conditions),seeds=len(SEEDS),policy_rows=len(rows),resource_fit_all=True,decomposition_all=True,identical_plan_reuse=True,wall_seconds=time.perf_counter()-t0,python=platform.python_version(),numpy=np.__version__,script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
 (ROOT/'verification.json').write_text(json.dumps(checks,indent=2));print(checks)
if __name__=='__main__':main()
