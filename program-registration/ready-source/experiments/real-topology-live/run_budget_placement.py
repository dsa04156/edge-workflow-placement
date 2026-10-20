"""Two-block measured pilot with an explicit workflow-only server slot budget."""
import random
import argparse
import hashlib
import json
from datetime import datetime,timezone
from pathlib import Path
import statistics
import time
from run_placement import Pilot, HERE, NODES, TASKS
from run_smoke import ASSETS, NS
from budget_placement import with_server_budget, validate_placement, simple_alternatives, select_budget_policies

POLICIES=('greedy','heft','ours','greedy-swap')

class BudgetPilot(Pilot):
    def __init__(self,calibration_run=None):
        self.calibration_run=calibration_run
        super().__init__(HERE.parents[1]/'.scratch/greedy-joint-budget/spec.md')
        calibration=[p for p in self.pairs if p['split']=='calibration']
        evaluation=[p for p in self.pairs if p['split']=='evaluation']
        self.pairs=calibration+evaluation[6:]+evaluation[:6]
        self.active_profile=None
        if calibration_run is not None:
            frozen=json.loads((calibration_run/'git-source.json').read_text())['files']
            names=['http_runtime.py','edge_client.py','model_adapter.py','bootstrap.py','load_trace.py','net_control.py','wan_gateway.py']
            if any(hashlib.sha256((HERE/n).read_bytes()).hexdigest()!=frozen[n] for n in names):raise ValueError('Runtime changed: fresh calibration required')
            if not (calibration_run/'plans-frozen.json').is_file():raise ValueError('Complete frozen calibration required')
            if any(list(calibration_run.glob(pattern)) for pattern in ['eval-*.rows.json','eval-*.events.jsonl','eval-*.config.json','*-start-eval-*.event.json']):raise ValueError('Resume only an attempt with no evaluation observations')
            helper_manifest=json.loads((calibration_run/'helpers.manifest.json').read_text())
            bundle_text=next(x['data']['bundles.json'] for x in helper_manifest['items'] if x['kind']=='ConfigMap')
            if bundle_text!=(ASSETS/'load-bundles.json').read_text():raise ValueError('Asset bundles changed')
            files=[p for p in calibration_run.iterdir() if p.is_file() and ('-qualify-' in p.name or p.name.startswith('qualify-') or p.name in ('qualification-all.json','compute-p75.json'))]
            for p in files:(self.out/p.name).write_bytes(p.read_bytes())
            source_names=['git-source.json','helpers.manifest.json','capacity-frozen.json','design.json','plans-frozen.json']+[prefix+condition+'.json' for prefix in ['profile-','transfer-'] for condition in ['calibration-10','calibration-2']]
            times=[json.loads(p.read_text())['finished_at'] for p in calibration_run.glob('*.event.json') if ('-qualify-' in p.name or '-calibration-' in p.name) and 'finished_at' in json.loads(p.read_text())]
            self.save('calibration-origin.json',dict(run=str(calibration_run),reused_at=datetime.now(timezone.utc).isoformat(),calibration_finished_at=max(times),
                source_files={n:hashlib.sha256((calibration_run/n).read_bytes()).hexdigest() for n in source_names},runtime_sha256={n:frozen[n] for n in names},
                copied={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
                scope='reused complete calibration from interrupted attempt; no prior evaluation observations'))

    def qualify_all(self):
        if self.calibration_run is None:return super().qualify_all()
        rows=json.loads((self.out/'qualification-all.json').read_text())
        if len(rows)!=585 or not all(r['quality_ok'] for r in rows):raise ValueError('Incomplete qualification')
        self.compute=json.loads((self.out/'compute-p75.json').read_text())
        print('reuse-frozen-qualification',self.calibration_run,flush=True)

    def capacity(self):
        self.current_capacity=super().capacity()
        if self.calibration_run is None:return self.current_capacity
        self.save('capacity-current.json',self.current_capacity)
        return json.loads((self.calibration_run/'capacity-frozen.json').read_text())

    def transfer_profiles(self,condition,mbps,delay,representation,capacity):
        if self.calibration_run is None:return super().transfer_profiles(condition,mbps,delay,representation,capacity)
        profile=json.loads((self.calibration_run/('profile-'+condition+'.json')).read_text())
        self.save('profile-'+condition+'.json',profile)
        self.save('transfer-'+condition+'.json',json.loads((self.calibration_run/('transfer-'+condition+'.json')).read_text()))
        return profile

    def execute(self,key,code,stem='operation',timeout=60,container='worker',stdin=None):
        readonly=container=='worker' and stdin is None and stem.startswith(('bypass-','restore-','relay-'))
        for attempt in range(3):
            tag=stem if attempt==0 else stem+'-retry-'+str(attempt)
            try:
                if readonly:
                    probe=code.replace("sys.path.insert(0,'/config')", "sys.path.insert(0,'/work/net-config')")
                    probe="import os;assert os.environ['POD_UID']=="+repr(self.actual[key]['metadata']['uid'])+";"+probe
                    return self.call(tag,['exec','-n',NS,self.run+'-'+key,'-c','netctl','--','python3','-u','-c',probe],timeout=timeout)
                return super().execute(key,code,tag,timeout,container,stdin)
            except RuntimeError:
                if not readonly:raise
                stderr=(self.out/f'{self.counter:03d}-{tag}.stderr').read_text()
                transport_eof='error sending request:' in stderr and 'unexpected EOF' in stderr
                if not transport_eof or attempt==2:raise
                time.sleep(1)

    def deploy_operators(self,placement,label):
        if self.active_profile is not None:
            validate_placement(self.active_profile,placement)
            current=dict(self.active_profile,nodes=self.current_capacity)
            validate_placement(with_server_budget(current,NODES['c'],2),placement)
        return super().deploy_operators(placement,label)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--calibration-run',type=Path);args=parser.parse_args()
    session=BudgetPilot(args.calibration_run)
    session.save('design.json',dict(comparison='server-admission-budget-v1',server_slots=2,blocks=2,
        seed=20261022,policies=POLICIES,warmup=2,evaluation_count=6,rate_rps=.1,
        evaluation_ids=[p['question_id'] for p in session.pairs if p['split']=='evaluation'][:6],
        calibration='fresh compute and transfer' if args.calibration_run is None else 'reused frozen calibration from '+str(args.calibration_run),
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
        if session.calibration_run is not None:
            original=json.loads((session.calibration_run/'plans-frozen.json').read_text())
            for condition,policies in plans.items():
                for policy,plan in policies.items():
                    for key in ['status','placement','predicted_e2e_proxy_ms','profile_sha256']:
                        if plan[key]!=original[condition][policy][key]:raise ValueError('Frozen selection changed')
            old_design=json.loads((session.calibration_run/'design.json').read_text())
            new_design=json.loads((session.out/'design.json').read_text())
            for key in ['seed','policies','evaluation_ids','server_slots','blocks','warmup','evaluation_count','rate_rps']:
                if old_design[key]!=new_design[key]:raise ValueError('Frozen design changed')
            if active!=[tuple(c) for c in json.loads((session.calibration_run/'active-conditions.json').read_text())['active']]:raise ValueError('Frozen conditions changed')
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
