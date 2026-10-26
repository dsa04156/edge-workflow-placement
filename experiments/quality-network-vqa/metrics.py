"""Release/terminal ledger -> goodput and official VQA quality for one open-loop phase.

Clock: latency = terminal finished_s - scheduled_s (scheduled origin; release lag and driver queue
included). finished_s of real-topology-live/load_trace.py is taken after the fusion ack, so the SLO
covers final return + ack. on_time iff latency <= SLO (inclusive). The old trace's quality_ok/status are
CPU-reference/10 s cancellation labels: they are reported as cpu_reference_mismatch / cancel-timeout
counts and never used as service quality. A returned output keeps its official VQA accuracy even when
late. Every planned arrival is in every denominator: drops, errors, timeouts and missing terminals.
Timeouts = client-side TimeoutError/timed out plus server deadline expiry (HTTP 504, direct or nested under 500
wrappers via the pilot adapter's HTTPChainError chain); other HTTP codes are errors whatever their text says.
Goodput = on-time returned requests / (count / rate). The quality gate is separate from the goodput
number: statistic = mean official accuracy over all planned arrivals, unreturned scored 0.
"""
import argparse
import json
from pathlib import Path
import re
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vqa_eval  # noqa: E402
import vqa_http_adapter  # noqa: E402

GATE_STATISTIC = 'all_arrivals_zero_imputed'


def _percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered)-1)*q
    low = int(pos)
    return ordered[low] + (ordered[min(low+1, len(ordered)-1)]-ordered[low])*(pos-low)


HTTP_ERROR = re.compile(r"<HTTPError (\d{3}): ")


def failure_evidence(row):
    """Structured cause of an unreturned terminal row. load_trace keeps only repr(error) (old source left untouched);
    urllib's HTTPError repr carries the status code. http_runtime answers serving-queue/execution/forwarding deadline
    expiry with HTTP 504, so 504 is a server deadline timeout; any other HTTP status stays an error."""
    if row['status'] == 'driver_capacity':
        return dict(outcome='dropped', cause='driver_capacity')
    error = row.get('error', '')
    chain = vqa_http_adapter.parse(error)
    if chain:
        # Pilot adapter evidence (outer -> innermost status). A deadline is verified only when the innermost
        # status is 504 and every wrapper answered 500 (generic re-raise) or 504; message text is never used.
        codes = chain['http_codes']
        deadline = codes[-1] == 504 and all(c in (500, 504) for c in codes[:-1])
        cause = ('server_deadline_http_504' if len(codes) == 1 else 'nested_server_deadline_http_504') if deadline else 'http_error'
        return dict(outcome='timeout' if deadline else 'error', cause=cause, http_code=codes[0], http_codes=codes,
                    message=chain.get('message'), http_code_source='pilot HTTPChainError evidence in the terminal row')
    match = HTTP_ERROR.match(error)
    if match:
        code = int(match.group(1))
        return dict(outcome='timeout' if code == 504 else 'error', cause='server_deadline_http_504' if code == 504 else 'http_error',
                    http_code=code, http_codes=[code], http_code_source='legacy HTTPError repr in the terminal row (outer status only)')
    if 'TimeoutError' in error or 'timed out' in error:
        return dict(outcome='timeout', cause='client_deadline', http_code=None)
    return dict(outcome='error', cause='other', http_code=None)


def summarize_phase(events, config, pairs, slo_s, qmin):
    """events: phase event rows; config: trace config (question_ids, offsets_s, rate_rps); pairs: {qid: pair}."""
    planned = list(zip(config['question_ids'], config['offsets_s']))
    released, terminals = set(), {}
    for e in events:
        if e.get('event') == 'release':
            released.add(e['index'])
        elif e.get('event') == 'terminal':
            if e['index'] in terminals:
                raise ValueError(f"Duplicate terminal for index {e['index']}")
            if e['index'] not in released:
                raise ValueError(f"Terminal without release for index {e['index']}")
            if not 0 <= e['index'] < len(planned) or e['question_id'] != planned[e['index']][0]:
                raise ValueError(f"Terminal index/question mismatch at {e['index']}")
            terminals[e['index']] = e
    slo_ms = slo_s*1000
    requests = []
    counts = dict(arrivals=len(planned), on_time=0, late=0, dropped=0, timeout=0, error=0,
                  missing_terminal=0, cpu_reference_mismatch=0, cancel_timeout_exceeded=0)
    for index, (qid, offset) in enumerate(planned):
        row = terminals.get(index)
        record = dict(index=index, question_id=qid, scheduled_s=offset)
        if row is None:
            record['outcome'] = 'missing_terminal'
        else:
            record['latency_ms'] = (row['finished_s'] - offset)*1000
            answers = (row.get('output') or {}).get('answers')
            if answers:
                record['answer'] = answers[0]
                record['vqa_accuracy'] = vqa_eval.score_answer(pairs[qid], answers[0])
                record['outcome'] = 'on_time' if record['latency_ms'] <= slo_ms else 'late'
                if row['status'] == 'quality_error' or row.get('answer_id_matches') is False:
                    counts['cpu_reference_mismatch'] += 1
                if row['status'] == 'deadline_error':
                    counts['cancel_timeout_exceeded'] += 1
            else:
                record['failure'] = failure_evidence(row)
                record['outcome'] = record['failure']['outcome']
                record['error'] = row.get('error', row['status'])
        counts[record['outcome']] += 1
        requests.append(record)
    returned = [r for r in requests if 'vqa_accuracy' in r]
    on_time = [r for r in returned if r['outcome'] == 'on_time']
    arrivals = len(planned)
    window = arrivals/config['rate_rps']
    total = sum(r['vqa_accuracy'] for r in returned)
    zero_imputed = total/arrivals if arrivals else None
    quality = dict(returned=dict(count=len(returned), mean=total/len(returned) if returned else None),
                   on_time=dict(count=len(on_time), mean=statistics.fmean(r['vqa_accuracy'] for r in on_time) if on_time else None),
                   coverage=len(returned)/arrivals if arrivals else None, all_arrivals_zero_imputed=zero_imputed)
    passed = zero_imputed is not None and zero_imputed >= qmin
    goodput = len(on_time)/window if window else None
    latencies = [r['latency_ms'] for r in returned]
    return dict(phase=config.get('phase'), slo_s=slo_s, window_s=window, rate_rps=config['rate_rps'], counts=counts,
                goodput_rps=goodput, on_time_rate=len(on_time)/arrivals if arrivals else None,
                quality=quality, quality_gate=dict(qmin=qmin, statistic=GATE_STATISTIC, value=zero_imputed, passed=passed),
                quality_qualified_goodput_rps=goodput if passed else None,
                returned_latency_ms=dict(p50=_percentile(latencies, .5), p95=_percentile(latencies, .95),
                                         p99=_percentile(latencies, .99), count=len(latencies)),
                ledger=dict(phase_end_seen=any(e.get('event') == 'phase_end' for e in events),
                            releases=len(released), terminals=len(terminals)),
                requests=requests)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Goodput and official VQA quality of one saved trace phase.')
    ap.add_argument('--pairs', required=True)
    ap.add_argument('--config', required=True, help='<phase>.config.json saved by the collector')
    ap.add_argument('--events', required=True, help='<phase>.events.jsonl saved by the collector')
    ap.add_argument('--slo-s', type=float, required=True)
    ap.add_argument('--qmin', type=float, required=True)
    ap.add_argument('--out', required=True, help='new JSON file; an existing file is refused')
    args = ap.parse_args(argv)
    pairs = {p['question_id']: p for p in json.loads(Path(args.pairs).read_text())['pairs']}
    events = [json.loads(line) for line in Path(args.events).read_text().splitlines() if line.strip()]
    out = summarize_phase(events, json.loads(Path(args.config).read_text()), pairs, args.slo_s, args.qmin)
    with Path(args.out).open('x') as handle:
        json.dump(out, handle, indent=1)
        handle.write('\n')
    print(json.dumps({k: out[k] for k in ('phase', 'counts', 'goodput_rps', 'quality_gate')}))


if __name__ == '__main__':
    sys.exit(main())
