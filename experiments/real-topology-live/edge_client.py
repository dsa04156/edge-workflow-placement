"""File-replay sources: payloads originate on E0/E1, never collector-relayed.

Source RPCs carry control, IDs and final results. Branch outputs go directly
to fusion. This smoke is closed-loop and never labelled fixed-RPS evaluation.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import time

from http_runtime import request


def read_input(pair, kind):
    data = Path(pair[kind + '_path']).read_bytes()
    if hashlib.sha256(data).hexdigest() != pair[kind + '_sha256']:
        raise ValueError('Input hash changed')
    return base64.b64encode(data).decode()


def run_once(pair, identity, endpoints, remote_source):
    start = time.perf_counter()
    request(endpoints['fusion'] + '/open', identity)
    with ThreadPoolExecutor(max_workers=2) as pool:
        vision = pool.submit(request, endpoints['vision'] + '/execute', dict(identity,
            data=read_input(pair, 'image'), fusion_url=endpoints['fusion']))
        speech = pool.submit(request, remote_source + '/branch', dict(identity,
            question_id=pair['question_id'], speech_url=endpoints['speech'], fusion_url=endpoints['fusion']))
        vision.result()
        speech.result()
    result = request(endpoints['fusion'] + '/result', identity)
    result['e2e_ms'] = (time.perf_counter() - start) * 1000
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--port', type=int, default=8080)
    args = ap.parse_args()
    root = Path(args.root)
    pairs = {p['question_id']: p for p in json.loads((root / 'pairs.json').read_text())['pairs']}
    references = {r['question_id']: r for r in map(json.loads, (root / 'reference.jsonl').read_text().splitlines())}

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
            status = 200
            try:
                size = int(self.headers.get('Content-Length', 0))
                if size < 1 or size > 1024 * 1024:
                    raise ValueError('Invalid control body')
                data = json.loads(self.rfile.read(size))
                pair = pairs[data['question_id']]
                identity = {k: data[k] for k in ('run_id', 'request_id')}
                if self.path == '/branch':
                    result = request(data['speech_url'] + '/execute', dict(identity,
                        data=read_input(pair, 'audio'), fusion_url=data['fusion_url']))
                    result['source_node'] = os.environ['NODE_NAME']
                    result['source_payload_sha256'] = pair['audio_sha256']
                elif self.path == '/run':
                    result = run_once(pair, identity, data['endpoints'], data['remote_source'])
                    output = json.loads(base64.b64decode(result.pop('data')))
                    ref = references[pair['question_id']]
                    result.update(question_id=pair['question_id'], split=pair['split'],
                                  source_node=os.environ['NODE_NAME'], scope='real_ai_file_replay',
                                  output=output, reference_answer_id=ref['answer_ids'][0],
                                  answer_id_matches=output['answer_ids'][0] == ref['answer_ids'][0],
                                  top5_order_matches=output['answer_ids'] == ref['answer_ids'],
                                  max_score_abs_error=max(abs(a-b) for a,b in zip(output['scores'], ref['scores'])),
                                  source_payload_sha256=pair['image_sha256'])
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

    print(json.dumps({'event': 'source_ready', 'node': os.environ['NODE_NAME'], 'pairs': len(pairs)}), flush=True)
    ThreadingHTTPServer(('0.0.0.0', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
