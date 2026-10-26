"""Finite, logged subprocesses for bootstrap installs, dependency downloads and the Mobilint compile child.

Every child gets its own process group (start_new_session) and a finite deadline. stdout/stderr are drained
continuously by reader threads; retained on-disk output is bounded per stream (the first `head_bytes` are written as
they arrive, the last `tail_bytes` are kept in memory and appended at the end after a truncation marker), and total /
truncated byte counts are recorded. While running, state['running'][name] carries start time, counters and live
tails for HTTP health. On timeout only that owned group is terminated (SIGTERM, then SIGKILL after a grace period).
The end event records start/end, returncode, timed_out, byte counts and bounded tails.
"""
import collections
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

TAIL = 2000
HEAD_BYTES = int(os.environ.get('PROC_LOG_HEAD_BYTES', str(1 << 20)))
TAIL_BYTES = int(os.environ.get('PROC_LOG_TAIL_BYTES', str(256 << 10)))


class Drain:
    """Reads a pipe to EOF; keeps a bounded head on disk and a bounded tail in memory."""

    def __init__(self, pipe, path, head_bytes, tail_bytes):
        self.pipe, self.path, self.head_bytes = pipe, Path(path), head_bytes
        self.total, self.written, self.lock, self.write_error = 0, 0, threading.Lock(), None
        self.tail = collections.deque()
        self.tail_size, self.tail_bytes = 0, tail_bytes
        self.handle = self.path.open('wb')
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            for block in iter(lambda: self.pipe.read1(65536) if hasattr(self.pipe, 'read1') else self.pipe.read(65536), b''):
                with self.lock:
                    self.total += len(block)
                    # Memory tail first: the latest output survives even if the disk is full.
                    self.tail.append(block)
                    self.tail_size += len(block)
                    while self.tail and self.tail_size - len(self.tail[0]) >= self.tail_bytes:
                        self.tail_size -= len(self.tail.popleft())
                    room = self.head_bytes - self.written
                    if room > 0 and self.write_error is None:
                        try:
                            self.handle.write(block[:room])
                            self.handle.flush()
                            self.written += min(room, len(block))
                        except OSError as error:  # e.g. ENOSPC: record it, keep draining so the child never blocks
                            self.write_error = repr(error)[:300]
        finally:
            self.pipe.close()

    def tail_text(self, limit=TAIL):
        with self.lock:
            return b''.join(self.tail)[-limit:].decode(errors='replace')

    def finish(self):
        self.thread.join(timeout=10)
        with self.lock:
            truncated = max(0, self.total - self.written)
            if truncated and self.write_error is None:
                tail = b''.join(self.tail)[-min(self.tail_bytes, truncated):]
                omitted = truncated - len(tail)
                try:
                    self.handle.write(f'\n[... {omitted} bytes not retained; last {len(tail)} bytes follow ...]\n'.encode())
                    self.handle.write(tail)
                except OSError as error:
                    self.write_error = repr(error)[:300]
            try:
                self.handle.close()
            except OSError as error:
                self.write_error = self.write_error or repr(error)[:300]
            try:
                size = self.path.stat().st_size
            except OSError:
                size = None
            return dict(total_bytes=self.total, retained_head_bytes=self.written, truncated_bytes=truncated,
                        retained_file_bytes=size, drained=not self.thread.is_alive(), write_error=self.write_error)


def tail(path, limit=TAIL):
    try:
        with Path(path).open('rb') as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            return handle.read().decode(errors='replace')
    except OSError:
        return ''


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def run(command, log_dir, name, timeout_s, state=None, env=None, grace_s=3.0, head_bytes=None, tail_bytes=None):
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    out_path, err_path = log_dir/f'{name}.stdout', log_dir/f'{name}.stderr'
    started_utc, started = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), time.monotonic()
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, start_new_session=True, env=env)
    head, keep = head_bytes or HEAD_BYTES, tail_bytes or TAIL_BYTES
    drains = dict(stdout=Drain(child.stdout, out_path, head, keep), stderr=Drain(child.stderr, err_path, head, keep))
    pgid = child.pid
    if state is not None:
        state.setdefault('running', {})[name] = dict(started_utc=started_utc, pid=child.pid, timeout_s=timeout_s, stdout=str(out_path),
                                                     stderr=str(err_path), drains=drains)
    timed_out = False
    try:
        returncode = child.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pgid, sig)
            except ProcessLookupError:
                break
            deadline = time.monotonic() + grace_s
            while time.monotonic() < deadline and group_alive(pgid):
                time.sleep(0.05)
            if not group_alive(pgid):
                break
        returncode = child.wait()
    finally:
        if state is not None:
            state.get('running', {}).pop(name, None)
    if group_alive(pgid):  # descendants that outlived a normal exit are still owned: stop them too
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    counts = {k: d.finish() for k, d in drains.items()}  # join the drains first ...
    tails = {k: d.tail_text() for k, d in drains.items()}  # ... then read the tails, so short final output is never lost
    event = dict(kind='subprocess', name=name, command=[str(c) for c in command][:8], started_utc=started_utc,
                 finished_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), seconds=time.monotonic()-started,
                 timeout_s=timeout_s, timed_out=timed_out, returncode=returncode, pgid=pgid, stdout=str(out_path), stderr=str(err_path),
                 stdout_bytes=counts['stdout']['total_bytes'], stderr_bytes=counts['stderr']['total_bytes'], retention=counts,
                 log_write_errors={k: v['write_error'] for k, v in counts.items() if v['write_error']},
                 stdout_tail=tails['stdout'], stderr_tail=tails['stderr'])
    if state is not None:
        state.setdefault('events', []).append(event)
    return event


def live(state):
    """Counters and tails of children still running (for /health); JSON-safe."""
    out = {}
    for name, info in state.get('running', {}).items():
        drains = info.get('drains', {})
        out[name] = dict({k: v for k, v in info.items() if k != 'drains'},
                         stdout_tail=drains['stdout'].tail_text() if 'stdout' in drains else tail(info['stdout']),
                         stderr_tail=drains['stderr'].tail_text() if 'stderr' in drains else tail(info['stderr']),
                         stdout_bytes=drains['stdout'].total if 'stdout' in drains else None,
                         stderr_bytes=drains['stderr'].total if 'stderr' in drains else None)
    return out
