"""Offline analysis of unstable-network ledgers (no kubectl). Never modifies the run directory.

Copied verbatim from experiments/quality-network-baselines/analyze.py (sha256 in the plan's copied_sources): device_ok,
canonical_json, percentile, receipt_reason, validate_row/_validate_row, NOT_RETURNED, vqa_score, scorer. trace_summary is
the copied cohort rule (exactly one result per scheduled arrival; timeliness recomputed from the generator-clock
completion offset and the frozen arrival offset/deadline; invalid evidence never counts) extended with the per-arrival
classification, fallbacks, observation ages, chronological plan switches and prediction residuals.

New (pre-registered before collection):
- phase_summary: every scheduled arrival belongs to the trace phase containing its scheduled offset (nominal schedule),
  exactly once, whatever its completion time; arrivals inside a boundary's timing interval (timing_validity) are
  counted in their nominal phase AND flagged boundary_uncertain (never dropped).
- recovery: design.RECOVERY (first in-deadline arrival after each degraded->good boundary, fixed 10 s window); since R1
  the search starts no earlier than the latest offset at which the good restoration can have taken effect.
- timing_validity / comparable_outputs (R1, fixed before live collection): phase and recovery outputs exist only for a
  cell whose timing is valid (finite alignment bound, the cell's frozen trace and hash, every boundary changed once
  successfully with its frozen condition and applied times, window end reached, total uncertainty including application
  lag <= the 250 ms align_tolerance_ms) and whose ledger is complete. Otherwise they are None with the reasons and the
  timing status invalid/unknown; the full-cell outcome counts of the whole offered cohort are always reported.
- network_timeline: netctl application lag/call time per boundary and qdisc counter deltas per phase (kernel counters of
  the owned netem/tbf qdiscs; parent/child overlap, so never summed as traffic; a configured rate is not throughput).
"""
import base64
import hashlib
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import design  # noqa: E402
import traces  # noqa: E402

BACKEND = {'cpu': 'cpu-torch', 'npu': 'openvino-npu', 'jgpu': 'cuda-torch'}
VQA_EVAL = 'experiments/quality-network-vqa/vqa_eval.py'  # the scoring source; plan-bound (reused_frozen_sources)
TRACE_KEYS = ('label', 'policy', 'arrivals', 'deadline_s', 'cancel_s', 'max_inflight', 'quality', 'observation_contract', 'start_delay_s')


def device_ok(backend, receipt):
    timing = receipt.get('timing') or {}
    if backend == 'cpu-torch':
        return receipt.get('device') == 'cpu'
    if backend == 'cuda-torch':
        return str(receipt.get('device', '')).startswith('cuda') and str(timing.get('output_device', '')).startswith('cuda')
    if backend == 'openvino-npu':
        return receipt.get('device') == 'NPU' and timing.get('execution_devices') == ['NPU']
    return False


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def receipt_reason(r, task, key, row, workers, created, run_id):
    """None if receipt r is the planned worker's (creation UID, node, backend, device), this run/request and the
    plan-bound model provenance (workers[key]['models'][task], plan model_provenance from the frozen assets), else a reason."""
    w = workers.get(key)
    if not isinstance(r, dict) or not w or not created.get(key) or r.get('pod_uid') != created.get(key) \
            or r.get('node') != w['node'] or r.get('backend') != w['backend']:
        return 'identity_mismatch'
    if not device_ok(w['backend'], r):
        return 'device_mismatch'
    if r.get('request_id') != row['request_id']:
        return 'request_id_mismatch'
    if not run_id or r.get('run_id') != run_id:
        return 'run_id_mismatch'
    model = (w.get('models') or {}).get(task)
    if not model or r.get('model_sha256') != model.get('model_sha256') or r.get('derived_artifact_sha256') != model.get('derived_artifact_sha256'):
        return 'model_mismatch'
    return None


def validate_row(row, workers, created, corpus_sha, run_id):
    """-> None if valid else a reason. Malformed evidence is a reason, never an exception."""
    try:
        return _validate_row(row, workers, created, corpus_sha, run_id)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return 'malformed_evidence'


def _validate_row(row, workers, created, corpus_sha, run_id):
    plan = (row.get('decision') or {}).get('plan') or {}
    receipts_by_branch = {k: (b.get('reply') or {}).get('receipts') or [] for k, b in (row.get('branches') or {}).items()}
    hops_by_branch = {k: (b.get('reply') or {}).get('hops') or [] for k, b in (row.get('branches') or {}).items()}
    expected = dict(image=('image_preprocess', 'vision'), audio=('audio_preprocess', 'speech'))
    join_payload = {}
    fusion = []
    for branch, tasks in expected.items():
        receipts, hops = receipts_by_branch.get(branch, []), hops_by_branch.get(branch, [])
        stage_receipts = [r for r in receipts if r.get('stage') != 'fusion']
        fusion += [r for r in receipts if r.get('stage') == 'fusion']
        if [r.get('stage') for r in stage_receipts] != list(tasks) or len(hops) != 2:
            return 'chain_shape'
        for task, r in zip(tasks, stage_receipts):
            reason = receipt_reason(r, task, plan.get(task), row, workers, created, run_id)
            if reason:
                return reason
        inputs = corpus_sha.get(str(row['question_id']))
        if not inputs or stage_receipts[0].get('input_sha256') != inputs.get(branch):
            return 'input_mismatch'
        if hops[0].get('output_sha256') != stage_receipts[0].get('output_sha256') or hops[0].get('output_sha256') != stage_receipts[1].get('input_sha256'):
            return 'hash_discontinuity'
        if hops[1].get('output_sha256') != stage_receipts[1].get('output_sha256'):
            return 'hash_discontinuity'
        join_payload['vision' if branch == 'image' else 'speech'] = hops[1].get('output_sha256')
    if len(fusion) != 2:
        return 'fusion_receipt_missing'
    if canonical_json(fusion[0]) != canonical_json(fusion[1]):
        return 'fusion_receipt_disagreement'
    f = fusion[0]
    reason = receipt_reason(f, 'fusion', plan.get('fusion'), row, workers, created, run_id)
    if reason:
        return reason
    if f.get('input_sha256') != join_payload:
        return 'hash_discontinuity'
    data = {(b.get('reply') or {}).get('data') for b in row['branches'].values()}
    if len(data) != 1:
        return 'output_hash_mismatch'
    try:
        raw = base64.b64decode(next(iter(data)), validate=True)
        answer = json.loads(raw)
    except (TypeError, ValueError):
        return 'malformed_output'
    if hashlib.sha256(raw).hexdigest() != f.get('output_sha256'):
        return 'output_hash_mismatch'
    if not isinstance(answer, dict) or not answer.get('answers') or row.get('answer') != answer['answers'][0] \
            or row.get('answer_ids') != answer.get('answer_ids') \
            or row.get('output_sha256') != hashlib.sha256(json.dumps(answer, separators=(',', ':')).encode()).hexdigest():
        return 'answer_mismatch'
    return None


NOT_RETURNED = ('failed', 'worker_rejected', 'timeout', 'admission_drop', 'not_sent', 'decision_error')


def trace_summary(ledger, cohort, window_s, workers, created, corpus_sha, gt, run_id):
    """Copied cohort rule + classification. -> (summary, classified[{index, offset_s, status, latency_ms}])."""
    label, deadline_ms = ledger['label'], float(cohort['deadline_s']) * 1000
    by_index = {}
    for row in ledger.get('rows') or []:
        by_index.setdefault(row.get('index') if isinstance(row, dict) else None, []).append(row)
    arrivals = cohort['arrivals']
    foreign = sum(len(v) for k, v in by_index.items() if k not in {a['index'] for a in arrivals})
    counts, invalid, latencies, classified, residuals = {}, {}, [], [], []
    scores = dict(returned=[], in_deadline=[])
    plans, decision_ms, lag, ages, fallbacks, sequence = {}, [], [], [], {}, []
    for a in arrivals:
        found = by_index.get(a['index'], [])
        row = found[0] if len(found) == 1 else None
        status = 'missing_result' if not found else ('duplicate_result' if len(found) > 1 else row.get('status') or 'missing_status')
        latency = None
        if row is not None and (row.get('request_id') != f"{label}-{a['index']}" or str(row.get('question_id')) != str(a['question_id'])
                                or row.get('scheduled_offset_s') != a['offset_s']):
            invalid['cohort_mismatch'] = invalid.get('cohort_mismatch', 0) + 1
            status = 'invalid'
        elif status in ('ok', 'late_ok'):
            reason = validate_row(row, workers, created, corpus_sha, run_id)
            completed = row.get('completed_offset_s')
            if not reason and not isinstance(completed, (int, float)):
                reason = 'malformed_evidence'
            elif not reason and (not math.isfinite(completed) or completed < float(a['offset_s'])):
                reason = 'impossible_timestamp'
            if reason:
                invalid[reason] = invalid.get(reason, 0) + 1
                status = 'invalid'
            else:
                latency = (float(completed) - float(a['offset_s'])) * 1000
                status = 'ok' if latency <= deadline_ms else 'late_ok'
                latencies.append(latency)
                score = vqa_score(gt, row)
                scores['returned'].append(score)
                if status == 'ok':
                    scores['in_deadline'].append(score)
                predicted = (row.get('decision') or {}).get('predicted_ms')
                if isinstance(predicted, (int, float)):
                    residuals.append(latency - predicted)
        elif row is not None and status not in NOT_RETURNED:
            invalid['unknown_status'] = invalid.get('unknown_status', 0) + 1
            status = 'invalid'
        counts[status] = counts.get(status, 0) + 1
        classified.append(dict(index=a['index'], offset_s=float(a['offset_s']), status=status, latency_ms=latency))
        d = (row or {}).get('decision') or {}
        if d:
            plans[d['plan_label']] = plans.get(d['plan_label'], 0) + 1
            sequence.append(d['plan_label'])
            decision_ms.append(d['decision_ms'])
            if d.get('fallback'):
                fallbacks[d['fallback']['reason']] = fallbacks.get(d['fallback']['reason'], 0) + 1
        age = (((row or {}).get('observation') or {}).get('summary') or {}).get('max_age_ms')
        if isinstance(age, (int, float)):
            ages.append(age)
        if (row or {}).get('dispatch_lag_ms') is not None:
            lag.append(row['dispatch_lag_ms'])
    n = len(arrivals)
    mean = lambda v: sum(v)/len(v) if v else None
    summary = dict(label=label, policy=ledger['policy']['name'], requests=n, offered_rps=n/window_s, window_s=window_s,
                   goodput_rps=counts.get('ok', 0)/window_s, in_deadline_fraction=counts.get('ok', 0)/n if n else None, counts=counts,
                   invalid_reasons=invalid, foreign_rows=foreign,
                   latency_ms=dict(p50=percentile(latencies, .5), p95=percentile(latencies, .95), p99=percentile(latencies, .99),
                                   population='valid returned answers (ok + late_ok); see counts for failed/dropped/missing'),
                   quality=dict(returned=mean(scores['returned']), in_deadline=mean(scores['in_deadline']),
                                all_offered_eventual_zero_imputed=sum(scores['returned'])/n if n else None,
                                all_offered_timely_zero_imputed=sum(scores['in_deadline'])/n if n else None,
                                coverage_returned=len(scores['returned'])/n if n else None,
                                coverage_in_deadline=len(scores['in_deadline'])/n if n else None),
                   quality_gate=ledger.get('quality_gate'), plans=plans,
                   plan_switches_chronological=sum(1 for a, b in zip(sequence, sequence[1:]) if a != b),
                   fallbacks=fallbacks, observation_max_age_ms=dict(p50=percentile(ages, .5), max=max(ages) if ages else None),
                   prediction_residual_ms=dict(p50=percentile(residuals, .5), p95=percentile(residuals, .95)),
                   decision_ms=dict(p50=percentile(decision_ms, .5), p95=percentile(decision_ms, .95)),
                   dispatch_lag_ms=dict(p50=percentile(lag, .5), max=max(lag) if lag else None),
                   max_seen_inflight=ledger.get('max_seen_inflight'), observation_polls=_polls(ledger))
    return summary, classified


def _polls(ledger):
    a, b = ledger.get('poll_before') or {}, ledger.get('poll_after') or {}
    return {k: b.get(k, 0) - a.get(k, 0) for k in ('polls', 'errors')}


def phase_summary(classified, trace_name, bound_ms=0.0, intervals=None):
    """intervals: [(lo_s, hi_s)] per inner boundary in the generator's schedule (timing_validity); without them the
    symmetric +-bound_ms around each inner boundary."""
    phases = traces.TRACES[trace_name]
    if intervals is None:
        intervals = [(p['start_s'] - bound_ms / 1000, p['start_s'] + bound_ms / 1000) for p in phases[1:]] if bound_ms > 0 else []
    out = []
    for i, p in enumerate(phases):
        rows = [c for c in classified if traces.phase_at(phases, c['offset_s']) == i]
        counts = {}
        for c in rows:
            counts[c['status']] = counts.get(c['status'], 0) + 1
        near = [c for c in rows if any(lo <= c['offset_s'] <= hi for lo, hi in intervals)]
        lat = [c['latency_ms'] for c in rows if c['latency_ms'] is not None]
        out.append(dict(phase=i, name=p['name'], start_s=p['start_s'], end_s=p['end_s'], condition=traces.condition(p), requests=len(rows),
                        counts=counts, in_deadline_fraction=counts.get('ok', 0) / len(rows) if rows else None,
                        latency_ms=dict(p50=percentile(lat, .5), p95=percentile(lat, .95)), boundary_uncertain=len(near)))
    return out


def recovery(classified, trace_name, rule=design.RECOVERY, restored_by=None):
    """restored_by: {boundary_s: latest generator offset at which the good restoration can have taken effect} (the
    boundary interval's hi_s); an arrival scheduled before it is never a recovery. time_to_recovery_s stays R - B."""
    end = traces.TRACES[trace_name][-1]['end_s']
    out = []
    for b in rule['boundaries'][trace_name]:
        start = max(b, (restored_by or {}).get(b, b))
        after = sorted((c for c in classified if c['offset_s'] >= start), key=lambda c: c['offset_s'])
        first = next((c['offset_s'] for c in after if c['status'] == 'ok'), None)
        if first is None:
            out.append(dict(boundary_s=b, search_from_s=start, state='not_recovered', first_recovery_offset_s=None, time_to_recovery_s=None,
                            window_end_s=None, window_requests=None, window_in_deadline=None, window_in_deadline_fraction=None))
            continue
        stop = min(first + rule['window_s'], end)
        window = [c for c in after if first <= c['offset_s'] < stop]
        ok = sum(1 for c in window if c['status'] == 'ok')
        out.append(dict(boundary_s=b, search_from_s=start, state='recovered', first_recovery_offset_s=first, time_to_recovery_s=round(first - b, 6),
                        window_end_s=stop, window_requests=len(window), window_in_deadline=ok, window_in_deadline_fraction=ok / len(window)))
    return out


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def timing_validity(trace_name, align, status, tolerance_ms):
    """-> dict(status valid|invalid|unknown, reasons, alignment_bound_ms, total_uncertainty_ms, boundaries).
    unknown: the cell's alignment record or netctl status is missing. valid needs all of:
      - an alignment bound that is a finite number >= 0;
      - the netctl status of this cell's frozen trace and hash;
      - one successful change per inner boundary, in order, with its frozen condition and applied start/end times, and
        the window end reached (no restore or failure cut the trace);
      - total uncertainty <= tolerance_ms. Boundary B applied over [s, e] (netctl clock, offsets from its start) lies in
        [s - bound, e + bound] of the generator's schedule (lo_s, hi_s); its uncertainty is max(B - lo, hi - B), and the
        total is the largest of these and the bound itself, so application lag counts toward the tolerance.
    Anything else is invalid. Boundary intervals are kept for invalid cells as diagnostics."""
    phases = traces.TRACES[trace_name]
    out = dict(status='unknown', reasons=[], tolerance_ms=tolerance_ms, alignment_bound_ms=None, total_uncertainty_ms=None, boundaries=[])
    reasons = out['reasons']
    if align is None:
        reasons.append('alignment record missing')
    if status is None:
        reasons.append('netctl status missing')
    if reasons:
        return out
    bound = align.get('bound_ms')
    if _finite(bound) and bound >= 0:
        out['alignment_bound_ms'] = bound
    else:
        reasons.append(f'alignment bound is not a finite non-negative number: {bound!r}')
        bound = None
    if status.get('trace') != trace_name or status.get('trace_sha256') != traces.trace_sha256(trace_name):
        reasons.append(f"netctl ran {status.get('trace')!r}/{status.get('trace_sha256')!r}, not this cell's frozen trace")
    start = status.get('start_local')
    if not _finite(start):
        reasons.append('netctl start time missing')
    events = status.get('events') or []
    changes = [e for e in events if e.get('kind') == 'change']
    expected = list(enumerate(phases[1:], 1))
    if len(changes) != len(expected):
        reasons.append(f'{len(changes)} phase changes recorded, {len(expected)} expected')
    for e, (i, p) in zip(changes, expected):
        if not e.get('ok') or e.get('phase') != i or e.get('planned_offset_s') != p['start_s'] or e.get('condition') != traces.condition(p):
            reasons.append(f'phase {i} change failed or differs from the frozen trace')
            continue
        s, t = e.get('applied_start_local'), e.get('applied_end_local')
        if not (_finite(s) and _finite(t) and s <= t) or not _finite(start):
            reasons.append(f'phase {i} applied times missing')
            continue
        if bound is not None:
            lo, hi = s - start - bound / 1000, t - start + bound / 1000
            out['boundaries'].append(dict(phase=i, nominal_s=p['start_s'], lo_s=lo, hi_s=hi,
                                          uncertainty_ms=max(p['start_s'] - lo, hi - p['start_s']) * 1000))
    if not any(e.get('kind') == 'window_end' and e.get('planned_offset_s') == phases[-1]['end_s'] for e in events):
        reasons.append('trace window end not reached')
    if bound is not None:
        out['total_uncertainty_ms'] = max([bound] + [b['uncertainty_ms'] for b in out['boundaries']])
        if out['total_uncertainty_ms'] > tolerance_ms:
            reasons.append(f"total timing uncertainty {out['total_uncertainty_ms']:.1f} ms exceeds the {tolerance_ms} ms tolerance")
    out['status'] = 'invalid' if reasons else 'valid'
    return out


def comparable_outputs(classified, trace_name, timing, ledger):
    """Phase cohorts and recovery only for valid timing and a complete ledger (terminal 'done', not a partial salvage);
    otherwise both are None with the reasons, never computed with zero uncertainty."""
    why = []
    if timing['status'] != 'valid':
        why.append(f"timing {timing['status']}: " + '; '.join(timing['reasons']))
    if ledger is None:
        why.append('ledger missing')
    elif ledger.get('partial') or ledger.get('state') != 'done':
        why.append(f"ledger incomplete (state {ledger.get('state')!r}, partial {ledger.get('partial')!r})")
    if why:
        return dict(comparable=False, suppressed_because=why, phases=None, recovery=None)
    return dict(comparable=True, suppressed_because=[],
                phases=phase_summary(classified, trace_name, intervals=[(b['lo_s'], b['hi_s']) for b in timing['boundaries']]),
                recovery=recovery(classified, trace_name, restored_by={b['nominal_s']: b['hi_s'] for b in timing['boundaries']}))


def network_timeline(status):
    """netctl status -> application lag per boundary and per-phase deltas of each owned qdisc's kernel counters. Phase i
    spans from the snapshot taken right after its change (install for phase 0) to the next change's snapshot."""
    events = status.get('events') or []
    changes = [e for e in events if e['kind'] == 'change']
    marks = [e for e in events if e['kind'] in ('install', 'change', 'window_end', 'restore', 'restore_after_error') and e.get('stats')]
    phases = []
    for a, b in zip(marks, marks[1:]):
        before = {q['handle']: q for q in a['stats'].get('qdisc_summary') or []}
        deltas = {}
        for q in b['stats'].get('qdisc_summary') or []:
            old = before.get(q['handle'])
            if old is not None and q.get('kind') in ('netem', 'tbf'):
                deltas[hex(q['handle'])] = dict(kind=q['kind'], **{k: q[k] - old[k] for k in ('bytes', 'packets', 'drops', 'overlimits')},
                                                backlog_end=q['backlog'])
        phases.append(dict(phase=a.get('phase'), from_kind=a['kind'], to_kind=b['kind'], qdiscs=deltas))
    lags = [e['apply_lag_ms'] for e in changes if e.get('ok')]
    return dict(trace=status.get('trace'), changes_ok=sum(1 for e in changes if e.get('ok')), changes_failed=sum(1 for e in changes if not e.get('ok')),
                max_apply_lag_ms=max(lags) if lags else None, max_call_ms=max((e.get('call_ms') or 0) for e in changes) if changes else None,
                phases=phases, state=status.get('state'))


def vqa_score(gt, row):
    return scorer().score_answer(gt[row['question_id']], row['answer'])


def scorer():
    sys.path.insert(0, str(design.ROOT/'experiments/quality-network-vqa'))
    import vqa_eval
    if Path(vqa_eval.__file__).resolve() != (design.ROOT/VQA_EVAL).resolve():
        raise ValueError(f'vqa_eval imported from {vqa_eval.__file__}, not {VQA_EVAL}')
    return vqa_eval


def contract_problems(plan, cell, cohort, ledger, ledger_present):
    """The saved request must be exactly the released plan/cell's generator input (TRACE_KEYS minus profile) and the
    ledger that request's; anything else (e.g. a network field) is a contract violation."""
    spec = next(dict(p) for p in plan['policies'] if p['name'] == cell['policy'])
    b = plan['budgets']
    want = dict(label=cell['label'], policy=spec, deadline_s=b['deadline_s'], cancel_s=b['cancel_s'], max_inflight=b['max_inflight'],
                quality=plan['quality'], observation_contract=dict(stale_after_s=b['obs_stale_s']), start_delay_s=b['start_delay_s'])
    wrong = [f'request {k}' for k, v in want.items() if cohort.get(k) != v]
    wrong += [f'request carries {k}' for k in sorted(set(cohort) - set(TRACE_KEYS))]
    if ledger_present:
        wrong += [f'ledger {k}' for k in ('label', 'policy', 'deadline_s', 'cancel_s', 'max_inflight', 'start_delay_s', 'observation_contract')
                  if ledger.get(k) != want[k]]
    return wrong


def sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else None


def analyze(run_dir, plan_dir):
    """Refuses (ValueError) unless the run was released for exactly this plan, GT/corpus/scorer are the plan's hashed
    inputs, cells.json is the plan's frozen schedule, and every saved request is its cell's seeded cohort and contract."""
    run, plan_dir = Path(run_dir), Path(plan_dir)
    plan_sha = sha_file(plan_dir/'plan.json')
    plan = json.loads((plan_dir/'plan.json').read_text())
    release = json.loads((run/'release.json').read_text())['binding']
    if release.get('plan_sha256') != plan_sha:
        raise ValueError('run was not released for this plan')
    gt_path = design.ROOT/plan['inputs']['gt']
    if sha_file(gt_path) != plan['inputs'].get('gt_sha256'):
        raise ValueError(f'{gt_path}: ground truth differs from the plan-bound hash')
    if sha_file(plan_dir/'corpus.json') != plan['corpus_sha256']:
        raise ValueError('corpus.json differs from the plan-bound hash')
    bound = (plan.get('reused_frozen_sources') or {}).get(VQA_EVAL)
    if not bound or sha_file(design.ROOT/VQA_EVAL) != bound:
        raise ValueError(f'{VQA_EVAL}: scoring source differs from the plan-bound hash')
    scorer()
    gt = {p['question_id']: p for p in json.loads(gt_path.read_text())['pairs']}
    corpus_sha = {}
    for f in json.loads((plan_dir/'corpus.json').read_text())['files']:
        corpus_sha.setdefault(str(f['question_id']), {})['image' if f['kind'] == 'image' else 'audio'] = f['sha256']
    created = {}
    for path in sorted(run.glob('*.created.json')):
        for r in json.loads(path.read_text())['confirmed']:
            if r['kind'] == 'Pod':
                created[r['name'].removeprefix(run.name+'-')] = r['uid']
    expected = plan['model_provenance']['expectations']
    workers = {w['key']: dict(node=w['node'], backend=w['backend'],
                              models={t: {k: d.get(k) for k in ('model_sha256', 'derived_artifact_sha256')}
                                      for t, d in (expected.get(w['key']) or {}).items()})
               for w in plan['workers']}
    window = plan['design']['window_s']
    questions = [p['question_id'] for p in plan['pairs'] if p['split'] == 'evaluation']
    cells = json.loads((run/'cells.json').read_text())
    if cells != plan['schedule']['cells'] or design.check_schedule(cells):
        raise ValueError('cells.json is not the plan frozen schedule')
    summaries = []
    for cell in cells:
        label = cell['label']
        cohort = load(run/f'trace-{label}.request.json')
        if cohort is None:
            continue  # never started (budget/fault skip, recorded in collection.json); not an offered cohort
        if cohort['arrivals'] != design.arrivals(cell['rate_rps'], window, cell['seed'], questions):
            raise ValueError(f'{label}: trace request is not the seeded frozen cohort')
        ledger = load(run/f'ledger-{label}.json')
        present = ledger is not None
        ledger = ledger or dict(label=label, policy=cohort.get('policy'), rows=[])
        wrong = contract_problems(plan, cell, cohort, ledger, present)
        if wrong:
            raise ValueError(f'{label}: execution contract differs from the released plan/cell: {wrong}')
        summary, classified = trace_summary(ledger, cohort, window, workers, created, corpus_sha, gt, release['run_id'])
        status = load(run/f'netctl-status-{label}.json')
        timing = timing_validity(cell['trace'], load(run/f'align-{label}.json'), status, plan['budgets']['align_tolerance_ms'])
        outputs = comparable_outputs(classified, cell['trace'], timing, ledger if present else None)
        gateway = load(run/f'gateway-{label}.json') or {}
        summaries.append(dict(summary, block=cell['block'], trace=cell['trace'], position=cell['position'], ledger_present=present,
                              ledger_state=ledger.get('state') if present else None, ledger_partial=ledger.get('partial') if present else None,
                              rows_without_terminal_status=sum(1 for r in ledger.get('rows') or [] if isinstance(r, dict) and r.get('status') is None),
                              generation=load(run/f'generation-{label}.json'), timing=timing, **outputs,
                              network=network_timeline(status) if status else None,
                              gateway_relay=dict(counters=gateway.get('counters'), note='relay body bytes per peer since the last reset'),
                              fault_affected=(run/f'fault-{label}.json').exists() or (run/f'fault-{label}-cell.json').exists()))
    collection = load(run/'collection.json') or {}
    return dict(run=run.name, plan_sha256=plan_sha, quality_contract=plan['quality'], cells=summaries, skipped=collection.get('skipped'),
                faults=collection.get('faults'), timing_invalid_at_collection=collection.get('timing_invalid'),
                comparable_cells=[s['label'] for s in summaries if s['comparable']],
                note='diagnostic (Qmin not met; reused 60 pairs are not a holdout); exploratory 2 blocks, no significance claim')
