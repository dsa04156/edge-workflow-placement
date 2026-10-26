"""Shared application gateway for cross-zone DAG traffic (owned server Pod), hardened version of the
real-topology-live/wan_gateway.py relay pattern (that file is unchanged).

Differences from wan_gateway.py: finite socket inactivity timeout AND absolute read deadline for request line + headers +
body; the whole upstream transfer (connect .. last response byte, bounded_http.exchange) ends at min(UPSTREAM_CAP_S, the
request's own budget_s read from the binary-framing metadata) and upstream bodies above MAX_BODY are refused (502); a peer
allow-list that is frozen once (POST /peers, later changes refused); per-target counters with an explicit drain/reset
call that refuses while relayed requests are still active, so accounting cannot leak across policy runs.
Bandwidth/delay are NOT implemented here: an optional owned-netns tc qdisc (net_control.py in the netctl sidecar)
shapes this Pod's egress. That is a gateway constraint, not physical link manipulation; the unshaped relay is the
mandatory control for the relay's own overhead.
  POST /to/<peer>/<path>  relay (allow-listed peer only)      GET /health  counters, active, peers sha, frozen
  POST /peers {peers{key:{url,zone}}}  freeze once           POST /reset {label}  returns and clears counters (active==0 only)
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
import socket
import struct
import threading
import time

from bounded_http import exchange

UPSTREAM_CAP_S = float(os.environ.get('GATEWAY_UPSTREAM_CAP_S', '60'))
BODY_TIMEOUT_S = float(os.environ.get('GATEWAY_BODY_TIMEOUT_S', '30'))
READ_DEADLINE_S = float(os.environ.get('GATEWAY_READ_DEADLINE_S', '90'))
MAX_BODY = 16 * 1024 * 1024
SLOTS = int(os.environ.get('GATEWAY_SLOTS', '96'))


def budget_of(raw, content_type):
    """budget_s from the binary-framing JSON metadata (http_runtime.encode_body) or a JSON body; None if absent/invalid."""
    try:
        if content_type == 'application/x-edge-binary':
            length = struct.unpack('!I', raw[:4])[0]
            meta = json.loads(raw[4:4 + length])
        else:
            meta = json.loads(raw)
        value = float(meta.get('budget_s')) if isinstance(meta, dict) and meta.get('budget_s') is not None else None
        return value if value and value > 0 else None
    except (ValueError, struct.error, TypeError):
        return None


class Relay:
    def __init__(self, clock=time.monotonic, allowed_prefixes=('http://10.',)):
        self.allowed_prefixes = tuple(allowed_prefixes)
        self.lock, self.slots = threading.Lock(), threading.BoundedSemaphore(SLOTS)
        self.peers, self.peers_sha, self.counters, self.active, self.resets = None, None, {}, 0, []
        self.clock = clock

    def freeze(self, peers):
        with self.lock:
            if self.peers is not None:
                return 409, dict(error='peers already frozen', peers_sha256=self.peers_sha)
            for key, value in peers.items():
                if not str(value.get('url', '')).startswith(self.allowed_prefixes):
                    raise ValueError(f'peer {key}: only cluster Pod IP URLs are allowed')
            self.peers = {k: dict(url=v['url'], zone=v.get('zone')) for k, v in peers.items()}
            self.peers_sha = hashlib.sha256(json.dumps(self.peers, sort_keys=True).encode()).hexdigest()
            return 200, dict(status='frozen', peers_sha256=self.peers_sha, peers=sorted(self.peers))

    def health(self):
        with self.lock:
            return dict(status='ready', frozen=self.peers is not None, peers_sha256=self.peers_sha, active=self.active,
                        counters=json.loads(json.dumps(self.counters)), resets=len(self.resets), upstream_cap_s=UPSTREAM_CAP_S,
                        body_timeout_s=BODY_TIMEOUT_S, read_deadline_s=READ_DEADLINE_S, slots=SLOTS)

    def reset(self, label):
        with self.lock:
            if self.active:
                return 409, dict(error='relayed requests still active; drain first', active=self.active)
            snapshot = dict(label=label, at=self.clock(), counters=self.counters)
            self.counters = {}
            self.resets.append(dict(label=label, at=snapshot['at']))
            return 200, snapshot

    def count(self, target, **inc):
        with self.lock:
            item = self.counters.setdefault(target, dict(requests=0, request_body_bytes=0, response_body_bytes=0, errors=0, timeouts=0,
                                                          upstream_ms_total=0.0))
            for k, v in inc.items():
                item[k] += v

    def relay(self, target, path, raw, content_type):
        """-> (status, body bytes, content type). Never forwards to a peer outside the frozen allow-list."""
        if self.peers is None:
            return 503, json.dumps(dict(error='peers not frozen')).encode(), 'application/json'
        if target not in self.peers:
            return 403, json.dumps(dict(error='peer not allow-listed')).encode(), 'application/json'
        if not self.slots.acquire(blocking=False):
            return 429, json.dumps(dict(error='gateway relay slots exhausted', slots=SLOTS)).encode(), 'application/json'
        with self.lock:
            self.active += 1
        started = self.clock()
        timeout = min(UPSTREAM_CAP_S, budget_of(raw, content_type) or UPSTREAM_CAP_S)
        try:
            status, body, _ = exchange(self.peers[target]['url'] + '/' + path, raw, content_type, timeout, MAX_BODY)
            self.count(target, requests=1, request_body_bytes=len(raw), response_body_bytes=len(body),
                       upstream_ms_total=(self.clock()-started)*1000, errors=0 if status < 400 else 1)
            return status, body, 'application/json'
        except Exception as error:
            timed_out = isinstance(error, TimeoutError) or 'timed out' in repr(error).lower()
            self.count(target, requests=1, request_body_bytes=len(raw), errors=1, timeouts=1 if timed_out else 0,
                       upstream_ms_total=(self.clock()-started)*1000)
            return 504 if timed_out else 502, json.dumps(dict(error=repr(error)[:300], gateway_timeout_s=timeout)).encode(), 'application/json'
        finally:
            with self.lock:
                self.active -= 1
            self.slots.release()


def serve(relay, host='0.0.0.0', port=8080):
    class Handler(BaseHTTPRequestHandler):
        timeout = BODY_TIMEOUT_S

        def setup(self):
            super().setup()
            self.read_done = threading.Event()
            self.timer = threading.Timer(READ_DEADLINE_S, self._expire)
            self.timer.daemon = True
            self.timer.start()

        def _expire(self):
            if not self.read_done.is_set():
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        def finish(self):
            self.timer.cancel()
            try:
                super().finish()
            except OSError:
                pass

        def _send(self, status, raw, content_type='application/json'):
            if isinstance(raw, dict):
                raw = json.dumps(raw).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Connection', 'close')
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            self.read_done.set()
            self._send(200, relay.health()) if self.path == '/health' else self._send(404, dict(error='unknown path'))

        def do_POST(self):
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= MAX_BODY:
                    self.read_done.set()
                    self._send(413, dict(error='invalid relay body size', limit=MAX_BODY))
                    return
                raw = self.rfile.read(size)
                self.read_done.set()
                if len(raw) != size:
                    return
            except OSError:
                return
            parts = self.path.split('/', 3)
            if self.path == '/peers':
                try:
                    status, value = relay.freeze(json.loads(raw)['peers'])
                except (ValueError, KeyError) as error:
                    status, value = 400, dict(error=repr(error)[:300])
                self._send(status, value)
            elif self.path == '/reset':
                self._send(*relay.reset(json.loads(raw).get('label')))
            elif len(parts) == 4 and parts[1] == 'to':
                status, body, content_type = relay.relay(parts[2], parts[3], raw, self.headers.get('Content-Type', 'application/json'))
                self._send(status, body, content_type)
            else:
                self._send(404, dict(error='only /to/<peer>/<path>, /peers, /reset'))

        def log_message(self, *args):
            pass

    class Server(ThreadingHTTPServer):
        daemon_threads = True
    return Server((host, port), Handler)


if __name__ == '__main__':
    serve(Relay(), port=int(os.environ.get('GATEWAY_PORT', '8080'))).serve_forever()
