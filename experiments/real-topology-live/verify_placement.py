"""Read-only audit of a completed automatic-placement pilot's frozen evidence."""
import argparse
import base64
import struct
import hashlib
import json
from pathlib import Path
import statistics

from run_smoke import ASSETS, NODES
from verify_load import native_backlogs


def read(path):
    return json.loads(path.read_text())


def verify(run):
    errors=[]
    def check(condition,message):
        if not condition: errors.append(message)
    pairs={p['question_id']:p for p in read(ASSETS/'load-pairs.json')['pairs']}
    refs={r['question_id']:r for r in map(json.loads,(ASSETS/'reference-v1/results.jsonl').read_text().splitlines())}
    models=read(ASSETS/'reference-v1/metadata.json')['stages']
    qualifications=read(run/'qualification-all.json')
    check(len(qualifications)==585,'585 qualification observations including45 warmups')
    for task in ('vision','speech','fusion'):
        for node in NODES.values():
            rows=[r for r in qualifications if r['task']==task and r['node']==node and not r['warmup']]
            check(len(rows)==60 and {r['question_id'] for r in rows}==set(pairs),f'qualification coverage {task}/{node}')
            check(all(r['quality_ok'] for r in rows),f'qualification quality {task}/{node}')
            check(sum(r['split']=='calibration' for r in rows)==20,f'calibration separation {task}/{node}')
    for task in ('vision','speech','fusion'):
        for alias,node in NODES.items():
            label='qualify-'+task+'-'+alias
            entries=[r for r in qualifications if r['task']==task and r['node']==node]
            pod=read(run/(label+'-deployment.json'))['pods'][task]
            for index,entry in enumerate(entries):
                paths=list(run.glob('*-'+label+'-'+str(index)+'-http-*.response.json'))
                check(len(paths)==1,label+': preserved successful response '+str(index))
                if len(paths)!=1:continue
                response=read(paths[0]);body=base64.b64decode(response['data']);receipt=response['receipt']
                check(receipt==entry['receipt'],label+': qualification receipt matches RPC')
                check(receipt['model_sha256']==models[task]['model_sha256'] and receipt['pod_uid']==pod['metadata']['uid'] and receipt['node']==node,label+': qualification identity')
                check(hashlib.sha256(body).hexdigest()==receipt['output_sha256'],label+': qualification output hash')
                qid=entry['question_id'];reference=refs[qid]
                if task=='vision':
                    expected=struct.unpack('<512f',(ASSETS/'reference-v1'/f'{qid}.npy').read_bytes()[-2048:])
                    observed=struct.unpack('<512f',body[-2048:])
                    check(all(abs(a-b)<=1e-5+1e-4*abs(b) for a,b in zip(observed,expected)),label+': reference feature tolerance')
                elif task=='speech':check(body.decode()==reference['transcript'],label+': reference transcript')
                else:
                    output=json.loads(body)
                    check(output['answer_ids']==reference['answer_ids'] and len(output['scores'])==5 and max(abs(a-b) for a,b in zip(output['scores'],reference['scores']))<=1e-4,label+': reference fusion output')
    plans=read(run/'plans-frozen.json');mapping=read(run/'policy-observation-map.json')
    baseline=read(run/'baseline-placement.json');active=read(run/'active-conditions.json')['active']
    for condition,_,_,_ in active:
        profile=read(run/('profile-'+condition+'.json'))
        digest=hashlib.sha256(json.dumps(profile,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        for policy,plan in plans[condition].items():
            if plan['status']=='ok':check(plan['profile_sha256']==digest,condition+': frozen profile hash '+policy)
        for task in profile['tasks']:
            for node,value in task['compute_ms'].items():
                times=sorted(r['receipt']['compute_ms'] for r in qualifications if r['task']==task['id'] and r['node']==node and not r['warmup'] and r['split']=='calibration')
                check(len(times)==20,condition+': exactly20 calibration samples')
                # For20 values the linear75th percentile interpolates indices14/15.
                check(abs(value-(times[14]*.75+times[15]*.25))<1e-8,condition+': p75 uses calibration only')
    check(set(mapping)=={c[0] for c in active},'all active conditions represented')
    expected_groups=set();label_conditions={}
    conditions={c[0]:c[1:] for c in active}
    for condition,policies in mapping.items():
        check(set(policies)=={'kube-default'}|{p for p,v in plans[condition].items() if v['status']=='ok'},condition+': policies accounted')
        placements={}
        for policy,label in policies.items():
            config=read(run/(label+'.config.json'))
            rate,delay,representation=conditions[condition]
            check((config['mbps'],config['oneway_delay_ms'],config['representation'])==(rate,delay,representation),condition+': actual condition '+policy)
            check(label not in label_conditions or label_conditions[label]==condition,condition+': cross-condition observation reuse')
            label_conditions[label]=condition
            placement=baseline if policy=='kube-default' else plans[condition][policy]['placement']
            check(config['placement']==placement,condition+': selected placement applied '+policy)
            key=tuple(sorted(placement.items()))
            check(key not in placements or placements[key]==label,condition+': same placement shares observation')
            placements[key]=label;expected_groups.add(label)
    phases=[];successes=0;receipts_count=0;max_error=0
    for path in sorted(run.glob('*.rows.json')):
        name=path.name.removesuffix('.rows.json')
        warm=name.endswith('-warm')
        group=name.removesuffix('-warm')
        check(group in expected_groups,name+': expected execution group')
        config=read(run/(name+'.config.json'));rows=read(path)
        actual=read(run/(group+'-deployment.json'))['pods']
        check(sorted(r['index'] for r in rows)==list(range(len(config['question_ids']))),name+': terminal ledger')
        check(len(rows)==(2 if warm else 6),name+': requested pilot size')
        for row in rows:
            check(row['question_id']==config['question_ids'][row['index']],name+': input trace')
            check(pairs[row['question_id']]['split']==('calibration' if warm else 'evaluation'),name+': heldout split')
            if row['status']!='ok':continue
            successes+=1
            rs={r['task']:r for r in row['receipts']};receipts_count+=len(row['receipts'])
            check(len(row['receipts'])==3 and set(rs)=={'vision','speech','fusion'},name+': three receipts')
            for task,r in rs.items():
                pod=actual[task]
                check(r['pod_uid']==pod['metadata']['uid'] and r['node']==pod['spec']['nodeName']==config['placement'][task],name+': runtime identity')
                check(r['device']=='cpu' and r['model_sha256']==models[task]['model_sha256'],name+': actual CPU model')
                check(r.get('serving_torch_threads')==r.get('serving_torch_interop_threads')==1,name+': serving thread contract')
            pair=pairs[row['question_id']];ref=refs[row['question_id']]
            prefix='raw_' if config['representation']=='raw' else ''
            for task,kind in [('vision','image'),('speech','audio')]:
                check(rs[task]['input_sha256']['source']==pair[prefix+kind+'_sha256'],name+': input hash')
                check(rs[task]['input_bytes']['source']==pair[prefix+kind+'_bytes'],name+': input bytes')
                check(rs['fusion']['input_sha256'][task]==rs[task]['output_sha256'],name+': join hash')
                check(rs['fusion']['input_bytes'][task]==rs[task]['output_bytes'],name+': join bytes')
            check(row['source_node']==NODES['e0'] and row['speech_source_node']==NODES['e1'],name+': fixed edge source')
            output=json.dumps(row['output'],separators=(',',':')).encode()
            check(hashlib.sha256(output).hexdigest()==rs['fusion']['output_sha256'],name+': final payload hash')
            check(row['output']['answer_ids']==ref['answer_ids'],name+': Top5 order')
            check(len(row['output']['scores'])==len(ref['scores'])==5,name+': score count')
            error=max(abs(a-b) for a,b in zip(row['output']['scores'],ref['scores']))
            max_error=max(max_error,error)
            check(error<=1e-4 and abs(error-row['max_score_abs_error'])<1e-12,name+': numeric quality')
            check(not row['deadline_exceeded'],name+': success within deadline')
        drain=read(run/(name+'.drain.json'))
        check(len(drain)>=2 and all(d['idle'] for d in drain[-2:]),name+': two idle captures')
        for entry in drain[-2:]:
            snapshot=read(run/(name+f"-drain-network-{entry['attempt']}.json"))
            check(all(v==0 for v in native_backlogs(snapshot)),name+': native backlog zero')
        valid=[r['e2e_ms'] for r in rows if r['status']=='ok']
        phases.append(dict(phase=name,warmup=warm,attempts=len(rows),successes=len(valid),
            mean_ms=statistics.mean(valid) if valid else None,failures=len(rows)-len(valid)))
    check({p['phase'] for p in phases if not p['warmup']}==expected_groups,'exact evaluated group set')
    check({p['phase'] for p in phases if p['warmup']}=={g+'-warm' for g in expected_groups},'exact warmup phase set')
    summary=read(run/'pilot-summary.json')
    check(summary['mapping']==mapping,'summary mapping matches frozen observation map')
    check(set(summary['observations'])==expected_groups,'summary observation coverage')
    for phase in phases:
        if phase['warmup']:continue
        stored=summary['observations'][phase['phase']]
        check(stored['attempts']==phase['attempts'] and stored['success']==phase['successes'],'summary counts '+phase['phase'])
        check(stored['mean_ms']==phase['mean_ms'],'summary mean '+phase['phase'])
        raw_failures=[r for r in read(run/(phase['phase']+'.rows.json')) if r['status']!='ok']
        check(stored['failures']==raw_failures,'summary failure ledger '+phase['phase'])
    check(read(run/'network-preflight.json')['status']=='passed','shared WAN preflight')
    cleanup=read(run/'cleanup.json')
    check(cleanup['remaining_owned']==0 and not cleanup['errors'],'clean owned resource removal')
    check((run/'completed.json').exists(),'pilot completion marker')
    # One helper-only calibration guard/restore plus one per fresh evaluation.
    expected_probes=6+9*len(expected_groups)
    for prefix,field,expected in [('bypass','blocked',True),('restore','status','ready')]:
        captures=list(run.glob('*-'+prefix+'-*.stdout'))
        check(len(captures)==expected_probes,prefix+': full calibration/deployment coverage')
        for capture in captures:
            check(read(capture).get(field)==expected,prefix+': probe result '+capture.name)
            event=read(capture.with_suffix('.event.json'))
            check(event.get('returncode')==0,prefix+': probe command completed')
    result=dict(status='passed' if not errors else 'needs_review',errors=errors,phases=phases,
        evaluation_attempts=sum(p['attempts'] for p in phases if not p['warmup']),
        evaluation_successes=sum(p['successes'] for p in phases if not p['warmup']),
        successes_including_warmup=successes,receipts=receipts_count,max_score_abs_error=max_error,
        protected=read(run/'protected-after.json'),scope='one-block compressed S1/S2 pilot',
        verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    with (run/'placement-verification-v1.json').open('x') as handle:json.dump(result,handle,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k not in ('phases','protected')}))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('run',type=Path);args=parser.parse_args()
    outcome=verify(args.run)
    raise SystemExit(bool(outcome['errors']))
