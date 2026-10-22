"""Audit the completed prefix of the 20261020 two-regime completion attempt.

No completion or restore evidence is synthesized. Derived summaries live only
in a temporary view; all unmet full-run checks are retained in the output.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import tempfile

from verify_placement import verify


def audit(run, output):
    run=run.resolve()
    read=lambda name:json.loads((run/name).read_text())
    jobs=read('execution-order.json')
    mapping={};observations={};completed=[];missing=[]
    for index,(condition,rate,delay,representation,nodes,policies) in enumerate(jobs):
        label=f'eval-{index}-{condition}'
        if not (run/(label+'.rows.json')).exists():
            missing.append(label)
            continue
        if missing:
            raise ValueError('Observed groups are not the frozen prefix')
        config=read(label+'.config.json')
        assert config['run_id']==run.name, 'Wrong evaluation run'
        assert (config['mbps'],config['oneway_delay_ms'],config['representation'])==(rate,delay,representation)
        assert [config['placement'][t] for t in ('vision','speech','fusion')]==nodes
        rows=read(label+'.rows.json')
        assert all(r.get('run_id')==run.name for r in rows if r['status']=='ok')
        valid=[r['e2e_ms'] for r in rows if r['status']=='ok']
        observations[label]=dict(attempts=len(rows),success=len(valid),
            mean_ms=statistics.mean(valid) if valid else None,
            failures=[r for r in rows if r['status']!='ok'])
        for policy in policies:
            mapping.setdefault(condition,{})[policy]=label
        completed.append(label)
    assert len(completed)==4 and len(missing)==4, 'This auditor expects the first full block only'
    with tempfile.TemporaryDirectory(prefix='completion-prefix-audit-') as temp:
        view=Path(temp)/run.name
        view.mkdir()
        for source in run.iterdir():
            if source.name not in ('policy-observation-map.json','pilot-summary.json','placement-verification-v1.json'):
                (view/source.name).symlink_to(source)
        (view/'policy-observation-map.json').write_text(json.dumps(mapping))
        (view/'pilot-summary.json').write_text(json.dumps(dict(mapping=mapping,observations=observations)))
        result=verify(view)
    expected={
        'all active conditions represented', 'pilot completion marker',
        'restore: full calibration/deployment coverage',
        'restore: bounded retries restore-sink-e1',
        'restore: terminal successful proof restore-sink-e1'}
    unexpected=sorted(set(result['errors'])-expected)
    if set(result['errors'])!=expected:
        unexpected.append('Unexpected set of incomplete-run checks')
    result.update(status='incomplete',observed_evidence_status='passed' if not unexpected else 'needs_review',
        unexpected_errors=unexpected,completed_groups=completed,missing_groups=missing,
        mapping=mapping,observations=observations,
        scope='first block at10/2Mbps only; second block absent; final restore probe failed',
        derivation='Temporary linked view; summaries derived from frozen execution order and original rows; all full-run failures retained',
        auditor_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    with output.open('x') as handle:
        json.dump(result,handle,indent=2)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('run',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    result=audit(args.run,args.out)
    print(json.dumps({k:result[k] for k in ('status','observed_evidence_status','unexpected_errors','evaluation_attempts','evaluation_successes','receipts')}))
    raise SystemExit(bool(result['unexpected_errors']))
