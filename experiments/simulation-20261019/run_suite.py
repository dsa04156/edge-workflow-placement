import os
os.environ['OPENBLAS_NUM_THREADS']='1'
import json,random,hashlib,datetime,time,concurrent.futures,platform
from pathlib import Path
from scenarios import Scenario
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'results';OUT.mkdir(exist_ok=True)
BASE=dict(model='resnet50',rate=2,bw=300,rtt=40,dynamic=True,vqa_scale=50,drift=False)
def env(name,**kwargs):return dict(BASE,name=name,**{})|kwargs
ENVS=[env('reference'),env('light_cloud',vqa_scale=1),env('medium_cloud',vqa_scale=12),
 env('steady',dynamic=False),env('burst',burst=True),env('steady_burst',dynamic=False,burst=True),
 env('slow_edges',edge_scale=4),env('fast_edges',edge_scale=.25),env('slow_cloud_feature',cloud_scale=4),
 env('large_raw',payload_scale=8),env('small_raw',payload_scale=.125),env('large_features',feature_scale=8),
 env('small_features',feature_scale=.125),env('slow_wan',bw=25),env('high_rtt',rtt=160),
 env('slow_wan_high_rtt',bw=25,rtt=160),env('resnet18',model='resnet18'),env('resnet101',model='resnet101'),
 env('resnet152',model='resnet152'),env('edge_drift',drift=True),env('low_rate',rate=.5),env('overload',rate=4),
 env('split_cloud',split=True),env('split_cloud_burst',split=True,burst=True)]
ARMS=['original','links','queue','both'];SEEDS=list(range(601,613))
def one(job):
    e,s,a=job
    return Scenario(a,'default',e,s).run()
if __name__=='__main__':
    plan=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),environments=ENVS,arms=ARMS,seeds=SEEDS,duration_s=90,prior='default',
      independent_unit='seed; paired exogenous arrivals/background within environment',
      analysis='Per-environment both-vs-original mean paired percentage reduction; seed bootstrap10000 pointwise95CI; two-sided paired t-test on ratios, Holm24. Secondary p95, SLO, WAN, completed within90s. Equal-weight aggregate over24chosen scenarios with seed-block bootstrap, not deployment-weighted.',
      success='mean improvement>=5%, pointwiseCI lower>0 and Holm p<.05; negative conditions retained',
      design='24 prespecified diagnostic scenarios, mostly one-factor contrasts about reference; not full factorial.12seeds fixed budget, no power claim. No adaptive tuning/early stopping.',
      limitations=['Synthetic resource multipliers, burst/background, and separate worker topology; public offline service profiles; no hardware execution.', 'Split adds one co-located cloud worker and its independent background. Both arms know topology; not resource-matched topology performance comparison.', '90s arrivals/background then drain, not stationary stability proof. Overload retained.', 'Link specs known; no external link traffic, scheduling overhead excluded from simulated E2E; one fixed feature model perenv, no accuracy optimization.', 'Four model comparisons are distinct fixed-quality scenarios, not equal-accuracy substitutions.'],
      python=platform.python_version(),code_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob('*.py')})
    (OUT/'design.json').write_text(json.dumps(plan,indent=2))
    jobs=[(e,s,a) for e in ENVS for s in SEEDS for a in ARMS];random.Random(20261019).shuffle(jobs)
    start=time.time()
    with (OUT/'runs.jsonl').open('w') as f,concurrent.futures.ProcessPoolExecutor(max_workers=6) as pool:
        for i,r in enumerate(pool.map(one,jobs),1):
            f.write(json.dumps(r)+'\n');f.flush()
            if i%96==0:print(i,len(jobs),round(time.time()-start,1),flush=True)
