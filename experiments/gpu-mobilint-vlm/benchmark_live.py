"""Bounded repeat measurement: one NEW run = live.execute qualification (probe + qualification inputs per arm, the excluded
warm-ups, its own ledger.jsonl), then the coordinator's frozen benchmark plan against the same owned worker Pods.

  python3 benchmark_live.py --frozen <dir> --benchmark-plan <plan.json> [live options] --print-release-draft
  python3 benchmark_live.py --frozen <dir> --benchmark-plan <plan.json> [live options] --runs <dir> --release <json> [--run-id gmv-...]

live options are live.py's --arm / --prepared / --cuda-cache-pvc / --io-profile, unchanged. The draft is live.py's own
draft plus `benchmark` (this file's sha256, the plan's sha256 and row count); the release must carry the same binding and
`benchmark` in its stages, and live.release_problems checks everything else as for live.py.

Plan: a JSON object whose `rows` each have measurement_id (unique), round, pair, arm, input. The FULL plan is validated
for the pair: every frozen input REPEATS times per arm; each (round, pair) is one contiguous group with one row per arm on
one input (the arm order inside a pair is the coordinator's); each round is contiguous and holds every (arm, input) exactly
once. --arm then runs only that arm's rows (the effective schedule, bound by its own hash next to the full plan's) as a
separate single-arm observation, never a paired comparison.

Reused unchanged: live.Session / execute / call_arm (exact receipt binding) / identity / check_occupancy / verify_cleanup,
collect.phase / protected_snapshot. Added here only: plan validation and binding, a fresh re-derivation of each arm's URL
and identity from its owned Pod (creation UID record, Running + Ready) and /health, the receipt sequence check, a separate
fsync'd benchmark ledger and an always-written summary. The shared Worker numbers every /run that takes its device lock
(receipt.sequence == /health queue.completed); this collector is the only client of the Pods it created, so after the
qualification the queue must be idle at exactly the qualification call count and each receipt must continue it by +1.
The same request id per input is reused across rounds: the worker keeps no response cache, and the sequence shows each
call reached the stage. The first non-ok reply or sequence gap stops the run; cleanup is live.py's UID-only cleanup.
"""
import argparse
from collections import Counter
import contextlib
from datetime import datetime, timezone
import io
import itertools
import json
import os
from pathlib import Path
import time

import live
import protocol as P
import collect
import bounded_http

REPEATS = 10
ROW_KEYS = ('measurement_id', 'round', 'pair', 'arm', 'input')
SOURCE = Path(__file__).resolve()
LEDGER = 'benchmark-ledger.jsonl'


def utc():
    return datetime.now(timezone.utc).isoformat()


def load_plan(path):
    raw = Path(path).read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise SystemExit('benchmark plan rejected: a JSON object with rows is required')
    return raw, value.get('rows')


def plan_problems(rows, arms, input_ids, repeats=REPEATS):
    if not isinstance(rows, list) or not rows:
        return ['plan needs a non-empty rows list']
    bad = [i for i, r in enumerate(rows) if not isinstance(r, dict) or any(k not in r for k in ROW_KEYS)]
    if bad:
        return [f'rows {bad[:5]} lack one of {ROW_KEYS}']
    problems = []
    ids = Counter(json.dumps(r['measurement_id']) for r in rows)
    if any(n > 1 for n in ids.values()):
        problems.append(f'duplicate measurement_id {[k for k, n in ids.items() if n > 1][:5]}')
    want = Counter({(a, i): repeats for a in arms for i in input_ids})
    counts = Counter((r['arm'], r['input']) for r in rows)
    if counts != want:
        problems.append(f'(arm, input) counts differ from {repeats} each of {list(input_ids)} on {list(arms)}: '
                        f'{sorted(set(counts.items()) ^ set(want.items()), key=str)[:6]}')
    pairs = [(k, list(g)) for k, g in itertools.groupby(rows, key=lambda r: json.dumps([r['round'], r['pair']]))]
    if len({k for k, _ in pairs}) != len(pairs):
        problems.append('a (round, pair) group is split or repeated')
    for k, group in pairs:
        if sorted(r['arm'] for r in group) != sorted(arms) or len({r['input'] for r in group}) != 1:
            problems.append(f"pair {k}: needs one row per arm {list(arms)} on one input, has {[(r['arm'], r['input']) for r in group]}")
    rounds = [(k, sorted((r['arm'], r['input']) for r in g)) for k, g in itertools.groupby(rows, key=lambda r: json.dumps(r['round']))]
    if len({k for k, _ in rounds}) != len(rounds):
        problems.append('a round is split or repeated')
    problems += [f'round {k}: does not hold every (arm, input) exactly once' for k, cells in rounds if cells != sorted(want)]
    return problems


def binding(raw, rows, selected):
    """The full plan bytes and this file, plus the effective (selected-arm) schedule actually executed."""
    effective = [r for r in rows if r['arm'] in selected]
    return dict(collector_sha256=P.file_sha(SOURCE), plan_sha256=P.sha256(raw), plan_rows=len(rows), repeats=REPEATS,
                selected_arms=list(selected), effective_rows=len(effective),
                effective_sha256=P.sha256(json.dumps(effective, sort_keys=True).encode())), effective


def release_draft(live_argv, bound):
    """live.py's own --print-release-draft output plus the benchmark binding."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        live.main(live_argv + ['--print-release-draft'])
    draft = json.loads(out.getvalue())
    draft['benchmark'] = bound
    return draft


def release_problems(release, frozen, arms, prepared, bound):
    problems = live.release_problems(release, frozen, live.dependency_hashes(), arms, prepared)
    if release.get('benchmark') != bound:
        problems.append('benchmark collector/plan binding is missing from or differs from the release')
    if 'benchmark' not in release.get('stages', []):
        problems.append('stage benchmark not released')
    return problems


class Prefixed:
    """The session as live.identity sees it here: Session.save is create-only and the qualification already wrote
    identity-<arm>.json, so this run's re-verification is saved as benchmark-identity-<arm>.json."""

    def __init__(self, session):
        self.session = session

    def __getattr__(self, name):
        return getattr(self.session, name)

    def save(self, name, value):
        self.session.save('benchmark-'+name, value)


@contextlib.contextmanager
def timed(session, timing, name):
    """collect.phase plus the same start/end/status in the summary."""
    entry = timing[name] = dict(started_at=utc(), status='error')
    start = time.monotonic()
    try:
        with collect.phase(session, name):
            yield
        entry['status'] = 'ok'
    except BaseException as error:
        entry['error'] = repr(error)[:500]
        raise
    finally:
        entry.update(finished_at=utc(), duration_ms=(time.monotonic()-start)*1000)


def idle_problem(arm, health, completed):
    queue = health.get('queue')
    if queue != dict(waiting=0, active=0, completed=completed):
        return f'{arm}: /health queue {queue} is not idle at completed={completed}'
    return None


def fresh_targets(session, arms, contract_sha, state):
    """Each arm's URL from its owned Pod now (creation UID, Running, Ready, podIP), its identity re-verified against /health
    and equal to the one verified at qualification readiness, its queue idle at this run's qualification call count."""
    qualified = json.loads((session.out/'identities.json').read_text())
    calls = Counter(json.loads(line)['arm'] for line in (session.out/'ledger.jsonl').read_text().splitlines())
    urls, identities = {}, {}
    for arm in arms:
        name = f'{session.run}-{arm}'
        if session.record(name) is None:
            raise RuntimeError(f'{arm}: no creation record for {name}')
        status = session.pod_state(name).get('status') or {}
        ready = next((c['status'] for c in status.get('conditions') or [] if c['type'] == 'Ready'), None)
        if status.get('phase') != 'Running' or ready != 'True' or not status.get('podIP'):
            raise RuntimeError(f"{arm}: owned Pod phase={status.get('phase')} Ready={ready} podIP={status.get('podIP')!r}")
        urls[arm] = f"http://{status['podIP']}:{P.PORT}"
        health = bounded_http.control(urls[arm]+'/health', timeout=30)
        session.save(f'benchmark-health-start-{arm}.json', health)
        identities[arm] = live.identity(Prefixed(session), arm, health, contract_sha)
        if identities[arm] != qualified.get(arm):
            raise RuntimeError(f'{arm}: identity differs from the one verified at qualification readiness')
        problem = idle_problem(arm, health, calls[arm])
        if problem:
            raise RuntimeError(problem)
        state['sequence_start'][arm] = state['sequence_last'][arm] = calls[arm]
    session.save('benchmark-targets.json', dict(urls=urls, sequence_start=state['sequence_start']))
    return urls, identities


def append(path, value):
    with path.open('a') as handle:
        handle.write(json.dumps(value)+'\n')
        handle.flush()
        os.fsync(handle.fileno())


def ledgered(path, measurement_id):
    """Whether the ledger's last row is already this measurement (an interrupt after append's write must not duplicate it)."""
    lines = path.read_text().splitlines() if path.exists() else []
    try:
        return bool(lines) and json.loads(lines[-1]).get('measurement_id') == measurement_id
    except ValueError:
        return False


def collect_benchmark(session, rows, urls, inputs, identities, state):
    """Sequential plan rows via live.call_arm; each reply (with measurement_id/round/pair) is appended + fsync'd before the
    next request. An ok receipt must carry the arm's next sequence; the first non-ok reply or gap stops the run. An interrupt
    (or any error) once the call has started is ledgered as `interrupted` — the request may have reached the worker, so it
    is attempted with an unknown outcome — with the partial collector time and any reply already received, then re-raised."""
    last, path = state['sequence_last'], session.out/LEDGER
    for index, row in enumerate(rows):
        arm, started_at, started, reply = row['arm'], utc(), time.monotonic(), None
        ids = dict(measurement_id=row['measurement_id'], round=row['round'], pair=row['pair'], plan_index=index, started_at=started_at)
        try:
            reply = live.call_arm(session, arm, urls[arm], row, inputs[row['input']], identities[arm])
            reply.update(ids, finished_at=utc())
            if reply.get('status') == 'ok':
                sequence = reply['receipt'].get('sequence')
                if sequence != last[arm] + 1:
                    reply.update(status='error', error=f'receipt sequence {sequence!r} is not the fresh next {last[arm]+1} of {arm}')
                else:
                    last[arm] = sequence
            append(path, reply)
        except BaseException as error:
            if not ledgered(path, row['measurement_id']):
                append(path, dict(reply or dict(arm=arm, input=row['input']), **ids, status='interrupted',
                                  outcome='unknown' if reply is None else 'reply received, not ledgered before the interrupt',
                                  reply_status=reply and reply.get('status'), error=repr(error)[:500], finished_at=utc(),
                                  collector_partial_ms=(time.monotonic()-started)*1000))
            raise
        if reply.get('status') != 'ok':
            try:
                health = bounded_http.control(urls[arm]+'/health', timeout=30)
            except Exception as error:
                health = dict(status='unavailable', error=repr(error)[:live.ERROR_KEEP])
            session.save(f'benchmark-health-after-failure-{arm}.json', health)
            raise RuntimeError(f"benchmark: {row['measurement_id']} ({index+1}/{len(rows)}) failed; remaining calls not executed")


def benchmark(session, arms, rows, timing, state):
    contract_sha = P.file_sha(session.frozen/'contract.json')
    manifest = json.loads((session.frozen/'assets-manifest.json').read_text())
    inputs = {row['id']: Path(row['source']).read_bytes() for row in manifest['inputs']}
    with timed(session, timing, 'benchmark-ready'):
        urls, identities = fresh_targets(session, arms, contract_sha, state)
        live.check_occupancy(session, 'occupancy-benchmark-start', arms)
    with timed(session, timing, 'benchmark'):
        collect_benchmark(session, rows, urls, inputs, identities, state)
    with timed(session, timing, 'benchmark-final'):
        live.check_occupancy(session, 'occupancy-benchmark-final', arms)
        for arm in arms:
            health = bounded_http.control(urls[arm]+'/health', timeout=30)
            session.save(f'benchmark-health-final-{arm}.json', health)
            problem = idle_problem(arm, health, state['sequence_last'][arm])
            if problem:
                raise RuntimeError(problem)


def summary(session, rows, arms, timing, state, error, started, started_at):
    """Counts from the durable benchmark ledger (qualification calls are the excluded warm-ups in ledger.jsonl)."""
    path = session.out/LEDGER
    ledger = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    ok = [r for r in ledger if r.get('status') == 'ok']
    interrupted = [r for r in ledger if r.get('status') == 'interrupted']
    failed = [r for r in ledger if r.get('status') not in ('ok', 'interrupted')]
    per_arm = {arm: dict(planned=sum(r['arm'] == arm for r in rows), completed=sum(r['arm'] == arm for r in ok),
                         failed=sum(r['arm'] == arm for r in failed), interrupted=sum(r['arm'] == arm for r in interrupted))
               for arm in arms}
    return dict(planned=len(rows), completed=len(ok), failed=len(failed), interrupted=len(interrupted),
                unattempted=len(rows)-len(ledger), per_arm=per_arm,
                stopped_at=[{k: r.get(k) for k in ('measurement_id', 'round', 'pair', 'arm', 'input', 'status', 'http_status', 'error')}
                            for r in ledger if r.get('status') != 'ok'],
                error=error, phases=timing, sequence_start=state['sequence_start'], sequence_last=state['sequence_last'],
                started_at=started_at, finished_at=utc(), duration_ms=(time.monotonic()-started)*1000, ledger=LEDGER,
                scope='repeat measurement after the qualification warm-ups of the same run; see cleanup.json for cleanup')


def cleanup(session, error):
    """live.main's cleanup block: UID-only delete of this run's records, absence verification, protected snapshot."""
    problems = ['cleanup did not run']
    try:
        with collect.phase(session, 'cleanup'):
            session.delete_owned('cleanup', [r['name'] for r in session.resources])
            problems = live.verify_cleanup(session)
            collect.protected_snapshot(session, 'protected-after')
    except BaseException as exc:
        problems.append(f'cleanup error {exc!r}'[:500])
    finally:
        session.save('cleanup.json', dict(records=session.resources, unresolved=session.unresolved, error=error,
                                          problems=problems, verified=not problems))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--frozen', required=True)
    ap.add_argument('--benchmark-plan', required=True)
    ap.add_argument('--runs')
    ap.add_argument('--release')
    ap.add_argument('--run-id', default='gmv-'+datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%S'))
    ap.add_argument('--print-release-draft', action='store_true')
    ap.add_argument('--arm', choices=tuple(P.ARMS), help='one arm only (arm-only measurement); default: the pair')
    ap.add_argument('--prepared')
    ap.add_argument('--cuda-cache-pvc')
    ap.add_argument('--io-profile', choices=(live.C.SD_PROFILE['name'],))
    a = ap.parse_args(argv)
    arms = (a.arm,) if a.arm else None
    frozen = Path(a.frozen)
    live_argv = ['--frozen', a.frozen] + [x for flag, value in (('--arm', a.arm), ('--prepared', a.prepared),
                                                                 ('--cuda-cache-pvc', a.cuda_cache_pvc), ('--io-profile', a.io_profile))
                                          if value for x in (flag, value)]
    manifest = json.loads((frozen/'assets-manifest.json').read_text())
    raw, full = load_plan(a.benchmark_plan)
    problems = plan_problems(full, tuple(P.ARMS), [r['id'] for r in manifest['inputs']])
    if problems:
        raise SystemExit('benchmark plan rejected: '+'; '.join(problems[:10]))
    selected, groups = live.selection(arms)
    bound, rows = binding(raw, full, selected)
    if a.print_release_draft:
        print(json.dumps(release_draft(live_argv, bound), indent=1))
        return
    if bool(a.prepared) != bool(a.cuda_cache_pvc) or (a.prepared and 'cuda' not in selected) or (a.io_profile and not a.prepared):
        raise SystemExit('--prepared and --cuda-cache-pvc go together and need the cuda arm; --io-profile needs --prepared')
    prepared = a.prepared and live.load_prepared(a.prepared, frozen, json.loads(Path(a.cuda_cache_pvc).read_text()),
                                                 io_profile_name=a.io_profile) or None
    release = json.loads(Path(a.release).read_text())
    problems = release_problems(release, frozen, arms, prepared, bound)
    if problems:
        raise SystemExit('release check failed: '+'; '.join(problems))
    session = live.Session(a.runs, a.run_id)
    session.frozen = frozen
    session.save('release.json', release)
    session.save('selection.json', dict(arms=list(selected), groups=sorted(groups), scope='pair' if selected == tuple(P.ARMS) else
                                        'arm-only measurement of ONE accelerator arm (its effective rows of the plan); not a paired comparison'))
    with (session.out/'benchmark-plan.json').open('xb') as handle:  # the exact approved bytes, before any request
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    session.save('benchmark-binding.json', dict(bound, plan_source=str(Path(a.benchmark_plan).resolve())))
    timing, state, error = {}, dict(sequence_start={}, sequence_last={}), None
    started, started_at = time.monotonic(), utc()
    try:
        with timed(session, timing, 'live-qualification'):
            live.execute(session, 'qualification', arms, prepared)
        benchmark(session, selected, rows, timing, state)
    except BaseException as exc:
        error = repr(exc)
        raise
    finally:
        try:
            session.save('benchmark-summary.json', summary(session, rows, selected, timing, state, error, started, started_at))
        finally:
            cleanup_problems = cleanup(session, error)
        if error is None and cleanup_problems:
            raise SystemExit('run not complete: '+'; '.join(cleanup_problems))


if __name__ == '__main__':
    main()
