"""Prepared CUDA runtime: the manifest/tar format (written offline by prepare_runtime.py) and the node-local persistent
cache (read/filled in the Pod by vlm_prepared_bootstrap.py). Standard library only; shipped in prepared mode only.

Tree (identical in the local build, the tar and the cache): contract.json, models/<model>/<file>, site/<pip --target tree>.
The tar holds exactly the manifest files, regular files only, in manifest (sorted path) order, mtime 0, uid/gid 0.

Cache under <CACHE_DIR>/gmv-prepared/v1 on the release-bound PVC (never /work):
  .lock                          flock LOCK_EX|LOCK_NB for the whole cache phase; a second Pod fails closed (cache_locked)
  .staging-<content16>-<pod uid> written only under the lock and never read; leftovers removed under the lock, one
                                 writer-gated unlink/rmdir at a time within the fill deadline (cache_cleanup_failed)
  <content_sha256>/              published by one rename after every file was hashed, size/mode-checked and fsynced:
      prepared-manifest.json, tree/...
A published directory is re-verified in full before every reuse (exact entries, regular files/dirs only, size, mode,
sha256); any difference is cache_corrupt and fails closed: nothing is deleted, repaired or re-transferred.
Before anything is written, the block device behind CACHE_DIR must be the release-approved one and never mmcblk (SD/eMMC),
except under the release-bound SD_PROFILE: then it must be exactly the bound partition of the bound whole disk (sysfs),
/proc/diskstats for that disk must give a valid baseline, and the fill uses AdaptiveWriter (I/O-feedback pacing).
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tarfile
import time

FORMAT = 'gmv-prepared-runtime/v1'
MODES = (0o644, 0o755)
SYNC_FDS = 256  # fds kept open for a batched fsync; the byte bound (sync_every) still applies first
VERIFY_RATE_BPS = 32 * 1024**2  # cache-hit re-hash read pace
MANIFEST_MAX = 64 * 1024**2
KiB, MiB = 1024, 1024**2
# The only SD I/O profile (bound in the release; the Pod refuses any other): rates of WRITTEN bytes, busy = % of wall time
# the whole disk had I/O in flight (diskstats io_ticks), sampled at most sample_s apart while writing or paused.
SD_PROFILE = dict(name='sd-adaptive-v1', sample_s=1.0, start_bps=1*MiB, max_bps=4*MiB, min_bps=256*KiB, step_bps=256*KiB,
                  unsynced_max=1*MiB, fds_max=32, chunk_max=256*KiB, slow_busy_pct=70, slow_fsync_s=1.0, pause_busy_pct=85,
                  resume_busy_pct=50, resume_hold_s=3.0, ramp_busy_pct=40, ramp_hold_s=10.0, stall_s=300, fill_budget_s=10800)


class CacheError(Exception):
    def __init__(self, phase, detail):
        super().__init__(f'{phase}: {detail}')
        self.phase, self.detail = phase, detail


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def content_sha256(files):
    return sha256(json.dumps([[f['path'], f['size'], f['sha256'], f['mode']] for f in files], separators=(',', ':')).encode())


def path_problem(path):
    parts = path.split('/') if isinstance(path, str) else ['']
    if '\x00' in path or '\\' in path or any(p in ('', '.', '..') for p in parts):
        return f'unsafe path {path!r}'
    if path != 'contract.json' and not (parts[0] in ('models', 'site') and len(parts) > 1):
        return f'path outside contract.json, models/, site/: {path!r}'
    return None


def manifest_problems(manifest, expect):
    """expect: recipe, machine, python, asset_manifest_sha256, content_sha256 (each must match exactly)."""
    problems = []
    seen = dict(format=manifest.get('format'), recipe=manifest.get('recipe'), machine=(manifest.get('runtime') or {}).get('machine'),
                python=(manifest.get('runtime') or {}).get('python'),
                asset_manifest_sha256=(manifest.get('frozen') or {}).get('assets-manifest.json'), content_sha256=manifest.get('content_sha256'))
    for key, want in dict(expect, format=FORMAT).items():
        if seen.get(key) != want:
            problems.append(f'{key}={seen.get(key)!r} expected {want!r}')
    files, previous = manifest.get('files') or [], ''
    for f in files:
        problem = path_problem(f.get('path'))
        if problem:
            problems.append(problem)
        elif f['path'] <= previous:
            problems.append(f'files not strictly sorted at {f["path"]!r}')
        elif f['path'].startswith(previous+'/'):
            problems.append(f'{previous!r} is both a file and a directory')
        if not isinstance(f.get('size'), int) or f['size'] < 0 or f.get('mode') not in MODES or len(str(f.get('sha256'))) != 64:
            problems.append(f'bad size/mode/sha256 for {f.get("path")!r}')
        previous = f.get('path') or previous
    if not files or 'contract.json' not in {f['path'] for f in files}:
        problems.append('no files or no contract.json')
    elif not problems and (content_sha256(files) != manifest['content_sha256'] or sum(f['size'] for f in files) != manifest.get('total_bytes')
                           or len(files) != manifest.get('file_count')):
        problems.append('content_sha256/total_bytes/file_count do not match the file list')
    tar = manifest.get('tar') or {}
    if not isinstance(tar.get('bytes'), int) or len(str(tar.get('sha256'))) != 64:
        problems.append('tar bytes/sha256 missing')
    return problems


def write_tar(out, files, source_of):
    """Deterministic tar of exactly `files` (local build). Every source is re-hashed while it is written."""
    with open(out, 'xb') as raw, tarfile.open(fileobj=raw, mode='w|', format=tarfile.PAX_FORMAT) as tar:
        for f in files:
            info = tarfile.TarInfo(f['path'])
            info.size, info.mode, info.mtime, info.uid, info.gid, info.uname, info.gname = f['size'], f['mode'], 0, 0, 0, '', ''
            with open(source_of(f), 'rb') as handle:
                reader = Counted(handle, f['size'], None)
                tar.addfile(info, reader)
            if reader.count != f['size'] or reader.hash.hexdigest() != f['sha256']:
                raise ValueError(f'{f["path"]}: source changed after the manifest was computed')


class Counted:
    """read() wrapper: at most `limit` bytes (one more raises), an absolute monotonic deadline, running sha256."""

    def __init__(self, raw, limit, deadline, clock=time.monotonic):
        self.raw, self.limit, self.deadline, self.clock, self.count, self.hash = raw, limit, deadline, clock, 0, hashlib.sha256()

    def read(self, n=-1):
        if self.deadline is not None and self.clock() > self.deadline:
            raise TimeoutError(f'transfer deadline passed after {self.count} bytes')
        room = self.limit - self.count + 1
        data = self.raw.read(room if n is None or n < 0 else min(n, room))
        self.count += len(data)
        if self.count > self.limit:
            raise ValueError(f'stream exceeds the expected {self.limit} bytes')
        self.hash.update(data)
        return data


class Writer:
    """Paced (rate_bps of WRITTEN bytes, no carried credit) writes; never more than sync_every bytes unsynced: pending fds
    are fsynced, their range dropped from the page cache (task files only) and closed. Every wait counts against deadline."""

    def __init__(self, rate_bps, sync_every, deadline, progress=None):
        self.rate, self.sync_every, self.deadline, self.progress = rate_bps, sync_every, deadline, progress
        self.pace, self.pending, self.unsynced, self.fds_max, self.clock = time.monotonic(), [], 0, SYNC_FDS, time.monotonic
        self.io = dict(written=0, synced=0, syncs=0, files=0)

    def check(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError(f'transfer deadline passed after {self.io["written"]} written bytes (pacing/sync included)')

    def meta(self):
        """Before a metadata operation (mkdir, create, rename)."""
        self.check()

    def fsync_dir(self, path):
        self.check()
        fsync_dir(path)

    def write(self, fd, block):
        self.check()
        if self.unsynced + len(block) > self.sync_every:  # sync first, so unsynced bytes never exceed sync_every
            self.sync(keep=fd)
        view = memoryview(block)
        while view:
            view = view[os.write(fd, view):]
        self.io['written'] += len(block)
        self.unsynced += len(block)
        step = len(block) / self.rate
        self.pace = max(self.pace, time.monotonic() - step) + step
        time.sleep(max(0.0, min(self.pace, self.deadline) - time.monotonic()))
        self.check()
        if self.progress:
            self.progress(dict(self.io))

    def close_file(self, fd):
        self.pending.append(fd)
        self.io['files'] += 1
        if len(self.pending) >= self.fds_max:
            self.sync()

    def sync(self, keep=None):
        """fsync + page-cache drop of every pending fd and of `keep` (the file being written, left open)."""
        for fd in self.pending + ([keep] if keep is not None else []):
            self.flush(fd)
        for fd in self.pending:
            os.close(fd)
        self.pending, self.unsynced = [], 0
        self.io['synced'] = self.io['written']
        self.io['syncs'] += 1

    def flush(self, fd):
        os.fsync(fd)
        if hasattr(os, 'posix_fadvise'):
            try:
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            except OSError:
                pass

    def remove(self, op, path):
        """One unlink/rmdir of a stale staging entry (before the fill); counts against the deadline."""
        self.check()
        op(path)


class IOStatsError(ValueError):
    pass


class DiskMonitor:
    """Busy % of one whole disk between two /proc/diskstats samples (io_ticks delta / wall delta). A missing, malformed
    or regressed sample raises IOStatsError, so writing never continues without valid feedback."""

    def __init__(self, disk, clock=time.monotonic, read=lambda: Path('/proc/diskstats').read_text()):
        self.disk, self.clock, self.read, self.last = disk, clock, read, None

    def counters(self):
        rows = [r for r in (line.split() for line in self.read().splitlines()) if len(r) > 2 and r[2] == self.disk]
        if len(rows) != 1 or len(rows[0]) < 14 or not all(x.isdigit() for x in rows[0][3:14]):
            raise IOStatsError(f'/proc/diskstats has no single valid row for {self.disk!r}: {rows[:2]}')
        r = rows[0]
        return dict(writes=int(r[7]), sectors_written=int(r[9]), io_ticks_ms=int(r[12]))

    def sample(self):
        """None for the baseline, then busy % (capped at 100) since the previous sample."""
        now, counters = self.clock(), self.counters()
        previous, self.last = self.last, None
        if previous is not None:
            then, before = previous
            if now <= then or any(counters[k] < before[k] for k in counters):
                raise IOStatsError(f'diskstats for {self.disk!r} regressed: {before} -> {counters} over {now-then:.3f}s')
        self.last = (now, counters)
        if previous is None:
            return None
        return min(100.0, 100.0 * (counters['io_ticks_ms'] - before['io_ticks_ms']) / ((now - then) * 1000))


class AdaptiveWriter(Writer):
    """Writer under SD_PROFILE: chunks <= chunk_max at the current rate (no credit carried across waits or pauses),
    <= unsynced_max unsynced, <= fds_max pending fds. Before every chunk, metadata operation, stale-staging unlink/rmdir,
    each single fsync and dir fsync, and again after each of those calls (so before whatever follows), the deadline is
    checked and the disk re-sampled when sample_s has passed. busy >= slow or one such call taking >= slow_fsync_s halves
    the rate (floor min_bps); busy >= pause stops all I/O, waiting in <= sample_s steps with live progress, until
    busy < resume for resume_hold_s; busy < ramp for ramp_hold_s adds step_bps (cap max_bps). The absolute deadline and
    stall_s without a written byte or removed entry both end the fill with TimeoutError."""

    def __init__(self, profile, monitor, deadline, progress=None, clock=time.monotonic, sleep=time.sleep):
        super().__init__(profile['start_bps'], profile['unsynced_max'], deadline, progress)
        self.p, self.monitor, self.clock, self.sleep, self.fds_max = profile, monitor, clock, sleep, profile['fds_max']
        self.pace = self.sampled = self.moved = clock()
        self.paused, self.cool, self.calm = False, None, None
        self.io.update(busy_pct=None, rate_bps=self.rate, paused=False, pause_s=0.0, unsynced=0, pending_fds=0, last_fsync_s=None,
                       max_fsync_s=0.0, removed=0, last_remove_s=None, max_remove_s=0.0, samples=0, slowdowns=0, ramps=0, pauses=0)

    def check(self):
        now = self.clock()
        if now >= self.deadline:
            raise TimeoutError(f'transfer deadline passed after {self.io["written"]} written bytes (pauses/pacing/sync included)')
        if now - self.moved >= self.p['stall_s']:
            raise TimeoutError(f'no written byte or removed entry for {now - self.moved:.0f}s (paused={self.paused}, '
                               f'busy={self.io["busy_pct"]}%)')

    def publish(self):
        self.io.update(rate_bps=self.rate, paused=self.paused, unsynced=self.unsynced, pending_fds=len(self.pending))
        if self.progress:
            self.progress(dict(self.io))

    def slow(self):
        self.rate = max(self.p['min_bps'], self.rate // 2)
        self.io['slowdowns'] += 1

    def observe(self):
        now, p = self.clock(), self.p
        if now - self.sampled < p['sample_s']:
            return
        busy, start = self.monitor.sample(), self.sampled
        self.sampled = now
        if busy is None:
            return
        self.io.update(busy_pct=round(busy, 1), samples=self.io['samples'] + 1)
        if busy >= p['pause_busy_pct']:
            self.io['pauses'] += not self.paused
            self.paused, self.cool = True, None
        elif self.paused:
            self.cool = (start if self.cool is None else self.cool) if busy < p['resume_busy_pct'] else None
            if self.cool is not None and now - self.cool >= p['resume_hold_s']:
                self.paused, self.cool = False, None
        if busy >= p['slow_busy_pct']:
            self.slow()
        if busy >= p['ramp_busy_pct'] or self.paused:
            self.calm = None
        else:
            self.calm = start if self.calm is None else self.calm
            if now - self.calm >= p['ramp_hold_s']:
                self.rate, self.calm = min(p['max_bps'], self.rate + p['step_bps']), now
                self.io['ramps'] += 1

    def wait(self, until):
        """Sleep until `until` in steps that end at the next sample; paused time is accumulated."""
        while True:
            self.check()
            self.observe()
            now = self.clock()
            if now >= until:
                return
            step = min(until, self.deadline, self.sampled + self.p['sample_s']) - now
            if step > 0:
                self.sleep(step)
                if self.paused:
                    self.io['pause_s'] += step

    def gate(self):
        self.check()
        self.observe()
        while self.paused:
            self.publish()
            self.wait(self.clock() + self.p['sample_s'])

    meta = gate

    def write(self, fd, block):
        view = memoryview(block)
        for start in range(0, len(view), self.p['chunk_max']):
            chunk = view[start:start + self.p['chunk_max']]
            self.gate()
            if self.unsynced + len(chunk) > self.sync_every:
                self.sync(keep=fd)
            rest = chunk
            while rest:
                rest = rest[os.write(fd, rest):]
            self.io['written'] += len(chunk)
            self.unsynced += len(chunk)
            self.moved = self.clock()
            self.pace = max(self.pace, self.moved) + len(chunk) / self.rate
            self.wait(self.pace)
            self.publish()

    def timed(self, call, kind='fsync'):
        """One gated call: gate before it, its own latency as feedback, and gate again before anything that follows."""
        self.gate()
        started = self.clock()
        call()
        took = round(self.clock() - started, 3)
        self.io.update({f'last_{kind}_s': took, f'max_{kind}_s': max(self.io[f'max_{kind}_s'], took)})
        if took >= self.p['slow_fsync_s']:
            self.slow()
        self.gate()

    def flush(self, fd):
        self.timed(lambda: Writer.flush(self, fd))

    def fsync_dir(self, path):
        self.timed(lambda: fsync_dir(path))

    def remove(self, op, path):
        self.timed(lambda: op(path), 'remove')
        self.moved = self.clock()
        self.io['removed'] += 1
        self.publish()


def stream_install(raw, tree, manifest, writer, deadline):
    """Extract the tar stream into the fresh directory `tree`: members must be exactly the manifest files in order
    (regular, same size/mode, sha256 checked while written); the whole stream must be exactly tar.bytes/tar.sha256."""
    source = Counted(raw, manifest['tar']['bytes'], deadline, writer.clock)
    expected = iter(manifest['files'])
    with tarfile.open(fileobj=source, mode='r|', bufsize=1 << 20) as tar:
        for member in tar:
            f = next(expected, None)
            if f is None or member.name != f['path'] or not member.isreg() or member.size != f['size'] or member.mode & 0o7777 != f['mode']:
                raise ValueError(f'unexpected tar member {member.name!r} type={member.type!r} size={member.size} (expected {f and f["path"]!r})')
            dest = tree/f['path']
            writer.meta()
            dest.parent.mkdir(parents=True, exist_ok=True)  # tree is fresh and private: only directories created here exist
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            try:
                h, body = hashlib.sha256(), tar.extractfile(member)
                for block in iter(lambda: body.read(1 << 20), b''):
                    h.update(block)
                    writer.write(fd, block)
                if h.hexdigest() != f['sha256']:
                    raise ValueError(f'{f["path"]}: sha256 mismatch')
                os.fchmod(fd, f['mode'])
            except BaseException:
                os.close(fd)
                raise
            writer.close_file(fd)
    if next(expected, None) is not None:
        raise ValueError('tar ended before every manifest file was received')
    while source.read(1 << 20):
        pass
    if source.count != manifest['tar']['bytes'] or source.hash.hexdigest() != manifest['tar']['sha256']:
        raise ValueError(f'tar stream {source.count} bytes / sha256 differ from the manifest')
    writer.check()
    writer.sync()
    for directory, _, _ in os.walk(tree):
        writer.fsync_dir(directory)


def raise_error(error):
    raise error


def remove_stale(root, writer):
    """Remove every leftover .staging-* entry of `root` (under the lock) deepest first, one writer-gated unlink/rmdir at a
    time (no recursive delete, symlinks never followed), so the cleanup is inside the fill's feedback and deadline."""
    removed = []
    for entry in sorted(os.listdir(root)):
        if not entry.startswith('.staging-'):
            continue
        stale = root/entry
        if stale.is_symlink() or not stale.is_dir():
            writer.remove(os.unlink, stale)
        else:
            for directory, subdirs, names in os.walk(stale, topdown=False, onerror=raise_error):
                for name in subdirs + names:
                    path = Path(directory)/name
                    writer.remove(os.rmdir if name in subdirs and not path.is_symlink() else os.unlink, path)
            writer.remove(os.rmdir, stale)
        removed.append(entry)
    return removed


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def verify_tree(final, manifest, rate_bps, deadline):
    """Problems (empty = exact match). Stops hashing at the first content difference."""
    problems, tree = [], final/'tree'
    if sorted(os.listdir(final)) != ['prepared-manifest.json', 'tree'] or not tree.is_dir() or tree.is_symlink():
        return [f'published entries {sorted(os.listdir(final))} are not prepared-manifest.json + tree/']
    want = {f['path']: f for f in manifest['files']}
    dirs = {str(Path(p).parents[i]) for p in want for i in range(len(Path(p).parts)-1)}
    for directory, subdirs, names in os.walk(tree):
        for name in subdirs + names:
            rel = str((Path(directory)/name).relative_to(tree))
            st = os.lstat(Path(directory)/name)
            is_dir, is_reg = (st.st_mode & 0o170000) == 0o040000, (st.st_mode & 0o170000) == 0o100000
            if not ((is_dir and rel in dirs) or (is_reg and rel in want)):
                problems.append(f'unexpected entry {rel!r} (mode {oct(st.st_mode)})')
            elif is_reg and (st.st_size != want[rel]['size'] or st.st_mode & 0o7777 != want[rel]['mode']):
                problems.append(f'{rel}: size/mode {st.st_size}/{oct(st.st_mode & 0o7777)} differ')
    missing = [p for p in want if not os.path.lexists(tree/p)]
    problems += [f'missing {p!r}' for p in missing[:20]]
    if problems:
        return problems[:50]
    pace = time.monotonic()
    for f in manifest['files']:
        fd = os.open(tree/f['path'], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            h = hashlib.sha256()
            for block in iter(lambda: os.read(fd, 1 << 20), b''):
                h.update(block)
                pace = max(pace, time.monotonic()) + len(block) / rate_bps
                time.sleep(max(0.0, min(pace, deadline) - time.monotonic()))
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'cache verification deadline passed at {f["path"]}')
            if hasattr(os, 'posix_fadvise'):
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        finally:
            os.close(fd)
        if h.hexdigest() != f['sha256']:
            return [f'{f["path"]}: sha256 differs']
    return []


def sysfs_block(path):
    dev = os.stat(path).st_dev
    return Path(f'/sys/dev/block/{os.major(dev)}:{os.minor(dev)}'), os.major(dev)


def block_device(path):
    """(device name, major) of the filesystem holding `path`, from sysfs."""
    sysfs, major = sysfs_block(path)
    for line in (sysfs/'uevent').read_text().splitlines():
        if line.startswith('DEVNAME='):
            return line.split('=', 1)[1], major
    raise OSError(f'no DEVNAME for block device {sysfs.name}')


def whole_disk(path):
    """Kernel name of the whole disk whose partition holds `path` (sysfs parent); OSError when it is not a partition."""
    sysfs = sysfs_block(path)[0].resolve()
    if not (sysfs/'partition').is_file():
        raise OSError(f'{sysfs.name} is not a partition')
    return sysfs.parent.name


def storage_problem(path, approved_device, backing_disk=None, io_profile=None):
    """None only when the cache filesystem is on exactly the approved, non-SD/eMMC block device, or, under exactly
    SD_PROFILE, on exactly the approved partition of the approved whole disk."""
    try:
        name, major = block_device(path)
        disk = whole_disk(path) if io_profile is not None else None
    except OSError as error:
        return f'cache backing device unknown ({error}); refusing to write'
    if io_profile is not None:
        if io_profile != SD_PROFILE or not approved_device or not backing_disk or (name, disk) != (approved_device, backing_disk):
            return (f'cache backing {name!r} on disk {disk!r} with profile {io_profile.get("name") if isinstance(io_profile, dict) else io_profile!r} '
                    f'is not the approved {approved_device!r} on {backing_disk!r} under {SD_PROFILE["name"]}')
        return None
    if not approved_device or name.startswith('mmcblk') or major == 179 or name != approved_device:
        return f'cache backing device {name!r} (major {major}) is not the approved non-SD device {approved_device!r}'
    return None


def ensure(cache_dir, manifest_sha, expect, approved_device, fetch_manifest, open_tar, rate_bps, sync_every, fetch_deadline_s,
           verify_deadline_s, pod_uid, progress=None, storage=storage_problem, io_profile=None, backing_disk=None, monitor=None,
           clock=time.monotonic, sleep=time.sleep):
    """-> (event, manifest, published dir). A hit calls neither fetch_manifest nor open_tar. With io_profile (SD_PROFILE
    only) the fill is an AdaptiveWriter on `monitor` (default: /proc/diskstats of backing_disk), whose baseline sample must
    be valid before anything is written; rate_bps/sync_every are then unused."""
    cache_dir = Path(cache_dir)
    if cache_dir.is_symlink() or not cache_dir.is_dir():
        raise CacheError('cache_storage_rejected', f'{cache_dir} is not a mounted directory')
    problem = storage(cache_dir, approved_device, backing_disk, io_profile)
    if problem:
        raise CacheError('cache_storage_rejected', problem)
    if io_profile is not None:
        monitor = monitor or DiskMonitor(backing_disk, clock)
        try:
            monitor.sample()
        except (OSError, IOStatsError) as error:
            raise CacheError('cache_io_stats_rejected', repr(error)[:1000]) from error
    root = cache_dir
    for part in ('gmv-prepared', 'v1'):
        root = root/part
        if not os.path.lexists(root):
            os.mkdir(root, 0o755)
        if root.is_symlink() or not root.is_dir():
            raise CacheError('cache_corrupt', f'{root} is not a plain directory')
    lock = os.open(root/'.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CacheError('cache_locked', 'another writer holds the cache lock') from None
        final = root/expect['content_sha256']
        if os.path.lexists(final):
            if final.is_symlink() or not final.is_dir():
                raise CacheError('cache_corrupt', f'{final.name} is not a directory')
            fd = os.open(final/'prepared-manifest.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            with os.fdopen(fd, 'rb') as handle:
                raw = handle.read(MANIFEST_MAX)
            if sha256(raw) != manifest_sha:
                raise CacheError('cache_mismatch', 'published manifest is not the release manifest')
            manifest = json.loads(raw)
            problems = manifest_problems(manifest, expect) or verify_tree(final, manifest, VERIFY_RATE_BPS, time.monotonic()+verify_deadline_s)
            if problems:
                raise CacheError('cache_corrupt', problems)
            return dict(kind='cache_hit', transfers=0, files=manifest['file_count'], bytes=manifest['total_bytes']), manifest, final
        raw = fetch_manifest()
        if sha256(raw) != manifest_sha:
            raise CacheError('manifest_rejected', 'fetched manifest sha256 differs')
        manifest = json.loads(raw)
        problems = manifest_problems(manifest, expect)
        if problems:
            raise CacheError('manifest_rejected', problems)
        deadline = clock() + fetch_deadline_s  # the stale cleanup counts against the fill budget too
        if io_profile is not None:
            writer = AdaptiveWriter(io_profile, monitor, deadline, progress, clock, sleep)
        else:
            writer = Writer(rate_bps, sync_every, deadline, progress)
        try:
            removed = remove_stale(root, writer)
        except Exception as error:  # what is left stays unpublished staging; the next writer resumes the removal
            raise CacheError('cache_cleanup_failed', repr(error)[:1000]) from error
        st = os.statvfs(root)
        need = manifest['total_bytes'] + 4096*manifest['file_count'] + 256*1024**2
        if st.f_bavail * st.f_frsize < need:
            raise CacheError('cache_no_space', f'{st.f_bavail * st.f_frsize} bytes free < {need}')
        staging = root/f'.staging-{expect["content_sha256"][:16]}-{pod_uid}'
        try:
            writer.meta()
            os.mkdir(staging, 0o755)
            writer.meta()
            os.mkdir(staging/'tree', 0o755)
            with open_tar() as response:
                stream_install(response, staging/'tree', manifest, writer, deadline)
            writer.meta()
            fd = os.open(staging/'prepared-manifest.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            try:
                writer.write(fd, raw)
                writer.sync(keep=fd)
            finally:
                os.close(fd)
            writer.fsync_dir(staging)
            writer.meta()
        except Exception as error:  # the staging directory is never published; the next writer removes it under the lock
            raise CacheError('cache_fill_failed', repr(error)[:1000]) from error
        os.rename(staging, final)  # final is absent: checked above under the lock
        fsync_dir(root)
        return dict(kind='cache_fill', transfers=1, files=manifest['file_count'], bytes=manifest['total_bytes'], io=writer.io,
                    removed_staging=removed), manifest, final
    finally:
        os.close(lock)
