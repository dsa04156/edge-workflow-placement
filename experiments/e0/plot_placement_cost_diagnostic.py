"""Raw paired holdout deltas; no inferential intervals or selected exclusions."""

import json
from pathlib import Path
import matplotlib.pyplot as plt


def main():
    base=Path('artifacts/placement-cost-diagnostic')
    result=json.loads((base/'results.json').read_text())
    fig,axes=plt.subplots(2,3,figsize=(14,7),sharex=True,layout='constrained')
    colors=['#2463a5','#bb5524','#287b58']
    for row,key in enumerate(['hubble_deep_field','chelsea']):
        for col,metric in enumerate(['workflow','state_validation','total']):
            ax=axes[row,col]
            for b,color in enumerate(colors):
                rr=[p for p in result['paired_anchors'] if p['mode']=='evaluation' and p['input_id']==key and p['block']==b and p['valid']]
                for first,marker in [('arm','o'),('intel_npu','x')]:
                    selected=[p for p in rr if p['first']==first]
                    ax.scatter([b*10+p['sweep']-1 for p in selected],
                               [p['npu_minus_arm_ms'][metric] for p in selected],
                               color=color,marker=marker,s=32,label=f'b{b}, {first} first')
            ax.axhline(0,color='#333333',linewidth=.8)
            for x in [10.5,20.5]:ax.axvline(x,color='#aaaaaa',linestyle=':',linewidth=.7)
            ax.set_title(f'{key.replace("_deep_field", "")} / {metric.replace("_", " ")}')
            ax.grid(axis='y',alpha=.18)
            ax.set_ylabel('NNN minus AAA (ms)')
            if row==1:ax.set_xlabel('Paired holdout sweep (10 per deployment)')
    handles,labels=axes[0,0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='outside lower center',ncols=6,fontsize=8)
    fig.suptitle('Adjacent anchor pairs: positive = all-ARM faster\nSame hosts/date; target inputs; raw measurements, no confidence intervals',fontsize=13)
    for suffix in ['png','svg']:
        path=base/f'paired-anchors.{suffix}';assert not path.exists()
        fig.savefig(path,dpi=170)
    plt.close(fig)


if __name__=='__main__':main()
