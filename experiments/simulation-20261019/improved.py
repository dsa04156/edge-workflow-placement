"""Causal link calendars + shared-cloud queue correction + optional E2E residual calibration.
Environment multipliers are synthetic stress tests, not measured hardware claims.
"""
import csv,math,random,hashlib,time
import numpy as np
import serial
from serial import Serial,PerformanceBelief
from trials import Trial

class Experiment(Trial):
    def __init__(self,method,prior,env,seed,capture=False):
        self.env=env;self.method_name=method
        tables=[{(s,w,m):float(v)/1000 for s,w,m,v in csv.reader(open(serial.PROFILE/f'execution_profile_{p}.csv'))} for p in ['p50','p90']]
        vals=[np.array([t['ExtractImageFeature',w,env['model']] for w in serial.WORKERS]+[t['VQA',serial.WORKERS[3],'default']]) for t in tables]
        self.med=vals[0];self.sigma=np.log(vals[1]/vals[0])/1.2815515655446004
        self.med[:3]*=env.get('edge_scale',1);self.med[3]*=env.get('cloud_scale',1);self.med[4]*=env.get('vqa_scale',1)
        serial.MED=self.med;serial.SIGMA=self.sigma;serial.TRUE_MEAN=self.med*np.exp(self.sigma**2/2)
        serial.RAW=813091.666015625*env.get('payload_scale',1)
        serial.FEATURE=float(next(r[4] for r in serial.MSG if r[:3]==['ExtractImageFeature','VQA',env['model']]))*1024
        base={'net':'passive','cal05':'passive','cal20':'passive','cal_ts':'thompson','net_ts':'thompson'}.get(method,method)
        super().__init__(base,prior,seed=seed,rate=env['rate'],bw=env['bw'],dynamic=env['dynamic'],duration=90,capture=capture)
        self.corrected=method in ['net','cal05','cal20','cal_ts','net_ts']
        self.alpha={'cal05':.05,'cal20':.2,'cal_ts':.1}.get(method,0.)
        self.bias=np.zeros(4);self.bias_count=np.zeros(4,dtype=int)
        self.link_calendar={k:0. for k in self.spec};self.predictions={};self.scored=set()
        self.prediction_errors=[];self.completion_cursor=0
        for k,(bw,delay) in list(self.spec.items()):
            self.spec[k]=(bw,env['rtt']/2000 if k.startswith('wan_') else delay)
    def send(self,a,b,size,kind,fn):
        path=self.route(a,b);self.bytes[kind]+=size*len(path)
        self.wanbytes[kind]+=size*sum(k.startswith('wan_') for k in path)
        def hop(i):
            if i==len(path):fn();return
            k=path[i];bw,delay=self.spec[k];service=size*8/bw
            # Records only locally submitted transfers; never reads simulator resource state.
            self.link_calendar[k]=max(self.e.now,self.link_calendar[k])+service
            self.links[k].submit(service,kind,lambda w,d:self.e.at(self.e.now+delay,lambda:hop(i+1)))
        hop(0)
    def forecast_send(self,a,b,size,ready):
        end=np.asarray(ready).copy()
        for k in self.route(a,b):
            bw,delay=self.spec[k]
            end=np.maximum(end,max(0,self.link_calendar[k]-self.e.now))+size*8/bw+delay
        return end
    def costs(self,means,states=None):
        if not getattr(self,'corrected',False):return super().costs(means,states)
        m=np.atleast_2d(means);s=self.states() if states is None else states
        q=s[:,0];rho=s[:,1];out=np.zeros((len(m),4))
        for n in range(4):
            pre=q[0]+self.pre
            ready=self.forecast_send(0,n,serial.RAW,np.full(len(m),pre))
            end=np.maximum(ready,q[n]+rho[n]*ready+(self.pre if n==0 else 0))+m[:,n]
            join=self.forecast_send(n,3,serial.FEATURE,end)
            # Feature and VQA re-enter the same cloud FCFS queue. Background work
            # can arrive while feature inference runs; old predictor skipped it.
            cloud_free=q[3]+rho[3]*join+(m[:,3] if n==3 else 0)
            finish=np.maximum(join,cloud_free)+m[:,4]
            out[:,n]=self.forecast_send(3,0,serial.ANSWER,finish)
        return np.maximum(.001,out+self.bias)
    def choose(self):
        # Read only service requests whose entire result has already returned.
        for r in self.rows[self.completion_cursor:]:
            n=r['node'];pred=self.predictions[r['rid']]
            error=r['e2e']-pred
            self.prediction_errors.append(error)
            if self.alpha:
                self.bias[n]=np.clip(self.bias[n]+self.alpha*error,-.5,.5)
                self.bias_count[n]+=1
        self.completion_cursor=len(self.rows)
        n=super().choose()
        self.predictions[self.count]=float(self.costs(self.belief.means())[0,n])
        return n
    def sample_service(self,k,rid,kind):
        value=super().sample_service(k,rid,kind)
        if self.env.get('drift') and k==2 and self.e.now>=45:value*=2
        return value
    def run(self):
        r=super().run();r.update(method=self.method_name,environment=self.env['name'],env=self.env,
            prediction_mae_ms=float(np.mean(np.abs(self.prediction_errors)))*1000 if self.prediction_errors else None,
            residual_bias_ms=(self.bias*1000).tolist())
        return r
