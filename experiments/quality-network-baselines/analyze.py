"""Offline analysis of baseline-first ledgers (no kubectl). Never modifies the run directory.

Per trace (policy x condition x load x block):
  goodput_rps = valid requests of the frozen arrival cohort answered within the deadline / arrival window length
  (cohort = every scheduled arrival of the trace, including drops and not-sent; no drain time in the denominator;
  branches and retries are never separate requests).
Validity of an answered request (otherwise counted 'invalid', never goodput): every receipt's Pod UID/node is the
creation UID/node of the worker the plan named for that task, device matches the backend (no CPU answer for a GPU/NPU
task), request id matches, and data continuity holds: corpus sha == first-stage input sha, each hop's sent output sha
== the next receipt's input sha, the fusion inputs == the two join hops' payload shas, fusion output sha == the answer.
Quality (official VQA via vqa_eval.score_answer) is reported separately for returned / in-deadline / all-offered
(zero-imputed) requests; the plan-level quality gate status is shown next to goodput and never multiplied into it.
CPU numerical parity of the profile outputs vs reference-v1 is a separate diagnostic table.
"""
import base64
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import design  # noqa: E402
import policies  # noqa: E402

BACKEND = {'cpu': 'cpu-torch', 'npu': 'openvino-npu', 'jgpu': 'cuda-torch'}
VQA_EVAL = 'experiments/quality-network-vqa/vqa_eval.py'  # the scoring source; plan-bound (reused_frozen_sources)


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
    """-> None if valid else a reason. workers: {key: {node, backend, models{task: {model_sha256, derived_artifact_sha256}}}},
    created: {key: creation uid}, corpus_sha: {qid: {image, audio}}, run_id: the collection's run id.
    Malformed evidence (missing fields, bad base64/JSON, unknown question) is a reason, never an exception."""
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
        return 'fusion_receipt_disagreement'  # both join parts must carry the one fusion execution's receipt
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
    # The scored fields must be the validated payload's own answer, never independently editable row fields.
    if not isinstance(answer, dict) or not answer.get('answers') or row.get('answer') != answer['answers'][0] \
            or row.get('answer_ids') != answer.get('answer_ids') \
            or row.get('output_sha256') != hashlib.sha256(json.dumps(answer, separators=(',', ':')).encode()).hexdigest():
        return 'answer_mismatch'
    return None


NOT_RETURNED = ('failed', 'worker_rejected', 'timeout', 'admission_drop', 'not_sent', 'decision_error')


def trace_summary(ledger, cohort, window_s, workers, created, corpus_sha, gt, run_id):
    """cohort: the collector's frozen trace request {arrivals[{index, offset_s, question_id}], deadline_s}. Exactly one
    result per scheduled arrival: missing -> missing_result, several -> duplicate_result (none counted), rows outside
    the cohort are ignored (foreign_rows). Timeliness is recomputed from the row's source-clock completion offset and the
    frozen arrival offset/deadline; the row's own status/latency strings are not trusted."""
    label, deadline_ms = ledger['label'], float(cohort['deadline_s']) * 1000
    by_index = {}
    for row in ledger.get('rows') or []:
        by_index.setdefault(row.get('index') if isinstance(row, dict) else None, []).append(row)
    arrivals = cohort['arrivals']
    foreign = sum(len(v) for k, v in by_index.items() if k not in {a['index'] for a in arrivals})
    counts, invalid, latencies = {}, {}, []
    scores = dict(returned=[], in_deadline=[])
    plans, switches, decision_ms, lag = {}, 0, [], []
    for a in arrivals:
        found = by_index.get(a['index'], [])
        row = found[0] if len(found) == 1 else None
        status = 'missing_result' if not found else ('duplicate_result' if len(found) > 1 else row.get('status') or 'missing_status')
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
                reason = 'impossible_timestamp'  # non-finite, or completed before its scheduled arrival
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
        elif row is not None and status not in NOT_RETURNED:
            invalid['unknown_status'] = invalid.get('unknown_status', 0) + 1
            status = 'invalid'
        counts[status] = counts.get(status, 0) + 1
        d = (row or {}).get('decision') or {}
        if d:
            plans[d['plan_label']] = plans.get(d['plan_label'], 0) + 1
            decision_ms.append(d['decision_ms'])
            switches += 1 if d.get('switched') and d.get('hold_reason') != 'initial' else 0
        if (row or {}).get('dispatch_lag_ms') is not None:
            lag.append(row['dispatch_lag_ms'])
    n = len(arrivals)
    mean = lambda v: sum(v)/len(v) if v else None
    return dict(label=label, policy=ledger['policy']['name'], requests=n, offered_rps=n/window_s, window_s=window_s,
                goodput_rps=counts.get('ok', 0)/window_s, in_deadline_fraction=counts.get('ok', 0)/n if n else None, counts=counts,
                invalid_reasons=invalid, foreign_rows=foreign,
                latency_ms=dict(p50=percentile(latencies, .5), p95=percentile(latencies, .95), p99=percentile(latencies, .99),
                                population='valid returned answers (ok + late_ok); see counts for failed/dropped/missing'),
                # returned/in_deadline: mean over valid answers (eventual / timely). *_zero_imputed: over the whole frozen
                # cohort, missing answers scored 0; 'eventual' keeps late answers, 'timely' scores late answers 0.
                quality=dict(returned=mean(scores['returned']), in_deadline=mean(scores['in_deadline']),
                             all_offered_eventual_zero_imputed=sum(scores['returned'])/n if n else None,
                             all_offered_timely_zero_imputed=sum(scores['in_deadline'])/n if n else None,
                             coverage_returned=len(scores['returned'])/n if n else None,
                             coverage_in_deadline=len(scores['in_deadline'])/n if n else None),
                quality_gate=ledger.get('quality_gate'), plans=plans, plan_switches=switches,
                decision_ms=dict(p50=percentile(decision_ms, .5), p95=percentile(decision_ms, .95)),
                dispatch_lag_ms=dict(p50=percentile(lag, .5), max=max(lag) if lag else None),
                max_seen_inflight=ledger.get('max_seen_inflight'), observation_polls=_polls(ledger))


def _polls(ledger):
    a, b = ledger.get('poll_before') or {}, ledger.get('poll_after') or {}
    return {k: b.get(k, 0) - a.get(k, 0) for k in ('polls', 'errors')}


def vqa_score(gt, row):
    return scorer().score_answer(gt[row['question_id']], row['answer'])


def scorer():
    """The official VQA scorer module, which must be the file at VQA_EVAL (the plan-bound source)."""
    sys.path.insert(0, str(design.ROOT/'experiments/quality-network-vqa'))
    import vqa_eval
    if Path(vqa_eval.__file__).resolve() != (design.ROOT/VQA_EVAL).resolve():
        raise ValueError(f'vqa_eval imported from {vqa_eval.__file__}, not {VQA_EVAL}')
    return vqa_eval


def contract_problems(plan, cell, cohort, ledger, ledger_present):
    """The trace's execution contract is the released plan/cell's: the saved request must carry the cell label, the
    plan's policy spec (fixed: the plan's fixed placement), deadline, cancel, admission cap and quality contract; the
    ledger must be that request's. -> list of mismatches (empty = bound)."""
    spec = next(dict(p) for p in plan['policies'] if p['name'] == cell['policy'])
    if spec['name'] == 'fixed':
        spec['plan'] = plan['fixed_plan']
    b = plan['budgets']
    want = dict(label=cell['label'], policy=spec, deadline_s=b['deadline_s'], cancel_s=b['cancel_s'], max_inflight=b['max_inflight'],
                quality=plan['quality'])
    wrong = [f'request {k}' for k, v in want.items() if cohort.get(k) != v]
    if ledger_present:
        wrong += [f'ledger {k}' for k in ('label', 'policy', 'deadline_s', 'cancel_s', 'max_inflight') if ledger.get(k) != want[k]]
    return wrong


def pilot_rows(summaries, cells):
    by_label = {c['label']: c for c in cells}
    return [dict(condition=by_label[s['label']]['condition'], policy=s['policy'], rate_rps=by_label[s['label']]['rate_rps'],
                 requests=s['requests'], ok=s['counts'].get('ok', 0), goodput_rps=s['goodput_rps'],
                 admission_drops=s['counts'].get('admission_drop', 0)) for s in summaries if s['label'] in by_label]


def sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def analyze(run_dir, plan_dir, frozen=None):
    """Refuses (ValueError) unless the run was released for exactly this plan, the GT/corpus files and the scoring source
    are the plan's hashed inputs, and (compare) `frozen` is the release-bound frozen-loads file whose cells are cells.json.
    Every trace request the collector saved is the frozen cohort: its arrivals must be the seeded trace of its cell (rate,
    seed, plan window, evaluation questions) and its execution contract the plan's (contract_problems); a trace without a
    ledger counts as all missing."""
    run, plan_dir = Path(run_dir), Path(plan_dir)
    plan_sha = sha_file(plan_dir/'plan.json')
    plan = json.loads((plan_dir/'plan.json').read_text())
    release = json.loads((run/'release.json').read_text())['binding']
    if release.get('plan_sha256') != plan_sha:
        raise ValueError('run was not released for this plan')
    gt_path = design.ROOT/plan['inputs']['gt']
    if sha_file(gt_path) != plan['inputs'].get('gt_sha256'):
        raise ValueError(f'{gt_path}: ground truth differs from the plan-bound hash')
    if not plan.get('corpus_sha256') or sha_file(plan_dir/'corpus.json') != plan['corpus_sha256']:
        raise ValueError('corpus.json differs from the plan-bound hash (or the plan predates corpus binding)')
    bound = (plan.get('reused_frozen_sources') or {}).get(VQA_EVAL)
    if not bound or sha_file(design.ROOT/VQA_EVAL) != bound:
        raise ValueError(f'{VQA_EVAL}: scoring source differs from the plan-bound hash (or the plan predates its binding)')
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
    if not plan.get('model_provenance'):
        raise ValueError('plan predates model provenance binding')
    expected = plan['model_provenance']['expectations']  # frozen-asset hashes; /health is checked against them in live
    workers = {w['key']: dict(node=w['node'], backend=w['backend'],
                              models={t: {k: d.get(k) for k in ('model_sha256', 'derived_artifact_sha256')}
                                      for t, d in (expected.get(w['key']) or {}).items()})
               for w in plan['workers']}
    stage = json.loads((run/'collection.json').read_text())['stage']
    window = plan['pilot']['window_s'] if stage == 'pilot' else plan['compare']['window_s']
    questions = [p['question_id'] for p in plan['pairs'] if p['split'] == 'evaluation']
    cells = json.loads((run/'cells.json').read_text())
    if stage == 'pilot' and cells != design.pilot_cells(plan['pilot']):
        raise ValueError('cells.json is not the plan pilot design')
    if stage != 'pilot':
        if not frozen or sha_file(frozen) != release.get('frozen_loads_sha256'):
            raise ValueError('compare analysis needs the release-bound frozen loads file')
        if cells != design.compare_cells(json.loads(Path(frozen).read_text()), plan['compare'], plan['policies']):
            raise ValueError('cells.json is not the compare design of the release-bound frozen loads')
    summaries = []
    for cell in cells:
        request_path = run/f"trace-{cell['label']}.request.json"
        if not request_path.exists():
            continue  # never started (budget skip, recorded in collection.json); not an offered cohort
        cohort = json.loads(request_path.read_text())
        if cohort['arrivals'] != design.arrivals(cell['rate_rps'], window, cell['seed'], questions):
            raise ValueError(f"{cell['label']}: trace request is not the seeded frozen cohort")
        ledger_path = run/f"ledger-{cell['label']}.json"
        ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else dict(label=cell['label'], policy=cohort.get('policy'), rows=[])
        wrong = contract_problems(plan, cell, cohort, ledger, ledger_path.exists())
        if wrong:
            raise ValueError(f"{cell['label']}: execution contract differs from the released plan/cell: {wrong}")
        summaries.append(dict(trace_summary(ledger, cohort, window, workers, created, corpus_sha, gt, release['run_id']),
                              ledger_present=ledger_path.exists()))
    out = dict(run=run.name, stage=stage, plan_sha256=plan_sha, quality_contract=plan['quality'], traces=summaries,
               note='diagnostic unless quality_gate is strict; reused 60 pairs are not an independent holdout')
    if stage == 'pilot':
        out['pilot_rows'] = pilot_rows(summaries, cells)
    return out
