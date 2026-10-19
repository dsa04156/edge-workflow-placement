"""Direct branch/join HTTP runtime. Test fixtures are explicitly labelled, not AI.

Operator adapters are bytes -> bytes; fusion adapters receive two named bytes.
Binary task bodies and JSON control. Payload counts exclude the wire envelope.
"""
import base64
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import struct
import threading
import queue
from concurrent.futures import Future
import time
import urllib.error
import urllib.request


def digest(data):
    return hashlib.sha256(data).hexdigest()


def request(url, data, timeout=60):
    raw, content_type = encode_body(data)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, raw, {'Content-Type': content_type})
    with opener.open(req, timeout=timeout) as response:
        return json.load(response)


def encode_body(data):
    """JSON control header followed by exact binary data; no padding/base64."""
    if isinstance(data.get('data'), bytes):
        metadata = {k: v for k, v in data.items() if k != 'data'}
        header = json.dumps(metadata, separators=(',', ':')).encode()
        return struct.pack('!I', len(header)) + header + data['data'], 'application/x-edge-binary'
    return json.dumps(data, separators=(',', ':')).encode(), 'application/json'


def decode_body(raw, content_type):
    if content_type == 'application/x-edge-binary':
        if len(raw) < 4:
            raise ValueError('Truncated binary header')
        length = struct.unpack('!I', raw[:4])[0]
        if length > len(raw) - 4:
            raise ValueError('Truncated binary metadata')
        return dict(json.loads(raw[4:4 + length]), data=raw[4 + length:])
    return json.loads(raw)


def payload(data):
    return data if isinstance(data, bytes) else base64.b64decode(data, validate=True)


class ProtocolError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def identity(data):
    values = tuple(data.get(k) for k in ('run_id', 'request_id'))
    if any(not isinstance(v, str) or not v or len(v) > 100 for v in values):
        raise ProtocolError('run_id and request_id must be nonempty short strings')
    return values


class OperatorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, task, adapter, metadata=None, capacity=64, ttl=120):
        super().__init__(address, Handler)
        self.task, self.adapter = task, adapter
        self.metadata = dict(metadata or {'scope': 'protocol_fixture_not_ai'})
        self.metadata.update(task=task, node=os.getenv('NODE_NAME', 'local-test'), pod_uid=os.getenv('POD_UID', 'local-test'))
        self.capacity, self.ttl = capacity, ttl
        self.entries = {}
        self.lock = threading.Lock()
        self.jobs = queue.Queue(maxsize=capacity)
        self.worker = threading.Thread(target=self._work, daemon=True)
        self.worker.start()
        self.tombstones = {}
        self.active = False

    def receipt(self, inputs, output, compute_ms, queue_ms):
        return dict(self.metadata, input_bytes={k: len(v) for k, v in inputs.items()},
                    input_sha256={k: digest(v) for k, v in inputs.items()}, output_bytes=len(output),
                    output_sha256=digest(output), compute_ms=compute_ms, queue_ms=queue_ms)

    def _work(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            inputs, arrived, deadline, future = job
            begin = time.monotonic()
            try:
                self.active = True
                if begin >= deadline:
                    raise ProtocolError('Expired in serving queue', 504)
                result = self.adapter(inputs)
                if not isinstance(result, bytes):
                    raise TypeError('Adapter must return bytes')
                end = time.monotonic()
                if end >= deadline:
                    raise ProtocolError('Expired during model execution', 504)
                future.set_result((result, (end - begin) * 1000, (begin - arrived) * 1000))
            except Exception as error:
                future.set_exception(error)
            finally:
                self.active = False
                self.jobs.task_done()

    def invoke(self, inputs, timeout=120):
        arrived, future = time.monotonic(), Future()
        try:
            self.jobs.put_nowait((inputs, arrived, arrived + timeout, future))
        except queue.Full:
            raise ProtocolError('Serving queue capacity reached', 429)
        return future.result(timeout=timeout)

    def server_close(self):
        super().server_close()
        self.jobs.put(None)

    def operate(self, path, data):
        if path == '/health':
            return dict(self.metadata, status='ready', serving_queue=self.jobs.qsize(), serving_active=self.active)
        if path == '/sink':
            content = payload(data['data'])
            return dict(status='ok', bytes=len(content), sha256=digest(content))
        key = identity(data)
        if path == '/infer':
            if self.task == 'fusion':
                inputs = {k: base64.b64decode(data['inputs'][k], validate=True) for k in ('vision', 'speech')}
                argument = inputs
            else:
                argument = payload(data['data'])
                inputs = {'source': argument}
            output, compute, queue = self.invoke(argument, data.get('remaining_s', 120))
            return {'status': 'ok', 'data': base64.b64encode(output).decode(),
                    'receipt': self.receipt(inputs, output, compute, queue)}
        if path == '/execute':
            started = time.monotonic()
            if self.task not in ('vision', 'speech'):
                raise ProtocolError('Only branch operators accept execute')
            content = payload(data['data'])
            output, compute, queue = self.invoke(content, data.get('remaining_s', 120))
            receipt = self.receipt({'source': content}, output, compute, queue)
            remaining = data.get('remaining_s', 120) - (time.monotonic() - started)
            if remaining <= 0:
                raise ProtocolError('Deadline before forwarding', 504)
            request(data['fusion_url'] + '/join', dict(run_id=key[0], request_id=key[1],
                    task=self.task, data=output, receipt=receipt, remaining_s=remaining), timeout=remaining)
            return {'status': 'forwarded', 'receipt': receipt}
        if self.task != 'fusion':
            raise ProtocolError('Only fusion accepts open/join/result')
        execute = None
        with self.lock:
            now = time.monotonic()
            self.entries = {k: v for k, v in self.entries.items() if now - v['created'] <= self.ttl}
            self.tombstones = {k: v for k, v in self.tombstones.items() if now - v <= self.ttl}
            if path == '/open':
                if key in self.entries or key in self.tombstones:
                    raise ProtocolError('Request already registered', 409)
                if sum('result' not in v for v in self.entries.values()) >= self.capacity:
                    raise ProtocolError('Join capacity reached', 429)
                self.entries[key] = {'created': now, 'inputs': {}, 'receipts': {}}
                return {'status': 'registered'}
            if key not in self.entries:
                raise ProtocolError('Unknown or released request', 410)
            item = self.entries[key]
            if path in ('/ack', '/cancel'):
                if path == '/ack' and 'result' not in item:
                    raise ProtocolError('Cannot acknowledge incomplete request', 409)
                del self.entries[key]
                self.tombstones[key] = now
                while len(self.tombstones) > 1024:
                    del self.tombstones[next(iter(self.tombstones))]
                return {'status': 'released'}
            if path == '/result':
                if 'result' not in item:
                    raise ProtocolError('Both branches have not completed', 409)
                return item['result']
            if path != '/join':
                raise ProtocolError('Unknown endpoint', 404)
            task = data['task']
            if task not in ('vision', 'speech'):
                raise ProtocolError('Invalid branch task')
            content = payload(data['data'])
            receipt = data['receipt']
            if receipt.get('task') != task or receipt.get('output_sha256') != digest(content) or receipt.get('output_bytes') != len(content):
                raise ProtocolError('Branch receipt does not match transferred payload')
            if task in item['receipts']:
                if item['receipts'][task] != receipt:
                    raise ProtocolError('Conflicting duplicate branch', 409)
                return {'status': 'duplicate'}
            item['inputs'][task], item['receipts'][task] = content, receipt
            if len(item['inputs']) == 2:
                execute = dict(item['inputs'])
        # Inference and FIFO waiting must never hold the global request-state lock.
        if execute is not None:
            output, compute, waiting = self.invoke(execute, data.get('remaining_s', 120))
            final = self.receipt(execute, output, compute, waiting)
            with self.lock:
                if self.entries.get(key) is not item:
                    raise ProtocolError('Request cancelled during inference', 410)
                item['result'] = {'status': 'ok', 'run_id': key[0], 'request_id': key[1],
                                  'data': base64.b64encode(output).decode(),
                                  'receipts': [item['receipts']['vision'], item['receipts']['speech'], final]}
                item['inputs'].clear()
                completed = [k for k, v in self.entries.items() if 'result' in v]
                for old in completed[:-128]:
                    del self.entries[old]
        return {'status': 'accepted'}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/health':
            self.send_error(404)
            return
        raw = json.dumps(dict(self.server.metadata, status='ready')).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        status = 200
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size <= 0 or size > 16 * 1024 * 1024:
                raise ProtocolError('Invalid or oversized body', 413)
            data = decode_body(self.rfile.read(size), self.headers.get('Content-Type'))
            result = self.server.operate(self.path, data)
        except ProtocolError as error:
            status, result = error.status, {'status': 'error', 'error': str(error)}
        except (ValueError, TypeError, KeyError) as error:
            status, result = 400, {'status': 'error', 'error': str(error)}
        except Exception as error:
            status, result = 500, {'status': 'error', 'error': repr(error)}
        raw = json.dumps(result, separators=(',', ':')).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Client deadline elapsed; source terminal row records it.

    def log_message(self, *args):
        pass


@contextmanager
def serve(task, adapter, **options):
    server = OperatorServer(('127.0.0.1', 0), task, adapter, **options)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    import importlib
    module = importlib.import_module(os.environ['ADAPTER_MODULE'])
    task = os.environ['TASK_ID']
    adapter, metadata = module.load(task)
    server = OperatorServer(('0.0.0.0', 8080), task, adapter, metadata)
    print(json.dumps({'event': 'ready', **server.metadata}), flush=True)
    server.serve_forever()
