"""Targeted warm placement diagnostics; state costs stay outside the runtime schema."""

import itertools
import statistics

from fresh_placement import plan_cost, data_greedy, dag_enumeration


def dag_with_state(snapshot,state_ms):
    profile=snapshot['profile']
    return list(min(itertools.product(*profile['candidates']),
                    key=lambda p:(plan_cost(profile,p)+state_ms[p[1]],p)))


def state_costs(rows,key):
    group=[r for r in rows if r['input_id']==key and not r['warmup']]
    return {a:statistics.median(r['timings_ms']['state_validation'] for r in group if r['plan'][1]==a)
            for a in sorted({r['plan'][1] for r in group})}


def schedule(corpus,aliases,measured,seed,assigned):
    """Balanced adjacent anchor pairs; other placements randomized within each sweep."""
    assert measured%2==0
    plans=list(itertools.product(aliases,repeat=3));anchors=[(a,)*3 for a in aliases]
    remaining=[p for p in plans if p not in anchors]
    result=[]
    orders={}
    for k,key in enumerate(corpus):
        for phase,n in [('warmup',2),('measured',measured)]:
            orders[key,phase]=assigned([{'first':i%2} for i in range(n)],seed+1000+k*10+(phase=='measured'))
    for sweep in range(2+measured):
        warmup=sweep<2;phase='warmup' if warmup else 'measured';index=sweep if warmup else sweep-2
        keys=assigned([{'image_id':key} for key in corpus],seed+10*sweep)
        for k,item in enumerate(keys):
            key=item['image_id'];first=orders[key,phase][index]['first']
            ordered=[anchors[first],anchors[1-first]]
            ordered += [tuple(r['plan']) for r in assigned([{'plan':list(p)} for p in remaining],seed+10*sweep+k+1)]
            for position,plan in enumerate(ordered):
                result.append({'id':f'{key}-s{sweep}-p{position}','image_id':key,'plan':list(plan),
                               'source':'arm','sweep':sweep,'warmup':warmup,'position':position})
    return result
