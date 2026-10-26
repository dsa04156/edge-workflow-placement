"""DAG-mode worker for the baseline-first comparison: the qualification Worker plus real stage-to-stage forwarding.

Shipped next to the qualification worker.py/stages.py (unchanged model code) and selected by bootstrap.py with
WORKER_MODULE=dag_worker. One process per allocated device unit; every model call still takes the Worker's one-unit
lock, so stages co-hosted on one device (e.g. vision + speech on one Jetson GPU) queue for it; the queue is visible in
/health and in every receipt (queue_wait_ms, queue_at_arrival).

POST /run with mode='dag':
  {mode:'dag', run_id, request_id, stage, input:'raw'|'tensor', data(bytes), forward:[hop,...], budget_s}
  The stage runs; its output is POSTed (binary framing) to forward[0]['url'] with forward[1:]; the reply carries this
  worker's receipt plus every downstream receipt and hop record. Data really travels worker -> next worker on the
  route the generator wrote into the hop (direct Pod IP or the shared gateway); nothing returns to the origin in between.
  stage='fusion_join', part='vision'|'speech': fan-in. The two branch chains of one request meet here; the second part
  to arrive runs fusion (one lock acquisition) and both waiting callers get the same fusion result. A part whose partner
  does not arrive within its budget answers join_timeout (no fusion, no substitute).
POST /run mode='queue' -> {waiting, active, completed, joins_open, inflight, dag_requests, node, pod_uid}: light observation
  (poller); inflight counts admitted dag/probe/sink requests until their reply, so forwarding and join waits are visible.
POST /run mode='sink' {data} -> {bytes, sha256}: payload sink for transfer probes.
POST /run mode='probe' {url, bytes, reps, timeout_s} -> this worker POSTs `bytes` random payload to url (a sink) reps times and
  returns the measured application round-trip times (route profiling from this worker's own network position).
Hop timing: rpc_ms is the sender's wall time of the downstream POST; it CONTAINS the downstream work, so it is never
added to anything; the generator measures end-to-end latency on its own clock only.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parent))
import worker as base  # noqa: E402
from bounded_http import post as bounded_post  # noqa: E402
from http_runtime import payload  # noqa: E402

Worker = None  # bound below
MAX_ACTIVE = int(os.environ.get('DAG_MAX_ACTIVE', '96'))
MAX_JOINS = 256
# Nested budgets shrink by this margin per hop so an inner timeout (e.g. join_timeout) is answered before the caller's
# own socket timeout fires; the caller then records the explicit cause instead of a bare timeout.
FORWARD_MARGIN_S = 0.1


def digest(data):
    return hashlib.sha256(data).hexdigest()


def slim(receipt):
    """DAG receipts drop the static load description (kept in /health) for its hash, so per-request evidence travelling
    back along the chain stays small; identity, hashes, device and timing fields are kept."""
    out = dict(receipt)
    static = out.pop('static', None)
    out['static_sha256'] = digest(json.dumps(static, sort_keys=True, default=str).encode()) if static is not None else None
    return out


class DagWorker(base.Worker):
    def __init__(self, *args, post=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.post = post or bounded_post  # absolute per-forward deadline (not a socket inactivity timeout)
        self.joins, self.join_lock = {}, threading.Lock()
        self.counters = dict(dag_requests=0, forwarded=0, forward_errors=0, join_timeouts=0, joins_completed=0, rejected_joins=0)
        # Admitted dag/probe/sink requests from arrival to reply, including compute, forwarding and join waits (drain).
        self.inflight = 0

    def health(self):
        out = super().health()
        with self.join_lock:
            out['joins_open'] = len(self.joins)
            out['dag_counters'] = dict(self.counters)
        return out

    def run(self, body):
        if body.get('mode') == 'queue':
            with self.join_lock:
                joins, inflight, requests = len(self.joins), self.inflight, self.counters['dag_requests']
            return 200, dict(status='ok', waiting=self.waiting, active=self.active, completed=self.sequence, joins_open=joins,
                             inflight=inflight, dag_requests=requests, node=self.node, pod_uid=self.pod_uid)
        if body.get('mode') not in ('sink', 'probe', 'dag'):
            return super().run(body)
        with self.join_lock:
            self.inflight += 1
        try:
            return self._run(body)
        finally:
            with self.join_lock:
                self.inflight -= 1

    def _run(self, body):
        if body.get('mode') == 'sink':
            data = payload(body['data'])
            return 200, dict(status='ok', bytes=len(data), sha256=digest(data))
        if body.get('mode') == 'probe':
            return self.probe(body)
        with self.join_lock:
            self.counters['dag_requests'] += 1
        started = time.monotonic()
        budget = float(body.get('budget_s') or 30)
        if body.get('stage') == 'fusion_join':
            return self.join(body, started, budget)
        stage_body = {k: v for k, v in body.items() if k not in ('mode', 'forward', 'budget_s')}
        stage_body['deadline_monotonic'] = started + budget - FORWARD_MARGIN_S  # absolute: covers decode, hashing and the lock
        status, reply = super().run(stage_body)
        if status != 200:
            return status, dict(reply, receipts=[], hops=[])
        receipts, hops = [slim(reply['receipt'])], []
        forward = body.get('forward') or []
        if not forward:
            return 200, dict(status='ok', data=reply['data'], receipts=receipts, hops=hops)
        hop, rest = forward[0], forward[1:]
        out = base64.b64decode(reply['data'])
        remaining = budget - (time.monotonic() - started) - FORWARD_MARGIN_S
        if remaining <= 0.05:
            return 504, dict(status='error', error='budget exhausted before forward', stage=body['stage'], receipts=receipts, hops=hops)
        nbody = dict(mode='dag', run_id=body.get('run_id'), request_id=body.get('request_id'), stage=hop['stage'],
                     input=hop.get('input', 'raw'), forward=rest, budget_s=remaining, data=out,
                     **({'part': hop['part']} if 'part' in hop else {}))
        sent = time.monotonic()
        record = dict(src=hop.get('src'), dst=hop.get('dst'), route=hop.get('route'), stage=hop['stage'], payload_bytes=len(out),
                      output_sha256=digest(out))
        try:
            downstream = self.post(hop['url'], nbody, timeout=remaining + FORWARD_MARGIN_S / 2)
            record.update(rpc_ms=(time.monotonic()-sent)*1000, status=downstream.get('status'))
        except urllib.error.HTTPError as error:
            text = error.read(4000).decode(errors='replace')
            try:
                downstream = json.loads(text)
            except ValueError:
                downstream = dict(status='error', error=text[:500])
            record.update(rpc_ms=(time.monotonic()-sent)*1000, status='http_error', code=error.code)
            with self.join_lock:
                self.counters['forward_errors'] += 1
            return error.code, dict(status='error', error=downstream.get('error'), failed_stage=downstream.get('failed_stage', hop['stage']),
                                    receipts=receipts+downstream.get('receipts', []), hops=[record]+downstream.get('hops', []))
        except Exception as error:
            timed_out = 'timed out' in repr(error).lower() or isinstance(error, TimeoutError)
            record.update(rpc_ms=(time.monotonic()-sent)*1000, status='forward_timeout' if timed_out else 'transport_error', error=repr(error)[:300])
            with self.join_lock:
                self.counters['forward_errors'] += 1
            return (504 if timed_out else 502), dict(status='error', error='forward_timeout' if timed_out else repr(error)[:300],
                                                     failed_stage=hop['stage'], receipts=receipts, hops=[record])
        with self.join_lock:
            self.counters['forwarded'] += 1
        return 200, dict(status='ok', data=downstream.get('data'), receipts=receipts+downstream.get('receipts', []),
                         hops=[record]+downstream.get('hops', []), join=downstream.get('join'))

    def join(self, body, started, budget):
        part, key = body.get('part'), (body.get('run_id'), body.get('request_id'))
        if part not in ('vision', 'speech'):
            return 400, dict(status='error', error='fusion_join part must be vision or speech', receipts=[], hops=[])
        data = payload(body['data'])
        with self.join_lock:
            entry = self.joins.get(key)
            if entry is None:
                if len(self.joins) >= MAX_JOINS:
                    self.counters['rejected_joins'] += 1
                    return 503, dict(status='error', error='join table full', receipts=[], hops=[])
                entry = self.joins[key] = dict(parts={}, event=threading.Event(), result=None, opened=time.monotonic())
            if part in entry['parts']:
                return 409, dict(status='error', error=f'duplicate {part} part', receipts=[], hops=[])
            entry['parts'][part] = data
            complete = len(entry['parts']) == 2
        if not complete:
            remaining = budget - (time.monotonic() - started) - FORWARD_MARGIN_S
            if not entry['event'].wait(max(0.0, remaining)):
                with self.join_lock:
                    if self.joins.get(key) is entry and entry['result'] is None:
                        del self.joins[key]
                        self.counters['join_timeouts'] += 1
                        return 504, dict(status='error', error='join_timeout', failed_stage='fusion', missing=[p for p in ('vision', 'speech')
                                                                                                             if p not in entry['parts']],
                                         receipts=[], hops=[])
                entry['event'].wait()  # the partner already started fusion; its result is about to be set
            status, reply = entry['result']
            return status, dict(reply, join=dict(role='first', waited_ms=(time.monotonic()-started)*1000))
        fusion = dict(run_id=body.get('run_id'), request_id=body.get('request_id'), stage='fusion',
                      inputs={k: base64.b64encode(v).decode() for k, v in entry['parts'].items()},
                      deadline_monotonic=started + budget - FORWARD_MARGIN_S)
        try:
            status, reply = base.Worker.run(self, fusion)
        except Exception as error:
            status, reply = 500, dict(status='error', error=repr(error)[:500])
        result = (status, dict(status=reply.get('status'), data=reply.get('data'), error=reply.get('error'),
                               receipts=[slim(reply['receipt'])] if 'receipt' in reply else [], hops=[],
                               **({'failed_stage': 'fusion'} if status != 200 else {})))
        with self.join_lock:
            entry['result'] = result
            self.joins.pop(key, None)
            self.counters['joins_completed'] += 1
        entry['event'].set()
        return result[0], dict(result[1], join=dict(role='second', waited_ms=0.0))

    def probe(self, body):
        size, reps = int(body.get('bytes', 1024)), int(body.get('reps', 3))
        if not 1 <= size <= 8 * 1024 * 1024 or not 1 <= reps <= 10:
            return 400, dict(status='error', error='probe bytes/reps out of range')
        data, rows = os.urandom(size), []
        for _ in range(reps):
            sent = time.monotonic()
            try:
                reply = self.post(body['url'], dict(mode='sink', run_id=body.get('run_id'), request_id='probe', data=data),
                                  timeout=float(body.get('timeout_s', 60)))
                ok = reply.get('sha256') == digest(data) and reply.get('bytes') == size
                rows.append(dict(rtt_ms=(time.monotonic()-sent)*1000, ok=ok))
            except Exception as error:
                rows.append(dict(rtt_ms=(time.monotonic()-sent)*1000, ok=False, error=repr(error)[:200]))
        return 200, dict(status='ok', bytes=size, rows=rows, node=self.node, pod_uid=self.pod_uid)


Worker = DagWorker


def serve(worker, host='0.0.0.0', port=8080, **kwargs):
    """Qualification serve() with a DAG connection admission cap. Chains are nested synchronous RPCs: a forwarding stage
    and a waiting join part each keep their connection open (without holding the device lock), so one request can hold
    up to 3 connections per branch on a worker hosting several of its tasks. The cap is therefore a connection limit,
    much larger than the one-unit device cap; overflow answers 503 and is recorded as worker_rejected."""
    kwargs.setdefault('max_active', MAX_ACTIVE)
    return base.serve(worker, host=host, port=port, **kwargs)
