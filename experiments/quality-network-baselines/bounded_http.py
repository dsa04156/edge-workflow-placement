"""Absolute-deadline HTTP POST for the baseline-first DAG runtime (stdlib only; shipped next to http_runtime.py).

urllib's timeout is a per-socket-operation inactivity timeout: a peer that trickles bytes can hold a request far past
its budget, and json.load(response) reads an unbounded body. exchange() bounds the WHOLE transfer (connect, send,
status, headers, body) by one monotonic deadline -- a timer shuts the socket when it passes -- and refuses response
bodies above max_body. post() keeps the http_runtime.request contract (binary framing, JSON reply, HTTPError on >= 400)
so callers' error handling is unchanged; timeout is then the absolute budget, not an inactivity limit. control() is the
same bound for the collector's control plane (GET when body is None, else post), the qualify.http contract.
"""
import http.client
import io
import json
import socket
import threading
import time
import urllib.error
import urllib.parse

from http_runtime import encode_body

MAX_RESPONSE_BODY = 32 * 1024 * 1024


class DeadlineExceeded(TimeoutError):
    def __init__(self, timeout_s):
        super().__init__(f'timed out: absolute transfer deadline {timeout_s:.3f}s exceeded')


def exchange(url, raw, content_type, timeout_s, max_body=MAX_RESPONSE_BODY):
    """-> (status, body bytes, content type); raw None sends a GET. Raises DeadlineExceeded past timeout_s, ValueError
    above max_body."""
    if timeout_s <= 0:
        raise DeadlineExceeded(timeout_s)
    parts = urllib.parse.urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout_s)
    expired, held = threading.Event(), []  # held: the connected socket (conn.sock is dropped once the reply is read)

    def expire():
        expired.set()
        for sock in held + ([conn.sock] if conn.sock is not None else []):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
    deadline = time.monotonic() + timeout_s
    timer = threading.Timer(timeout_s, expire)
    timer.daemon = True
    timer.start()
    response = None
    try:
        target = (parts.path or '/') + (('?' + parts.query) if parts.query else '')
        if raw is None:
            conn.request('GET', target, headers={'Connection': 'close'})
        else:
            conn.request('POST', target, raw, {'Content-Type': content_type, 'Content-Length': str(len(raw)), 'Connection': 'close'})
        held.append(conn.sock)
        if expired.is_set():
            expire()
        response = conn.getresponse()
        declared = response.getheader('Content-Length')
        if declared is not None and int(declared) > max_body:
            raise ValueError(f'response body {declared} B exceeds {max_body} B')
        chunks, size = [], 0
        while True:
            chunk = response.read(min(65536, max_body + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > max_body:
                raise ValueError(f'response body exceeds {max_body} B')
        if expired.is_set() or time.monotonic() > deadline:
            raise DeadlineExceeded(timeout_s)
        return response.status, b''.join(chunks), response.getheader('Content-Type', 'application/json')
    except (OSError, http.client.HTTPException) as error:
        if expired.is_set() or time.monotonic() > deadline:
            raise DeadlineExceeded(timeout_s) from error
        raise
    finally:
        timer.cancel()
        if response is not None:
            response.close()
        conn.close()


def post(url, data, timeout=60, max_body=MAX_RESPONSE_BODY):
    """http_runtime.request with an absolute transfer deadline and a response-body cap."""
    raw, content_type = encode_body(data)
    status, body, _ = exchange(url, raw, content_type, timeout, max_body)
    if status >= 400:
        raise urllib.error.HTTPError(url, status, 'upstream error', {}, io.BytesIO(body))
    return json.loads(body)


def control(url, body=None, timeout=60, max_body=MAX_RESPONSE_BODY):
    """qualify.http (GET when body is None, else POST; JSON reply; HTTPError on >= 400) with the absolute deadline and cap."""
    if body is not None:
        return post(url, body, timeout, max_body)
    status, raw, _ = exchange(url, None, None, timeout, max_body)
    if status >= 400:
        raise urllib.error.HTTPError(url, status, 'control error', {}, io.BytesIO(raw))
    return json.loads(raw)
