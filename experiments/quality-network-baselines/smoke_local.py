"""Local end-to-end smoke on REAL CPU models (no cluster, no accelerator): two cpu-torch DagWorkers (zones edge/cloud),
the hardened gateway and the generator on 127.0.0.1; profile job -> route probes -> measured profile -> open-loop trace
-> analyze.validate_row + official VQA score + reference-v1 answer comparison. Plumbing/semantics evidence only:
loopback routes, one host CPU, no shaping; not a performance or device result.
Run: .scratch/quality-network-heterogeneous/venv-ref/bin/python experiments/quality-network-baselines/smoke_local.py
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(ROOT/'experiments/quality-network-heterogeneous'))
sys.path.insert(2, str(ROOT/'experiments/real-topology-live'))
import analyze  # noqa: E402
import dag_worker  # noqa: E402
import design  # noqa: E402
import gateway  # noqa: E402
import generator  # noqa: E402
import policies  # noqa: E402

LIVE = ROOT/'artifacts/topology-profile-live'
MODEL_ROOT, SOURCE = LIVE/'model-assets', ROOT/'.scratch/quality-network-heterogeneous/work/jellybean'
CORPUS = LIVE/'load-cache/corpus'


def start(srv):
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f'http://127.0.0.1:{srv.server_address[1]}'


def main():
    started = time.monotonic()
    pairs = json.loads((LIVE/'load-pairs.json').read_text())['pairs']
    gt = {p['question_id']: p for p in json.loads((ROOT/'artifacts/topology-profile-assets/20261019T091725Z/selected-data/pairs.json').read_text())['pairs']}
    reference = {r['question_id']: r for r in map(json.loads, (LIVE/'reference-v1/results.jsonl').read_text().splitlines())}
    cal = [p for p in pairs if p['split'] == 'calibration'][:2]
    ev = [p for p in pairs if p['split'] == 'evaluation'][:4]
    workers, urls, zones = {}, {}, {'cpu-e': 'edge', 'cpu-c': 'cloud'}
    for key in zones:
        w = dag_worker.DagWorker('cpu-torch', list(policies.TASKS), model_root=str(MODEL_ROOT), source_root=str(SOURCE))
        if w.errors:
            raise RuntimeError(f'{key} load errors {w.errors}')
        w.node, w.pod_uid = 'local-'+key, 'local-uid-'+key
        workers[key], urls[key] = w, start(dag_worker.serve(w, host='127.0.0.1', port=0))
    relay = gateway.Relay(allowed_prefixes=('http://127.0.0.1',))
    gw_url = start(gateway.serve(relay, host='127.0.0.1', port=0))
    gen = generator.Generator()
    origin = start(generator.serve(gen, host='127.0.0.1', port=0))
    relay.freeze({**{k: dict(url=u, zone=zones[k]) for k, u in urls.items()}, 'origin': dict(url=origin, zone='edge')})
    for p in cal + ev:
        gen.corpus[str(p['question_id'])] = dict(image=(CORPUS/Path(p['raw_image_path']).name).read_bytes(),
                                                 audio=(CORPUS/Path(p['raw_audio_path']).name).read_bytes())
    cands = {t: list(zones) for t in policies.TASKS}
    gen.configure(dict(run_id='qnb-local-smoke', workers={k: dict(url=urls[k], zone=zones[k], node='local-'+k, pod_uid='local-uid-'+k) for k in zones},
                       gateway_url=gw_url, origin_url=origin, origin_zone='edge', candidates=cands, poll_s=1.0, poll_timeout_s=0.5))
    prof = gen.profile(dict(question_ids=[p['question_id'] for p in cal], warmup=1, producers={t: 'cpu-c' for t in policies.TASKS}))
    probes = gen.probes(dict(pairs=design.dag_pairs(cands), sizes=[4096, 1048576], reps=2, timeout_s=30))
    profile = dict(compute_ms=prof['compute_ms'], links={k: v['fit'] for k, v in probes['links'].items()}, payload_bytes=prof['payload_bytes'],
                   source='measured:local-smoke (loopback, one host CPU)')
    arrivals = design.arrivals(0.5, 12.0, 7, [p['question_id'] for p in ev])
    body = dict(label='smoke-dag_reopt', policy=dict(name='dag_reopt'), arrivals=arrivals, deadline_s=design.SLO_S, cancel_s=30.0,
                max_inflight=8, profile=profile, quality=dict(mode='diagnostic', qmin=design.QMIN))
    gen.start_trace(body)
    while gen.status()['traces']['smoke-dag_reopt']['state'] == 'running':
        time.sleep(0.2)
    ledger = gen.ledger('smoke-dag_reopt')
    corpus_sha = {q: dict(image=generator.digest(v['image']), audio=generator.digest(v['audio'])) for q, v in gen.corpus.items()}
    vworkers = {k: dict(node='local-'+k, backend='cpu-torch', models={t: {f: d.get(f) for f in ('model_sha256', 'derived_artifact_sha256')}
                                                                   for t, d in workers[k].descriptions.items()}) for k in zones}
    created = {k: 'local-uid-'+k for k in zones}
    checks = []
    for row in ledger['rows']:
        q = row['question_id']
        checks.append(dict(request_id=row['request_id'], status=row['status'], latency_ms=row.get('latency_ms'),
                           plan=(row.get('decision') or {}).get('plan_label'), validation=analyze.validate_row(row, vworkers, created, corpus_sha, 'qnb-local-smoke')
                           if row['status'] in ('ok', 'late_ok') else row['status'],
                           answer=row.get('answer'), reference_answer=reference[q]['answers'][0] if 'answers' in reference[q] else reference[q].get('answer'),
                           vqa=analyze.vqa_score(gt, row) if row.get('answer') else None))
    profile_parity = []
    for r in prof['rows']:
        if r['task'] == 'speech' and r.get('status') == 'ok' and not r['warmup']:
            import base64
            text = base64.b64decode(r['output_b64']).decode()
            profile_parity.append(dict(worker=r['worker'], question_id=r['question_id'], transcript_equal_reference=text == reference[int(r['question_id'])]['transcript']))
    out = dict(at=datetime.now(timezone.utc).isoformat(), elapsed_s=time.monotonic()-started, profile=profile,
               profile_status={r['status'] for r in prof['rows']} == {'ok'}, speech_parity=profile_parity, checks=checks,
               split_vs_original=[{k: r.get(k) for k in ('task', 'worker', 'question_id', 'split_answer', 'original_answer', 'answer_equal',
                                                         'output_bytes_equal', 'error')} for r in prof['equivalence']],
               gateway=relay.health()['counters'], ledger_counts=gen.status()['traces']['smoke-dag_reopt']['counts'],
               scope='loopback, one host CPU (etri-ser0001 x86_64), torch CPU, no shaping; plumbing/semantics only')
    log = ROOT/'.scratch/quality-network-baseline-first/logs'/f"local-smoke-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    log.write_text(json.dumps(dict(out, ledger=ledger), indent=1, default=str))
    print(json.dumps(dict({k: v for k, v in out.items() if k != 'profile'}, compute_ms=profile['compute_ms'], log=str(log)), indent=1, default=str))
    gen.close()
    ok = all(c['validation'] is None for c in checks) and out['profile_status'] and all(p['transcript_equal_reference'] for p in profile_parity) \
        and all(r['answer_equal'] for r in prof['equivalence']) and all(r['status'] == 'ok' for r in prof['original_rows'])
    return 0 if ok else 1


if __name__ == '__main__':
    os.environ.setdefault('MODEL_ROOT', str(MODEL_ROOT))
    sys.exit(main())
