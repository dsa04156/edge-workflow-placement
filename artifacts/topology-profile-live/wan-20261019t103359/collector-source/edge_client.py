"""File-replay sources: payloads originate on E0/E1, never collector-relayed.

Source RPCs carry control, IDs and final results. Branch outputs go directly
to fusion. This smoke is closed-loop and never labelled fixed-RPS evaluation.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, wait
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import time
import threading
import re
from functools import lru_cache

from http_runtime import request, decode_body, payload, digest


@lru_cache(maxsize=120)
def read_file(path, expected):
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Input hash changed')
    return data


def read_input(pair, kind, representation='compressed'):
    prefix = 'raw_' if representation == 'raw' else ''
    return read_file(pair[prefix + kind + '_path'], pair[prefix + kind + '_sha256'])


def run_once(pair, identity, endpoints, remote_source, representation='compressed', deadline_s=120):
    start = time.monotonic()
    deadline = start + deadline_s
    def rpc(url, body):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Absolute workflow deadline elapsed')
        return request(url, dict(body, remaining_s=remaining), timeout=remaining)
    rpc(endpoints['fusion'] + '/open', identity)
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        vision = pool.submit(rpc, endpoints['vision'] + '/execute', dict(identity,
            data=read_input(pair, 'image', representation),
            fusion_url=endpoints.get('vision_fusion', endpoints['fusion'])))
        speech = pool.submit(rpc, remote_source + '/branch', dict(identity,
            representation=representation, question_id=pair['question_id'],
            speech_url=endpoints['speech'], fusion_url=endpoints.get('speech_fusion', endpoints['fusion'])))
        done, pending = wait([vision, speech], timeout=max(0, deadline - time.monotonic()))
        if pending:
            raise TimeoutError('Absolute workflow deadline elapsed waiting for branches')
        vision.result()
        speech_source = speech.result()
        result = rpc(endpoints['fusion'] + '/result', identity)
        result['e2e_ms'] = (time.monotonic() - start) * 1000
        result['speech_source_node'] = speech_source['source_node']
        result['speech_source_payload_sha256'] = speech_source['source_payload_sha256']
        # Ack is lifecycle/control overhead, separately included in source_total_ms.
        rpc(endpoints['fusion'] + '/ack', identity)
        result['source_total_ms'] = (time.monotonic() - start) * 1000
        return result
    except Exception:
        try:
            request(endpoints['fusion'] + '/cancel', identity, timeout=3)
        except Exception:
            pass
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def validate_result(pair, result, reference, representation='compressed'):
    raw_output = base64.b64decode(result.pop('data'), validate=True)
    final = next(r for r in result['receipts'] if r['task'] == 'fusion')
    if hashlib.sha256(raw_output).hexdigest() != final['output_sha256'] or len(raw_output) != final['output_bytes']:
        raise ValueError('Fusion output receipt mismatch')
    output = json.loads(raw_output)
    prefix = 'raw_' if representation == 'raw' else ''
    result.update(question_id=pair['question_id'], split=pair['split'],
        source_node=os.environ['NODE_NAME'], scope='real_ai_file_replay', representation=representation,
        output=output, reference_answer_id=reference['answer_ids'][0],
        answer_id_matches=output['answer_ids'][0] == reference['answer_ids'][0],
        top5_order_matches=output['answer_ids'] == reference['answer_ids'],
        max_score_abs_error=max(abs(a-b) for a,b in zip(output['scores'], reference['scores'])),
        source_payload_sha256=pair[prefix + 'image_sha256'])
    receipts = {r['task']: r for r in result['receipts']}
    if receipts['vision']['input_sha256']['source'] != pair[prefix + 'image_sha256'] or receipts['speech']['input_sha256']['source'] != pair[prefix + 'audio_sha256']:
        raise ValueError('Source-to-operator hash mismatch')
    for task in ('vision', 'speech'):
        if receipts['fusion']['input_sha256'][task] != receipts[task]['output_sha256']:
            raise ValueError('Branch-to-fusion hash mismatch')
    if result['speech_source_payload_sha256'] != pair[prefix + 'audio_sha256']:
        raise ValueError('Remote source hash mismatch')
    result['quality_ok'] = result['answer_id_matches'] and result['max_score_abs_error'] <= 1e-4
    return result


def source_server(root, host='0.0.0.0', port=8080):
    root = Path(root)
    pairs = {p['question_id']: p for p in json.loads((root / 'pairs.json').read_text())['pairs']}
    references = {r['question_id']: r for r in map(json.loads, (root / 'reference.jsonl').read_text().splitlines())}
    # Both edge sources are raw-ready before their readiness probe can pass.
    for pair in pairs.values():
        if 'raw_audio_path' in pair:
            read_input(pair, 'audio', 'raw')

    def start_trace(server, config):
        phase = config['phase']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', phase):
            raise ValueError('Invalid trace phase')
        with server.trace_lock:
            if phase in server.traces:
                if server.traces[phase]['config'] != config:
                    raise ValueError('Conflicting trace configuration')
                return dict(status=server.traces[phase]['status'], phase=phase)
            if any(s['status']=='running' for s in server.traces.values()):
                raise ValueError('Another trace is active')
            state = dict(status='running', config=config, events=[])
            server.traces[phase] = state

        def task():
            try:
                from load_trace import run_trace
                with (root / ('trace-'+phase+'.jsonl')).open('x') as handle:
                    def emit(row):
                        handle.write(json.dumps(row)+'\n')
                        handle.flush()
                        with server.trace_lock:
                            state['events'].append(row)
                        print(json.dumps(row), flush=True)
                    for qid in set(config['question_ids']):
                        read_input(pairs[qid], 'image', config['representation'])
                    run_trace(config, pairs, references, emit)
                with server.trace_lock:
                    state['status']='complete'
            except Exception as error:
                with server.trace_lock:
                    state.update(status='failed', error=repr(error))
                print(json.dumps(dict(event='trace_error', phase=phase, error=repr(error))), flush=True)
        threading.Thread(target=task, daemon=True).start()
        return dict(status='running', phase=phase)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/health':
                self.send_error(404)
                return
            raw = json.dumps({'status': 'ready', 'node': os.environ['NODE_NAME']}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            counted = self.path != '/health'
            if counted: self.server.active_requests += 1
            try: self._post()
            finally:
                if counted: self.server.active_requests -= 1

        def _post(self):
            status = 200
            try:
                size = int(self.headers.get('Content-Length', 0))
                if size < 1 or size > 16 * 1024 * 1024:
                    raise ValueError('Invalid control body')
                data = decode_body(self.rfile.read(size), self.headers.get('Content-Type'))
                if self.path in ('/start_trace', '/trace_status'):
                    if self.path == '/start_trace':
                        result = start_trace(self.server, data)
                    else:
                        with self.server.trace_lock:
                            state = self.server.traces[data['phase']]
                            result = dict(status=state['status'], events=list(state['events']), error=state.get('error'))
                    raw = json.dumps(result).encode()
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if self.path == '/health':
                    raw = json.dumps(dict(status='ready', node=os.environ['NODE_NAME'], active_requests=self.server.active_requests, raw_audio_ready=sum('raw_audio_path' in p for p in pairs.values()))).encode()
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if self.path == '/sink':
                    content = payload(data['data'])
                    raw = json.dumps(dict(status='ok', bytes=len(content), sha256=digest(content))).encode()
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                pair = pairs[data['question_id']]
                representation = data.get('representation', 'compressed')
                remaining = data.get('remaining_s', 120)
                identity = {k: data[k] for k in ('run_id', 'request_id')}
                if self.path == '/branch':
                    result = request(data['speech_url'] + '/execute', dict(identity,
                        data=read_input(pair, 'audio', representation), fusion_url=data['fusion_url'], remaining_s=remaining), timeout=remaining)
                    result['source_node'] = os.environ['NODE_NAME']
                    result['source_payload_sha256'] = pair[('raw_' if representation == 'raw' else '') + 'audio_sha256']
                elif self.path == '/profile':
                    result = request(data['operator_url']+'/infer', dict(identity,
                        data=read_input(pair, data['kind'], representation), remaining_s=remaining), timeout=remaining)
                elif self.path == '/run':
                    result = run_once(pair, identity, data['endpoints'], data['remote_source'], representation, remaining)
                    result = validate_result(pair, result, references[pair['question_id']], representation)
                else:
                    raise ValueError('Unknown endpoint')
            except Exception as error:
                status, result = 500, {'status': 'error', 'error': repr(error)}
            raw = json.dumps(result).encode()
            self.send_response(status)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    server.active_requests = 0
    server.traces = {}
    server.trace_lock = threading.Lock()
    return server


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--port', type=int, default=8080)
    args = ap.parse_args()
    server = source_server(args.root, port=args.port)
    print(json.dumps({'event': 'source_ready', 'node': os.environ['NODE_NAME']}), flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
