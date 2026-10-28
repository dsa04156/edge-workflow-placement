"""In-Pod diagnostic (task-owned, stdlib + the shared proc.py): time `python3 -m pip --version` on the dev0009 Jetson
under the same image/env/limits as the CUDA worker, before and after locally generated writes to /work.

No network fetch and no model: the 7.17 GB cache download is replaced by writing zero blocks (1 MiB, sha256-hashed like
bootstrap.fetch) into /work, so the same emptyDir/disk/memory-cgroup state is produced without re-downloading.
Steps (each child bounded by proc.run: own process group, finite timeout, bounded stdout/stderr tails):
  baseline   python -c pass x2, pip --version x3 (the exact bootstrap argv)
  low dose   write to WRITE_LOW_BYTES, pip --version immediately; os.sync() (bounded); pip --version
  full dose  write to WRITE_FULL_BYTES (the GPU group footprint), pip --version immediately (the failed-run moment);
             os.sync() (bounded); pip --version
  cleanup    remove the fill files, bounded sync
PROBE_PLAN=fsync (r2, no global sync): pip baseline; write WRITE_FULL with os.fsync of every task-owned fill file before
close (the proposed bootstrap adaptation), pip immediately (cold image cache, no dirty backlog); remove the fill; write
WRITE_FULL again without fsync (control replicate of r1's full dose), pip twice; remove the fill.
A sampler records the container cgroup (memory.current/max, file_dirty/file_writeback, memory.events, memory/io
pressure, cpu throttling) and node /proc/meminfo Dirty/Writeback every SAMPLE_S. /ready is 200 at once; /health
serves the whole state (partial results stay readable if a step hangs or the probe deadline passes).
"""
import hashlib
import http.server
import json
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, os.environ.get('CONFIG_DIR', '/config'))
import proc  # noqa: E402

WORK = Path(os.environ.get('WORK_DIR', '/work'))
PIP = [sys.executable, '-m', 'pip', '--version']
CHILD_TIMEOUT_S = float(os.environ.get('CHILD_TIMEOUT_S', '180'))
SYNC_TIMEOUT_S = float(os.environ.get('SYNC_TIMEOUT_S', '600'))
WRITE_LOW = int(os.environ.get('WRITE_LOW_BYTES', str(int(2.5 * 2**30))))
WRITE_FULL = int(os.environ.get('WRITE_FULL_BYTES', '7170000000'))
WRITE_DEADLINE_S = float(os.environ.get('WRITE_DEADLINE_S', '1200'))
PROBE_DEADLINE_S = float(os.environ.get('PROBE_DEADLINE_S', '2700'))
SAMPLE_S = float(os.environ.get('SAMPLE_S', '2'))
SAMPLES_KEPT = 1500
CG = Path('/sys/fs/cgroup')
STAT_KEYS = ('anon', 'file', 'file_dirty', 'file_writeback', 'active_file', 'inactive_file', 'pgscan', 'pgsteal',
             'workingset_refault_file', 'pgmajfault')
MEMINFO_KEYS = ('MemTotal', 'MemAvailable', 'Cached', 'Dirty', 'Writeback')

# Every top-level key exists from the start (assignments never resize STATE while /health serializes it).
STATE = dict(phase='starting', started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), finished_utc=None, error=None,
             skipped_from=None, steps=[], samples=[], running={}, events=[],
             config=dict(pip=PIP, child_timeout_s=CHILD_TIMEOUT_S, sync_timeout_s=SYNC_TIMEOUT_S, write_low=WRITE_LOW,
                         write_full=WRITE_FULL, probe_deadline_s=PROBE_DEADLINE_S, cwd=os.getcwd(), cpu_count=os.cpu_count(),
                         env={k: os.environ.get(k) for k in ('HOME', 'TMPDIR', 'PYTHONDONTWRITEBYTECODE', 'NODE_NAME', 'POD_UID',
                                                              'OMP_NUM_THREADS', 'PIP_CONFIG_FILE')}))
LOCK = threading.Lock()
T0 = time.monotonic()


def read(path, limit=4000):
    try:
        return Path(path).read_text(errors='replace')[:limit]
    except OSError as error:
        return f'unreadable: {type(error).__name__}'


def kv(text, keys=None):
    out = {}
    for line in text.splitlines():
        parts = line.replace(':', ' ').split()
        if len(parts) >= 2 and (keys is None or parts[0] in keys):
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return out


def pressure(text):
    """'some avg10=.. total=..' lines -> {some: {avg10, total}, full: {...}}"""
    out = {}
    for line in text.splitlines():
        kind, *fields = line.split()
        out[kind] = {k: float(v) for k, v in (f.split('=') for f in fields if '=' in f)}
    return out or text[:200]


def snapshot():
    return dict(t=round(time.monotonic()-T0, 2), utc=time.strftime('%H:%M:%S', time.gmtime()),
                mem_current=read(CG/'memory.current', 40).strip(), mem_max=read(CG/'memory.max', 40).strip(),
                mem_stat=kv(read(CG/'memory.stat', 20000), STAT_KEYS), mem_events=kv(read(CG/'memory.events', 2000)),
                mem_pressure=pressure(read(CG/'memory.pressure', 400)), io_pressure=pressure(read(CG/'io.pressure', 400)),
                cpu_stat=kv(read(CG/'cpu.stat', 2000), ('usage_usec', 'nr_throttled', 'throttled_usec')),
                node_meminfo_kB=kv(read('/proc/meminfo', 8000), MEMINFO_KEYS))


def sampler():
    while True:
        sample = snapshot()
        with LOCK:
            STATE['samples'].append(sample)
            del STATE['samples'][:-SAMPLES_KEPT]
        time.sleep(SAMPLE_S)


def done(record, **fields):
    with LOCK:  # /health serializes under LOCK; records are only mutated under it
        record.update(fields, end=snapshot())


def step(name, **fields):
    record = dict(name=name, start=snapshot(), **fields)
    with LOCK:
        STATE['steps'].append(record)
    return record


def child(name, argv):
    record = step(name, argv=argv)
    event = proc.run(argv, WORK/'logs', name, CHILD_TIMEOUT_S, state=STATE)
    done(record, seconds=round(event['seconds'], 3), timed_out=event['timed_out'], returncode=event['returncode'],
         stdout_tail=event['stdout_tail'][-600:], stderr_tail=event['stderr_tail'][-2000:])


def write_to(name, target, fsync=False):
    """Write zero 1 MiB blocks (sha256 each, as fetch does) into /work/fill until `target` bytes exist; fsync=True
    flushes each (task-owned) file before close, otherwise no fsync."""
    record = step(name, target_bytes=target, fsync=fsync)
    fsync_s = 0.0
    fill = WORK/'fill'
    fill.mkdir(parents=True, exist_ok=True)
    have = sum(p.stat().st_size for p in fill.glob('*.bin'))
    block, h, started = bytes(1 << 20), hashlib.sha256(), time.monotonic()
    deadline, index, wrote = started + WRITE_DEADLINE_S, len(list(fill.glob('*.bin'))), 0
    while have < target:
        with (fill/f'fill-{index:03d}.bin').open('wb') as out:
            for _ in range(256):  # 256 MiB per file
                if have >= target or time.monotonic() > deadline:
                    break
                n = min(len(block), target-have)
                h.update(block[:n])
                out.write(block[:n])
                have += n
                wrote += n
            if fsync:
                flushed = time.monotonic()
                out.flush()
                os.fsync(out.fileno())
                fsync_s += time.monotonic()-flushed
        index += 1
        if time.monotonic() > deadline:
            done(record, deadline_exceeded=True)
            break
    seconds = time.monotonic()-started
    done(record, bytes_written=wrote, bytes_total=have, seconds=round(seconds, 3), MBps=round(wrote/1e6/max(seconds, 1e-9), 2),
         fsync_s=round(fsync_s, 3))


def sync(name):
    record = step(name)
    flushed, started = threading.Event(), time.monotonic()
    threading.Thread(target=lambda: (os.sync(), flushed.set()), daemon=True).start()
    finished = flushed.wait(SYNC_TIMEOUT_S)
    done(record, seconds=round(time.monotonic()-started, 3), finished=finished)


def remove_fill():
    record = step('remove-fill')
    removed = 0
    for path in sorted((WORK/'fill').glob('*.bin')):
        path.unlink()
        removed += 1
    done(record, files_removed=removed)


PLAN_R1 = [lambda: child('python-pass-1', [sys.executable, '-c', 'pass']), lambda: child('python-pass-2', [sys.executable, '-c', 'pass']),
        lambda: child('pip-baseline-1', PIP), lambda: child('pip-baseline-2', PIP), lambda: child('pip-baseline-3', PIP),
        lambda: write_to('write-low', WRITE_LOW), lambda: child('pip-after-write-low', PIP), lambda: sync('sync-low'),
        lambda: child('pip-after-sync-low', PIP),
        lambda: write_to('write-full', WRITE_FULL), lambda: child('pip-after-write-full', PIP), lambda: sync('sync-full'),
        lambda: child('pip-after-sync-full', PIP),
        remove_fill, lambda: sync('sync-cleanup')]
PLAN_FSYNC = [lambda: child('pip-baseline-1', PIP),
              lambda: write_to('write-full-fsync', WRITE_FULL, fsync=True), lambda: child('pip-after-write-full-fsync', PIP),
              remove_fill,
              lambda: write_to('write-full', WRITE_FULL), lambda: child('pip-after-write-full', PIP),
              lambda: child('pip-after-write-full-2', PIP), remove_fill]
PLAN = dict(r1=PLAN_R1, fsync=PLAN_FSYNC)[os.environ.get('PROBE_PLAN', 'r1')]
STATE['config']['plan'] = os.environ.get('PROBE_PLAN', 'r1')


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/ready':
            body = b'{"status":"ready"}'
        elif self.path == '/health':
            try:
                live = proc.live(STATE)
            except RuntimeError as error:  # a child finished while its tails were read
                live = dict(error=repr(error))
            with LOCK:  # proc.run's own 'running'/'events' entries are summarized per step instead
                body = json.dumps(dict({k: v for k, v in STATE.items() if k not in ('running', 'events')}, live=live,
                                       elapsed_s=round(time.monotonic()-T0, 1))).encode()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def run_plan():
    try:
        for index, item in enumerate(PLAN):
            if time.monotonic()-T0 > PROBE_DEADLINE_S:
                STATE.update(phase='probe_deadline', skipped_from=index)
                return
            STATE['phase'] = f'step-{index}'
            item()
        STATE['phase'] = 'done'
    except Exception as error:
        STATE.update(phase='probe_failed', error=repr(error)[:1500])
    finally:
        STATE['finished_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def main():
    Path(os.environ.get('TMPDIR') or '/tmp').mkdir(parents=True, exist_ok=True)
    server = http.server.ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('WORKER_PORT', '8080'))), Handler)
    threading.Thread(target=sampler, daemon=True).start()
    threading.Thread(target=run_plan, daemon=True).start()
    print(json.dumps(dict(event='probe_started')), flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
