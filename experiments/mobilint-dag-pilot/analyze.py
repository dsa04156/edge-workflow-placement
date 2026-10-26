"""Failure-inclusive analysis of one pilot run (stdlib only).

  python3 analyze.py --run <runs/mdp-...> --frozen <frozen.json> [--out analysis.json]

Every frozen schedule row gets exactly one result: rows absent from the ledger are missing_result, repeated ones
duplicate_result (neither counted as ok). An ok row is re-verified against the run's identities.json and creation
records (run/request id, input tensor sha, Pod UID/node, model/runtime/frozen hashes, logits hash); any mismatch or
missing field makes it identity_rejected. Ledger source: ledger-main.json, else the pre-cleanup salvage, else the
incremental checkpoint (recorded as ledger_source). Accuracy uses all 200 scheduled labeled rows per arm as the denominator
(a failed request is not a correct answer) and also reports the ok-only value. Parity compares the two arms only on
pairs where both succeeded and the edge sent byte-identical tensors. Latency quantiles use ok rows only (linear
interpolation); warmup is reported separately and excluded from the evaluation statistics.
"""
import argparse
import base64
import json
import math
from pathlib import Path
import struct

import protocol as P

STAGES = ('e2e_ms', 'pre_ms', 'rpc_ms', 'infer_ms', 'server_ms', 'residual_ms', 'post_ms')


def quantile(values, q):
    xs = sorted(values)
    if not xs:
        return None
    pos = (len(xs)-1)*q
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi]-xs[lo])*(pos-lo)


def summary(values):
    if not values:
        return dict(n=0)
    return dict(n=len(values), mean=sum(values)/len(values), min=min(values), p50=quantile(values, .5), p90=quantile(values, .9),
                p95=quantile(values, .95), p99=quantile(values, .99), max=max(values))


def wilson(k, n, z=1.959963984540054):
    if n == 0:
        return None
    p = k/n
    d = 1 + z*z/n
    c = (p + z*z/(2*n))/d
    h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/d
    return [c-h, c+h]


def logits(row):
    raw = base64.b64decode(row['logits_b64'])
    return struct.unpack(f'<{len(raw)//4}f', raw)


def flatten(row):
    r = dict(row)
    rec = row.get('receipt') or {}
    r['infer_ms'], r['server_ms'] = rec.get('infer_ms'), rec.get('server_ms')
    if r.get('rpc_ms') is not None and r['server_ms'] is not None:
        r['residual_ms'] = r['rpc_ms'] - r['server_ms']
    return r


IDENTITY_FIELDS = ('pod_uid', 'node', 'model_sha256', 'runtime_version', 'frozen_sha256')


def identity_ok(row, run, identities, created_uids):
    rec = row.get('receipt') or {}
    want = identities.get(row['arm']) or {}
    try:
        raw = base64.b64decode(row['logits_b64'], validate=True)
    except Exception:
        return False
    return (bool(want) and all(rec.get(k) == want.get(k) and want.get(k) for k in IDENTITY_FIELDS)
            and want['pod_uid'] in created_uids and rec.get('run_id') == run and rec.get('arm') == row['arm']
            and rec.get('request_id') == f"{run}-{row['seq']}-{row['arm']}" == row.get('request_id')
            and rec.get('input_sha256') == row.get('tensor_sha256') and P.sha256(raw) == rec.get('logits_sha256')
            and isinstance(row.get('top5'), list) and len(row['top5']) == 5)


def load_ledger(run_dir):
    for name in ('ledger-main.json', 'salvage-ledger-main.json'):
        path = run_dir/name
        if path.exists():
            return name, json.loads(path.read_text()).get('rows') or []
    path = run_dir/'ledger-main.jsonl'
    if path.exists():
        return path.name, [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return None, []


def match(schedule, rows):
    """-> ({seq: row or synthetic missing/duplicate row}, foreign rows)."""
    by_seq = {}
    foreign = []
    expected = {r['seq']: r for r in schedule}
    for row in rows:
        seq = row.get('seq')
        if seq not in expected or any(row.get(k) != expected[seq][k] for k in ('phase', 'sample', 'arm')):
            foreign.append(row)
            continue
        by_seq.setdefault(seq, []).append(row)
    out = {}
    for seq, s in expected.items():
        got = by_seq.get(seq, [])
        if len(got) == 1:
            out[seq] = flatten(got[0])
        else:
            out[seq] = dict(s, status='missing_result' if not got else 'duplicate_result')
    return out, foreign


def analyze(run_dir, frozen):
    run_dir = Path(run_dir)
    source, rows = load_ledger(run_dir)
    labels = {s['id']: s['label'] for s in frozen['evaluation'] + frozen['warmup'] + frozen['parity']}
    results, foreign = match(frozen['schedule'], rows)
    identities = json.loads((run_dir/'identities.json').read_text()) if (run_dir/'identities.json').exists() else {}
    cleanup = json.loads((run_dir/'cleanup.json').read_text()) if (run_dir/'cleanup.json').exists() else {}
    created = {r['uid'] for r in cleanup.get('records', []) if r.get('kind') == 'Pod'}
    rejected = 0
    for r in results.values():
        if r.get('status') == 'ok' and not identity_ok(r, run_dir.name, identities, created):
            r['status'] = 'identity_rejected'
            rejected += 1
    out = dict(run=run_dir.name, ledger_present=source is not None, ledger_source=source, ledger_rows=len(rows),
               foreign_rows=len(foreign), identity_rejected=rejected, scheduled=len(frozen['schedule']), phases={})
    for phase in ('warmup', 'eval', 'parity'):
        block = {}
        for arm in P.ARMS:
            rs = [r for r in results.values() if r['phase'] == phase and r['arm'] == arm]
            ok = [r for r in rs if r.get('status') == 'ok']
            statuses = {}
            for r in rs:
                statuses[r.get('status')] = statuses.get(r.get('status'), 0) + 1
            entry = dict(scheduled=len(rs), status=statuses, failures=len(rs)-len(ok),
                         latency={k: summary([r[k] for r in ok if r.get(k) is not None]) for k in STAGES},
                         bytes={k: summary([r[k] for r in ok if r.get(k) is not None])
                                for k in ('jpeg_bytes', 'request_body_bytes', 'response_body_bytes')},
                         receipts=sorted({(r['receipt'].get('node'), r['receipt'].get('pod_uid')) for r in ok}))
            if phase == 'eval':
                top1 = sum(1 for r in ok if r['top5'][0] == labels[r['sample']])
                top5 = sum(1 for r in ok if labels[r['sample']] in r['top5'])
                n = len(rs)
                entry['accuracy'] = dict(denominator_scheduled=n, top1=top1/n if n else None, top1_ci95=wilson(top1, n),
                                         top5=top5/n if n else None, top5_ci95=wilson(top5, n),
                                         ok_only=dict(n=len(ok), top1=top1/len(ok) if ok else None, top5=top5/len(ok) if ok else None))
            block[arm] = entry
        out['phases'][phase] = block
    out['parity'] = {phase: parity(results, labels, phase) for phase in ('eval', 'parity')}
    first = {}
    for r in sorted(results.values(), key=lambda r: r['seq']):
        if r['phase'] == 'eval':
            first.setdefault(r['sample'], r['arm'])
    out['arm_order'] = dict(pairs=len(first), mobilint_first=sum(1 for a in first.values() if a == 'mobilint'),
                            design='paired; seeded shuffled sample order; seeded arm order per pair; closed loop c=1')
    for key in ('probe-result.json', 'identities.json', 'cleanup.json', 'npu-occupancy-probe.json', 'npu-occupancy-after-trace.json',
                'health-final-mobilint.json', 'health-final-cpu-fp32.json', 'health-final-edge.json', 'preflight.json'):
        path = run_dir/key
        if path.exists():
            out.setdefault('evidence', {})[key] = json.loads(path.read_text())
    return out


def parity(results, labels, phase):
    pairs = {}
    for r in results.values():
        if r['phase'] == phase:
            pairs.setdefault(r['sample'], {})[r['arm']] = r
    both = [p for p in pairs.values() if all(p.get(a, {}).get('status') == 'ok' for a in P.ARMS)]
    same = [p for p in both if p['mobilint'].get('tensor_sha256') == p['cpu-fp32'].get('tensor_sha256')]
    stats = dict(pairs=len(pairs), both_ok=len(both), identical_input=len(same), top1_agree=0, top5_set_equal=0,
                 top5_order_equal=0, max_abs=[], mean_abs=[], cosine=[], discordant_top1=dict(mobilint_only=0, cpu_only=0))
    for p in same:
        m, c = p['mobilint'], p['cpu-fp32']
        stats['top1_agree'] += m['top5'][0] == c['top5'][0]
        stats['top5_set_equal'] += set(m['top5']) == set(c['top5'])
        stats['top5_order_equal'] += m['top5'] == c['top5']
        a, b = logits(m), logits(c)
        diff = [abs(x-y) for x, y in zip(a, b)]
        stats['max_abs'].append(max(diff))
        stats['mean_abs'].append(sum(diff)/len(diff))
        norm = math.sqrt(sum(x*x for x in a)*sum(y*y for y in b))
        if norm:
            stats['cosine'].append(sum(x*y for x, y in zip(a, b))/norm)
        label = labels[m['sample']]
        if label is not None:
            mc, cc = m['top5'][0] == label, c['top5'][0] == label
            stats['discordant_top1']['mobilint_only'] += mc and not cc
            stats['discordant_top1']['cpu_only'] += cc and not mc
    n = len(same)
    for key in ('top1_agree', 'top5_set_equal', 'top5_order_equal'):
        stats[key+'_rate'] = stats[key]/n if n else None
    for key in ('max_abs', 'mean_abs', 'cosine'):
        stats[key] = summary(stats[key])
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', required=True)
    ap.add_argument('--frozen', required=True)
    ap.add_argument('--out')
    a = ap.parse_args(argv)
    result = analyze(a.run, json.loads(Path(a.frozen).read_text()))
    out = Path(a.out or Path(a.run)/'analysis.json')
    with out.open('x') as f:
        json.dump(result, f, indent=1)
        f.write('\n')
    print(out)


if __name__ == '__main__':
    main()
