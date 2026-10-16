"""Observed per-deployment means and their equal-weight means; no inference bars."""

import json,statistics
from pathlib import Path
import matplotlib.pyplot as plt


def main():
    base=Path('artifacts/edge-network-placement');r=json.loads((base/'results.json').read_text())
    fig,axes=plt.subplots(1,3,figsize=(13,4.6),layout='constrained',sharey=True)
    regimes=['native','cap50','cap20']
    names={'task_fastest':'Task fastest','data_greedy':'Data greedy','current_dag':'Current-profile DAG','fixed_native_dag':'Native-fixed DAG'}
    colors=['#a54035','#8b7029','#1d7664','#485db0'];offsets=[-.12,-.04,.04,.12]
    for ax,key in zip(axes,['rocket','chelsea','hubble_deep_field']):
        for index,(policy,label) in enumerate(names.items()):
            means=[]
            for x,regime in enumerate(regimes):
                cases=[c for c in r['policy_cases'] if c['input_id']==key and c['regime']==regime and c['policy']==policy]
                values=[c['metrics_ms_or_bytes']['total']['mean'] for c in cases]
                assert len(values)==3
                means.append(statistics.mean(values))
                for c,value in zip(cases,values):
                    ax.scatter(x+offsets[index],value,color=colors[index],marker=['o','s','^'][c['block']],s=30,alpha=.55)
            ax.plot([x+offsets[index] for x in range(3)],means,color=colors[index],linewidth=1.8,label=label)
        ax.set_title(key.replace('_deep_field',''))
        ax.set_xticks(range(3),['Native','50 Mbps cap','20 Mbps cap'])
        ax.grid(axis='y',alpha=.2);ax.set_xlabel('Controlled cross-node egress condition')
    axes[0].set_ylabel('Mean request total (ms)')
    handles,labels=axes[0].get_legend_handles_labels();fig.legend(handles,labels,loc='outside lower center',ncols=4)
    fig.suptitle('Fixed edge input: placement changes as transmission becomes expensive\nPoints: three Pod deployments on same hosts; lines: means; shared plans share observations',fontsize=12)
    for extension in ['png','svg']:
        path=base/f'policy-network-cost.{extension}';assert not path.exists();fig.savefig(path,dpi=180)
    plt.close(fig)


if __name__=='__main__':main()
