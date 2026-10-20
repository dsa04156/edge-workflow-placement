"""Audit an interrupted budget run without inventing a completion marker.

The complete-run verifier operates on a temporary linked view. Only summary and
policy mapping are derived there from the frozen execution order and raw rows.
Its unmet completeness checks remain visible; original evidence is untouched.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import tempfile

from verify_placement import verify, read


def audit(run):
    run=run.resolve()
    jobs=read(run/'execution-order.json')
    mapping={};observations={};completed=[];missing=[]
    for index,(condition,rate,delay,representation,nodes,policies) in enumerate(jobs):
        label=f'eval-{index}-{condition}'
        if not (run/(label+'.rows.json')).exists():
            missing.append(label);continue
        if missing:raise ValueError('Completed observations must be a prefix of frozen execution order')
        config=read(run/(label+'.config.json'))
        if (config['mbps'],config['oneway_delay_ms'],config['representation'])!=(rate,delay,representation):raise ValueError('Actual condition differs from frozen job')
        if [config['placement'][t] for t in ('vision','speech','fusion')]!=nodes:raise ValueError('Actual placement differs from frozen job')
        rows=read(run/(label+'.rows.json'));valid=[r['e2e_ms'] for r in rows if r['status']=='ok']
        observations[label]=dict(attempts=len(rows),success=len(valid),mean_ms=statistics.mean(valid) if valid else None,failures=[r for r in rows if r['status']!='ok'])
        for policy in policies:mapping.setdefault(condition,{})[policy]=label
        completed.append(label)
    if not completed or not missing:raise ValueError('Expected a nonempty interrupted run')
    expected_unmet={'all active conditions represented','pilot completion marker','restore: full calibration/deployment coverage'}
    with tempfile.TemporaryDirectory(prefix='partial-budget-audit-') as temp:
        view=Path(temp)
        for source in run.iterdir():
            if source.name not in ('policy-observation-map.json','pilot-summary.json','placement-verification-v1.json'):
                (view/source.name).symlink_to(source)
        (view/'policy-observation-map.json').write_text(json.dumps(mapping))
        (view/'pilot-summary.json').write_text(json.dumps(dict(mapping=mapping,observations=observations)))
        result=verify(view)
    # Missing post-evaluation restore is recorded, never promoted to a pass.
    unexpected=[e for e in result['errors'] if e not in expected_unmet]
    if set(result['errors'])!=expected_unmet:
        unexpected.append('Unexpected set of incomplete-design checks')
    result.update(status='incomplete',observed_evidence_status='passed' if not unexpected else 'needs_review',
        unexpected_errors=unexpected,completed_groups=completed,missing_groups=missing,
        mapping=mapping,observations=observations,
        scope='partial first10Mbps block only; no second block or2Mbps control; final restore unverified; cleanup independently checked',
        derivation='Temporary linked view; summary/map derived from frozen jobs and original rows. No synthetic observations or completion marker.',
        partial_auditor_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    with (run/'partial-verification-v1.json').open('x') as f:json.dump(result,f,indent=2)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('run',type=Path);args=parser.parse_args()
    result=audit(args.run)
    print(json.dumps({k:result[k] for k in ('status','observed_evidence_status','errors','unexpected_errors','evaluation_attempts','evaluation_successes','receipts')}))
    raise SystemExit(bool(result['unexpected_errors']))
