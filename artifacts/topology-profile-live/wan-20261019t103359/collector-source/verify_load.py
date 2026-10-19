"""Audit frozen traces against input/reference/Pod manifests, without new calls."""
import argparse
import hashlib
import json
from pathlib import Path

from run_smoke import ASSETS, NODES


def sha(data):
    return hashlib.sha256(data).hexdigest()


def verify(run):
    errors = []
    def check(ok, message):
        if not ok:
            errors.append(message)
    pairs = {p['question_id']: p for p in json.loads((ASSETS/'load-pairs.json').read_text())['pairs']}
    references = {r['question_id']: r for r in map(json.loads, (ASSETS/'reference-v1/results.jsonl').read_text().splitlines())}
    models = json.loads((ASSETS/'reference-v1/metadata.json').read_text())['stages']
    actual = json.loads((run/'actual-pods.json').read_text())
    qualification = json.loads((run/'qualification.json').read_text())
    check(len(qualification)==240 and all(r['quality_ok'] for r in qualification), 'Full raw quality gate')
    branch_hash = {(r['question_id'],r['candidate']):r['output_sha256'] for r in qualification}
    phases = []
    hashes = {}
    receipts = 0
    for path in sorted(run.glob('*.rows.json')):
        hashes[path.name] = sha(path.read_bytes())
        name = path.name.removesuffix('.rows.json')
        config = json.loads((run/(name+'.config.json')).read_text())
        rows = json.loads(path.read_text())
        check(sorted(r['index'] for r in rows)==list(range(len(config['question_ids']))),name+': terminal ledger')
        check(all(r['question_id']==config['question_ids'][r['index']] for r in rows),name+': question allocation')
        check(all(r['scheduled_s']==config['offsets_s'][r['index']] for r in rows),name+': release allocation')
        errors_before = len(errors)
        for row in rows:
            if row['status']!='ok':
                continue  # Kept in denominator; successful-path checks require receipts.
            prefix = 'raw_' if config['representation']=='raw' else ''
            pair, ref = pairs[row['question_id']], references[row['question_id']]
            rs = {r['task']:r for r in row['receipts']}
            receipts += len(rs)
            check(set(rs)=={'vision','speech','fusion'},name+': three receipts')
            for task,key in [('vision',config['placement'][0]),('speech',config['placement'][1]),('fusion','fusion')]:
                receipt, pod = rs[task], actual[key]
                check(receipt['pod_uid']==pod['metadata']['uid'] and receipt['node']==pod['spec']['nodeName'],name+': '+task+' location')
                check(receipt['model_sha256']==models[task]['model_sha256'] and receipt['device']=='cpu',name+': '+task+' real model')
                check(receipt.get('serving_torch_threads')==receipt.get('serving_torch_interop_threads')==1,name+': '+task+' worker thread settings')
            for task,kind,key in [('vision','image',config['placement'][0]),('speech','audio',config['placement'][1])]:
                check(rs[task]['input_sha256']['source']==pair[prefix+kind+'_sha256'],name+': '+task+' source hash')
                check(rs[task]['input_bytes']['source']==pair[prefix+kind+'_bytes'],name+': '+task+' source bytes')
                check(rs['fusion']['input_sha256'][task]==rs[task]['output_sha256'],name+': '+task+' join hash')
                check(rs['fusion']['input_bytes'][task]==rs[task]['output_bytes'],name+': '+task+' join bytes')
                # Exact same candidate output hash is stricter than reference allclose;
                # any disagreement is retained as an audit issue, never excluded.
                check(rs[task]['output_sha256']==branch_hash[(row['question_id'],key)],name+': '+task+' qualification output hash')
            check(row['source_node']==NODES['e0'] and row['speech_source_node']==NODES['e1'],name+': fixed edge sources')
            output=json.dumps(row['output'],separators=(',',':')).encode()
            check(sha(output)==rs['fusion']['output_sha256'] and len(output)==rs['fusion']['output_bytes'],name+': stored final payload')
            check(row['output']['answer_ids'][0]==ref['answer_ids'][0] and row['max_score_abs_error']<=1e-4,name+': reference quality')
            check(not row['deadline_exceeded'],name+': successful result after deadline')
        drain=json.loads((run/(name+'.drain.json')).read_text())
        check(len(drain)>=2 and all(d['idle'] and d['backlog_bytes']==0 for d in drain[-2:]),name+': stable full-path drain')
        phases.append(dict(phase=name,attempts=len(rows),successes=sum(r['status']=='ok' for r in rows),audit_issues=len(errors)-errors_before))
    for key in ('source-e0','source-e1','v-edge','s-edge','v-cloud','s-cloud','fusion'):
        check(json.loads((run/('bypass-'+key+'.json')).read_text())['blocked'],key+': bypass block')
        check(json.loads((run/('restored-'+key+'.json')).read_text())['status']=='ready',key+': direct restore')
    check(json.loads((run/'network-preflight.json').read_text())['status']=='passed','Network preflight')
    check(json.loads((run/'cleanup.json').read_text())['remaining_owned']==0,'Cleanup')
    check((run/'completed.json').exists(),'All planned phases completed')
    check(len(phases)==33 and sum(p['attempts'] for p in phases)==606,'33 phases /606 planned attempts')
    result=dict(status='passed' if not errors else 'needs_review',errors=errors,phases=phases,source_sha256=hashes,receipts=receipts,
                protected=json.loads((run/'protected-after.json').read_text()),script_sha256=sha(Path(__file__).read_bytes()))
    with (run/'verification-v1.json').open('x') as handle:
        json.dump(result,handle,indent=2)
    print(json.dumps(dict(status=result['status'],errors=errors,receipts=receipts)))
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('run',type=Path);args=ap.parse_args();verify(args.run)
