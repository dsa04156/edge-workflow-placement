"""2x2 predictor ablation; same Thompson decisions and service learner in all arms."""
import numpy as np
import serial
from improved import Experiment

class Components(Experiment):
    def __init__(self,arm,prior,env,seed,capture=False):
        self.arm=arm;self.use_links=arm in ['links','both'];self.use_queue=arm in ['queue','both']
        super().__init__('net_ts',prior,env,seed,capture=capture)
    def forecast_send(self,a,b,size,ready):
        if self.use_links:return super().forecast_send(a,b,size,ready)
        return np.asarray(ready)+self.transfer(a,b,size)
    def costs(self,means,states=None):
        m=np.atleast_2d(means);s=self.states() if states is None else states
        q=s[:,0];rho=s[:,1];out=np.zeros((len(m),4))
        for n in range(4):
            ready=self.forecast_send(0,n,serial.RAW,np.full(len(m),q[0]+self.pre))
            end=np.maximum(ready,q[n]+rho[n]*ready+(self.pre if n==0 else 0))+m[:,n]
            join=self.forecast_send(n,3,serial.FEATURE,end)
            if self.use_queue:
                start=np.maximum(join,q[3]+rho[3]*join+(m[:,3] if n==3 else 0))
            else:
                start=end if n==3 else np.maximum(join,q[3]+rho[3]*join)
            out[:,n]=self.forecast_send(3,0,serial.ANSWER,start+m[:,4])
        return out
    def run(self):
        r=super().run();r['arm']=self.arm
        waits=np.array([[rec['wait'] for rec in row['records']] for row in self.rows])
        r.update(mean_pre_wait_ms=float(waits[:,0].mean()*1000),mean_feature_wait_ms=float(waits[:,1].mean()*1000),mean_vqa_wait_ms=float(waits[:,2].mean()*1000),
            cloud_feature_fraction=sum(row['node']==3 for row in self.rows)/len(self.rows),
            mandatory_cloud_utilization=self.rate*float(serial.TRUE_MEAN[4]),
            longrun_cloud_load_lower_bound=self.rate*float(serial.TRUE_MEAN[4])+(.425 if self.dynamic else .1),
            peak_background_cloud_load_lower_bound=self.rate*float(serial.TRUE_MEAN[4])+(.75 if self.dynamic else .1))
        return r
