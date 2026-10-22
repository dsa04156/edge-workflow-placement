"""Freeze per-input CPU reference; outputs and time costs are distinct evidence."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import time

import numpy as np
from model_adapter import load


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--pairs', required=True)
    p.add_argument('--out', required=True)
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(exist_ok=False)
    pairs_path = Path(a.pairs)
    pairs = json.loads(pairs_path.read_text())['pairs']
    operators, metadata = {}, {}
    for task in ('vision', 'speech', 'fusion'):
        operators[task], metadata[task] = load(task)
    (out / 'metadata.json').write_text(json.dumps(dict(stages=metadata,
        pairs_sha256=hashlib.sha256(pairs_path.read_bytes()).hexdigest(),
        quality_rule={'feature_rtol': 1e-4, 'feature_atol': 1e-5, 'transcript': 'exact',
                      'answer_id': 'exact', 'top5_order': 'record separately', 'score_atol': 1e-4},
        note='Rules fixed before reference inference; not dataset accuracy equivalence'), indent=2) + '\n')
    rows = []
    with (out / 'results.jsonl').open('x') as handle:
        for pair in pairs:
            row = {'index': pair['index'], 'question_id': pair['question_id'], 'split': pair['split']}
            try:
                inputs = {}
                for kind in ('image', 'audio'):
                    value = Path(pair[kind + '_path']).read_bytes()
                    assert hashlib.sha256(value).hexdigest() == pair[kind + '_sha256']
                    inputs[kind] = value
                start = time.perf_counter()
                feature = operators['vision'](inputs['image'])
                middle = time.perf_counter()
                speech = operators['speech'](inputs['audio'])
                joined = time.perf_counter()
                result = json.loads(operators['fusion']({'vision': feature, 'speech': speech}))
                end = time.perf_counter()
                (out / (str(pair['question_id']) + '.npy')).write_bytes(feature)
                row.update(status='ok', transcript=speech.decode(), **result,
                           feature_sha256=hashlib.sha256(feature).hexdigest(),
                           feature_bytes=len(feature), speech_bytes=len(speech),
                           stage_ms={'vision': (middle-start)*1000, 'speech': (joined-middle)*1000,
                                     'fusion': (end-joined)*1000},
                           ground_truth=pair.get('multiple_choice_answer'),
                           answer_matches_gt=result['answers'][0] == pair.get('multiple_choice_answer'))
            except Exception as error:
                row.update(status='error', error=repr(error))
            rows.append(row)
            handle.write(json.dumps(row) + '\n')
            handle.flush()
            print(json.dumps({k: row[k] for k in ('index', 'status', 'question_id')}), flush=True)
    summary = {'attempts': len(rows), 'ok': sum(r['status'] == 'ok' for r in rows),
               'errors': sum(r['status'] != 'ok' for r in rows),
               'simple_mc_answer_agreement': sum(r.get('answer_matches_gt', False) for r in rows),
               'note': 'Single-process sequential baseline; not distributed E2E or official VQA accuracy metric'}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary), flush=True)
    if summary['errors']:
        raise SystemExit('Reference has errors; preserve them and do not silently remove inputs')


if __name__ == '__main__':
    main()
