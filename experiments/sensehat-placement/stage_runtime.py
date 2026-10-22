"""Sense HAT 3-stage forecast runtime (stdlib only; shipped verbatim in the run ConfigMap).

T1 window validation/features -> T2 frozen least-squares line -> T3 result package.
This is a small learned linear forecast, not anomaly detection or a neural network.
Stage payloads never carry the target row; errors are computed by the caller after return.
"""
import argparse
from datetime import datetime
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import platform
import socket
import sys
import time
import urllib.parse

WINDOW = 8
ROW_FIELDS = frozenset(['configurationSha256', 'deviceId', 'deviceUid', 'namespace', 'observedAt', 'property',
                        'quality', 'rawPayload', 'receivedAt', 'sequence', 'sessionId', 'sourceGeneration',
                        'timeBasis', 'type', 'unit', 'value'])
CONSTANT_FIELDS = ('deviceUid', 'deviceId', 'namespace', 'property', 'unit', 'configurationSha256', 'sessionId',
                   'sourceGeneration')
RAW_CHECKS = ('configurationSha256', 'property', 'sequence', 'sessionId', 'sourceGeneration', 'unit', 'value')
MODEL_IDENTITY = ('deviceUid', 'unit', 'configurationSha256', 'sessionId', 'sourceGeneration')


class InputError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def parse_time(text):
    if not isinstance(text, str) or not text.endswith('Z'):
        raise InputError('observedAt must be ISO UTC with Z: ' + repr(text))
    return datetime.fromisoformat(text[:-1] + '+00:00')


def validate_rows(rows):
    """Reject anything that is not a gap-free, single-metadata, qualified source series."""
    if not isinstance(rows, list) or not rows:
        raise InputError('rows must be a nonempty list')
    first = rows[0]
    previous = None
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != ROW_FIELDS:
            extra = sorted(set(row) - ROW_FIELDS) if isinstance(row, dict) else []
            raise InputError(f'row {index} fields differ from source schema; extra={extra}')
        if row['quality'] != 'QUALIFIED' or row['timeBasis'] != 'SOURCE' or row['type'] != 'float64':
            raise InputError(f'row {index} is not QUALIFIED/SOURCE/float64')
        value = row['value']
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise InputError(f'row {index} value is not a finite number')
        for key in CONSTANT_FIELDS:
            if row[key] != first[key]:
                raise InputError(f'metadata {key} changes at sequence {row["sequence"]}: {first[key]!r} -> {row[key]!r}')
        observed = parse_time(row['observedAt'])
        raw = json.loads(row['rawPayload'])
        for key in RAW_CHECKS:
            if raw.get(key) != row[key]:
                raise InputError(f'rawPayload {key} differs at sequence {row["sequence"]}')
        if raw.get('observedAt') != round(observed.timestamp() * 1000):
            raise InputError(f'rawPayload observedAt differs at sequence {row["sequence"]}')
        if previous is not None:
            if row['sequence'] != previous['sequence'] + 1:
                raise InputError(f'sequence gap after {previous["sequence"]} -> {row["sequence"]}')
            if observed <= parse_time(previous['observedAt']):
                raise InputError(f'observedAt not strictly increasing at sequence {row["sequence"]}')
        previous = row
    return first


def window_mean(values):
    return math.fsum(values) / len(values)


def predict(model, mean):
    return model['a'] * mean + model['b']


def t1_features(data):
    if not isinstance(data, dict) or set(data) != {'rows'}:
        raise InputError('T1 payload must contain only rows')
    rows = data['rows']
    if not isinstance(rows, list) or len(rows) != WINDOW:
        raise InputError(f'T1 window must have exactly {WINDOW} rows')
    first = validate_rows(rows)
    values = [row['value'] for row in rows]
    return {'channel': first['property'], 'unit': first['unit'],
            **{key: first[key] for key in MODEL_IDENTITY if key != 'unit'},
            'window_size': WINDOW, 'mean': window_mean(values), 'last': values[-1],
            'first_sequence': rows[0]['sequence'], 'last_sequence': rows[-1]['sequence'],
            'first_observedAt': rows[0]['observedAt'], 'last_observedAt': rows[-1]['observedAt'],
            'source_sha256': digest(canonical(rows))}


def t2_forecast(features, model_doc, model_sha256):
    channel = features.get('channel')
    model = model_doc['channels'].get(channel)
    if model is None:
        raise InputError('no frozen model for channel ' + repr(channel))
    for key in MODEL_IDENTITY:
        if features.get(key) != model[key]:
            raise InputError(f'feature {key} does not match frozen model')
    if features.get('window_size') != model_doc['window_size']:
        raise InputError('window size does not match frozen model')
    return {'channel': channel, 'unit': features['unit'], 'deviceUid': features['deviceUid'],
            'forecast': predict(model, features['mean']), 'persistence': features['last'],
            'model_sha256': model_sha256, 'source_sha256': features['source_sha256'],
            'last_sequence': features['last_sequence'], 'last_observedAt': features['last_observedAt']}


def t3_package(prediction, model_sha256):
    if prediction.get('model_sha256') != model_sha256:
        raise InputError('prediction model_sha256 differs from this stage model')
    return {'schema': 'sensehat-forecast-result/v1', 'channel': prediction['channel'], 'unit': prediction['unit'],
            'deviceUid': prediction['deviceUid'], 'forecast': prediction['forecast'],
            'persistence': prediction['persistence'], 'model_sha256': model_sha256,
            'method': 'least-squares line on 8-row window mean (not anomaly detection, not neural)',
            'input': {'source_sha256': prediction['source_sha256'], 'last_sequence': prediction['last_sequence'],
                      'last_observedAt': prediction['last_observedAt'],
                      'predicted_sequence': prediction['last_sequence'] + 1}}


# ---------------------------------------------------------------- HTTP serving (stdlib)
MAX_BODY = 128 * 1024
MAX_RESPONSE = 1024 * 1024
STAGE_TIMEOUT = 20
STAGE_COMPUTE = {'t1': lambda data, server: t1_features(data),
                 't2': lambda data, server: t2_forecast(data, server.model_doc, server.model_sha256),
                 't3': lambda data, server: t3_package(data, server.model_sha256)}


def call(url, value, timeout):
    """One POST over a fresh http.client connection, no retry. Returns (status, parsed body).

    urllib's opener is avoided: it loads the system CA store on every build (~13 ms locally),
    which would be a client artifact inside every measured hop. Network errors propagate.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != 'http' or not parts.hostname:
        raise InputError('only http:// URLs are allowed: ' + url)
    connection = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    try:
        connection.request('POST', parts.path or '/', canonical(value), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE + 1)
    finally:
        connection.close()
    if len(raw) > MAX_RESPONSE:
        return response.status, {'error': f'response exceeds {MAX_RESPONSE} bytes'}
    try:
        return response.status, json.loads(raw)
    except ValueError:
        return response.status, {'error': raw[:500].decode(errors='replace')}


def check_ids(message, chain_len):
    for key in ('run_id', 'request_id'):
        value = message.get(key)
        if not isinstance(value, str) or not value or len(value) > 120:
            raise InputError(key + ' must be a nonempty short string')
    chain = message.get('chain')
    if not isinstance(chain, list) or len(chain) not in chain_len or not all(
            isinstance(u, str) and u.startswith('http://') and len(u) < 200 for u in chain):
        raise InputError('chain must be a list of http:// stage URLs')
    return chain


class StageServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, role, config_root):
        super().__init__(address, Handler)
        self.role = role
        model_raw = (config_root / 'model.json').read_bytes()
        self.model_doc, self.model_sha256 = json.loads(model_raw), digest(model_raw)
        self.identity = {'task': role, 'node': os.getenv('NODE_NAME', 'local'), 'pod_uid': os.getenv('POD_UID', 'local'),
                         'hostname': socket.gethostname(), 'model_sha256': self.model_sha256,
                         'backend': {'impl': 'python-stdlib-cpu', 'python': platform.python_version(),
                                     'machine': platform.machine(), 'pid': os.getpid()}}
        self.windows = {}
        if role == 'source':
            for wid, window in json.loads((config_root / 'windows.json').read_text())['windows'].items():
                if digest(canonical(window['payload'])) != window['payload_sha256']:
                    raise RuntimeError('frozen window hash mismatch: ' + wid)
                self.windows[wid] = window


class Handler(BaseHTTPRequestHandler):
    timeout = 30
    server_version = 'sensehat-stage/1'
    # Headers and body are separate writes; avoid Nagle/delayed-ACK stalls between them.
    disable_nagle_algorithm = True

    def log_message(self, fmt, *args):
        sys.stderr.write('%s %s\n' % (self.server.role, fmt % args))

    def reply(self, status, value):
        raw = canonical(value)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == '/health':
            return self.reply(200, self.server.identity)
        self.reply(404, {'error': 'not found'})

    def do_POST(self):
        started = time.monotonic()
        length = self.headers.get('Content-Length')
        if length is None or not length.isdigit():
            return self.reply(411, {'error': 'Content-Length required'})
        if int(length) > MAX_BODY:
            self.close_connection = True
            return self.reply(413, {'error': f'body exceeds {MAX_BODY} bytes'})
        body = self.rfile.read(int(length))
        if self.path == '/health':
            return self.reply(200, self.server.identity)
        try:
            message = json.loads(body)
            if not isinstance(message, dict):
                raise InputError('body must be a JSON object')
            if self.path == '/stage' and self.server.role in STAGE_COMPUTE:
                status, value = self.stage(message, started)
            elif self.path == '/run' and self.server.role == 'source':
                status, value = self.run(message)
            else:
                status, value = 404, {'error': 'unknown path for role ' + self.server.role}
        except (InputError, ValueError, KeyError, TypeError) as error:
            status, value = 400, {'error': f'{type(error).__name__}: {error}'}
        self.reply(status, value)

    def stage(self, message, started):
        server = self.server
        chain = check_ids(message, range(0, 3))
        data = message.get('data')
        data_raw = canonical(data)
        receipt = dict(server.identity, request_id=message['request_id'], run_id=message['run_id'],
                       input_sha256=digest(data_raw), input_bytes=len(data_raw), output=None, output_sha256=None,
                       output_bytes=None, source_sha256=None, compute_ms=None, downstream_rpc_ms=0, handler_total_ms=None)
        begin = time.monotonic()
        try:
            output = STAGE_COMPUTE[server.role](data, server)
        except (InputError, KeyError, TypeError, AttributeError) as error:
            receipt.update(compute_ms=(time.monotonic() - begin) * 1000,
                           handler_total_ms=(time.monotonic() - started) * 1000)
            return 400, {'error': f'{server.role} rejected input: {type(error).__name__}: {error}', 'receipts': [receipt]}
        receipt['compute_ms'] = (time.monotonic() - begin) * 1000
        out_raw = canonical(output)
        # The actual output travels with its hash so the verifier can re-hash it and compare it to the
        # local reference numerically, without assuming cross-architecture float byte identity.
        receipt.update(output=output, output_sha256=digest(out_raw), output_bytes=len(out_raw),
                       source_sha256=output.get('source_sha256') or output.get('input', {}).get('source_sha256'))
        receipts, result, status, error = [receipt], output, 200, None
        if chain:
            begin = time.monotonic()
            try:
                code, downstream = call(chain[0] + '/stage', {'run_id': message['run_id'],
                                        'request_id': message['request_id'], 'chain': chain[1:], 'data': output},
                                        STAGE_TIMEOUT)
            except (OSError, ValueError) as exc:
                code, downstream = None, {'error': f'downstream unreachable: {type(exc).__name__}: {exc}'}
            receipt['downstream_rpc_ms'] = (time.monotonic() - begin) * 1000
            receipts += downstream.get('receipts', [])
            if code != 200:
                status, error = 502, f'downstream {chain[0]} status {code}: {downstream.get("error")}'
            else:
                result = downstream['result']
        receipt['handler_total_ms'] = (time.monotonic() - started) * 1000
        value = {'receipts': receipts}
        value.update({'error': error} if error else {'result': result})
        return status, value

    def run(self, message):
        server = self.server
        chain = check_ids(message, (3,))
        window = server.windows.get(message.get('window_id'))
        if window is None:
            raise InputError('unknown window_id')
        body = {'run_id': message['run_id'], 'request_id': message['request_id'], 'chain': chain[1:],
                'data': window['payload']}
        source = dict(server.identity, payload_sha256=window['payload_sha256'], payload_bytes=window['payload_bytes'],
                      started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        # Timed span: edge source -> T1 request to final response; no window build/target/artifact access.
        begin = time.monotonic()
        try:
            code, reply = call(chain[0] + '/stage', body, STAGE_TIMEOUT * 3)
        except (OSError, ValueError) as exc:
            code, reply = None, {'error': f'T1 unreachable: {type(exc).__name__}: {exc}'}
        source['e2e_ms'] = (time.monotonic() - begin) * 1000
        value = {'request_id': message['request_id'], 'window_id': message['window_id'], 'attempts': 1,
                 'http_status': code, 'receipts': reply.get('receipts', []), 'source': source}
        if code == 200:
            value.update(status='ok', result=reply['result'])
        else:
            value.update(status='error', error=f'stage http {code}: {reply.get("error")}')
        return 200, value


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('serve')
    client = sub.add_parser('client')
    client.add_argument('--url', required=True)
    client.add_argument('--timeout', type=float, default=90)
    args = parser.parse_args(argv)
    if args.command == 'serve':
        role = os.environ['TASK_ID']
        if role not in (*STAGE_COMPUTE, 'source'):
            raise SystemExit('unknown TASK_ID ' + role)
        server = StageServer((os.getenv('BIND_HOST', '0.0.0.0'), int(os.getenv('PORT', '8080'))), role,
                             Path(os.getenv('CONFIG_ROOT', '/config')))
        print(json.dumps(server.identity), flush=True)
        server.serve_forever()
    control = json.loads(sys.stdin.read(MAX_BODY))
    try:
        code, reply = call(args.url, control, args.timeout)
    except (OSError, ValueError) as exc:
        code, reply = None, {'status': 'client_error', 'error': f'{type(exc).__name__}: {exc}'}
    print(json.dumps(reply, sort_keys=True))
    return 0 if code == 200 else 1


if __name__ == '__main__':
    sys.exit(main())
