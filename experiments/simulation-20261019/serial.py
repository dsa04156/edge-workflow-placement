"""Fixed image workflow: source preprocessing -> ResNet18 features -> cloud VQA -> source.
Public service profiles, synthetic infrastructure and load. No accuracy claims.
The simulator owns true service parameters; learning controllers use only replies.
"""
from legacy import Engine,Resource,quantile
from revised import QueueBelief
from pathlib import Path
import csv,math,random,hashlib,json,time
from collections import Counter
import numpy as np

ROOT=Path(__file__).resolve().parent
PROFILE=ROOT/'data/jellybean/VQA/examples/optimizer_inputs'
WORKERS=['workflow-compute-cpu-1','workflow-compute-cpu-3','workflow-compute-cpu-5','workflow-compute-gpu-1']
def profiles():
    data=[]
    for percentile in ['p50','p90']:
        d={(s,w,m):float(v)/1000 for s,w,m,v in csv.reader(open(PROFILE/f'execution_profile_{percentile}.csv'))}
        data.append([d['ExtractImageFeature',w,'resnet18'] for w in WORKERS]+[d['VQA',WORKERS[3],'default']])
    med,p90=map(np.array,data);sigma=np.log(p90/med)/1.2815515655446004
    return med,sigma,med*np.exp(sigma*sigma/2)
MED,SIGMA,TRUE_MEAN=profiles()
MSG=list(csv.reader(open(PROFILE/'message_sizes.csv')))
RAW=float(next(r[4] for r in MSG if r[:2]==['ReadImage','ExtractImageFeature']))*1024
FEATURE=float(next(r[4] for r in MSG if r[:3]==['ExtractImageFeature','VQA','resnet18']))*1024
ANSWER=1024.

class PerformanceBelief:
    def __init__(self,known=False):
        self.known=known
        # Generic priors fixed independently of the five public ground-truth means.
        self.mu=np.log([.100,.070,.040,.020,.010])-.125
        self.v=np.full(5,.7**2);self.count=np.zeros(5,dtype=int)
    def means(self):
        return TRUE_MEAN.copy() if self.known else np.exp(self.mu+.5*(self.v+.25))
    def update(self,k,value):
        if self.known:return
        gain=self.v[k]/(self.v[k]+.25)
        self.mu[k]+=gain*(math.log(value)-self.mu[k]);self.v[k]*=1-gain;self.count[k]+=1

class Serial:
    def __init__(self,policy,seed=31,rate=2,bw=100,dynamic=False,duration=120,pre_ms=5,budget=20,capture=False):
        self.policy=policy;self.seed=seed;self.rate=rate;self.bw=bw;self.dynamic=dynamic;self.duration=duration
        self.pre=pre_ms/1000;self.budget=budget;self.capture=capture;self.e=Engine()
        self.cpu=[Resource(self.e) for _ in range(4)];self.spec={}
        for n in range(3):
            for direction in ['up','down']:self.spec[f'e{n}_{direction}']=(1e9,.0005)
        for direction in ['up','down']:self.spec[f'wan_{direction}']=(bw*1e6,.020)
        self.links={k:Resource(self.e) for k in self.spec};self.filter=[QueueBelief() for _ in range(4)]
        self.belief=PerformanceBelief(policy in ['cloud','edge0','edge2','e2e_known'])
        self.rows=[];self.audit=[];self.decisions=[];self.bglog=[];self.arrivals=[]
        self.count=0;self.finished=0;self.probes=0;self.probe_done=0;self.pending=[False]*4
        self.outstanding=Counter();self.bytes=Counter();self.wanbytes=Counter();self.plans=Counter()
        self.last_query=-1e9;self.wall=0.;self.probe_times=[]
    def route(self,a,b):
        if a==b:return []
        return ([f'e{a}_up'] if a<3 else ['wan_down'])+([f'e{b}_down'] if b<3 else ['wan_up'])
    def transfer(self,a,b,size):return sum(size*8/self.spec[k][0]+self.spec[k][1] for k in self.route(a,b))
    def send(self,a,b,size,kind,fn):
        path=self.route(a,b);self.bytes[kind]+=size*len(path)
        self.wanbytes[kind]+=size*sum(k.startswith('wan_') for k in path)
        def hop(i):
            if i==len(path):fn();return
            k=path[i];bw,delay=self.spec[k]
            self.links[k].submit(size*8/bw,kind,lambda w,d:self.e.at(self.e.now+delay,lambda:hop(i+1)))
        hop(0)
    def sample_service(self,k,rid,kind):
        key=f'{self.seed}:{rid}:{k}:{kind}'.encode();seed=int.from_bytes(hashlib.sha256(key).digest()[:8],'big')
        return random.Random(seed).lognormvariate(math.log(MED[k]),SIGMA[k])
    def states(self,t=None):return np.array([f.at(self.e.now if t is None else t) for f in self.filter])
    def costs(self,means,states=None):
        """Four complete placements, including source preprocessing and result return.
        Forecasts mean costs with causal queue/rate estimates, not true queues.
        """
        m=np.atleast_2d(means);states=self.states() if states is None else states
        q=states[:,0];rho=states[:,1];out=np.zeros((len(m),4))
        for n in range(4):
            prefinish=q[0]+self.pre
            ready=prefinish+self.transfer(0,n,RAW)
            free=q[n]+rho[n]*ready+(self.pre if n==0 else 0)
            feature_end=np.maximum(ready,free)+m[:,n]
            join=feature_end+self.transfer(n,3,FEATURE)
            if n==3:vqa_start=feature_end
            else:vqa_start=np.maximum(join,q[3]+rho[3]*join)
            out[:,n]=vqa_start+m[:,4]+self.transfer(3,0,ANSWER)
        return out
    def enqueue(self,n,k,rid,kind,fn):
        queued=self.e.now;anchor=self.filter[n].reserve_order();token=(kind,rid,k)
        mean=self.pre if k==-1 else float(self.belief.means()[k])
        variance=0. if k==-1 else mean*mean*.3
        self.filter[n].add(queued,'input',mean,variance,token)
        self.outstanding[n]+=1
        actual=self.pre if k==-1 else self.sample_service(k,rid,kind)
        def done(wait,dur):
            self.outstanding[n]-=1
            fn(dict(node=n,k=k,queued=queued,wait=wait,duration=dur,anchor=anchor,token=token,finish=self.e.now))
        self.cpu[n].submit(actual,kind,done)
    def feedback(self,records):
        for r in records:
            self.filter[r['node']].correct(r['token'],r['duration'])
            self.filter[r['node']].add(r['queued'],'obs',r['wait'],1e-6,anchor=r['anchor'])
            if r['k']>=0:self.belief.update(r['k'],r['duration'])
    def probe(self,n,score=None):
        if self.probes>=self.budget or self.pending[n] or self.e.now>=self.duration:return
        self.probes+=1;pid=self.probes;self.pending[n]=True;t0=self.e.now
        # Cached representative prepared image; FULL input and feature return occupy links.
        def arrived():
            def finished(rec):
                def receive():
                    self.feedback([rec]);self.pending[n]=False;self.probe_done+=1
                    self.audit.append(dict(node=n,t=t0,received=self.e.now,service=rec['duration'],score=score))
                self.send(n,0,FEATURE,'probe',receive)
            self.enqueue(n,n,pid,'probe',finished)
        self.send(0,n,RAW,'probe',arrived)
    def active(self):
        if self.probes>=self.budget or self.e.now-self.last_query<.5:return
        started=time.perf_counter();state=self.states();base=self.belief.means()
        rng=np.random.default_rng(600000+self.seed*1009+self.count)
        latent=rng.normal(self.belief.mu,np.sqrt(self.belief.v),(128,5))
        true_means=np.exp(latent+.125);choices=[];horizon=min(getattr(self,'horizon',2.),max(0,self.duration-self.e.now))
        for n in range(4):
            if self.pending[n]:continue
            delay=self.transfer(0,n,RAW)+state[n,0]+base[n]+self.transfer(n,0,FEATURE)
            if delay>=horizon:continue
            future=self.states(self.e.now+delay)
            before=self.costs(base,future)[0];baseline=int(np.argmin(before))
            y=latent[:,n]+rng.normal(0,.5,128)
            gain=self.belief.v[n]/(self.belief.v[n]+.25)
            posterior_mean=self.belief.mu[n]+gain*(y-self.belief.mu[n])
            posterior_var=self.belief.v[n]*(1-gain)
            predicted=np.tile(base,(128,1));predicted[:,n]=np.exp(posterior_mean+.5*(posterior_var+.25))
            chosen=self.costs(predicted,future).argmin(axis=1)
            real=self.costs(true_means,future)
            delta=real[:,baseline]-real[np.arange(128),chosen]
            benefit=float(delta.mean())*self.rate*(horizon-delay)
            se=float(delta.std(ddof=1)/math.sqrt(128))*self.rate*(horizon-delay)
            wire=sum(RAW*8/self.spec[k][0] for k in self.route(0,n))+sum(FEATURE*8/self.spec[k][0] for k in self.route(n,0))
            cost=base[n]*(self.outstanding[n]+self.rate*delay/4)+wire*self.rate*horizon
            score=benefit-1.64*se-cost;choices.append((score,n,dict(benefit=benefit,mc_se=se,cost=cost,delay=delay)))
        if choices:
            score,n,detail=max(choices,key=lambda v:v[0])
            if score>0:self.probe(n,dict(detail,net=score));self.last_query=self.e.now
        self.wall+=time.perf_counter()-started
    def choose(self):
        if self.policy in ['cloud','edge0','edge2']:return {'cloud':3,'edge0':0,'edge2':2}[self.policy]
        start=time.perf_counter();n=int(self.costs(self.belief.means())[0].argmin());self.wall+=time.perf_counter()-start
        if self.policy=='learn_active':self.active()
        return n
    def arrive(self,rid):
        t0=self.e.now;n=self.choose();self.count+=1;self.plans[n]+=1
        if self.capture:self.decisions.append((t0,rid,n))
        records=[]
        def complete():
            self.feedback(records);self.finished+=1
            self.rows.append(dict(rid=rid,t=t0,e2e=self.e.now-t0,node=n,records=records))
        def vqa_done(r):records.append(r);self.send(3,0,ANSWER,'app',complete)
        def feature_done(r):
            records.append(r)
            self.send(n,3,FEATURE,'app',lambda:self.enqueue(3,4,rid,'app',vqa_done))
        def pre_done(r):
            records.append(r)
            self.send(0,n,RAW,'app',lambda:self.enqueue(n,n,rid,'app',feature_done))
        self.enqueue(0,-1,rid,'app',pre_done)
    def setup(self):
        rng=random.Random(self.seed);t=0;rid=0
        while True:
            t+=rng.expovariate(self.rate)
            if t>=self.duration:break
            self.arrivals.append(t);self.e.at(t,lambda rid=rid:self.arrive(rid));rid+=1
        for n in range(4):
            rng=random.Random(10000+self.seed*31+n);t=0.
            while t<self.duration:
                block=int(t//30);end=min((block+1)*30,self.duration);util=.1
                if self.dynamic and ((n==2 and block%2==0) or (n==3 and block%2==1)):util=.75
                t+=rng.expovariate(util/.04)
                if t>=end:t=end;continue
                self.bglog.append((n,t));self.e.at(t,lambda n=n:self.cpu[n].submit(.04,'background',lambda w,d:None))
        if self.policy=='learn_uniform':
            for i in range(self.budget):
                t=(i+.5)*self.duration/self.budget
                self.e.at(t,lambda i=i:self.probe(i%4))
    def run(self):
        self.setup();self.e.run()
        assert self.count==self.finished and self.probes==self.probe_done and not any(self.outstanding.values())
        for r in self.rows:
            assert len(r['records'])==3
            assert all(a['finish']<=b['queued']+1e-9 for a,b in zip(r['records'],r['records'][1:]))
        vals=[r['e2e']*1000 for r in self.rows];late=[r['e2e']*1000 for r in self.rows if r['t']>=15]
        return dict(policy=self.policy,seed=self.seed,rate=self.rate,wan_mbps=self.bw,dynamic=self.dynamic,duration=self.duration,pre_ms=self.pre*1000,budget=self.budget,
            n=len(vals),mean_ms=float(np.mean(vals)),p95_ms=quantile(vals,.95),slo_1s_pct=100*sum(v>1000 for v in vals)/len(vals),after15_mean_ms=float(np.mean(late)) if late else None,
            probes=self.probes,probe_cpu_ms=1000*sum(c.busy['probe'] for c in self.cpu),probe_link_bytes=self.bytes['probe'],probe_wan_bytes=self.wanbytes['probe'],app_wan_bytes=self.wanbytes['app'],
            controller_wall_s=self.wall,plan_counts=dict(self.plans),learned_means_ms=(self.belief.means()*1000).tolist(),observations=self.belief.count.tolist(),
            background_sha256=hashlib.sha256(json.dumps(self.bglog).encode()).hexdigest(),arrival_sha256=hashlib.sha256(json.dumps(self.arrivals).encode()).hexdigest())
