"""Display deployment-level paired means without inferential error bars."""

import json
from pathlib import Path
import statistics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    base=Path('artifacts/fresh-placement')
    result=json.loads((base/'results.json').read_text())
    inputs=list(json.loads((base/'corpus.json').read_text()))
    fig,axes=plt.subplots(2,1,figsize=(12,7),sharex=True,layout='constrained')
    baselines=[('task_fastest','Task-fastest','#2563eb',-.16),
               ('data_greedy','Data-aware greedy','#d97706',.16)]
    markers=['o','s','^']
    for ax,source,title in zip(axes,['arm','intel_npu'],['Input at ARM edge','Input at Intel NPU server']):
        ax.axhline(0,color='#64748b',linewidth=1)
        for name,label,color,offset in baselines:
            for i,key in enumerate(inputs):
                rows=[r for r in result['paired_comparisons'] if r['source']==source and
                      r['input_id']==key and r['baseline']==name and r['quality_controlled']]
                values=[r['baseline_minus_dag_ms'] for r in rows]
                for row in rows:
                    ax.scatter(i+offset+(row['block']-1)*.055,row['baseline_minus_dag_ms'],
                               marker=markers[row['block']],color=color,s=30,alpha=.7)
                if values:
                    ax.plot([i+offset-.11,i+offset+.11],[statistics.mean(values)]*2,
                            color=color,linewidth=2.5)
            ax.scatter([],[],label=label,color=color,s=30)
        ax.set_title(title,loc='left',fontsize=12)
        ax.set_ylabel('Baseline minus DAG (ms)')
        ax.grid(axis='y',alpha=.2)
        ax.spines[['top','right']].set_visible(False)
        ax.legend(loc='upper right',frameon=False)
    axes[-1].set_xticks(range(len(inputs)),[key.replace('hubble_deep_field','hubble') for key in inputs])
    axes[-1].tick_params(axis='x',rotation=15)
    fig.suptitle('Warm static placement: observed paired differences',fontsize=15)
    fig.supxlabel('Points: three deployments on the same hosts/date; bars: their mean. Positive = DAG faster.\n'
                  'Each point compares five measured requests per plan. Identical plans share observations (zero).',fontsize=10)
    for extension in ['png','svg']:
        fig.savefig(base/f'paired-differences.{extension}',dpi=180)
    plt.close(fig)


if __name__=='__main__':main()
