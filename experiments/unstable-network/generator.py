"""Edge-origin request generator and DAG executor (one owned CPU Pod on an edge node; stdlib only).

Every request of a trace starts here, at the edge: the policy decides a plan from the frozen measured profile and the
current observation, then the two branches start concurrently as real chains
  origin -raw image-> image_preprocess -tensor-> vision -feature-> fusion_join
  origin -raw audio-> audio_preprocess -input_values-> speech -text-> fusion_join
over the routes written into each hop (direct Pod IP inside a zone, the owned shared gateway across zones). The fusion
answer returns to the origin as the HTTP response of both chains. Latency is measured on this Pod's monotonic clock
only, from the request's SCHEDULED arrival (selection, observation and transition are inside the boundary); hop rpc
times nest and are never summed.

Open loop: arrivals are fired at their scheduled offsets whether or not earlier requests finished. A request is dropped
at admission when max_inflight requests are open (admission_drop). Terminal states:
  ok (answer within deadline) | late_ok (answer after deadline, before cancel) | failed (a stage returned an error) |
  worker_rejected (a worker answered 503 admission) | timeout (no answer before cancel_s) | admission_drop |
  not_sent (trace stopped before its scheduled time) | decision_error
HTTP (control from the collector; data never from the collector):
  GET  /ready /status  /ledger?label=L[&partial=1]
  POST /configure {run_id, workers{key:{url,zone,node,pod_uid}}, gateway_url, origin_url, origin_zone, candidates, poll_s}
       /corpus {cache_url, files[{question_id, kind, path, sha256}]}   /profile {...}  /probes {...}
       /trace {label, policy, arrivals[{index, offset_s, question_id}], deadline_s, cancel_s, max_inflight, profile, quality}
       /stop   /run (mode=sink only: probe sink for worker->origin routes)

unstable-network copy (source experiments/quality-network-baselines/generator.py, sha256 in the plan's copied_sources).
Changes, nothing else:
  - poll_once records every worker poll as success (state + time) or error (error + time) in policies.Observation; a
    failed poll is never written as an empty queue and never refreshes the success time.
  - /trace accepts exactly TRACE_KEYS (a network trace, phase table or any future condition is refused with 400), the
    observation contract {stale_after_s} for every policy, and start_delay_s: t0 = this Pod's receipt time + start_delay_s
    (the collector aligns the netctl schedule to it from its own round trips; no cross-host clock is subtracted).
  - every row records the per-worker observation (ages, last error) and its summary for every policy; the ledger
    records received_local / t0_local / start_delay_s / observation_contract (this Pod's monotonic clock).
  - (R1) /ledger?label=L&partial=1 salvages a trace that did not stop: a marked snapshot (partial=true, state running)
    with a row per scheduled arrival (status None while pending). A plain /ledger still refuses a running trace, and
    every ledger now carries `state` and `partial`.
"""
import base64
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import policies  # noqa: E402
from bounded_http import DeadlineExceeded, exchange, post as bounded_post  # noqa: E402
from http_runtime import decode_body, payload  # noqa: E402

TERMINAL = ('ok', 'late_ok', 'failed', 'worker_rejected', 'timeout', 'admission_drop', 'not_sent', 'decision_error')
MAX_TRACE_REQUESTS = 5000
TRACE_KEYS = frozenset({'label', 'policy', 'arrivals', 'deadline_s', 'cancel_s', 'max_inflight', 'profile', 'quality',
                        'observation_contract', 'start_delay_s'})
MAX_START_DELAY_S = 10.0
READ_DEADLINE_S = 90.0  # absolute bound for reading one incoming request (line + headers + body)
FUTURE_SLACK_S = 5.0    # a branch future past its absolute budget + slack is a timeout, never an unbounded wait
CORPUS_BUDGET_S = 590.0  # whole /corpus load when the collector sends none (its own call is bounded at 600 s)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fetch(url, expected, timeout=120, limit=64 * 1024 * 1024):
    """One asset GET bounded by an absolute deadline (bounded_http: connect .. last byte; the connection is closed on
    expiry) and a body cap; the bytes must hash to `expected`."""
    status, data, _ = exchange(url, None, None, timeout, max_body=limit)
    if status != 200 or digest(data) != expected:
        raise ValueError(f'{url}: status {status} or sha256 mismatch')
    return data


class Generator:
    def __init__(self, post=None, clock=time.monotonic, sleep=time.sleep, fetcher=fetch):
        self.post, self.clock, self.sleep, self.fetcher = post or bounded_post, clock, sleep, fetcher
        self.lock = threading.Lock()
        self.config, self.corpus, self.traces, self.jobs = None, {}, {}, {}
        self.observation, self.poll_stats, self.stop_flag = policies.Observation(), dict(polls=0, errors=0, request_bytes=0), threading.Event()
        self.node, self.pod_uid = os.environ.get('NODE_NAME'), os.environ.get('POD_UID')
        self.poller, self.closed = None, threading.Event()

    # ------------------------------------------------------------------ configuration
    def configure(self, body):
        workers = body['workers']
        for key, w in workers.items():
            if not w.get('url') or w.get('zone') not in ('edge', 'cloud') or not w.get('pod_uid'):
                raise ValueError(f'worker {key}: url, zone and pod_uid are required')
        candidates = body['candidates']
        if set(candidates) != set(policies.TASKS) or any(w not in workers for ws in candidates.values() for w in ws):
            raise ValueError('candidates must name every task and only configured workers')
        self.config = dict(run_id=body['run_id'], workers=workers, gateway_url=body.get('gateway_url'), origin_zone=body.get('origin_zone', 'edge'),
                           origin_url=body.get('origin_url'),
                           candidates=candidates, poll_s=float(body.get('poll_s', 1.0)), poll_timeout_s=float(body.get('poll_timeout_s', 0.5)))
        self.uid_to_key = {w['pod_uid']: k for k, w in workers.items()}
        if self.poller is None:
            self.poller = threading.Thread(target=self.poll_loop, daemon=True)
            self.poller.start()
        return dict(status='configured', workers=sorted(workers), node=self.node, pod_uid=self.pod_uid)

    def zone(self, key):
        return self.config['origin_zone'] if key == policies.ORIGIN else self.config['workers'][key]['zone']

    def route(self, src, dst, force_direct=False):
        """URL for src -> dst. Cross-zone traffic must use the shared gateway; refusing is safer than a silent bypass.
        force_direct is used only by the relay-overhead control probe (unshaped, labelled 'direct_control')."""
        same = self.zone(src) == self.zone(dst)
        if same or force_direct:
            if dst == policies.ORIGIN:
                if not self.config.get('origin_url'):
                    raise ValueError('origin_url not configured')
                return self.config['origin_url'] + '/run', 'direct' if same else 'direct_control'
            return self.config['workers'][dst]['url'] + '/run', 'direct' if same else 'direct_control'
        if not self.config.get('gateway_url'):
            raise ValueError(f'{src}->{dst} crosses zones and no gateway is configured')
        return self.config['gateway_url'] + '/to/' + dst + '/run', 'gateway'

    def hop(self, src, dst, stage, input_kind='raw', part=None):
        url, route = self.route(src, dst)
        out = dict(url=url, route=route, src=src, dst=dst, stage=stage, input=input_kind)
        if part:
            out['part'] = part
        return out

    def load_corpus(self, body):
        """Every file fetch gets min(120 s, the remaining whole-load budget): the load ends inside the collector's call."""
        loaded, until = 0, self.clock() + float(body.get('budget_s', CORPUS_BUDGET_S))
        for item in body['files']:
            remaining = until - self.clock()
            if remaining <= 0:
                raise DeadlineExceeded(float(body.get('budget_s', CORPUS_BUDGET_S)))
            data = self.fetcher(body['cache_url'] + '/' + item['path'], item['sha256'], timeout=min(120.0, remaining))
            self.corpus.setdefault(str(item['question_id']), {})[item['kind']] = data
            loaded += 1
        return dict(status='ok', files=loaded, questions=len(self.corpus))

    # ------------------------------------------------------------------ observation (same poller for every policy)
    def poll_once(self):
        if not self.config:
            return
        for key in self.config['workers']:
            url, _ = self.route(policies.ORIGIN, key)
            try:
                reply = self.post(url, dict(mode='queue', run_id=self.config['run_id'], request_id='poll'), timeout=self.config['poll_timeout_s'])
                state = dict(waiting=int(reply['waiting']), active=int(reply['active']), joins_open=int(reply.get('joins_open', 0)))
                self.observation.record_poll(key, state, None, self.clock())
            except Exception as error:
                self.observation.record_poll(key, None, repr(error)[:120], self.clock())
                self.poll_stats['errors'] += 1
            self.poll_stats['polls'] += 1

    def poll_loop(self):
        while not self.closed.is_set():
            started = self.clock()
            try:
                self.poll_once()
            except Exception:
                self.poll_stats['errors'] += 1
            self.closed.wait(max(0.05, self.config['poll_s'] - (self.clock() - started)))

    def close(self):
        """Stops the observation poller (tests / orderly shutdown); a running trace is stopped first."""
        self.stop_flag.set()
        self.closed.set()
        if self.poller is not None:
            self.poller.join(timeout=5)

    # ------------------------------------------------------------------ one request
    def chains(self, plan, request_id, question, budget_s):
        run_id = self.config['run_id']
        image = dict(mode='dag', run_id=run_id, request_id=request_id, stage='image_preprocess', input='raw', budget_s=budget_s,
                     data=self.corpus[question]['image'],
                     forward=[self.hop(plan['image_preprocess'], plan['vision'], 'vision', 'tensor'),
                              self.hop(plan['vision'], plan['fusion'], 'fusion_join', 'raw', 'vision')])
        audio = dict(mode='dag', run_id=run_id, request_id=request_id, stage='audio_preprocess', input='raw', budget_s=budget_s,
                     data=self.corpus[question]['audio'],
                     forward=[self.hop(plan['audio_preprocess'], plan['speech'], 'speech', 'tensor'),
                              self.hop(plan['speech'], plan['fusion'], 'fusion_join', 'raw', 'speech')])
        first = dict(image=self.hop(policies.ORIGIN, plan['image_preprocess'], 'image_preprocess'),
                     audio=self.hop(policies.ORIGIN, plan['audio_preprocess'], 'audio_preprocess'))
        return dict(image=(first['image'], image), audio=(first['audio'], audio))

    def branch(self, first, body, timeout):
        sent = self.clock()
        out = dict(url_route=first['route'], src=first['src'], dst=first['dst'], payload_bytes=len(body['data']), sent_at=sent)
        try:
            reply = self.post(first['url'], body, timeout=timeout)
            out.update(status='ok' if reply.get('status') == 'ok' else 'failed', reply=reply)
        except urllib.error.HTTPError as error:
            text = error.read(8000).decode(errors='replace')
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = dict(error=text[:500])
            # 503 'admission capacity' = worker admission cap; 429 = shared gateway relay slots exhausted
            # 504 = an inner budget expired (forward_timeout / join_timeout / gateway upstream timeout): a cancel-budget timeout
            rejected = (error.code == 503 and 'admission' in text) or error.code == 429
            out.update(status='worker_rejected' if rejected else ('timeout' if error.code == 504 else 'failed'), code=error.code, reply=parsed)
        except Exception as error:
            timed_out = 'timed out' in repr(error).lower() or isinstance(error, TimeoutError)
            out.update(status='timeout' if timed_out else 'failed', error=repr(error)[:300])
        out['done_at'] = self.clock()
        out['rpc_ms'] = (out['done_at'] - sent) * 1000
        return out

    def execute(self, trace, arrival, decision, t_sched, branch_pool):
        row = trace['rows'][arrival['index']]
        cancel_at = t_sched + trace['cancel_s']
        budget = cancel_at - self.clock()
        if budget <= 0:
            row.update(status='timeout', error='cancel budget exhausted before send')
            return
        chains = self.chains(decision['plan'], row['request_id'], str(arrival['question_id']), budget)
        row['send_offset_s'] = self.clock() - trace['t0']
        jobs = {k: branch_pool.submit(self.branch, first, body, budget) for k, (first, body) in chains.items()}
        branches = {}
        for k, j in jobs.items():
            try:
                branches[k] = j.result(timeout=max(0.0, cancel_at - self.clock()) + FUTURE_SLACK_S)
            except FutureTimeout:
                first = chains[k][0]
                branches[k] = dict(url_route=first['route'], src=first['src'], dst=first['dst'], status='timeout',
                                   error='branch exceeded its absolute budget', done_at=self.clock(), rpc_ms=None)
        done = max(b['done_at'] for b in branches.values())
        row['completed_offset_s'] = done - trace['t0']
        row['latency_ms'] = (done - t_sched) * 1000
        row['branches'] = {k: {kk: vv for kk, vv in b.items() if kk not in ('sent_at', 'done_at')} for k, b in branches.items()}
        statuses = {b['status'] for b in branches.values()}
        if statuses == {'ok'}:
            datas = {b['reply'].get('data') for b in branches.values()}
            if len(datas) != 1 or None in datas:
                row.update(status='failed', error='branch answers differ or are missing')
                return
            answer = json.loads(base64.b64decode(datas.pop()))
            row.update(answer=answer['answers'][0], answer_ids=answer['answer_ids'], output_sha256=digest(json.dumps(answer, separators=(',', ':')).encode()))
            met = row['latency_ms'] <= trace['deadline_s'] * 1000
            row.update(status='ok' if met else 'late_ok', deadline_met=met)
            self.learn(trace, branches)
        else:
            for state in ('worker_rejected', 'failed', 'timeout'):  # an explicit cause wins over a sibling branch's timeout
                if state in statuses:
                    row['status'] = state
                    break
            failed = [b for b in branches.values() if b['status'] != 'ok']
            row['error_stage'] = next(((b.get('reply') or {}).get('failed_stage') or (b.get('reply') or {}).get('stage') for b in failed), None)
            row['deadline_met'] = False

    def learn(self, trace, branches):
        """Observation update from a completed request (receipt compute per worker and per-hop transfer estimates)."""
        obs = trace['observation']
        profile = trace['profile']
        for key, b in branches.items():
            receipts, hops = b['reply'].get('receipts', []), b['reply'].get('hops', [])
            for r in receipts:
                worker = self.uid_to_key.get(r.get('pod_uid'))
                if worker and r.get('worker_wall_ms') is not None:
                    obs.record_receipt(r['stage'], worker, float(r['worker_wall_ms']))
            for est in hop_transfers(b, receipts, hops):
                link = profile.links.get((est['src'], est['dst'])) or {}
                obs.record_transfer(est['src'], est['dst'], est['transfer_ms'], est['payload_bytes'], float(link.get('latency_ms', 0.0)))

    # ------------------------------------------------------------------ trace (open loop)
    def start_trace(self, body):
        if not self.config:
            raise ValueError('not configured')
        unknown = set(body) - TRACE_KEYS
        if unknown:
            raise ValueError(f'trace body carries keys outside the generator contract: {sorted(unknown)}')
        received = self.clock()
        delay = float(body['start_delay_s'])
        stale_after_s = float(body['observation_contract']['stale_after_s'])
        if not 0 < delay <= MAX_START_DELAY_S or not stale_after_s > 0:
            raise ValueError('start_delay_s must be in (0, 10] and stale_after_s > 0')
        label = body['label']
        arrivals = sorted(body['arrivals'], key=lambda a: a['offset_s'])
        if len(arrivals) > MAX_TRACE_REQUESTS or [a['index'] for a in arrivals] != list(range(len(arrivals))):
            raise ValueError('arrivals must be indexed 0..n-1 in offset order and bounded')
        missing = {str(a['question_id']) for a in arrivals} - {q for q, v in self.corpus.items() if {'image', 'audio'} <= set(v)}
        if missing:
            raise ValueError(f'corpus lacks {sorted(missing)[:5]}')
        with self.lock:
            if label in self.traces or any(t['state'] == 'running' for t in self.traces.values()):
                raise ValueError('trace label reused or another trace running')
            profile = policies.Profile.from_json(body['profile'])
            gate = policies.QualityGate(body['quality']['mode'], body['quality']['qmin'], body['quality'].get('evidence'))
            policy = policies.build(body['policy'], self.config['candidates'], gate, profile, stale_after_s=stale_after_s)
            # Receipt estimates restart per trace; the per-worker poll states (last success, last error) carry over, so
            # every policy's first decision sees the same current observation (never an empty one depending on poll phase).
            previous, self.observation = self.observation, policies.Observation()
            self.observation.carry_polls(previous)
            trace = dict(label=label, state='running', policy=policy, policy_spec=body['policy'], profile=profile, gate=gate,
                         observation=self.observation, stale_after_s=stale_after_s, received_local=received, start_delay_s=delay,
                         t0=received + delay, deadline_s=float(body['deadline_s']), cancel_s=float(body['cancel_s']),
                         max_inflight=int(body['max_inflight']), arrivals=arrivals, inflight=0, max_seen_inflight=0,
                         rows=[dict(index=a['index'], request_id=f"{label}-{a['index']}", question_id=a['question_id'],
                                    scheduled_offset_s=float(a['offset_s']), status=None) for a in arrivals],
                         poll_before=dict(self.poll_stats))
            self.traces[label] = trace
        self.stop_flag.clear()
        threading.Thread(target=self.run_trace, args=(trace,), daemon=True).start()
        return dict(status='started', label=label, requests=len(arrivals))

    def run_trace(self, trace):
        cap = trace['max_inflight']
        try:
            with ThreadPoolExecutor(max_workers=cap) as pool, ThreadPoolExecutor(max_workers=2 * cap) as branch_pool:
                t0 = trace['t0']
                futures = []
                for arrival in trace['arrivals']:
                    row = trace['rows'][arrival['index']]
                    t_sched = t0 + arrival['offset_s']
                    while not self.stop_flag.is_set() and self.clock() < t_sched:
                        self.sleep(min(0.02, max(0.0, t_sched - self.clock())))
                    if self.stop_flag.is_set():
                        row['status'] = 'not_sent'
                        continue
                    row['dispatch_lag_ms'] = (self.clock() - t_sched) * 1000
                    with self.lock:
                        if trace['inflight'] >= cap:
                            row.update(status='admission_drop', inflight_at_arrival=trace['inflight'])
                            continue
                        trace['inflight'] += 1
                        trace['max_seen_inflight'] = max(trace['max_seen_inflight'], trace['inflight'])
                        row['inflight_at_arrival'] = trace['inflight'] - 1
                    futures.append(pool.submit(self.handle, trace, arrival, t_sched, branch_pool))
                for f in futures:
                    f.result()
            trace['state'] = 'done'
        except Exception as error:
            trace.update(state='error', error=repr(error)[:500])
        trace['finished_offset_s'] = self.clock() - trace.get('t0', self.clock())
        trace['poll_after'] = dict(self.poll_stats)

    def handle(self, trace, arrival, t_sched, branch_pool):
        row = trace['rows'][arrival['index']]
        try:
            obs = trace['observation']
            row['observation'] = obs.to_json(self.clock(), trace['stale_after_s'])  # recorded for every policy
            try:
                decision = trace['policy'].decide(obs, self.clock())
            except Exception as error:
                row.update(status='decision_error', error=repr(error)[:300])
                return
            row['decision'] = dict(decision)
            self.execute(trace, arrival, decision, t_sched, branch_pool)
        except Exception as error:
            row.update(status='failed', error=repr(error)[:300])
        finally:
            with self.lock:
                trace['inflight'] -= 1

    def status(self):
        out = dict(configured=bool(self.config), node=self.node, pod_uid=self.pod_uid, corpus=len(self.corpus), poll=dict(self.poll_stats),
                   jobs={k: {kk: vv for kk, vv in v.items() if kk != 'result'} for k, v in self.jobs.items()}, traces={})
        for label, t in self.traces.items():
            counts = {}
            for r in t['rows']:
                counts[r['status']] = counts.get(r['status'], 0) + 1
            out['traces'][label] = dict(state=t['state'], counts=counts, inflight=t['inflight'], error=t.get('error'))
        return out

    def ledger(self, label, partial=False):
        t = self.traces[label]
        running = t['state'] == 'running'
        if running and not partial:
            raise ValueError('trace still running')
        rows = snapshot(t['rows']) if running else t['rows']
        return dict(label=label, state=t['state'], partial=running, policy=t['policy_spec'], quality_gate=t['gate'].label(), deadline_s=t['deadline_s'],
                    cancel_s=t['cancel_s'], max_inflight=t['max_inflight'], max_seen_inflight=t['max_seen_inflight'],
                    finished_offset_s=t.get('finished_offset_s'), poll_before=t['poll_before'], poll_after=t.get('poll_after'),
                    profile_source=t['profile'].source, generator=dict(node=self.node, pod_uid=self.pod_uid), rows=rows,
                    final_observation=t['observation'].to_json(self.clock(), t['stale_after_s']), error=t.get('error'),
                    received_local=t['received_local'], t0_local=t['t0'], start_delay_s=t['start_delay_s'],
                    observation_contract=dict(stale_after_s=t['stale_after_s']))

    # ------------------------------------------------------------------ calibration jobs (closed loop, sequential)
    def job(self, name, fn, body):
        with self.lock:
            if name in self.jobs and self.jobs[name]['state'] == 'running':
                raise ValueError(f'job {name} running')
            self.jobs[name] = dict(state='running')

        def target():
            try:
                self.jobs[name].update(state='done', result=fn(body))
            except Exception as error:
                self.jobs[name].update(state='error', error=repr(error)[:500])
        threading.Thread(target=target, daemon=True).start()
        return dict(status='started', job=name)

    def call_stage(self, worker, stage, data, input_kind='raw', part=None, request_id='profile', timeout=120):
        url, route = self.route(policies.ORIGIN, worker)
        body = dict(mode='dag', run_id=self.config['run_id'], request_id=request_id, stage=stage, input=input_kind, data=data, forward=[],
                    budget_s=timeout, **({'part': part} if part else {}))
        sent = self.clock()
        reply = self.post(url, body, timeout=timeout)
        return reply, (self.clock() - sent) * 1000, route

    def profile(self, body):
        """Closed-loop per-candidate compute profile. Inputs for model/fusion tasks are produced once by the configured
        reference producers (body['producers']); the first `warmup` calls per candidate are excluded and kept."""
        qids, warmup = [str(q) for q in body['question_ids']], int(body.get('warmup', 1))
        producers, rows, inputs, prepared = body['producers'], [], {}, {}
        order = [('image_preprocess', 'image'), ('audio_preprocess', 'audio'), ('vision', 'image_tensor'), ('speech', 'input_values'), ('fusion', None)]
        for task, kind in order:
            for worker in self.config['candidates'][task]:
                for n, q in enumerate(([qids[0]] * warmup) + qids):
                    row = dict(task=task, worker=worker, question_id=q, warmup=n < warmup, request_id=f'profile-{task}-{worker}-{n}')
                    try:
                        if task == 'fusion':
                            reply, rpc_ms, route = self.fuse(worker, dict(vision=inputs[('vision', q)], speech=inputs[('speech', q)]),
                                                             row['request_id'])
                        else:
                            data = self.corpus[q][kind] if task in policies.PREPROCESS else inputs[(kind, q)]
                            reply, rpc_ms, route = self.call_stage(worker, task, data, 'raw' if task in policies.PREPROCESS else 'tensor',
                                                                   request_id=row['request_id'])
                        receipt = (reply.get('receipts') or [None])[-1]
                        out = base64.b64decode(reply['data'])
                        row.update(status=reply.get('status'), rpc_ms=rpc_ms, route=route, receipt=receipt, output_bytes=len(out),
                                   output_sha256=digest(out))
                        if task in ('vision', 'speech', 'fusion'):
                            row['output_b64'] = reply['data']
                        elif not row['warmup'] and row['status'] == 'ok':
                            prepared[(task, worker, q)] = out  # kept in memory for the preprocessing-candidate check
                        if worker == producers.get(task) and not row['warmup']:
                            produced = {'image_preprocess': 'image_tensor', 'audio_preprocess': 'input_values', 'vision': 'vision', 'speech': 'speech'}.get(task)
                            if produced:
                                inputs[(produced, q)] = out
                    except Exception as error:
                        row.update(status='error', error=repr(error)[:300])
                    rows.append(row)
        compute, sizes = {}, {}
        for task, _ in order:
            for worker in self.config['candidates'][task]:
                walls = [r['receipt']['worker_wall_ms'] for r in rows if r['task'] == task and r['worker'] == worker and not r['warmup']
                         and r.get('status') == 'ok' and r.get('receipt')]
                if walls:
                    compute[f'{task}@{worker}'] = statistics.median(walls)
        kinds = {'image_preprocess': 'image_tensor', 'audio_preprocess': 'input_values', 'vision': 'feature', 'speech': 'text', 'fusion': 'answer'}
        for task, kind in kinds.items():
            values = [r['output_bytes'] for r in rows if r['task'] == task and r.get('status') == 'ok' and not r['warmup']]
            if values:
                sizes[kind] = statistics.median(values)
        sizes['image'] = statistics.median(len(self.corpus[q]['image']) for q in qids)
        sizes['audio'] = statistics.median(len(self.corpus[q]['audio']) for q in qids)
        original_rows, equivalence = self.equivalence(qids, producers, rows, inputs)
        return dict(rows=rows, compute_ms=compute, payload_bytes=sizes, question_ids=qids, warmup=warmup,
                    original_rows=original_rows, equivalence=equivalence,
                    preprocess_equivalence=self.preprocessing(qids, producers, rows, inputs, prepared, equivalence))

    def fuse(self, worker, parts, request_id):
        with ThreadPoolExecutor(2) as pool:
            jobs = {p: pool.submit(self.call_stage, worker, 'fusion_join', d, 'raw', p, request_id) for p, d in parts.items()}
            replies = {p: j.result() for p, j in jobs.items()}
        return replies['speech']

    def equivalence(self, qids, producers, rows, inputs):
        """Split-vs-original check on the calibration questions for every vision/speech candidate: the candidate's
        unchanged original path (raw input, in-stage preprocessing) runs on the same worker, then the reference fusion
        worker fuses (candidate split output + reference split partner) and (candidate original output + reference
        original partner); the final top answers must be equal. Feature/text byte equality is recorded, not required."""
        split = {(r['task'], r['worker'], r['question_id']): base64.b64decode(r['output_b64']) for r in rows
                 if r['task'] in ('vision', 'speech') and not r['warmup'] and r.get('status') == 'ok' and r.get('output_b64')}
        original_rows, originals = [], {}
        for task, kind in (('vision', 'image'), ('speech', 'audio')):
            for worker in self.config['candidates'][task]:
                for n, q in enumerate(qids):
                    row = dict(task=task, worker=worker, question_id=q, path='original', request_id=f'profile-orig-{task}-{worker}-{n}')
                    try:
                        reply, rpc_ms, route = self.call_stage(worker, task, self.corpus[q][kind], 'raw', request_id=row['request_id'])
                        out = base64.b64decode(reply['data'])
                        row.update(status=reply.get('status'), rpc_ms=rpc_ms, route=route, receipt=(reply.get('receipts') or [None])[-1],
                                   output_sha256=digest(out))
                        originals[(task, worker, q)] = out
                    except Exception as error:
                        row.update(status='error', error=repr(error)[:300])
                    original_rows.append(row)
        equivalence = []
        for task, other in (('vision', 'speech'), ('speech', 'vision')):
            for worker in self.config['candidates'][task]:
                for n, q in enumerate(qids):
                    row = dict(task=task, worker=worker, question_id=q, fusion_worker=producers['fusion'], answer_equal=False)
                    try:
                        answers = {}
                        for path, parts in (('split', {task: split[(task, worker, q)], other: inputs[(other, q)]}),
                                            ('original', {task: originals[(task, worker, q)], other: originals[(other, producers[other], q)]})):
                            rid = f'profile-eq-{path}-{task}-{worker}-{n}'
                            reply, _, _ = self.fuse(producers['fusion'], parts, rid)
                            answers[path] = json.loads(base64.b64decode(reply['data']))['answers'][0]
                            row[path+'_request_id'], row[path+'_receipt'] = rid, (reply.get('receipts') or [None])[-1]
                        row.update(split_answer=answers['split'], original_answer=answers['original'],
                                   answer_equal=answers['split'] == answers['original'],
                                   output_bytes_equal=digest(split[(task, worker, q)]) == digest(originals[(task, worker, q)]))
                    except Exception as error:
                        row['error'] = repr(error)[:300]
                    equivalence.append(row)
        return original_rows, equivalence

    def preprocessing(self, qids, producers, rows, inputs, prepared, equivalence):
        """Every non-reference image/audio preprocessing candidate on every calibration question. Proof 1, bytes_identical:
        its output equals the reference producer's (sha256, size, receipt shape/dtype), whose tensors fed every model
        candidate's split-vs-original check, so no model call is repeated. Otherwise proof 2, downstream_answer: the
        reference model worker consumes the candidate's tensor and the reference fusion worker fuses it with the reference
        partner; the final top answer must equal the reference model's original-path answer (equivalence). Neither -> not
        qualified (answer_equal False, evidence kept)."""
        done = {(r['task'], r['worker'], r['question_id']): r for r in rows if not r['warmup'] and r.get('status') == 'ok'}
        original = {(r['task'], r['worker'], r['question_id']): r.get('original_answer') for r in equivalence
                    if r.get('fusion_worker') == producers['fusion'] and 'original_answer' in r}
        out = []
        for task, model, other in (('image_preprocess', 'vision', 'speech'), ('audio_preprocess', 'speech', 'vision')):
            ref = producers[task]
            for worker in [w for w in self.config['candidates'][task] if w != ref]:
                for n, q in enumerate(qids):
                    row = dict(task=task, worker=worker, question_id=q, reference=ref, bytes_identical=False, proof=None, answer_equal=False)
                    cand, base = done.get((task, worker, q)), done.get((task, ref, q))
                    shape = lambda r: {k: ((r.get('receipt') or {}).get('timing') or {}).get(k) for k in ('output_shape', 'output_dtype')}
                    if cand and base and (cand['output_sha256'], cand['output_bytes'], shape(cand)) == \
                            (base['output_sha256'], base['output_bytes'], shape(base)):
                        row.update(bytes_identical=True, proof='bytes_identical', answer_equal=True)
                        out.append(row)
                        continue
                    try:
                        mid = f'profile-pre-{task}-{worker}-{n}'
                        reply, _, _ = self.call_stage(producers[model], model, prepared[(task, worker, q)], 'tensor', request_id=mid)
                        row.update(model_worker=producers[model], model_request_id=mid, model_receipt=(reply.get('receipts') or [None])[-1])
                        fid = f'profile-pre-eq-{task}-{worker}-{n}'
                        fused, _, _ = self.fuse(producers['fusion'], {model: base64.b64decode(reply['data']), other: inputs[(other, q)]}, fid)
                        answer = json.loads(base64.b64decode(fused['data']))['answers'][0]
                        expected = original[(model, producers[model], q)]
                        row.update(fusion_worker=producers['fusion'], fusion_request_id=fid, fusion_receipt=(fused.get('receipts') or [None])[-1],
                                   answer=answer, original_answer=expected, proof='downstream_answer', answer_equal=answer == expected)
                    except Exception as error:
                        row['error'] = repr(error)[:300]
                    out.append(row)
        return out

    def probes(self, body):
        sizes, reps, timeout = body.get('sizes', [4096, 1048576]), int(body.get('reps', 3)), float(body.get('timeout_s', 60))
        results = {}
        for src, dst in body['pairs']:
            url, route = self.route(src, dst, force_direct=bool(body.get('force_direct')))
            medians, raw = [], []
            for size in sizes:
                if src == policies.ORIGIN:
                    rows = []
                    data = os.urandom(size)
                    for _ in range(reps):
                        sent = self.clock()
                        try:
                            reply = self.post(url, dict(mode='sink', run_id=self.config['run_id'], request_id='probe', data=data), timeout=timeout)
                            rows.append(dict(rtt_ms=(self.clock()-sent)*1000, ok=reply.get('sha256') == digest(data)))
                        except Exception as error:
                            rows.append(dict(rtt_ms=(self.clock()-sent)*1000, ok=False, error=repr(error)[:200]))
                else:
                    src_url, _ = self.route(policies.ORIGIN, src)
                    reply = self.post(src_url, dict(mode='probe', run_id=self.config['run_id'], request_id='probe', url=url, bytes=size, reps=reps,
                                                    timeout_s=timeout), timeout=timeout * reps + 10)
                    rows = reply['rows']
                raw.append(dict(bytes=size, rows=rows))
                good = [r['rtt_ms'] for r in rows if r.get('ok')]
                medians.append((size, statistics.median(good)) if len(good) == len(rows) else None)
            key = f'{src}->{dst}'
            results[key] = dict(route=route, raw=raw, fit=policies.link_fit(*medians) if None not in medians and len(medians) == 2 else None,
                                complete=None not in medians)
        return dict(links=results, sizes=sizes, reps=reps)

    def sink(self, body):
        data = payload(body['data'])
        return dict(status='ok', bytes=len(data), sha256=digest(data))


def snapshot(value, attempts=5):
    """JSON copy of rows that request threads may still be updating (a concurrent dict change raises RuntimeError)."""
    for _ in range(attempts):
        try:
            return json.loads(json.dumps(value, default=str))
        except RuntimeError:
            continue
    raise ValueError('partial ledger kept changing during the snapshot; retry')


def hop_transfers(branch, receipts, hops):
    """Per-hop transfer estimates of one completed branch: sender rpc minus everything the receiver reported doing.
    origin leg: branch rpc - (r0 queue+wall + rpc(h0)); h_i: rpc(h_i) - (r_{i+1} queue+wall + rpc(h_{i+1})); the join leg
    subtracts the join wait (role first) or the fusion queue+wall (role second). Estimates only (HTTP overhead included)."""
    out = []
    join = (branch.get('reply') or {}).get('join') or {}
    def work(r):
        return float(r.get('queue_wait_ms') or 0) + float(r.get('worker_wall_ms') or 0)
    stage_receipts = [r for r in receipts if r.get('stage') != 'fusion']
    fusion = next((r for r in receipts if r.get('stage') == 'fusion'), None)
    if stage_receipts:
        down = work(stage_receipts[0]) + (float(hops[0]['rpc_ms']) if hops else 0.0)
        out.append(dict(src=branch['src'], dst=branch['dst'], transfer_ms=branch['rpc_ms'] - down, payload_bytes=branch['payload_bytes']))
    for i, h in enumerate(hops):
        if h.get('rpc_ms') is None:
            continue
        if h['stage'] == 'fusion_join':
            down = float(join.get('waited_ms') or 0) if join.get('role') == 'first' else (work(fusion) if fusion else 0.0)
        else:
            nxt = stage_receipts[i + 1] if i + 1 < len(stage_receipts) else None
            down = (work(nxt) if nxt else 0.0) + (float(hops[i + 1]['rpc_ms']) if i + 1 < len(hops) and hops[i + 1].get('rpc_ms') is not None else 0.0)
        out.append(dict(src=h['src'], dst=h['dst'], transfer_ms=float(h['rpc_ms']) - down, payload_bytes=h['payload_bytes']))
    return out


def serve(gen, host='0.0.0.0', port=8080):
    class Handler(BaseHTTPRequestHandler):
        timeout = 60

        def setup(self):
            super().setup()
            self.read_done = threading.Event()
            self.read_timer = threading.Timer(READ_DEADLINE_S, self._expire)
            self.read_timer.daemon = True
            self.read_timer.start()

        def _expire(self):
            if not self.read_done.is_set():
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        def finish(self):
            self.read_timer.cancel()
            try:
                super().finish()
            except OSError:
                pass

        def _send(self, status, value):
            raw = json.dumps(value, separators=(',', ':'), default=str).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.read_done.set()
            url = urllib.parse.urlparse(self.path)
            try:
                if url.path == '/ready':
                    self._send(200, dict(ready=True))
                elif url.path == '/status':
                    self._send(200, gen.status())
                elif url.path == '/ledger':
                    query = urllib.parse.parse_qs(url.query)
                    self._send(200, gen.ledger(query['label'][0], partial=query.get('partial') == ['1']))
                elif url.path == '/job':
                    name = urllib.parse.parse_qs(url.query)['name'][0]
                    self._send(200, gen.jobs[name])
                else:
                    self._send(404, dict(error='unknown path'))
            except (KeyError, ValueError) as error:
                self._send(400, dict(error=repr(error)[:300]))

        def do_POST(self):
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 32 * 1024 * 1024:
                    raise ValueError('body size')
                raw = self.rfile.read(size)
                self.read_done.set()
                if len(raw) != size:
                    return  # cut by the absolute read deadline or the client went away
                body = decode_body(raw, self.headers.get('Content-Type'))
                routes = {'/configure': gen.configure, '/corpus': gen.load_corpus, '/trace': gen.start_trace,
                          '/profile': lambda b: gen.job('profile', gen.profile, b), '/probes': lambda b: gen.job(b.get('job', 'probes'), gen.probes, b),
                          '/stop': lambda b: (gen.stop_flag.set(), dict(status='stopping'))[1], '/run': gen.sink}
                if self.path not in routes:
                    raise KeyError(self.path)
                self._send(200, routes[self.path](body))
            except (KeyError, ValueError, TypeError) as error:
                self._send(400, dict(error=repr(error)[:500]))
            except Exception as error:
                self._send(500, dict(error=repr(error)[:500]))

        def log_message(self, *args):
            pass
    return ThreadingHTTPServer((host, port), Handler)


def main():
    gen = Generator()
    server = serve(gen, port=int(os.environ.get('GENERATOR_PORT', '8080')))
    print(json.dumps(dict(event='generator_ready', node=gen.node, pod_uid=gen.pod_uid)), flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
