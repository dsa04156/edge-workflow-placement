"""Direct branch/join HTTP runtime. Test fixtures are explicitly labelled, not AI.

Operator adapters are bytes -> bytes; fusion adapters receive two named bytes.
Wire format is JSON/base64. Payload byte counts exclude this wire envelope.
"""
import base64
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import time
import urllib.error
import urllib.request


def digest(data):
    return hashlib.sha256(data).hexdigest()


def request(url, data, timeout=60):
    raw = json.dumps(data, separators=(',', ':')).encode()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, raw, {'Content-Type': 'application/json'})
    with opener.open(req, timeout=timeout) as response:
        return json.load(response)


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
        self.lock, self.model_lock = threading.Lock(), threading.Lock()

    def receipt(self, inputs, output, compute_ms, queue_ms):
        return dict(self.metadata, input_bytes={k: len(v) for k, v in inputs.items()},
                    input_sha256={k: digest(v) for k, v in inputs.items()}, output_bytes=len(output),
                    output_sha256=digest(output), compute_ms=compute_ms, queue_ms=queue_ms)

    def invoke(self, inputs):
        start = time.perf_counter()
        with self.model_lock:
            begin = time.perf_counter()
            result = self.adapter(inputs)
            if not isinstance(result, bytes):
                raise TypeError('Adapter must return bytes')
            end = time.perf_counter()
        return result, (end - begin) * 1000, (begin - start) * 1000

    def operate(self, path, data):
        if path == '/health':
            return dict(self.metadata, status='ready')
        key = identity(data)
        if path == '/infer':
            if self.task == 'fusion':
                inputs = {k: base64.b64decode(data['inputs'][k], validate=True) for k in ('vision', 'speech')}
                argument = inputs
            else:
                argument = base64.b64decode(data['data'], validate=True)
                inputs = {'source': argument}
            output, compute, queue = self.invoke(argument)
            return {'status': 'ok', 'data': base64.b64encode(output).decode(),
                    'receipt': self.receipt(inputs, output, compute, queue)}
        if path == '/execute':
            if self.task not in ('vision', 'speech'):
                raise ProtocolError('Only branch operators accept execute')
            payload = base64.b64decode(data['data'], validate=True)
            output, compute, queue = self.invoke(payload)
            receipt = self.receipt({'source': payload}, output, compute, queue)
            request(data['fusion_url'] + '/join', dict(run_id=key[0], request_id=key[1],
                    task=self.task, data=base64.b64encode(output).decode(), receipt=receipt))
            return {'status': 'forwarded', 'receipt': receipt}
        if self.task != 'fusion':
            raise ProtocolError('Only fusion accepts open/join/result')
        with self.lock:
            if path == '/open':
                now = time.monotonic()
                self.entries = {k: v for k, v in self.entries.items() if now - v['created'] <= self.ttl}
                if key in self.entries:
                    raise ProtocolError('Request already registered', 409)
                if len(self.entries) >= self.capacity:
                    raise ProtocolError('Join capacity reached', 429)
                self.entries[key] = {'created': now, 'inputs': {}, 'receipts': {}}
                return {'status': 'registered'}
            if key not in self.entries:
                raise ProtocolError('Unknown request', 404)
            item = self.entries[key]
            if time.monotonic() - item['created'] > self.ttl:
                raise ProtocolError('Request expired', 410)
            if path == '/result':
                if 'result' not in item:
                    raise ProtocolError('Both branches have not completed', 409)
                return item['result']
            if path != '/join':
                raise ProtocolError('Unknown endpoint', 404)
            task = data['task']
            if task not in ('vision', 'speech'):
                raise ProtocolError('Invalid branch task')
            payload = base64.b64decode(data['data'], validate=True)
            receipt = data['receipt']
            if receipt.get('task') != task or receipt.get('output_sha256') != digest(payload) or receipt.get('output_bytes') != len(payload):
                raise ProtocolError('Branch receipt does not match transferred payload')
            if task in item['inputs']:
                if item['inputs'][task] != payload or item['receipts'][task] != receipt:
                    raise ProtocolError('Conflicting duplicate branch', 409)
                return {'status': 'duplicate'}
            item['inputs'][task], item['receipts'][task] = payload, receipt
            if len(item['inputs']) == 2:
                output, compute, queue = self.invoke(item['inputs'])
                final = self.receipt(item['inputs'], output, compute, queue)
                item['result'] = {'status': 'ok', 'run_id': key[0], 'request_id': key[1],
                                  'data': base64.b64encode(output).decode(),
                                  'receipts': [item['receipts']['vision'], item['receipts']['speech'], final]}
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
            data = json.loads(self.rfile.read(size))
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
        self.wfile.write(raw)

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
