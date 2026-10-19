"""Causal delayed-state estimator and decision-value observation policy.
No controller receives true queues except named diagnostic modes or paid replies.
All forward scenarios are sampled from controller estimates, never environment events.
"""
from legacy import Sim,Resource,MED,P90,PLANS,INPUT,INTER,OUTPUT,service,quantile
from collections import Counter,deque
import math,random,statistics,time,bisect,hashlib,json
import numpy as np

MEAN=np.array(MED)*np.exp(.5*(np.log(np.array(P90)/np.array(MED))/1.2815515655446004)**2)
VAR=(np.exp((np.log(np.array(P90)/np.array(MED))/1.2815515655446004)**2)-1)*MEAN**2
P=np.array(PLANS);NPLAN=len(P);IX=np.arange(NPLAN)

class QueueBelief:
    """Approximate reflected Gaussian workload/rate filter with historical replay.
    Workload in server seconds, hidden background input in server seconds/second.
    App enqueue work and variance are known inputs. Delayed waiting-time feedback
    observes TOTAL workload immediately BEFORE that task was enqueued.
    """
    def __init__(self):
        self.events=[];self.snapshots=[];self.order=0;self.time=0.;self.state=np.array([0.,.1,.0004,0.,.0225])
    @staticmethod
    def advance(state,dt):
        q,r,v,c,w=map(float,state)
        # 0.25s reflected approximation; no forecast of true future arrivals.
        while dt>1e-10:
            h=min(.25,dt);dt-=h
            raw=q+(r-1)*h
            nv=max(1e-8,v+2*h*c+h*h*w+.04*max(.01,r)*h+.0004*h**3/3)
            nc=c+h*w+.0004*h*h/2;w=min(.16,w+.0004*h)
            if raw<0:
                q=0.;v=min(nv,.0016*max(.01,r)/max(.1,2*(1-r)));c=0.
            else:q=raw;v=nv;c=nc
        return np.array([q,r,v,c,w])
    def at(self,t):
        assert t>=self.time-1e-8
        return self.advance(self.state,max(0,t-self.time))
    def reserve_order(self):
        self.order+=1
        return self.order
    def add(self,t,kind,value,variance=0.,token=None,anchor=None):
        self.order+=1
        # Preserve real enqueue order for simultaneous stages. A delayed observation
        # retains the ordering slot reserved when its measured event happened.
        event=(t,self.order if anchor is None else anchor,self.order,kind,value,variance,token)
        index=bisect.bisect_right(self.events,event)
        self.events.insert(index,event);self.snapshots.insert(index,None)
        self.replay(index)
    def correct(self,token,actual):
        for j,e in enumerate(self.events):
            if e[3]=='input' and e[6]==token:
                self.events[j]=(e[0],e[1],e[2],e[3],actual,0.,token);self.replay(j);return
    def replay(self,index=0):
        if index:t=self.events[index-1][0];s=self.snapshots[index-1].copy()
        else:t=0.;s=np.array([0.,.1,.0004,0.,.0225])
        for j in range(index,len(self.events)):
            ts,_,__,kind,value,variance,token=self.events[j]
            s=self.advance(s,ts-t);t=ts
            if kind=='input':s[0]+=value;s[2]+=variance
            else:
                q,r,v,c,w=s;den=v+max(1e-6,variance);err=value-q
                kq=v/den;kr=c/den
                s=np.array([max(0,q+kq*err),min(1.2,max(.01,r+kr*err)),max(1e-8,v-v*v/den),c-v*c/den,max(1e-7,w-c*c/den)])
            self.snapshots[j]=s.copy()
        self.state=s;self.time=t

class Revised(Sim):
    def __init__(self,policy,*args,**kwargs):
        self.pattern=kwargs.pop('pattern','legacy');self.exogenous_log=[]
        self.mode=policy;self.filter=[QueueBelief() for _ in range(4)]
        self.learned_ready={};self.planned_pending={};self.decision_log=[];self.query_audit=[]
        self.filter_errors=[];self.known_service={};self.query_serial=0;self.controller_seconds=0.
        self._compiled=False;self._period=1. if policy.startswith('diag_') else 5.
        self.last_change_query=-1e9
        # Legacy init calls predict to choose a static placement; use its implementation until compiled.
        super().__init__(policy,*args,**kwargs)
        self._compile();self._compiled=True
        self.static=PLANS[int(np.argmin(self.costs(np.zeros((1,4)),np.zeros((1,4)))[0]))]
    def _compile(self):
        self.arrival=np.array([[self.transfer_time(0,int(p[s]),INPUT[s]) for s in range(2)] for p in P])
        self.middle=np.array([[self.transfer_time(int(p[s]),int(p[2]),INTER[s]) for s in range(2)] for p in P])
        self.ret=np.array([self.transfer_time(int(p[2]),0,OUTPUT) for p in P])
        self.first=(self.arrival[:,1]<self.arrival[:,0]).astype(int)
    def costs(self,q,rho):
        # Resource calendar within each candidate DAG. Enqueue order and join explicit.
        # rho predicts additional background arrivals BEFORE each stage reaches its node.
        m=len(q);free=np.broadcast_to(q[:,None,:],(m,NPLAN,4)).copy()
        rates=np.broadcast_to(rho[:,None,:],free.shape);last=np.zeros_like(free)
        ends=np.zeros((m,NPLAN,2))
        for s in [self.first,1-self.first]:
            n=P[IX,s];a=self.arrival[IX,s]
            free[:,IX,n]=np.maximum(a,free[:,IX,n]+rates[:,IX,n]*(a-last[:,IX,n]))+MEAN[n,s]
            last[:,IX,n]=a
            ends[:,IX,s]=free[:,IX,n]+self.middle[IX,s]
        join=ends.max(axis=2);n=P[:,2]
        finish=np.maximum(join,free[:,IX,n]+rates[:,IX,n]*np.maximum(0,join-last[:,IX,n]))+MEAN[n,2]
        return finish+self.ret
    def state(self):return np.array([f.at(self.e.now) for f in self.filter])
    def predict(self,plan,backlog=None):
        if not self._compiled:return super().predict(plan,backlog)
        states=self.state();q=states[:,0] if backlog is None else np.array(backlog)
        score=self.costs(q[None,:],states[None,:,1])[0,PLANS.index(tuple(plan))]
        return float(score),set(plan)
    def input_event(self,n,t,cost,var,token):
        self.filter[n].add(t,'input',cost,var,token)
    def observe(self,n,ts,q,variance=1e-6,anchor=None):
        # No stale observation overwrites 'now': historical update then causal replay.
        self.filter[n].add(ts,'obs',q,variance,anchor=anchor)
    def probe(self,node,mode='paid',audit=None):
        if self.pending[node] or self.e.now>=self.duration:return
        self.pending[node]=True;self.probes+=1;self.query_serial+=1
        token=('probe',self.query_serial);t0=self.e.now
        audit=dict(audit or {});audit.update(node=node,request_s=t0,mode=mode)
        def sampled():
            sample_time=self.e.now;value=max(0,self.cpu[node].free-self.e.now)
            anchor=self.filter[node].reserve_order()
            def receive():
                self.observe(node,sample_time,value,anchor=anchor);self.pending[node]=False;self.probe_finished+=1
                audit.update(sample_s=sample_time,received_s=self.e.now,value_s=value)
                self.query_audit.append(audit)
                if self.capture:self.probe_log.append((t0,self.e.now,node,value))
            if mode=='instant':receive()
            elif mode=='delayed_free':
                self.e.at(self.e.now+value+self.probe_cost+self.transfer_time(node,0,1024),receive)
            else:
                self.input_event(node,self.e.now,self.probe_cost,0.,token)
                self.cpu[node].submit(self.probe_cost,'probe',lambda w,d:self.send(node,0,1024,'probe',receive))
        if mode=='instant':sampled()
        elif mode=='delayed_free':self.e.at(self.e.now+self.transfer_time(0,node,1024),sampled)
        else:self.send(0,node,1024,'probe',sampled)
    def periodic(self):
        if self.e.now>=self.duration:return
        mode={'diag_instant':'instant','diag_delayed':'delayed_free'}.get(self.mode,'paid')
        for n in range(4):self.probe(n,mode)
        self.e.at(min(self.duration,self.e.now+self._period),self.periodic)
    def sample_states(self,states,count=128):
        # Independent controller randomness, independent of request service/background RNGs.
        rng=np.random.default_rng(900001+self.seed*1009+self.nrequests*17)
        z=rng.standard_normal((count,4,2));out=np.zeros((count,4,2))
        for n,(q,r,v,c,w) in enumerate(states):
            a=math.sqrt(max(v,1e-8));b=c/a;d=math.sqrt(max(w-b*b,1e-8))
            out[:,n,0]=np.maximum(0,q+a*z[:,n,0])
            out[:,n,1]=np.clip(r+b*z[:,n,0]+d*z[:,n,1],.01,1.2)
        return out,rng
    def active_query(self,states):
        if self.e.now<self.next_allowed:return
        # Conditional decision-value approximation with held-out Monte Carlo scoring.
        # Only uncertainty available BEFORE asking is used. A 2s horizon is a design assumption.
        samples,rng=self.sample_states(states);qs=samples[:,:,0];rs=samples[:,:,1]
        choices=[];horizon=2.;half=len(samples)//2
        for n in range(4):
            if self.pending[n]:continue
            forward=self.transfer_time(0,n,1024);back=self.transfer_time(n,0,1024)
            predicted_wait=max(0,states[n,0]+(states[n,1]-1)*forward)
            delay=forward+predicted_wait+self.probe_cost+back
            if delay>=horizon:continue
            # Shared latent current state and hidden rate across candidate placements.
            # Future shot noise is not revealed by a current queue observation.
            early=rng.normal(0,1,(len(samples),4))*np.sqrt(.04*rs*forward)
            later=rng.normal(0,1,(len(samples),4))*np.sqrt(.04*rs*max(0,delay-forward))
            qsample=np.maximum(0,qs+(rs-1)*forward+early)
            y=qsample[:,n]
            raw_future=qsample+(rs-1)*(delay-forward)+later
            qfuture=np.maximum(0,raw_future)
            before=self.costs(qfuture,rs)
            # Add probe work BEFORE reflection/draining, so an idle node does not
            # retain fictitious probe backlog after the probe has already finished.
            raw_paid=raw_future.copy();raw_paid[:,n]+=self.probe_cost
            qpaid=np.maximum(0,raw_paid)
            after=self.costs(qpaid,rs)
            cuts=np.unique(np.quantile(y[:half],[.25,.5,.75]));bins=np.searchsorted(cuts,y,side='right')
            baseline=int(np.argmin(before[:half].mean(axis=0)))
            chosen=np.full(len(samples),baseline,dtype=int)
            for b in np.unique(bins):
                train=(bins[:half]==b)
                if train.sum()>=4:chosen[bins==b]=int(np.argmin(after[:half][train].mean(axis=0)))
            delta=before[half:,baseline]-after[np.arange(half,len(samples)),chosen[half:]]
            requests=self.rate*(horizon-delay)
            estimate=float(delta.mean())*requests
            uncertainty=float(delta.std(ddof=1)/math.sqrt(len(delta)))*requests
            # Conservative proxy for interference before the response; not an exact total-latency model.
            pending=sum(v==n for v in self.planned_pending.values())
            wire=sum(1024*8/self.spec[k][0] for k in self.route(0,n)+self.route(n,0))
            early_cost=self.probe_cost*(pending+self.rate*delay)+wire*(len(self.planned_pending)/3+self.rate*delay)
            gain=estimate-1.64*uncertainty-early_cost
            choices.append((gain,n,dict(estimated_gain_s=estimate,mc_se_s=uncertainty,early_cost_s=early_cost,score_s=gain,delay_s=delay)))
        if choices:
            gain,n,audit=max(choices,key=lambda x:x[0])
            if gain>0:self.probe(n,audit=audit);self.next_allowed=self.e.now+.5
    def select(self):
        started=time.perf_counter();states=self.state()
        if self.mode=='diag_current':
            # Explicit diagnostic with free exact current workload; no future event access.
            for n,cpu in enumerate(self.cpu):self.observe(n,self.e.now,max(0,cpu.free-self.e.now))
            states=self.state()
        # Match the placement objective to the observation-value objective:
        # E[L(plan, state)], not L(plan, E[state]); the DAG max is nonlinear.
        samples,_=self.sample_states(states)
        scores=self.costs(samples[:,:,0],samples[:,:,1]).mean(axis=0)
        k=int(np.argmin(scores));p=PLANS[k]
        if self.mode=='static':p=self.static;k=PLANS.index(p)
        if self.mode=='active':self.active_query(states)
        self.controller_seconds+=time.perf_counter()-started
        if self.capture:self.decision_log.append(dict(t=self.e.now,plan=p,q=states[:,0].tolist(),rho=states[:,1].tolist(),variance=states[:,2].tolist()))
        return p,float(scores[k])
    def arrive(self,rid):
        t0=self.e.now;plan,pred=self.select();self.nrequests+=1;self.plans[str(plan)]+=1
        if self.prev is not None and self.prev!=plan:self.switches+=1
        self.prev=plan
        if self.capture:self.decisions.append((t0,rid,plan,pred))
        for s,n in enumerate(plan):self.planned_pending[(rid,s)]=n;self.reserved[n]+=float(MEAN[n,s])
        rec=dict(request_id=rid,arrival_s=t0,plan=str(plan),prediction_s=pred,stages={},join_inputs={})
        def complete():
            latency=self.e.now-t0;self.completed+=1;rec.update(e2e_s=latency,finish_s=self.e.now,slo_miss=latency>1)
            for s,n in enumerate(plan):
                d=rec['stages'][s];token=(rid,s)
                self.filter[n].correct(token,d['service_s'])
                self.observe(n,d['queued_s'],d['wait_s'],anchor=d['observation_order'])
                self.filter_errors.append(d['wait_s']-d['predicted_wait_s'])
            self.prediction_errors.append(latency-pred)
            if self.mode=='change_detect' and len(self.prediction_errors)>=5 and self.e.now-self.last_change_query>=5 and statistics.mean(self.prediction_errors)>.1:
                for n in set(plan):self.probe(n)
                self.last_change_query=self.e.now
            if t0>=self.warmup:self.rows.append(rec)
        def stage(s):
            n=plan[s];queued=self.e.now;token=(rid,s)
            anchor=self.filter[n].reserve_order()
            predicted=float(self.filter[n].at(queued)[0]);self.planned_pending.pop(token)
            self.input_event(n,queued,float(MEAN[n,s]),float(VAR[n,s]),token)
            def done(wait,dur):
                self.task_count+=1;self.reserved[n]=max(0,self.reserved[n]-float(MEAN[n,s]))
                rec['stages'][s]=dict(queued_s=queued,start_s=queued+wait,finish_s=self.e.now,wait_s=wait,service_s=dur,predicted_wait_s=predicted,observation_order=anchor)
                if s==2:self.send(n,0,OUTPUT,'app',complete)
                else:
                    def at_join():
                        rec['join_inputs'][s]=self.e.now
                        if len(rec['join_inputs'])==2:stage(2)
                    self.send(n,plan[2],INTER[s],'app',at_join)
            self.cpu[n].submit(service(self.seed,rid,s,n),'app',done)
        for s in range(2):self.send(0,plan[s],INPUT[s],'app',lambda s=s:stage(s))
    def setup(self):
        # EXACT original request generator. Legacy background generator also retained.
        rng=random.Random(self.seed);t=0.;rid=0
        while True:
            t+=rng.expovariate(self.rate)
            if t>=self.duration:break
            self.e.at(t,lambda r=rid:self.arrive(r));rid+=1
        for n in range(4):
            rng=random.Random(10000+self.seed*31+n);t=0.
            schedule_rng=random.Random(70000+self.seed*31+n)
            boundary=15.;util=.1
            while t<self.duration:
                if self.pattern=='legacy':
                    interval=int(t//30);boundary=min((interval+1)*30,self.duration);util=.1
                    if self.dynamic:
                        if n==3 and interval%2==1:util=.9
                        if n==2 and interval%2==0:util=.65
                elif t>=boundary:
                    boundary=min(self.duration,boundary+schedule_rng.uniform(10,25))
                    util=schedule_rng.choice([.1,.4,.7,.9])
                t+=rng.expovariate(util/.04)
                if t>=boundary:t=boundary;continue
                self.exogenous_log.append((n,t))
                self.e.at(t,lambda n=n:self.cpu[n].submit(.04,'background',lambda w,d:None))
        if self.mode in ['periodic','diag_instant','diag_delayed','diag_paid']:self.e.at(0,self.periodic)
    def run(self):
        r=super().run();r['policy']=self.mode;r['estimator']='revised'
        r['pattern']=self.pattern
        r['background_sha256']=hashlib.sha256(json.dumps(self.exogenous_log).encode()).hexdigest()
        r['wait_rmse_ms']=float(np.sqrt(np.mean(np.array(self.filter_errors)**2))*1000)
        r['controller_wall_s']=self.controller_seconds
        r['query_nodes']=dict(Counter(a['node'] for a in self.query_audit))
        r['positive_queries']=sum(a['value_s']>.001 for a in self.query_audit)
        r['mean_query_delay_ms']=statistics.mean((a['received_s']-a['request_s'])*1000 for a in self.query_audit) if self.query_audit else 0.
        return r
