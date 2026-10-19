import json,csv
from pathlib import Path
import numpy as np
from scipy import stats
from run_suite import ENVS,ARMS,SEEDS
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'results'
def boot(x):
    x=np.asarray(x);rng=np.random.default_rng(20261019)
    draws=x[rng.integers(len(x),size=(10000,len(x)))].mean(axis=1)
    return [float(x.mean()),*map(float,np.percentile(draws,[2.5,97.5]))]
def main():
    rows=[json.loads(x) for x in (OUT/'runs.jsonl').read_text().splitlines()]
    assert len(rows)==24*12*4
    lookup={(r['environment'],r['seed'],r['arm']):r for r in rows};assert len(lookup)==len(rows)
    out=[];all_gains=[]
    for e in ENVS:
        name=e['name'];groups={a:[lookup[name,s,a] for s in SEEDS] for a in ARMS}
        for i in range(len(SEEDS)):
            for key in ['arrival_sha256','background_sha256','n']:
                assert len({groups[a][i][key] for a in ARMS})==1
        b=np.array([r['mean_ms'] for r in groups['original']]);v=np.array([r['mean_ms'] for r in groups['both']]);g=100*(b-v)/b
        all_gains.append(g);effect=boot(g)
        p=float(stats.ttest_1samp(g,0).pvalue) if np.std(g)>0 else (1. if g[0]==0 else 0.)
        entry=dict(name=name,improvement_pct=effect[0],ci_low=effect[1],ci_high=effect[2],p=p,
          original_ms=float(b.mean()),both_ms=float(v.mean()),
          original_p95_ms=float(np.mean([r['p95_ms'] for r in groups['original']])),both_p95_ms=float(np.mean([r['p95_ms'] for r in groups['both']])),
          original_slo_pct=float(np.mean([r['slo_1s_pct'] for r in groups['original']])),both_slo_pct=float(np.mean([r['slo_1s_pct'] for r in groups['both']])),
          mandatory_cloud_load_lower_bound=groups['both'][0]['longrun_cloud_load_lower_bound'],
          original_completed_fraction=float(np.mean([r['completed_by_90']/r['n'] for r in groups['original']])),
          both_completed_fraction=float(np.mean([r['completed_by_90']/r['n'] for r in groups['both']])),
          max_drain_s=max(r['drain_s'] for a in ARMS for r in groups[a]))
        for a in ['links','queue']:
            entry[a+'_improvement_pct']=float(np.mean(100*(b-np.array([r['mean_ms'] for r in groups[a]]))/b))
        out.append(entry)
    last=0
    for rank,i in enumerate(np.argsort([r['p'] for r in out])):
        last=max(last,min(1.,(len(out)-rank)*out[i]['p']));out[i]['holm_p']=last
    for r in out:r['passes']=bool(r['improvement_pct']>=5 and r['ci_low']>0 and r['holm_p']<.05)
    all_gains=np.array(all_gains)
    summary=dict(runs=len(rows),scenarios=len(out),aggregate_pct=boot(all_gains.mean(axis=0)),
       excluding_reference_pct=boot(all_gains[1:].mean(axis=0)),passes=sum(r['passes'] for r in out),negative=sum(r['improvement_pct'] < -1e-9 for r in out),results=out)
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2))
    with (OUT/'summary.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(out[0]));w.writeheader();w.writerows(out)
    print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
