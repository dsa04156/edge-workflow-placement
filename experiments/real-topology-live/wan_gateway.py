"""Explicit HTTP relay; kernel egress classes share one cap per zone/direction.

This splits TCP connections. Unlimited relay is a mandatory separate control.
Endpoints are a frozen allow-list; no HTTP_PROXY or arbitrary URL forwarding.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request

slots = threading.BoundedSemaphore(96)
lock = threading.Lock()
counters = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        raw = json.dumps({'status': 'ready', 'counters': counters}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if not slots.acquire(blocking=False):
            self.send_error(429)
            return
        started = time.monotonic()
        target = 'invalid'
        try:
            parts = self.path.split('/', 3)
            if len(parts) != 4 or parts[1] != 'to':
                raise ValueError('Only named relay endpoints are allowed')
            target, path = parts[2:]
            peers = json.loads(Path('/work/peers.json').read_text())
            url = peers[target]['url'] + '/' + path
            size = int(self.headers.get('Content-Length', 0))
            if size < 1 or size > 16*1024*1024:
                raise ValueError('Invalid relay body')
            body = self.rfile.read(size)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(url, body, {'Content-Type': self.headers.get('Content-Type', 'application/json')})
            try:
                response = opener.open(req, timeout=120)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                raw, status = response.read(), response.code
            with lock:
                item = counters.setdefault(target, dict(requests=0, request_body_bytes=0, response_body_bytes=0))
                item['requests'] += 1
                item['request_body_bytes'] += size
                item['response_body_bytes'] += len(raw)
        except Exception as error:
            status, raw = 502, json.dumps({'error': repr(error)}).encode()
        finally:
            slots.release()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass
        print(json.dumps(dict(target=target, status=status, elapsed_ms=(time.monotonic()-started)*1000)), flush=True)

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
