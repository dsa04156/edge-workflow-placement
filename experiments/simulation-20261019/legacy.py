#!/usr/bin/env python3
"""Fork/join E2E discrete event experiment. Seconds and bytes internally.
Public median/P90 service profiles; all infrastructure and load assumptions explicit.
No policy can read future events or actual resource queues. Paid probes read current
background queue work; passive feedback is delivered only at workflow completion.
"""
from pathlib import Path
from collections import deque, Counter
from itertools import product
import csv, math, random, heapq, json, statistics, argparse, hashlib

ROOT=Path(__file__).resolve().parent
PROFILE=ROOT/'data/jellybean/VQA/examples/optimizer_inputs'
NODES=['E0','E1','E2','C']
WORKERS=['workflow-compute-cpu-1','workflow-compute-cpu-3','workflow-compute-cpu-5','workflow-compute-gpu-1']
STAGES=['ExtractImageFeature','SpeechRecognition','VQA']
MODELS=['resnet34','wav2vec2-large-960h-lv60-self','default']
def read_profiles(percentile):
    rows={(s,w,m):float(v)/1000 for s,w,m,v in csv.reader(open(PROFILE/f'execution_profile_{percentile}.csv'))}
    return [[rows[s,w,m] for s,m in zip(STAGES,MODELS)] for w in WORKERS]
MED=read_profiles('p50'); P90=read_profiles('p90')
PLANS=list(product(range(4),repeat=3))
MESSAGES=list(csv.reader(open(PROFILE/'message_sizes.csv')))
def msg(src,dst,model):
    return float(next(r[4] for r in MESSAGES if r[0:3]==[src,dst,model]))*1024
INPUT=[msg('ReadImage',STAGES[0],'default'),msg('ReadSpeech',STAGES[1],'default')]
INTER=[msg(STAGES[0],'VQA','resnet34'),msg(STAGES[1],'VQA','default')]
OUTPUT=1024 # assumed answer payload
POLICIES=['local','cloud','static','passive','periodic_5','change_detect','active']

def quantile(x,p):
    s=sorted(x); a=(len(s)-1)*p; i=int(a)
    return s[i]+(s[min(i+1,len(s)-1)]-s[i])*(a-i)
def service(seed,rid,stage,node):
    # Stable per-request/node draw, independent of policy and event ordering.
    key=f'{seed}:{rid}:{stage}:{node}'.encode()
    rng=random.Random(int.from_bytes(hashlib.sha256(key).digest()[:8],'big'))
    sigma=max(0,math.log(P90[node][stage]/MED[node][stage])/1.2815515655446004)
    return rng.lognormvariate(math.log(MED[node][stage]),sigma)

class Engine:
    def __init__(self): self.now=0.; self.events=[]; self.seq=0
    def at(self,t,fn):
        assert t>=self.now-1e-9, (t,self.now)
        self.seq+=1; heapq.heappush(self.events,(t,self.seq,fn))
    def run(self):
        while self.events:
            self.now,_,fn=heapq.heappop(self.events); fn()

class Resource:
    def __init__(self,engine): self.e=engine; self.free=0.; self.busy=Counter(); self.bg=deque()
    def submit(self,duration,kind,callback):
        arrival=self.e.now; start=max(arrival,self.free); finish=start+duration
        self.free=finish; self.busy[kind]+=duration
        if kind=='background': self.bg.append((start,finish))
        self.e.at(finish,lambda:callback(start-arrival,duration))
    def background_work(self):
        t=self.e.now
        while self.bg and self.bg[0][1]<=t: self.bg.popleft()
        return sum(end-max(t,start) for start,end in self.bg)

class Sim:
    def __init__(self,policy,seed=1,rate=2.,wan_mbps=100,wan_rtt_ms=40,
                 dynamic=True,duration=120.,probe_ms=10.,warmup=15.,capture=False):
        self.policy=policy; self.seed=seed; self.rate=rate; self.duration=duration
        self.dynamic=dynamic; self.wan_mbps=wan_mbps; self.wan_rtt_ms=wan_rtt_ms
        self.probe_cost=probe_ms/1000; self.warmup=warmup; self.capture=capture
        self.e=Engine(); self.cpu=[Resource(self.e) for _ in NODES]
        self.links={}; self.spec={}
        for i in range(3):
            for direction in ['up','down']: self.spec[f'e{i}_{direction}']=(1e9,.0005)
        for direction in ['up','down']: self.spec[f'wan_{direction}']=(wan_mbps*1e6,wan_rtt_ms/2000)
        self.links={k:Resource(self.e) for k in self.spec}
        self.reserved=[0.]*4 # controller-known unfinished application work, median estimates
        self.hidden=[0.]*4; self.last=[0.]*4; self.pending=[False]*4
        self.probes=0; self.probe_bytes=0; self.app_bytes=0; self.rows=[]; self.plans=Counter()
        self.decisions=[]; self.switches=0; self.prev=None; self.nrequests=0; self.completed=0
        self.task_count=0; self.probe_finished=0; self.prediction_errors=deque(maxlen=10)
        self.next_allowed=0.; self.probe_log=[]
        self.static=min(PLANS,key=lambda p:self.predict(p,[0.]*4)[0])
    def route(self,src,dst):
        if src==dst:return []
        return ([f'e{src}_up'] if src<3 else ['wan_down']) + ([f'e{dst}_down'] if dst<3 else ['wan_up'])
    def transfer_time(self,src,dst,size):
        return sum(size*8/self.spec[k][0]+self.spec[k][1] for k in self.route(src,dst))
    def send(self,src,dst,size,kind,callback):
        path=self.route(src,dst)
        if kind=='probe':self.probe_bytes+=size*len(path)
        else:self.app_bytes+=size*len(path)
        def hop(i):
            if i==len(path):callback();return
            k=path[i]; bw,lat=self.spec[k]
            self.links[k].submit(size*8/bw,kind,lambda w,d:self.e.at(self.e.now+lat,lambda:hop(i+1)))
        hop(0)
    def predict(self,plan,backlog=None):
        # Approximate controller model: existing own reservations and estimated hidden backlog.
        # Link propagation/serialization included; future link contention is NOT known.
        if backlog is None:
            backlog=[self.reserved[n]+max(0,self.hidden[n]-(self.e.now-self.last[n])) for n in range(4)]
        free=list(backlog); branch=[]
        arrivals=[self.transfer_time(0,plan[s],INPUT[s]) for s in range(2)]
        for s in sorted(range(2),key=lambda s:(arrivals[s],s)):
            n=plan[s]; finish=max(arrivals[s],free[n])+MED[n][s];free[n]=finish
            branch.append((finish+self.transfer_time(n,plan[2],INTER[s]),s,n))
        join,crit,_=max(branch)
        finish=max(join,free[plan[2]])+MED[plan[2]][2]
        return finish+self.transfer_time(plan[2],0,OUTPUT),{plan[crit],plan[2]}
    def probe(self,node):
        if self.pending[node] or self.e.now>=self.duration:return
        self.pending[node]=True;self.probes+=1; t0=self.e.now
        def arrived():
            def sampled(w,d):
                value=self.cpu[node].background_work();sample_time=self.e.now
                def received():
                    self.hidden[node]=max(0,value-(self.e.now-sample_time))
                    self.last[node]=self.e.now;self.pending[node]=False;self.probe_finished+=1
                    if self.capture:self.probe_log.append((t0,self.e.now,node,value))
                self.send(node,0,1024,'probe',received)
            self.cpu[node].submit(self.probe_cost,'probe',sampled)
        self.send(0,node,1024,'probe',arrived)
    def periodic(self):
        if self.e.now>=self.duration:return
        for n in range(4):self.probe(n)
        self.e.at(min(self.duration,self.e.now+5),self.periodic)
    def select(self):
        if self.policy=='local':return (0,0,0),self.predict((0,0,0))[0]
        if self.policy=='cloud':return (3,3,3),self.predict((3,3,3))[0]
        if self.policy=='static':return self.static,self.predict(self.static)[0]
        ranked=sorted((self.predict(p)[0],p) for p in PLANS)
        best,plan=ranked[0]
        if self.policy=='active' and self.e.now>=self.next_allowed:
            # Explicit heuristic value-of-information proxy, not a learned optimum.
            # Ask whether a plausible error in stale node state changes the plan;
            # compare short-horizon avoided regret to probe CPU + network time.
            candidates=set(self.predict(plan)[1])
            for score,p in ranked[1:]:
                if score-best>.05:break
                candidates.update(self.predict(p)[1])
            candidates=[n for n in candidates if not self.pending[n] and self.e.now-self.last[n]>=2]
            choices=[]
            base=[self.reserved[n]+max(0,self.hidden[n]-(self.e.now-self.last[n])) for n in range(4)]
            for n in candidates:
                uncertainty=min(.25,.01*(self.e.now-self.last[n]))
                regret=0.
                for sign in [-1,1]:
                    hypothetical=base.copy()
                    hypothetical[n]=max(self.reserved[n],base[n]+sign*uncertainty)
                    regret+=max(0,self.predict(plan,hypothetical)[0]-min(self.predict(p,hypothetical)[0] for p in PLANS))/2
                value=regret*min(10,self.rate*5)
                cost=self.probe_cost+self.transfer_time(0,n,1024)+self.transfer_time(n,0,1024)
                if value>cost:choices.append((value-cost,n))
            if choices:
                _,n=max(choices)
                self.probe(n);self.next_allowed=self.e.now+1
        return plan,best
    def arrive(self,rid):
        t0=self.e.now; plan,pred=self.select(); self.nrequests+=1;self.plans[str(plan)]+=1
        if self.prev is not None and self.prev!=plan:self.switches+=1
        self.prev=plan
        if self.capture:self.decisions.append((t0,rid,plan,pred))
        own_before=self.reserved.copy()
        for s,n in enumerate(plan):self.reserved[n]+=MED[n][s]
        rec={'request_id':rid,'arrival_s':t0,'plan':str(plan),'prediction_s':pred,'stages':{},'join_inputs':{}}
        def complete():
            latency=self.e.now-t0;self.completed+=1
            rec.update(e2e_s=latency,finish_s=self.e.now,slo_miss=latency>1.)
            # Same delayed feedback for every adaptive policy. Unobservable background
            # queue is approximated by residual waiting above pre-dispatch own reservations.
            if self.policy in ['passive','periodic_5','change_detect','active']:
                for s,n in enumerate(plan):
                    wait=rec['stages'][s]['wait_s']
                    residual=max(0,wait-own_before[n])
                    old=max(0,self.hidden[n]-(self.e.now-self.last[n]))
                    self.hidden[n]=.7*old+.3*residual;self.last[n]=self.e.now
                self.prediction_errors.append(latency-pred)
                if self.policy=='change_detect' and self.e.now>=self.next_allowed and len(self.prediction_errors)>=5:
                    if statistics.mean(self.prediction_errors)>.1:
                        for n in set(plan):self.probe(n)
                        self.next_allowed=self.e.now+5
            if t0>=self.warmup:self.rows.append(rec)
        def stage(s):
            n=plan[s];queued=self.e.now
            def done(wait,dur):
                self.task_count+=1;self.reserved[n]=max(0,self.reserved[n]-MED[n][s])
                rec['stages'][s]={'queued_s':queued,'start_s':queued+wait,'finish_s':self.e.now,'wait_s':wait,'service_s':dur}
                if s==2:self.send(n,0,OUTPUT,'app',complete)
                else:
                    def at_join():
                        rec['join_inputs'][s]=self.e.now
                        if len(rec['join_inputs'])==2:stage(2)
                    self.send(n,plan[2],INTER[s],'app',at_join)
            self.cpu[n].submit(service(self.seed,rid,s,n),'app',done)
        for s in range(2):self.send(0,plan[s],INPUT[s],'app',lambda s=s:stage(s))
    def setup(self):
        rng=random.Random(self.seed);t=0;rid=0
        while True:
            t+=rng.expovariate(self.rate)
            if t>=self.duration:break
            self.e.at(t,lambda r=rid:self.arrive(r));rid+=1
        # Exogenous background CPU jobs: 40ms fixed service, Poisson arrivals.
        # 30s waves alternate a busy cloud and a busy fast edge. Same jobs for all policies.
        for n in range(4):
            rng=random.Random(10000+self.seed*31+n);t=0.
            while t<self.duration:
                interval=int(t//30); boundary=min((interval+1)*30,self.duration)
                util=.1
                if self.dynamic:
                    if n==3 and interval%2==1:util=.9
                    if n==2 and interval%2==0:util=.65
                t+=rng.expovariate(util/.04)
                if t>=boundary:
                    t=boundary
                    continue
                self.e.at(t,lambda n=n:self.cpu[n].submit(.04,'background',lambda w,d:None))
        if self.policy=='periodic_5':self.e.at(0,self.periodic)
    def run(self):
        self.setup();self.e.run()
        assert self.completed==self.nrequests and self.task_count==3*self.nrequests
        assert all(x<1e-7 for x in self.reserved) and self.probe_finished==self.probes
        for r in self.rows:
            assert r['stages'][2]['queued_s']>=max(r['join_inputs'].values())-1e-9
            assert r['finish_s']>=r['stages'][2]['finish_s']
        times=[r['e2e_s'] for r in self.rows]
        return dict(policy=self.policy,seed=self.seed,rate=self.rate,wan_mbps=self.wan_mbps,wan_rtt_ms=self.wan_rtt_ms,
                    dynamic=self.dynamic,probe_ms=self.probe_cost*1000,n=len(times),arrivals=self.nrequests,
                    mean_ms=statistics.mean(times)*1000,p50_ms=quantile(times,.5)*1000,p95_ms=quantile(times,.95)*1000,
                    p99_ms=quantile(times,.99)*1000,slo_miss_pct=100*sum(x>1 for x in times)/len(times),
                    probes=self.probes,probe_cpu_ms=sum(r.busy['probe'] for r in self.cpu)*1000,
                    probe_link_bytes=self.probe_bytes,app_link_bytes=self.app_bytes,switches=self.switches,
                    last_completion_s=max(r['finish_s'] for r in self.rows),static_plan=str(self.static),
                    plan_counts=dict(self.plans))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--quick',action='store_true');args=ap.parse_args()
    out=ROOT/'results';out.mkdir(exist_ok=True)
    rows=[]
    settings=list(product([.5,2.,6.],[(100,40),(20,80)],[False,True],[1,2,3],POLICIES))
    if args.quick:settings=[(2.,(100,40),True,1,p) for p in POLICIES]
    for i,(rate,(bw,rtt),dynamic,seed,policy) in enumerate(settings):
        sim=Sim(policy,seed,rate,bw,rtt,dynamic,capture=(rate==2 and bw==100 and dynamic and seed==1))
        row=sim.run(); rows.append(row)
        if sim.capture:
            (out/f'trace_{policy}.json').write_text(json.dumps({'requests':sim.rows,'decisions':sim.decisions,'probes':sim.probe_log},ensure_ascii=False))
        if i%14==0:print(f'{i+1}/{len(settings)} {policy}: mean={row["mean_ms"]:.1f}ms p95={row["p95_ms"]:.1f}ms',flush=True)
    # Separate observation-cost sensitivity; do not pool different costs into one winner.
    if not args.quick:
        for cost,seed,policy in product([2.,50.],[1,2,3],['passive','periodic_5','change_detect','active']):
            rows.append(Sim(policy,seed,2.,100,40,True,probe_ms=cost).run())
    (out/'runs.json').write_text(json.dumps(rows,indent=2))
    fields=[k for k in rows[0] if k!='plan_counts']
    with open(out/'runs.csv','w') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows({k:r[k] for k in fields} for r in rows)
    print(f'Completed {len(rows)} runs',flush=True)
if __name__=='__main__':main()
