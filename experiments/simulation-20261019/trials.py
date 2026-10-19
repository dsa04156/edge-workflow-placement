from serial import Serial
import numpy as np
import time

class Trial(Serial):
    def __init__(self,method,prior='default',**kwargs):
        self.method=method;self.prior=prior
        mode='learn_active' if method.startswith('voi_') else ('learn_uniform' if method=='uniform' else 'learn_passive')
        self.horizon=float(method.split('_')[1]) if method.startswith('voi_') else 2.
        super().__init__(mode,**kwargs)
        if prior=='flat':self.belief.mu=np.log([.06,.06,.06,.06,.010])-.125
        if prior=='reversed':self.belief.mu=np.log([.020,.040,.070,.100,.010])-.125
    def choose(self):
        if self.method in ['thompson','lcb1','lcb2']:
            start=time.perf_counter()
            if self.method=='thompson':
                rng=np.random.default_rng(910000+self.seed*1009+self.count)
                mu=rng.normal(self.belief.mu,np.sqrt(self.belief.v))
            else:mu=self.belief.mu-float(self.method[-1])*np.sqrt(self.belief.v)
            means=np.exp(mu+.125);n=int(self.costs(means)[0].argmin())
            self.wall+=time.perf_counter()-start;return n
        return super().choose()
    def uncertainty_probe(self):
        candidates=[n for n in range(4) if not self.pending[n]]
        if candidates:self.probe(max(candidates,key=lambda n:self.belief.v[n]))
    def setup(self):
        super().setup()
        if self.method=='uncertainty':
            for i in range(self.budget):self.e.at((i+.5)*self.duration/self.budget,self.uncertainty_probe)
    def run(self):
        r=super().run();r.update(method=self.method,prior=self.prior,horizon=self.horizon)
        return r
