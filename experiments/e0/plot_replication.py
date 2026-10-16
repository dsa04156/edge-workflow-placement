"""Paired descriptive deployment means; no confidence intervals."""

import json
from pathlib import Path
import statistics
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

base=Path('artifacts/e3-input-replication');result=json.loads((base/'results.json').read_text())
images=list(json.loads((base/'corpus.json').read_text()))
fig,axes=plt.subplots(2,2,figsize=(13,8),constrained_layout=True)
for row,source in enumerate(['arm','intel_npu']):
    for col,state in enumerate(['warm','eviction']):
        ax=axes[row,col]
        for x,image in enumerate(images):
            r=next(r for r in result['paired_contrasts'] if r['image_id']==image and r['source']==source and r['state']==state)
            values=r['arm_minus_npu_ms_by_deployment']
            ax.scatter([x-.12,x,x+.12],values,color='#768493',s=22,label='Deployment means' if x==0 else None)
            ax.scatter([x],[statistics.mean(values)],marker='_',s=240,color='#04699b',label='Mean of 3 deployments' if x==0 else None)
        ax.axhline(0,color='#b84343',linewidth=1)
        ax.set_xticks(range(len(images)),[i.replace('hubble_deep_field','hubble')+('*' if i=='camera' else '') for i in images],rotation=35,ha='right')
        ax.set_title(('Edge input' if source=='arm' else 'Server input')+' / '+state)
        ax.set_ylabel('ARM minus NPU worker latency (ms)\npositive = NPU worker faster')
        ax.grid(axis='y',alpha=.2)
axes[0,0].legend(fontsize=8)
fig.suptitle('E3: same 8 inputs, 3 new worker deployments; 5 measured requests per condition\n'
             '* camera: NPU top-5 overlap 4/5; all measured top-1 matches CPU reference (not labeled accuracy)',fontsize=11)
for suffix in ['png','svg']:fig.savefig(base/f'paired-inputs.{suffix}',dpi=160)
print(json.dumps({'figures':['paired-inputs.png','paired-inputs.svg']}))
