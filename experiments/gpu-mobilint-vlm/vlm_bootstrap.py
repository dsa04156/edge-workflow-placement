"""Task-owned copy of experiments/quality-network-heterogeneous/bootstrap.py (sha256 20029fed8e6f2c6c4a83529b476ce99743d258f30571f2fba7035711d3dbee4a,
commit a5c44e9f), shipped for the gpu-mobilint-vlm CUDA arm only. The shared helper is unchanged.

Only differences (test_vlm.VlmBootstrapProvenanceTest pins them): bounded fetch IO. fetch() paces every response at
FETCH_RATE_BPS (8 MiB/s, at most one block of credit, restarted per file), flushes + fsyncs a written destination
whenever SYNC_EVERY_BYTES (16 MiB) are unsynced and at the end, then drops the synced range from the page cache with
POSIX_FADV_DONTNEED when os.posix_fadvise exists (task file only; never drop_caches/global sync). Pacing sleeps and
syncs count against the existing per-file monotonic deadline; once a wait spends it, no further read or final sync
starts. main() publishes fetch_io (the bounds, fadvise
availability) and fetch_progress (path, bytes, synced bytes, elapsed) in /health. Evidence (gmv-diag-20261028t0718 on
the dev0009 eMMC): right after 7.17 GB of unflushed writes (~970 MiB dirty), `python3 -m pip --version` took 30 s
instead of 2.6 s; after the backlog was flushed it took 2.8 s. gmv-20261028t-gpu-arm1 (fsync per completed file only)
lost dev0009 readiness during the unpaced fetch. Neither bound has been measured on the node yet. No timeout, retry,
recipe, installer, or device behaviour is changed.

In-Pod bootstrap for the HTTP-only qualification worker (no exec/logs/attach is ever needed by the collector).

1. Serves /ready and /health on :8080 immediately, so discovery/installation errors are readable over HTTP.
2. Read-only device discovery: platform, /proc/device-tree/model, matching /dev nodes, NVIDIA_VISIBLE_DEVICES,
   libcuda cuInit/cuDriverGetVersion/cuDeviceGetName via ctypes (no driver change, no host mount).
3. Recipe gate by the OBSERVED CUDA driver API version (never by node name): cu130-aarch64-cp311 needs >= 13000;
   jp61-aarch64-cp310 (NVIDIA JetPack 6.1 redist torch on the l4t-jetpack r36.4 image) needs 12000 <= v < 13000.
   A failed gate is reported dependency_unavailable; no other runtime is tried.
4. Fetches the asset manifest from the owned cache Pod (sha256-verified, per-file deadline), installs wheels offline
   into /work/site with a finite deadline via proc.run (incremental durable logs, live tails in /health, owned
   process group killed on timeout); if the image has no pip, wheels are unpacked directly (install_without_pip).
   Recipes with a `preload` list (JP6.1: libgfortran -> libopenblas -> libcusparseLt from pinned official packages, see
   prepare_jp61_sysroot.py) are dlopen'ed by absolute path with RTLD_GLOBAL before the worker imports torch, so the loader
   finds those sonames already loaded (LD_LIBRARY_PATH cannot change after process start); failure -> preload_failed.
   Then loads worker.Worker. RECIPE=probe-allocation stops after discovery (allocation/device-node probe only).
Environment: RECIPE, BACKEND, STAGES, CACHE_URL, ASSET_MANIFEST_SHA256, NODE_NAME, POD_UID,
             INSTALL_TIMEOUT_S (default 1800), FETCH_DEADLINE_S per file (default 1800),
             WORKER_MODULE (default worker; quality-network-baselines uses dag_worker).
"""
import ctypes
import hashlib
import zipfile
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
import urllib.request

WORK = Path(os.environ.get('WORK_DIR', '/work'))  # overridable only for the local bootstrap smoke
CONFIG = os.environ.get('CONFIG_DIR', '/config')
INSTALL_TIMEOUT_S = float(os.environ.get('INSTALL_TIMEOUT_S', '1800'))
FETCH_DEADLINE_S = float(os.environ.get('FETCH_DEADLINE_S', '1800'))
DRIVER_GATES = {'cu130-aarch64-cp311': (13000, None), 'jp61-aarch64-cp310': (12000, 13000)}
DEV_PATTERNS = ('nvidia', 'nvhost', 'nvmap', 'nvgpu', 'hailo', 'aries', 'regulus', 'mobilint', 'accel', 'dri', 'rknpu', 'galcore')
FETCH_RATE_BPS = 8 * 1024**2  # vlm_bootstrap: bounded fetch IO (see header)
SYNC_EVERY_BYTES = 16 * 1024**2
FADVISE = hasattr(os, 'posix_fadvise') and hasattr(os, 'POSIX_FADV_DONTNEED')


def sync_segment(out, io):
    """flush + fsync the task-owned file, then drop its newly synced range from the page cache when supported."""
    out.flush()
    os.fsync(out.fileno())
    io['syncs'] += 1
    if FADVISE and io['written'] > io['synced']:
        try:
            os.posix_fadvise(out.fileno(), io['synced'], io['written'] - io['synced'], os.POSIX_FADV_DONTNEED)
            io['fadvise_calls'] += 1
        except OSError as error:
            io['fadvise_error'] = repr(error)[:200]
    io['synced'] = io['written']


def fetch(url, expected, dest=None, limit=8 * 1024**3, deadline_s=None, progress=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    h, size = hashlib.sha256(), 0
    deadline = time.monotonic() + (deadline_s or FETCH_DEADLINE_S)
    with opener.open(url, timeout=120) as response:
        out = dest.open('wb') if dest else None
        chunks = []
        pace, io = time.monotonic(), dict(written=0, synced=0, syncs=0, fadvise_calls=0)
        try:
            for block in iter(lambda: response.read(1 << 20), b''):
                size += len(block)
                if size > limit:
                    raise ValueError(f'{url} exceeds limit')
                if time.monotonic() > deadline:
                    raise TimeoutError(f'{url} exceeded fetch deadline after {size} bytes')
                h.update(block)
                (out.write(block) if out else chunks.append(block))
                if out:
                    io['written'] = size
                    if size - io['synced'] >= SYNC_EVERY_BYTES:
                        sync_segment(out, io)
                step = len(block) / FETCH_RATE_BPS  # credit never exceeds one block: idle time is not saved up
                pace = max(pace, time.monotonic() - step) + step
                time.sleep(max(0.0, min(pace, deadline) - time.monotonic()))
                if time.monotonic() >= deadline:  # a sync/pacing wait spent the budget: start no further read
                    raise TimeoutError(f'{url} exceeded fetch deadline after {size} bytes (pacing/sync included)')
                if progress:
                    progress(dict(io, bytes=size))
            if out:
                if time.monotonic() >= deadline:  # already expired (e.g. the EOF read): no final sync
                    raise TimeoutError(f'{url} exceeded fetch deadline after {size} bytes (pacing/sync included)')
                sync_segment(out, io)
                if progress:
                    progress(dict(io, bytes=size))
            if time.monotonic() > deadline:
                raise TimeoutError(f'{url} exceeded fetch deadline after {size} bytes (pacing/sync included)')
        finally:
            if out:
                out.close()
    if h.hexdigest() != expected:
        raise ValueError(f'{url} sha256 mismatch')
    return None if dest else b''.join(chunks)


def cuda_driver():
    out = dict(library=None)
    for name in ('libcuda.so.1', 'libcuda.so'):
        try:
            lib = ctypes.CDLL(name)
            out['library'] = name
            break
        except OSError as error:
            out.setdefault('load_errors', []).append(f'{name}: {error}'[:200])
    if not out['library']:
        return out
    out['cuInit'] = lib.cuInit(0)
    version = ctypes.c_int()
    out['cuDriverGetVersion'] = lib.cuDriverGetVersion(ctypes.byref(version))
    out['driver_version'] = version.value
    count = ctypes.c_int()
    out['cuDeviceGetCount'] = lib.cuDeviceGetCount(ctypes.byref(count))
    out['device_count'] = count.value
    names = []
    for index in range(count.value):
        device, buf = ctypes.c_int(), ctypes.create_string_buffer(256)
        if lib.cuDeviceGet(ctypes.byref(device), index) == 0 and lib.cuDeviceGetName(buf, 256, device) == 0:
            names.append(buf.value.decode(errors='replace'))
    out['device_names'] = names
    return out


def discover():
    info = dict(machine=platform.machine(), python=platform.python_version(), kernel=platform.release(), node=os.environ.get('NODE_NAME'))
    for path in ('/proc/device-tree/model', '/etc/nv_tegra_release'):
        try:
            info[path] = Path(path).read_bytes().replace(b'\x00', b'').decode(errors='replace')[:300]
        except OSError as error:
            info[path] = f'unreadable: {type(error).__name__}'
    try:
        info['dev'] = sorted(e for e in os.listdir('/dev') if e.startswith(DEV_PATTERNS))
    except OSError as error:
        info['dev'] = repr(error)
    info['NVIDIA_VISIBLE_DEVICES'] = os.environ.get('NVIDIA_VISIBLE_DEVICES')
    try:
        info['cuda_driver'] = cuda_driver()
    except Exception as error:
        info['cuda_driver'] = dict(error=repr(error)[:300])
    return info


def choose(recipe, info):
    if recipe not in DRIVER_GATES:
        return recipe, None
    low, high = DRIVER_GATES[recipe]
    version = (info.get('cuda_driver') or {}).get('driver_version') or 0
    if version >= low and (high is None or version < high):
        return recipe, None
    return None, f'CUDA driver API version {version} outside the {recipe} gate [{low}, {high or "inf"}); no other runtime tried'


def install_without_pip(wheels, site):
    """Images without pip (e.g. l4t-jetpack): unpack every prepared wheel (zip) into site; no dependency resolution."""
    site = Path(site)
    site.mkdir(parents=True, exist_ok=True)
    installed = []
    for wheel in sorted(Path(wheels).glob('*.whl')):
        with zipfile.ZipFile(wheel) as archive:
            for member in archive.namelist():
                target = (site/member).resolve()
                if not str(target).startswith(str(site.resolve())+os.sep):
                    raise ValueError(f'{wheel.name}: unsafe member {member}')
            archive.extractall(site)
        installed.append(wheel.name)
    return installed


def preload(paths):
    """dlopen each library (absolute path, dependency order) RTLD_GLOBAL; returns the evidence event or raises OSError."""
    loaded = []
    for path in paths:
        ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL)
        loaded.append(str(path))
    return dict(kind='preload', libraries=loaded)


def main():
    sys.path.insert(0, CONFIG)
    # pip stages wheels under TMPDIR (/work/tmp, sized by the plan); a missing TMPDIR would silently fall back to /tmp.
    Path(os.environ.get('TMPDIR') or '/tmp').mkdir(parents=True, exist_ok=True)
    import importlib
    # WORKER_MODULE (additive, default 'worker'): quality-network-baselines ships dag_worker.py (same Worker/serve interface).
    worker_module = importlib.import_module(os.environ.get('WORKER_MODULE') or 'worker')
    import proc
    state = dict(phase='discovery', started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), events=[])

    class Proxy:
        loaded = False

        def health(self):
            return dict(status=state['phase'], bootstrap=dict({k: v for k, v in state.items() if k != 'running'}, live=proc.live(state)), node=os.environ.get('NODE_NAME'),
                        pod_uid=os.environ.get('POD_UID'))

        def run(self, body):
            return 503, dict(status='error', error='not loaded', phase=state['phase'])
    proxy = Proxy()
    server = worker_module.serve(proxy, port=int(os.environ.get('WORKER_PORT', '8080')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        state['discovery'] = discover()
        recipe, blocker = choose(os.environ['RECIPE'], state['discovery'])
        state['recipe'] = recipe
        if recipe == 'probe-allocation':
            state['phase'] = 'probe_complete'
            return finish(proxy, state)
        if blocker:
            state.update(phase='dependency_unavailable', blocker=blocker)
            return finish(proxy, state)
        cache = os.environ['CACHE_URL']
        manifest = json.loads(fetch(cache+'/assets-manifest.json', os.environ['ASSET_MANIFEST_SHA256']))
        # keys exist before the HTTP thread reads them; each update replaces the value (no in-place mutation)
        state.update(fetch_io=dict(rate_cap_bps=FETCH_RATE_BPS, sync_every_bytes=SYNC_EVERY_BYTES, fadvise_dontneed=FADVISE), fetch_progress=None)
        state['phase'] = 'fetching'
        started, done = time.perf_counter(), dict(files=0, bytes=0)
        for item in manifest['files']:
            if item['group'] not in ('common', recipe) and not (item['group'] == 'onnx' and os.environ['BACKEND'] == 'openvino-npu'):
                continue
            dest = WORK/item['path']
            dest.parent.mkdir(parents=True, exist_ok=True)

            def progress(io, path=item['path']):
                state['fetch_progress'] = dict(io, path=path, files_done=done['files'], total_bytes=done['bytes']+io['bytes'],
                                               elapsed_s=round(time.perf_counter()-started, 3))
            fetch(cache+'/'+item['path'], item['sha256'], dest, progress=progress)
            done.update(files=done['files']+1, bytes=done['bytes']+dest.stat().st_size)
        state['events'].append(dict(kind='fetched', ms=(time.perf_counter()-started)*1000))
        if (WORK/'jellybean.tar').exists():
            import tarfile
            with tarfile.open(WORK/'jellybean.tar') as tar:
                tar.extractall(WORK, filter='data')
        state['phase'] = 'installing'
        wheels, spec = WORK/'wheels'/recipe, manifest['recipes'][recipe]
        has_pip = subprocess.run([sys.executable, '-m', 'pip', '--version'], capture_output=True, timeout=60).returncode == 0
        if has_pip:
            event = proc.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-cache-dir', '--disable-pip-version-check',
                              *(['--no-deps'] if spec.get('no_deps') else []), '--find-links', str(wheels), '--target', str(WORK/'site'),
                              *spec['requirements']], WORK/'logs', 'pip-install', INSTALL_TIMEOUT_S, state=state)
            if event['returncode'] or event['timed_out']:
                state['phase'] = 'install_timeout' if event['timed_out'] else 'install_failed'
                return finish(proxy, state)
        else:
            state['events'].append(dict(kind='install_without_pip', wheels=install_without_pip(wheels, WORK/'site')))
        if spec.get('preload'):
            try:
                state['events'].append(preload([WORK/p for p in spec['preload']]))
            except OSError as error:
                state.update(phase='preload_failed', error=repr(error)[:1500])
                return finish(proxy, state)
        sys.path.insert(0, str(WORK/'site'))
        os.environ.update(MODEL_ROOT=str(WORK/'models'), JELLYBEAN_SOURCE=str(WORK/'jellybean'))
        state['phase'] = 'loading'
        onnx = manifest.get('onnx', {})
        loaded = worker_module.Worker(os.environ['BACKEND'], os.environ['STAGES'].split(','), model_root=str(WORK/'models'),
                                      source_root=str(WORK/'jellybean'), onnx={k: str(WORK/v['path']) for k, v in onnx.items()},
                                      onnx_sha256={k: v['sha256'] for k, v in onnx.items()})
        state['phase'] = 'loaded'
        proxy.health = lambda: dict(loaded.health(), bootstrap={k: v for k, v in state.items() if k != 'running'})
        proxy.run = loaded.run
    except Exception as error:
        state.update(phase='bootstrap_failed', error=repr(error)[:1500])
    finish(proxy, state)


def finish(proxy, state):
    state['finished_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    proxy.loaded = True
    print(json.dumps(dict(event='bootstrap_done', phase=state['phase'])), flush=True)
    threading.Event().wait()


if __name__ == '__main__':
    main()
