"""netctl sidecar of the owned gateway Pod: the ONLY holder of a network trace. It applies the pre-registered trace
(traces.py) to this Pod's own netns egress on the edge<->server path and records every application.

qdisc tree (same shape as real-topology-live/net_control.py, which is unchanged): root prio 1: (bands 3, priomap -> 1:3
unshaped default), band 1:1 = gateway -> cloud Pod IPs, band 1:2 = gateway -> edge Pod IPs (u32 /32 dst filters); each
shaped band is tbf (rate, burst 4096 B, limit 131072 B) -> netem (delay, jitter, loss, limit 64 packets).

Difference from net_control.py: a phase boundary never deletes and re-creates the root. The tree is installed once per
cell (phase 0); every later phase is an in-place `tc qdisc change` of each band's tbf and netem (RTM_NEWQDISC with
NLM_F_REPLACE via pyroute2 'change'), so queued packets, handles, filters and counters survive the boundary (verified on
the real kernel by test_netctl.RealKernelChange). Teardown (root delete) happens only between cells after the collector's
drain/idle barrier, and on exit. pyroute2 0.9.6 has no TCA_NETEM_PRNG_SEED, so netem loss/jitter draws are not seeded:
the trace is reproducible at the configuration level only, never packet by packet.

HTTP (port 8081, this Pod netns): GET /health, GET /status?label=L,
POST /arm {run_id, label, trace, trace_sha256, cloud[ip], edge[ip]} | /start {run_id, label, delay_s} |
     /restore {run_id, label} (cancel pending phases, change to good) | /teardown {run_id} | /snapshot {run_id}
Only approved, frozen trace names are accepted (never phases from the wire). Times are this Pod's monotonic clock; the
collector aligns them with the generator's clock from its own call round trips (it never subtracts cross-host clocks).
"""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import traces  # noqa: E402

ROOT_HANDLE = 0x10000
BANDS = ((1, 'cloud'), (2, 'edge'))
TBF = dict(burst=4096, limit=131072)  # frozen from net_control.py
NETEM_LIMIT = 64                     # frozen from net_control.py: TCP drops/retransmissions are measured, not hidden
MAX_DELAY_S = 10.0
MAX_BODY = 64 * 1024


def pod_ip(value):
    result = str(ipaddress.IPv4Address(value))
    if not result.startswith('10.'):
        raise ValueError('Only frozen cluster Pod IPs are allowed')
    return result


def utc():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


class Pyroute2Tc:
    """tc in THIS process's netns only (env RESEARCH_LINK_CONTROL=owned-pod-netns, as net_control.py)."""

    def __init__(self, ifname='eth0'):
        if os.environ.get('RESEARCH_LINK_CONTROL') != 'owned-pod-netns':
            raise RuntimeError('Refusing unowned network namespace')
        from pyroute2 import IPRoute
        self.IPRoute, self.ifname = IPRoute, ifname

    def _index(self, ip):
        return ip.link_lookup(ifname=self.ifname)[0]

    @staticmethod
    def _roots(ip, index):
        return [q['handle'] for q in ip.get_qdiscs(index=index) if q['parent'] == 0xffffffff]

    @staticmethod
    def _tbf(cond):
        return dict(rate=f"{cond['mbps']}mbit", **TBF)

    @staticmethod
    def _netem(cond):
        return dict(delay=cond['delay_ms'] * 1000, jitter=cond['jitter_ms'] * 1000, loss=cond['loss_pct'], limit=NETEM_LIMIT)

    def install(self, cond, cloud, edge):
        with self.IPRoute() as ip:
            index = self._index(ip)
            roots = self._roots(ip, index)
            if ROOT_HANDLE in roots:
                raise RuntimeError('owned root qdisc already present; teardown first')
            if any(h != 0 for h in roots):
                raise RuntimeError('Unrelated root qdisc; refusing replacement')
            ip.tc('add', 'prio', index, '1:', bands=3, priomap=[2] * 16)
            targets = dict(cloud=cloud, edge=edge)
            for band, zone in BANDS:
                ip.tc('add', 'tbf', index, f'{band}0:', parent=f'1:{band}', **self._tbf(cond))
                ip.tc('add', 'netem', index, f'{band}1:', parent=f'{band}0:1', **self._netem(cond))
                for priority, value in enumerate(targets[zone], band * 100):
                    dst = pod_ip(value)
                    ip.tc('add-filter', 'u32', index, parent='1:', prio=priority, protocol=0x0800, target=f'1:{band}',
                          keys=[f'{hex(int(ipaddress.IPv4Address(dst)))}/0xffffffff+16'])

    def change(self, cond):
        with self.IPRoute() as ip:
            index = self._index(ip)
            if ROOT_HANDLE not in self._roots(ip, index):
                raise RuntimeError('owned root qdisc missing; refusing to create one during a trace')
            for band, _ in BANDS:
                ip.tc('change', 'tbf', index, f'{band}0:', parent=f'1:{band}', **self._tbf(cond))
                ip.tc('change', 'netem', index, f'{band}1:', parent=f'{band}0:1', **self._netem(cond))

    def teardown(self):
        with self.IPRoute() as ip:
            index = self._index(ip)
            if ROOT_HANDLE in self._roots(ip, index):
                ip.tc('del', 'prio', index, '1:')

    def snapshot(self):
        with self.IPRoute() as ip:
            index = self._index(ip)
            summary = []
            for q in ip.get_qdiscs(index=index):
                stats = q.get_attr('TCA_STATS')
                if stats is None:
                    raise RuntimeError('Kernel queue stats unavailable; refuse false idle')
                summary.append(dict(handle=q['handle'], parent=q['parent'], kind=q.get_attr('TCA_KIND'), bytes=int(stats['bytes']),
                                    packets=int(stats['packets']), drops=int(stats['drop']), overlimits=int(stats['overlimits']),
                                    qlen=int(stats['qlen']), backlog=int(stats['backlog'])))
            filters = len(list(ip.get_filters(index=index, parent=ROOT_HANDLE))) if ROOT_HANDLE in self._roots(ip, index) else 0
            # Parent/child stats overlap (tbf counts what its netem child holds); never sum them as traffic.
            return dict(observer='native-TCA_STATS', netns=os.readlink('/proc/self/ns/net'), qdisc_summary=summary,
                        installed=ROOT_HANDLE in self._roots(ip, index), filters=filters)


class Runner:
    """Arms one cell's trace (install phase 0), runs it from a start command (in-place change per boundary), restores and
    tears down. clock/wait are the time seam (wait(event, timeout) -> event set)."""

    def __init__(self, tc, clock=time.monotonic, wait=None, run_id=None, wall=utc):
        self.tc, self.clock, self.wall, self.run_id = tc, clock, wall, run_id
        self.wait = wait or (lambda event, timeout: event.wait(timeout))
        self.lock, self.cells, self.current, self.installed = threading.Lock(), {}, None, False

    def _check_run(self, body):
        if self.run_id is None or body.get('run_id') != self.run_id:
            raise ValueError('run_id does not match this gateway Pod')

    def _snapshot(self):
        try:
            return self.tc.snapshot()
        except Exception as error:
            return dict(error=repr(error)[:300])

    def _event(self, cell, kind, **fields):
        cell['events'].append(dict(kind=kind, local=self.clock(), wall_utc=self.wall(), **fields))

    def arm(self, body):
        self._check_run(body)
        name, label = body.get('trace'), body.get('label')
        if name not in traces.TRACES:
            raise ValueError(f'unapproved trace {name!r}')
        if body.get('trace_sha256') != traces.trace_sha256(name):
            raise ValueError('trace_sha256 does not match the frozen trace table')
        cloud, edge = [pod_ip(v) for v in body.get('cloud') or []], [pod_ip(v) for v in body.get('edge') or []]
        if not cloud or not edge:
            raise ValueError('cloud and edge targets are required')
        with self.lock:
            if self.installed:
                raise ValueError('a trace is still installed; restore and teardown first')
            if not label or label in self.cells:
                raise ValueError('label missing or reused')
            phases = traces.TRACES[name]
            cell = dict(label=label, trace=name, trace_sha256=body['trace_sha256'], state='armed', events=[], cancel=threading.Event(),
                        thread=None, cloud=cloud, edge=edge)
            self.tc.install(traces.condition(phases[0]), cloud, edge)
            self.installed, self.current = True, label
            self.cells[label] = cell
            self._event(cell, 'install', phase=0, name=phases[0]['name'], condition=traces.condition(phases[0]), stats=self._snapshot())
        return dict(status='armed', label=label, trace=name)

    def start(self, body):
        self._check_run(body)
        delay = float(body.get('delay_s', 0))
        if not 0 < delay <= MAX_DELAY_S:
            raise ValueError(f'delay_s must be in (0, {MAX_DELAY_S}]')
        with self.lock:
            cell = self.cells.get(body.get('label'))
            if cell is None or cell['state'] != 'armed' or self.current != cell['label']:
                raise ValueError('label is not the armed, unstarted trace')
            received = self.clock()
            cell.update(state='running', received_local=received, start_local=received + delay, delay_s=delay)
            cell['thread'] = threading.Thread(target=self._run, args=(cell,), daemon=True)
            cell['thread'].start()
        return dict(status='started', label=cell['label'], received_local=received, start_local=cell['start_local'])

    def _sleep_until(self, cell, deadline):
        while True:
            remaining = deadline - self.clock()
            if remaining <= 0:
                return True
            if self.wait(cell['cancel'], min(remaining, 0.05)) or cell['cancel'].is_set():
                return False

    def _run(self, cell):
        phases, t0 = traces.TRACES[cell['trace']], cell['start_local']
        for i, p in enumerate(phases[1:], 1):
            planned = t0 + p['start_s']
            if not self._sleep_until(cell, planned):
                return
            begun = self.clock()
            try:
                self.tc.change(traces.condition(p))
            except Exception as error:
                self._event(cell, 'change', phase=i, name=p['name'], planned_offset_s=p['start_s'], planned_local=planned,
                            apply_lag_ms=(begun - planned) * 1000, call_ms=(self.clock() - begun) * 1000, condition=traces.condition(p),
                            ok=False, error=repr(error)[:300])
                cell['state'] = 'failed'
                self._restore_good(cell, 'restore_after_error')
                return
            ended = self.clock()
            self._event(cell, 'change', phase=i, name=p['name'], planned_offset_s=p['start_s'], planned_local=planned,
                        applied_start_local=begun, applied_end_local=ended, apply_lag_ms=(begun - planned) * 1000,
                        call_ms=(ended - begun) * 1000, condition=traces.condition(p), ok=True, stats=self._snapshot())
        if self._sleep_until(cell, t0 + phases[-1]['end_s']):
            self._event(cell, 'window_end', planned_offset_s=phases[-1]['end_s'], stats=self._snapshot())
            cell['state'] = 'completed'

    def _restore_good(self, cell, kind):
        begun = self.clock()
        try:
            self.tc.change(traces.GOOD)
            self._event(cell, kind, condition=dict(traces.GOOD), ok=True, call_ms=(self.clock() - begun) * 1000, stats=self._snapshot())
        except Exception as error:
            self._event(cell, kind, condition=dict(traces.GOOD), ok=False, error=repr(error)[:300])

    def join(self, label, timeout=None):
        thread = self.cells[label]['thread']
        if thread is not None:
            thread.join(timeout)

    def _stop(self, cell):
        cell['cancel'].set()
        if cell['thread'] is not None:
            cell['thread'].join(5)

    def restore(self, body):
        """Cancel pending phases, then an in-place change to good (no flush); the tree stays installed for the drain."""
        self._check_run(body)
        with self.lock:
            cell = self.cells.get(body.get('label'))
            if cell is None:
                raise ValueError('unknown label')
            self._stop(cell)
            if self.installed and self.current == cell['label']:
                self._restore_good(cell, 'restore')
            if cell['state'] in ('armed', 'running'):
                cell['state'] = 'restored'
        return self.status(cell['label'])

    def teardown(self, body):
        """Root delete: only between cells (after the drain barrier) and at exit. Stats are snapshotted first."""
        self._check_run(body)
        return self.teardown_all()

    def teardown_all(self):
        with self.lock:
            cell = self.cells.get(self.current)
            if cell is not None:
                self._stop(cell)
                self._event(cell, 'pre_teardown', stats=self._snapshot())
            error = None
            try:
                self.tc.teardown()
                self.installed = False
            except Exception as exc:
                error = repr(exc)[:300]
            if cell is not None:
                self._event(cell, 'teardown', ok=error is None, error=error)
            self.current = None
        return dict(status='torn_down' if error is None else 'teardown_error', error=error, installed=self.installed)

    def status(self, label):
        cell = self.cells[label]
        return {k: v for k, v in cell.items() if k not in ('cancel', 'thread')}

    def health(self):
        return dict(ready=True, run_id=self.run_id, installed=self.installed, current=self.current, cells=sorted(self.cells),
                    traces={k: traces.trace_sha256(k) for k in traces.TRACES})


def serve(runner, host='0.0.0.0', port=8081):
    class Handler(BaseHTTPRequestHandler):
        timeout = 30

        def _send(self, status, value):
            raw = json.dumps(value, separators=(',', ':'), default=str).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            try:
                if url.path == '/health':
                    self._send(200, runner.health())
                elif url.path == '/status':
                    self._send(200, runner.status(urllib.parse.parse_qs(url.query)['label'][0]))
                else:
                    self._send(404, dict(error='unknown path'))
            except (KeyError, ValueError) as error:
                self._send(400, dict(error=repr(error)[:300]))

        def do_POST(self):
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= MAX_BODY:
                    raise ValueError('body size')
                body = json.loads(self.rfile.read(size))
                routes = {'/arm': runner.arm, '/start': runner.start, '/restore': runner.restore, '/teardown': runner.teardown,
                          '/snapshot': lambda b: (runner._check_run(b), runner._snapshot())[1]}
                if self.path not in routes:
                    raise KeyError(self.path)
                self._send(200, routes[self.path](body))
            except (KeyError, ValueError, TypeError) as error:
                self._send(400, dict(error=repr(error)[:300]))
            except Exception as error:
                self._send(500, dict(error=repr(error)[:300]))

        def log_message(self, *args):
            pass
    return ThreadingHTTPServer((host, port), Handler)


def main():
    runner = Runner(Pyroute2Tc(os.environ.get('NETCTL_IFNAME', 'eth0')), run_id=os.environ['RUN_ID'])
    server = serve(runner, port=int(os.environ.get('NETCTL_PORT', '8081')))
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(0)))
    print(json.dumps(dict(event='netctl_ready', run_id=runner.run_id)), flush=True)
    try:
        server.serve_forever()
    finally:
        runner.teardown_all()  # never leave the owned qdisc behind on exit/error


if __name__ == '__main__':
    main()
