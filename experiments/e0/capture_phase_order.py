"""Archive original collector-host file order before Git changes file mtimes.

Supplementary evidence, not a signed execution log. Frozen calibration hashes,
evaluation design hashes and client timestamps are independently audited.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def main():
    base=Path('artifacts/edge-network-placement')
    assert (base/'verification.json').exists()
    target=base/'phase-order-evidence.json'
    assert not target.exists(), 'Never replace original collection evidence'
    run=json.loads((base/'run.json').read_text())
    names=[]
    for d in run['deployments']:
        b=d['block']
        names+=[f'frozen-b{b}.json', f'evaluation-design-b{b}.json']
        for regime in run['regimes']:
            names += [f'calibration-b{b}-{regime}.log', f'evaluation-b{b}-{regime}.input.json']
    evidence={'run_id':run['run_id'],'captured_at':datetime.now(timezone.utc).isoformat(),
              'provenance':'Original collector filesystem, captured after collection before Git staging; supplementary evidence, not signed event timestamps',
              'files':{name:{'sha256':hashlib.sha256((base/name).read_bytes()).hexdigest(),
                             'original_host_mtime_ns':(base/name).stat().st_mtime_ns} for name in names}}
    target.write_text(json.dumps(evidence,indent=2)+'\n')


if __name__=='__main__':main()
