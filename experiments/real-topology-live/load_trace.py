"""Source-local open-loop trace. Every planned release gets one terminal row."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time

from edge_client import read_input, run_once, validate_result


def run_trace(config, pairs, references, emit):
    offsets, questions = config['offsets_s'], config['question_ids']
    if len(offsets) != len(questions) or offsets != sorted(offsets):
        raise ValueError('Trace offsets must be sorted and match question count')
    start = time.monotonic()
    slots = threading.BoundedSemaphore(config['max_inflight'])
    lock = threading.Lock()
    terminals = []
    def log(row):
        with lock:
            emit(dict(row, phase=config['phase']))
    log({'event': 'phase_start', 'utc': datetime.now(timezone.utc).isoformat(), 'config': config})

    def attempt(index, qid, released):
        row = dict(event='terminal', index=index, question_id=qid, scheduled_s=offsets[index], actual_release_s=released)
        row['dispatch_s'] = time.monotonic() - start
        row['driver_queue_ms'] = (row['dispatch_s'] - released) * 1000
        identity = dict(run_id=config['run_id'], request_id=config['phase'] + '-' + str(index))
        try:
            pair = pairs[qid]
            remaining = config['deadline_s'] - (time.monotonic() - start - offsets[index])
            if remaining <= 0:
                raise TimeoutError('Expired before dispatch')
            result = run_once(pair, identity, config['endpoints'], config['remote_source'], config['representation'], remaining)
            row.update(validate_result(pair, result, references[qid], config['representation']))
            row['status'] = 'ok' if row['quality_ok'] else 'quality_error'
        except Exception as error:
            row.update(status='error', error=repr(error), quality_ok=False)
        finally:
            row['finished_s'] = time.monotonic() - start
            row['scheduled_to_terminal_ms'] = (row['finished_s'] - offsets[index]) * 1000
            row['deadline_exceeded'] = row['scheduled_to_terminal_ms'] > config['deadline_s'] * 1000
            if row['deadline_exceeded'] and row['status'] == 'ok':
                row.update(status='deadline_error', quality_ok=False)
            with lock:
                terminals.append(row)
            log(row)
            slots.release()

    with ThreadPoolExecutor(max_workers=config['max_inflight']) as pool:
        for index, (offset, qid) in enumerate(zip(offsets, questions)):
            remaining = start + offset - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            released = time.monotonic() - start
            log(dict(event='release', index=index, question_id=qid, scheduled_s=offset,
                     actual_release_s=released, release_lag_ms=(released - offset) * 1000))
            if slots.acquire(blocking=False):
                pool.submit(attempt, index, qid, released)
            else:
                row = dict(event='terminal', index=index, question_id=qid, scheduled_s=offset,
                    actual_release_s=released, finished_s=released, status='driver_capacity', quality_ok=False)
                terminals.append(row)
                log(row)
        log({'event': 'stop_release', 'elapsed_s': time.monotonic() - start})
    summary = dict(event='phase_end', attempts=len(questions), terminals=len(terminals),
                   failures=sum(r['status'] != 'ok' for r in terminals), elapsed_s=time.monotonic() - start)
    log(summary)
    return summary


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', required=True)
    ap.add_argument('--root', default='/work/corpus')
    args = ap.parse_args()
    config = json.loads(Path(args.config).read_text())
    root = Path(args.root)
    pairs = {p['question_id']: p for p in json.loads((root / 'pairs.json').read_text())['pairs']}
    references = {r['question_id']: r for r in map(json.loads, (root / 'reference.jsonl').read_text().splitlines())}
    # Raw-ready input replay: file reading/hashing is before the release clock.
    for qid in set(config['question_ids']):
        read_input(pairs[qid], 'image', config['representation'])
    run_trace(config, pairs, references, lambda row: print(json.dumps(row), flush=True))
