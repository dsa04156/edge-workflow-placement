"""Collect warm calibration or apply previously frozen, deduplicated plans."""

from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from policy_runner import PolicyRunner
from probe import MODEL_SHA256,fetch,emit


def main():
    spec=json.load(sys.stdin);peers=json.loads(os.environ['PEERS_JSON'])
    corpus=json.loads(Path(os.environ['CORPUS_FILE']).read_text())
    source=spec['source'];mode=spec['mode'];aliases=sorted(peers)
    images={}
    for key,sample in corpus.items():
        path=Path('/work')/sample['filename']
        if not path.exists():path.write_bytes(fetch(os.environ['ASSET_BASE']+'/corpus/'+sample['filename'],sample['sha256']))
        images[key]=path.read_bytes()
        assert hashlib.sha256(images[key]).hexdigest()==sample['sha256']
    count=0
    with PolicyRunner(peers) as runner:
        for condition in spec['conditions']:
            key=condition['image_id'];plan=condition['plan']
            if mode=='calibration':
                profile={'id':f"{spec['run_id']}-cal-{source}-{key}",
                    'provenance':'Calibration collection only; costs not yet measured',
                    'input_id':key,'source':source,'model_sha256':MODEL_SHA256,
                    'candidates':[list(aliases) for _ in range(3)],
                    'cost_ms':{'task':{f'{i}|{a}':None for i in range(3) for a in aliases},
                               'ingress':{a:None for a in aliases},
                               'edges':[{f'{a}|{b}':None for a in aliases for b in aliases} for _ in range(2)],
                               'service_prepare':{a:None for a in aliases}}}
                warmups=1;measured=3
            else:
                assert mode=='evaluation'
                profile=spec['profiles'][source+'|'+key]
                warmups=2;measured=5

            def apply_frozen(snapshot):
                if not snapshot['workers'][plan[1]]['service_object_ready']:
                    raise ValueError('Warm-only placement encountered an unready inference service')
                return plan

            for iteration in range(warmups+measured):
                started=time.perf_counter()
                row=runner.run(request_id=f"{spec['run_id']}-b{spec['block']}-{mode}-{source}-{condition['id']}-{iteration}",
                    input_id=key,body=images[key],source=source,profile=profile,
                    policy_name='calibration-fixed-plan' if mode=='calibration' else 'frozen-plan-group',policy=apply_frozen)
                valid=row['status']=='ok' and row['result']['top5']==corpus[key]['reference_top5']
                emit('workflow',mode=mode,source=source,block=spec['block'],condition=condition,
                     iteration=iteration,warmup=iteration<warmups,valid_complete=valid,
                     completed_at=datetime.now(timezone.utc).isoformat(),
                     pacing_overrun=(time.perf_counter()-started)>.25,**row)
                count+=1
                if mode=='calibration' and row['status']!='ok':
                    raise RuntimeError('Calibration failure recorded; cannot freeze incomplete costs')
                time.sleep(max(0,.25-(time.perf_counter()-started)))
    emit('client_complete',mode=mode,source=source,block=spec['block'],requests=count)


if __name__=='__main__':main()
