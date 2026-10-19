"""Synthetic structural stress tests. No fitted/tuned candidate parameters."""
import random, time
import numpy as np
import serial
from serial import Resource
from revised import QueueBelief
from components import Components

class Scenario(Components):
    def __init__(self,arm,prior,env,seed):
        super().__init__(arm,prior,env,seed,capture=True)
        serial.FEATURE*=env.get('feature_scale',1)
        self.split=env.get('split',False)
        if self.split:
            self.cpu.append(Resource(self.e));self.filter.append(QueueBelief())
    def enqueue(self,n,k,rid,kind,fn):
        # A separate physical worker for VQA, co-located in the cloud (no added WAN).
        return super().enqueue(4 if self.split and k==4 else n,k,rid,kind,fn)
    def costs(self,means,states=None):
        if not self.split:return super().costs(means,states)
        m=np.atleast_2d(means);s=self.states() if states is None else states
        q=s[:,0];rho=s[:,1];out=np.zeros((len(m),4))
        for n in range(4):
            ready=self.forecast_send(0,n,serial.RAW,np.full(len(m),q[0]+self.pre))
            end=np.maximum(ready,q[n]+rho[n]*ready+(self.pre if n==0 else 0))+m[:,n]
            join=self.forecast_send(n,3,serial.FEATURE,end)
            # All arms know the topology. No shared-queue correction is warranted.
            start=np.maximum(join,q[4]+rho[4]*join)
            out[:,n]=self.forecast_send(3,0,serial.ANSWER,start+m[:,4])
        return out
    def setup(self):
        rng=random.Random(self.seed);rid=0
        # Piecewise Poisson; 3x10s blocks, equal expected request count per30s.
        for begin in range(0,90,10):
            multiplier=([.25,.25,2.5][(begin//10)%3] if self.env.get('burst') else 1.)
            t=float(begin)
            while True:
                t+=rng.expovariate(self.rate*multiplier)
                if t>=begin+10:break
                self.arrivals.append(t);self.e.at(t,lambda rid=rid:self.arrive(rid));rid+=1
        for n in range(len(self.cpu)):
            rng=random.Random(10000+self.seed*31+n);t=0.
            while t<self.duration:
                block=int(t//30);end=min((block+1)*30,self.duration);util=.1
                if self.dynamic and ((n==2 and block%2==0) or (n in [3,4] and block%2==1)):util=.75
                t+=rng.expovariate(util/.04)
                if t>=end:t=end;continue
                self.bglog.append((n,t));self.e.at(t,lambda n=n:self.cpu[n].submit(.04,'background',lambda w,d:None))
    def run(self):
        r=super().run()
        r['completed_by_90']=sum(row['t']+row['e2e']<=90 for row in self.rows)
        r['drain_s']=max(row['t']+row['e2e'] for row in self.rows)-90
        r['decisions']=self.decisions
        return r
