"""Warm static three-task baselines; no published scheduler reproduction."""

import itertools
from collections import defaultdict
import statistics

from measured_policy_bridge import task_fastest
from probe import MODEL_SHA256


def plan_cost(profile,plan):
    costs=profile['cost_ms']
    values=[costs['ingress'][plan[0]],*[costs['task'][f'{i}|{a}'] for i,a in enumerate(plan)],
            *[costs['edges'][i][f'{plan[i]}|{plan[i+1]}'] for i in range(2)]]
    if any(value is None for value in values):
        raise ValueError('Warm placement requires measured costs')
    return sum(values)


def data_greedy(snapshot):
    profile=snapshot['profile'];costs=profile['cost_ms'];candidates=profile['candidates']
    plan=[min(candidates[0],key=lambda a:(costs['ingress'][a]+costs['task'][f'0|{a}'],a))]
    for i in [1,2]:
        plan.append(min(candidates[i],key=lambda a:(costs['edges'][i-1][f'{plan[-1]}|{a}']+
                                                   costs['task'][f'{i}|{a}'],a)))
    return plan


def dag_enumeration(snapshot):
    profile=snapshot['profile']
    return list(min(itertools.product(*profile['candidates']),key=lambda p:(plan_cost(profile,p),p)))


POLICIES={'task_fastest':task_fastest,'data_greedy':data_greedy,'dag_enumeration':dag_enumeration}


def freeze_profiles(rows,corpus,aliases,evidence_id):
    """Use calibration rows only; preserve every mismatch in the eligibility counts."""
    aliases=sorted(aliases)
    quality={key:{a:{'observations':0,'matches':0,'eligible':False} for a in aliases} for key in corpus}
    for row in rows:
        if row['status']!='ok':
            raise ValueError('Cannot freeze an incomplete HTTP calibration')
        entry=quality[row['input_id']][row['plan'][1]]
        entry['observations']+=1
        entry['matches']+=int(row['result']['top5']==corpus[row['input_id']]['reference_top5'])
    for key,devices in quality.items():
        for entry in devices.values():
            entry['eligible']=entry['observations']>0 and entry['matches']==entry['observations']
        if not any(e['eligible'] for e in devices.values()):
            raise ValueError(f'No inference candidate satisfies the quality contract: {key}')
    measured=[row for row in rows if not row['warmup']]
    sources=sorted({r['source'] for r in measured})
    profiles={}
    for source,key in itertools.product(sources,corpus):
        group=[r for r in measured if r['source']==source and r['input_id']==key]
        if {tuple(r['plan']) for r in group}!=set(itertools.product(aliases,repeat=3)):
            raise ValueError('Every input/source requires all three-task calibration placements')
        tasks=defaultdict(list);ingress=defaultdict(list);edges=[defaultdict(list),defaultdict(list)]
        for row in group:
            trace=row['result']['trace'];plan=row['plan']
            if trace[1]['device'].get('was_cold',False):
                raise ValueError('Warm calibration unexpectedly prepared a cold model')
            for i,entry in enumerate(trace):tasks[f'{i}|{plan[i]}'].append(entry['task_ms'])
            ingress[plan[0]].append(row['timings_ms']['workflow']-trace[0]['task_ms']-trace[0]['rpc_next_ms'])
            edges[0][f'{plan[0]}|{plan[1]}'].append(trace[0]['rpc_next_ms']-trace[1]['task_ms']-trace[1]['rpc_next_ms'])
            edges[1][f'{plan[1]}|{plan[2]}'].append(trace[1]['rpc_next_ms']-trace[2]['task_ms'])
        median=lambda values:{k:statistics.median(v) for k,v in values.items()}
        costs={'task':median(tasks),'ingress':median(ingress),'edges':[median(e) for e in edges],
               'service_prepare':{a:None for a in aliases}}
        if any(v<0 for values in [costs['task'],costs['ingress'],*costs['edges']] for v in values.values()):
            raise ValueError('Negative observed RPC residual; preserve and investigate calibration')
        candidates=[list(aliases),[a for a in aliases if quality[key][a]['eligible']],list(aliases)]
        profiles[source+'|'+key]={'id':evidence_id+'|'+source+'|'+key,
            'provenance':'Fresh warm-only calibration '+evidence_id+'; CPU-reference exact Top5 order gate',
            'input_id':key,'source':source,'model_sha256':MODEL_SHA256,'candidates':candidates,'cost_ms':costs}
    return profiles,quality
