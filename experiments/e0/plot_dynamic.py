"""Standalone descriptive figure; no confidence intervals or independence claim."""

from collections import defaultdict
import json
from pathlib import Path
import statistics
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

base=Path('artifacts/e2-dynamic');run=json.loads((base/'run.json').read_text())
assert (base/'verification.json').exists()
offsets=[];offset=0
for source,count in run['design']['source_phases']:
    offsets.append(offset);offset+=count
rows=[]
for path in base.glob('evaluation-*.log'):
    rows += [r for line in path.read_text().splitlines() if line.startswith('{')
             if (r:=json.loads(line))['kind']=='workflow']
fig,axes=plt.subplots(2,1,figsize=(11,6.5),sharex=True,constrained_layout=True)
for scenario,ax in zip(['warm','eviction'],axes):
    for phase,(source,count) in enumerate(run['design']['source_phases']):
        if source=='intel_npu':ax.axvspan(offsets[phase]-.5,offsets[phase]+count-.5,color='#fee7c7',alpha=.65)
    for policy,label,color in [('fixed','Fixed initial placement','#69737d'),('ready_dag','Readiness-aware DAG (H=4)','#176b9b')]:
        values=defaultdict(list);cold=defaultdict(list)
        for r in rows:
            if r['scenario']==scenario and r['policy']==policy:
                index=offsets[r['phase']]+r['iteration']
                values[index].append(r['total_ms'])
                if r['trace'][1]['device']['was_cold']:cold[index].append(r['total_ms'])
        assert len(values)==46 and all(len(v)==3 for v in values.values())
        x=sorted(values);y=[statistics.mean(values[i]) for i in x]
        ax.plot(x,y,color=color,label=label,linewidth=1.8)
        if cold:ax.scatter(sorted(cold),[statistics.mean(cold[i]) for i in sorted(cold)],marker='^',color=color,s=40,label='Request includes NPU preparation')
    ax.set_title('All models warm' if scenario=='warm' else 'NPU service objects evicted at each server phase',loc='left')
    ax.set_ylabel('Mean total latency (ms)');ax.grid(axis='y',alpha=.2)
axes[0].legend(loc='upper left',fontsize=8)
axes[1].legend(loc='upper left',fontsize=8)
axes[1].set_xlabel('Request index in the frozen 46-request trajectory; shaded = server input source')
fig.suptitle('E2 pilot: 3 shared-worker repeats, one image fixture; descriptive means',fontsize=12)
for suffix in ['svg','png']:fig.savefig(base/f'latency-trajectories.{suffix}',dpi=160)
print(json.dumps({'figures':['latency-trajectories.svg','latency-trajectories.png']}))
