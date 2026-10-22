"""Two-block measured pilot with an explicit workflow-only server slot budget."""
import random
import statistics
from run_placement import Pilot, HERE, NODES, TASKS
from budget_placement import with_server_budget, validate_placement, simple_alternatives, select_budget_policies

POLICIES=('greedy','heft','ours','greedy-swap')

class BudgetPilot(Pilot):
    def __init__(self):
        super().__init__(HERE.parents[1]/'.scratch/greedy-joint-budget/spec.md')
        calibration=[p for p in self.pairs if p['split']=='calibration']
        evaluation=[p for p in self.pairs if p['split']=='evaluation']
        self.pairs=calibration+evaluation[6:]+evaluation[:6]
        self.active_profile=None

    def deploy_operators(self,placement,label):
        if self.active_profile is not None:validate_placement(self.active_profile,placement)
        return super().deploy_operators(placement,label)


def main():
    session=BudgetPilot()
    session.save('design.json',dict(comparison='server-admission-budget-v1',server_slots=2,blocks=2,
        seed=20261022,policies=POLICIES,warmup=2,evaluation_count=6,rate_rps=.1,
        evaluation_ids=[p['question_id'] for p in session.pairs if p['split']=='evaluation'][:6],
        calibration='fresh compute and transfer; shared frozen profiles for two temporal blocks',
        scope='workflow admission-budget mechanism pilot; no physical saturation or independent-environment claim'))
    try:
        session.deploy();session.network_preflight();session.qualify_all()
        capacity=session.capacity();session.save('capacity-frozen.json',capacity)
        session.guard(True)
        physical={}
        for rate in (10,2):
            label='calibration-'+str(rate)
            print('profile',label,flush=True)
            physical[rate]=session.transfer_profiles(label,rate,20,'compressed',capacity)
            session.drain(label)
        profiles={};plans={};alternatives={};sources={};active=[];jobs=[]
        rng=random.Random(20261022)
        for block in range(2):
            rates=[10,2];rng.shuffle(rates)
            for rate in rates:
                condition=f'b{block}-compressed-{rate}'
                profile=with_server_budget(physical[rate],NODES['c'],2)
                profile['condition']=condition
                profiles[condition]=profile
                session.save('profile-'+condition+'.json',profile)
                sources[condition]='calibration-'+str(rate)
                plans[condition]=select_budget_policies(profile,NODES['c'])
                if any(v['status']!='ok' for v in plans[condition].values()):raise RuntimeError('Primary policy infeasible')
                alternatives[condition]=simple_alternatives(profile,plans[condition]['greedy']['placement'])
                active.append((condition,rate,20,'compressed'))
                groups={}
                for policy,plan in plans[condition].items():
                    groups.setdefault(tuple(plan['placement'][t] for t in TASKS),[]).append(policy)
                group_items=list(groups.items());rng.shuffle(group_items)
                for key,policies in group_items:jobs.append((condition,rate,20,'compressed',key,policies))
        session.save('active-conditions.json',dict(active=active,deferred=[],profile_sources=sources))
        session.save('plans-frozen.json',plans)
        session.save('simple-alternatives-frozen.json',alternatives)
        session.save('execution-order.json',jobs)
        session.guard(False)
        mapping={condition:{} for condition in profiles};observations={}
        for index,(condition,rate,delay,representation,key,policies) in enumerate(jobs):
            label=f'eval-{index}-{condition}';placement=dict(zip(TASKS,key))
            session.active_profile=profiles[condition]
            print('deploy',label,policies,placement,flush=True)
            actual=session.deploy_operators(placement,label)
            session.guard(True);session.shape(rate,delay,label+'-shape')
            session.trace(label+'-warm',actual,rate,delay,.1,2,representation=representation,split='calibration')
            observations[label]=session.trace(label,actual,rate,delay,.1,6,representation=representation)
            for policy in policies:mapping[condition][policy]=label
            session.remove_operators(label)
        session.save('policy-observation-map.json',mapping)
        summaries={label:dict(attempts=len(rows),success=sum(r['status']=='ok' for r in rows),
            mean_ms=statistics.mean([r['e2e_ms'] for r in rows if r['status']=='ok']) if any(r['status']=='ok' for r in rows) else None,
            failures=[r for r in rows if r['status']!='ok']) for label,rows in observations.items()}
        session.save('pilot-summary.json',dict(mapping=mapping,observations=summaries,plans=plans))
        session.shape(None,0,'restored-final')
        session.save('completed.json',dict(status='admission-budget mechanism pilot completed',remaining='dynamic transition costs and independent replication'))
    finally:session.close()

if __name__=='__main__':main()
