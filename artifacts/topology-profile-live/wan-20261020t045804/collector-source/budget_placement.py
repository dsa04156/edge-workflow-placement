"""Declared per-workflow admission budget; never claims physical saturation."""
import copy
import itertools
from placement_policies import prepare, sequential


def with_server_budget(profile,server,slots):
    if type(slots) is not int or slots<1:raise ValueError('Positive integer slot budget required')
    requests={t['cpu_m'] for t in profile['tasks']}
    if len(requests)!=1:raise ValueError('Slot experiment requires equal operator requests')
    result=copy.deepcopy(profile)
    result['admission_budget']=dict(server=server,slots=slots,scope='this workflow only; not physical server pressure',
        physical_cpu_free_m=profile['nodes'][server]['cpu_free_m'],operator_cpu_m=next(iter(requests)))
    result['nodes'][server]['cpu_free_m']=min(profile['nodes'][server]['cpu_free_m'],slots*next(iter(requests)))
    prepare.validate(result)
    return result


def validate_placement(profile,placement):
    if set(placement)!={t['id'] for t in profile['tasks']}:raise ValueError('Complete task placement required')
    if any(placement[t['id']] not in t['candidates'] for t in profile['tasks']):raise ValueError('Unqualified candidate')
    if not prepare.feasible(profile,placement):raise ValueError('Placement exceeds effective admission budget')


def simple_alternatives(profile,greedy):
    reordered=copy.deepcopy(profile)
    reordered['tasks']=[next(t for t in profile['tasks'] if t['id']==name) for name in ['speech','vision','fusion']]
    speech_first=sequential(reordered)
    candidates=[greedy]
    for a,b in itertools.combinations(greedy,2):
        alternative=dict(greedy);alternative[a],alternative[b]=alternative[b],alternative[a]
        try:validate_placement(profile,alternative)
        except ValueError:continue
        candidates.append(alternative)
    repaired=min(candidates,key=lambda p:(prepare.cost(profile,p,'topology_profile'),tuple(sorted(p.items()))))
    return {name:dict(placement=plan,predicted_e2e_proxy_ms=prepare.cost(profile,plan,'topology_profile'))
        for name,plan in [('speech-first-greedy',speech_first),('greedy-one-pair-swap',repaired)]}


def select_budget_policies(profile,server):
    import time
    from placement_policies import select_policies
    results={p:v for p,v in select_policies(profile,server).items() if p in ('greedy','heft','ours')}
    start=time.perf_counter()
    alternative=simple_alternatives(profile,results['greedy']['placement'])['greedy-one-pair-swap']
    results['greedy-swap']=dict(alternative,status='ok',profile_sha256=prepare.digest(profile),
        decision_ms=(time.perf_counter()-start)*1000,
        decision_scope='repair diagnostics after greedy; excludes original greedy selection')
    return results
