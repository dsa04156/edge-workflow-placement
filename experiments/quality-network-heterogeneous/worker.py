"""HTTP-only qualification worker: ONE process per allocated accelerator unit, hosting every requested stage.

Runs inside an owned Pod (no exec/logs needed: all evidence is returned over HTTP). Stages execute sequentially
under one lock so a single allocated GPU/NPU unit is never shared by concurrent model calls.

  GET  /ready   200 once loading finished (success or failure), so load errors stay readable over HTTP
  GET  /health  {status, node, pod_uid, stages: {stage: describe() | load error}}
  POST /run     {run_id, request_id, stage, data(bytes, binary framing) | inputs{vision,speech}(base64)}
                -> {status, data(base64), receipt}; errors -> HTTP 4xx/5xx with {status:error, error, stage}
Load failures are never replaced by another backend: the stage stays failed and /run answers 503.
Protocol framing reuses the frozen real-topology-live/http_runtime.py helpers (unchanged copy).
"""
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
from pathlib import Path
import sys
import threading
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stages  # noqa: E402
from http_runtime import decode_body, payload  # noqa: E402


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Worker:
    def __init__(self, backend, stage_names, model_root=None, source_root=None, onnx=None, onnx_sha256=None, loader=None):
        self.backend, self.lock, self.sequence = backend, threading.Lock(), 0
        # Visible device queue (additive): callers waiting for / holding the one-unit device lock.
        self.queue_lock, self.waiting, self.active = threading.Lock(), 0, 0
        # Identity is fixed per process (the Pod's downward-API env at start), never re-read per request.
        self.node, self.pod_uid = os.environ.get('NODE_NAME'), os.environ.get('POD_UID')
        self.stages, self.errors, self.descriptions = {}, {}, {}
        loader = loader or stages.load
        for name in stage_names:
            try:
                stage = loader(name, backend, model_root=model_root, source_root=source_root, onnx_path=onnx, expected_onnx_sha256=onnx_sha256)
                self.stages[name], self.descriptions[name] = stage, stage.describe()
                if self.descriptions[name].get('backend') != backend:
                    raise stages.DeviceMismatch(f'{name} loaded as {self.descriptions[name].get("backend")}, requested {backend}')
            except Exception as error:
                self.stages.pop(name, None)
                self.errors[name] = dict(type=type(error).__name__, error=repr(error)[:1000],
                                         status='dependency_unavailable' if isinstance(error, stages.BackendUnavailable) else 'load_failed',
                                         traceback=traceback.format_exc()[-2000:])
        self.loaded = True

    def health(self):
        return dict(status='ready' if not self.errors else ('partial' if self.stages else 'failed'), backend=self.backend,
                    node=self.node, pod_uid=self.pod_uid, stages=self.descriptions, errors=self.errors,
                    queue=dict(waiting=self.waiting, active=self.active, completed=self.sequence))

    def run(self, body):
        # Optional bounded admission (additive, quality-network-baselines DAG mode): one absolute local monotonic expiry,
        # fixed before input preparation (decode/hash), bounds the lock wait and is rechecked once the lock is held, so an
        # expired request is answered without a model call. Neither field -> unbounded wait (qualification path).
        expires = body.get('deadline_monotonic')
        if expires is None and body.get('queue_deadline_s') is not None:
            expires = time.monotonic() + max(0.0, float(body['queue_deadline_s']))
        name = body.get('stage')
        if name in self.errors:
            return 503, dict(status='error', stage=name, error='stage not loaded', load_error=self.errors[name])
        if name not in self.stages:
            return 400, dict(status='error', stage=name, error='stage not hosted by this worker')
        tensor = body.get('input') == 'tensor'
        if tensor and not hasattr(self.stages[name], 'run_tensor'):
            return 400, dict(status='error', stage=name, error='stage has no tensor-input form on this backend')
        if name == 'fusion':
            data = {k: base64.b64decode(body['inputs'][k], validate=True) for k in ('vision', 'speech')}
            input_sha = {k: digest(v) for k, v in data.items()}
        else:
            data = payload(body['data'])
            input_sha = digest(data)
        queued = time.perf_counter()
        with self.queue_lock:
            depth_at_arrival = dict(waiting=self.waiting, active=self.active)
            self.waiting += 1
        try:
            if expires is None:
                acquired = self.lock.acquire()
            else:
                acquired = time.monotonic() < expires and self.lock.acquire(timeout=max(0.0, expires - time.monotonic()))
                if acquired and time.monotonic() >= expires:
                    self.lock.release()
                    acquired = False
        finally:
            with self.queue_lock:
                self.waiting -= 1
        if not acquired:
            return 504, dict(status='error', stage=name, error='queue_deadline_expired', request_id=body.get('request_id'),
                             queue_wait_ms=(time.perf_counter() - queued) * 1000, queue_at_arrival=depth_at_arrival)
        with self.queue_lock:
            self.active += 1
        try:
            self.sequence += 1
            sequence = self.sequence
            started = time.perf_counter()
            queue_wait_ms = (started - queued) * 1000
            try:
                output, timing = (self.stages[name].run_tensor if tensor else self.stages[name].run)(data)
            except Exception as error:  # structured: which phase failed (e.g. request-time NPU compile vs inference)
                return 500, dict(status='error', stage=name, phase=getattr(error, 'phase', 'inference'), type=type(error).__name__,
                                 error=repr(getattr(error, 'original', error))[:1500], request_id=body.get('request_id'), sequence=sequence,
                                 queue_wait_ms=queue_wait_ms)
            wall = (time.perf_counter() - started) * 1000
        finally:
            with self.queue_lock:
                self.active -= 1
            self.lock.release()
        description = self.descriptions[name]
        if timing.get('output_device') and not timing['output_device'].startswith(description['device'].split(':')[0]):
            raise stages.DeviceMismatch(f'{name} produced output on {timing["output_device"]}, worker device {description["device"]}')
        receipt = dict(stage=name, backend=self.backend, device=description['device'], node=self.node,
                       pod_uid=self.pod_uid, run_id=body.get('run_id'), request_id=body.get('request_id'),
                       sequence=sequence, input_sha256=input_sha, output_sha256=digest(output), output_bytes=len(output),
                       model_sha256=description.get('model_sha256'), derived_artifact_sha256=description.get('derived_artifact_sha256'),
                       worker_wall_ms=wall, timing=timing, static=description,
                       input_kind='tensor' if tensor else 'raw', queue_wait_ms=queue_wait_ms, queue_at_arrival=depth_at_arrival)
        return 200, dict(status='ok', data=base64.b64encode(output).decode(), receipt=receipt)


def serve(worker, host='0.0.0.0', port=8080, max_active=2, body_timeout_s=30.0, max_body=32 * 1024 * 1024, read_deadline_s=None):
    """Bounded HTTP admission: at most `max_active` connections are handled (each slot is taken before any byte of
    the request is read; excess connections get an immediate structured 503 and are closed), every socket read has a
    `body_timeout_s` inactivity timeout AND an absolute `read_deadline_s` for request line + headers + body (a timer
    shuts the socket if reading is not finished in time, so trickling clients cannot hold a slot), bodies above
    `max_body` are refused with 413 before reading, and the slot is released
    on every path (success, error, timeout, disconnect). Model calls stay serialized by Worker.lock, so at most
    `max_active` decoded payloads exist at once. Qualification infrastructure only, not an admission-control study."""
    absolute = read_deadline_s if read_deadline_s is not None else 2 * body_timeout_s

    class Handler(BaseHTTPRequestHandler):
        timeout = body_timeout_s  # StreamRequestHandler sets this as the socket timeout before reading the request

        def setup(self):
            super().setup()
            self.read_done = threading.Event()
            self.read_timer = threading.Timer(absolute, self._expire)
            self.read_timer.daemon = True
            self.read_timer.start()

        def _expire(self):
            if not self.read_done.is_set():
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        def _reading_finished(self):
            self.read_done.set()
            self.read_timer.cancel()

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
            self._reading_finished()
            if self.path == '/ready':
                self._send(200 if worker.loaded else 503, dict(loaded=worker.loaded))
            elif self.path == '/health':
                self._send(200, worker.health())
            else:
                self._send(404, dict(status='error', error='unknown path'))

        def do_POST(self):
            if self.path != '/run':
                self._send(404, dict(status='error', error='unknown path'))
                return
            try:
                size = int(self.headers.get('Content-Length', 0))
                if size < 1:
                    raise ValueError('Invalid body size')
                if size > max_body:
                    self._reading_finished()
                    self._send(413, dict(status='error', error='body too large', limit=max_body))
                    return
                raw = self.rfile.read(size)
                self._reading_finished()
                if len(raw) != size:
                    return  # socket shut by the absolute read deadline or the client went away
                body = decode_body(raw, self.headers.get('Content-Type'))
                status, value = worker.run(body)
            except (ValueError, KeyError, TypeError) as error:
                status, value = 400, dict(status='error', error=repr(error)[:1000])
            except OSError:
                return  # read deadline / disconnect: nothing can be answered; slot is released by the server
            except Exception as error:
                status, value = 500, dict(status='error', error=repr(error)[:1000], type=type(error).__name__)
            self._send(status, value)

        def log_message(self, *args):
            pass

    class BoundedServer(ThreadingHTTPServer):
        daemon_threads = True
        slots = threading.BoundedSemaphore(max_active)
        held = [0]
        lock = threading.Lock()

        def active_slots(self):
            with self.lock:
                return self.held[0]

        def process_request(self, request, client_address):
            if not self.slots.acquire(blocking=False):
                raw = json.dumps(dict(status='error', error='worker at admission capacity', max_active=max_active)).encode()
                try:
                    request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\nConnection: close\r\n'
                                    b'Content-Length: ' + str(len(raw)).encode() + b'\r\n\r\n' + raw)
                except OSError:
                    pass
                self.shutdown_request(request)
                return
            with self.lock:
                self.held[0] += 1
            try:
                super().process_request(request, client_address)
            except BaseException:
                self._release()
                raise

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self._release()

        def _release(self):
            with self.lock:
                self.held[0] -= 1
            self.slots.release()
    return BoundedServer((host, port), Handler)


def main(argv=None):
    ap = argparse.ArgumentParser(description='HTTP-only multi-stage qualification worker (one per accelerator unit).')
    ap.add_argument('--backend', required=True, choices=stages.BACKENDS)
    ap.add_argument('--stages', required=True, help='comma list of vision,speech,fusion')
    ap.add_argument('--onnx', default=None)
    ap.add_argument('--onnx-sha256', default=None)
    ap.add_argument('--port', type=int, default=8080)
    args = ap.parse_args(argv)
    names = [s for s in args.stages.split(',') if s]
    if any(s not in stages.STAGES for s in names):
        ap.error('unknown stage')
    class Pending:
        loaded, backend = False, args.backend

        def health(self):
            return dict(status='loading', backend=args.backend, node=os.environ.get('NODE_NAME'), pod_uid=os.environ.get('POD_UID'))

        def run(self, body):
            return 503, dict(status='error', error='loading')
    proxy = Pending()
    server = serve(proxy, port=args.port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    worker = Worker(args.backend, names, onnx=args.onnx, onnx_sha256=args.onnx_sha256)
    # The handler is bound to `proxy`; swap in the loaded worker's methods (readiness flips only after loading).
    proxy.health, proxy.run, proxy.loaded = worker.health, worker.run, True
    print(json.dumps(dict(event='worker_loaded', **{k: v for k, v in worker.health().items() if k != 'stages'})), flush=True)
    threading.Event().wait()


if __name__ == '__main__':
    main()
