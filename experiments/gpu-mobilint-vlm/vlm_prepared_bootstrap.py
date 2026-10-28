"""CUDA arm, prepared mode only (opt-in by the collector's --prepared; vlm_bootstrap.py stays the unchanged default).

No pip and no wheel decompression on the node. Discovery and the CUDA driver gate are vlm_bootstrap.discover/choose
unchanged (the hard CUDA device assertions stay in vlm_stage). Then vlm_runtime_cache.ensure on the release-bound PVC
mounted at CACHE_DIR: the backing block device must be CACHE_BACKING_DEVICE and not SD/eMMC before anything is written;
a verified hit transfers nothing; a miss fetches the sha256-bound prepared manifest and streams the one tar from the
owned server cache (8 MiB/s written, <= 16 MiB unsynced, per-file and whole-stream sha256, exact member list, absolute
deadline) into a private staging directory published by rename. /work keeps only the contract copy, the model link and
logs, so the published cache is never written by the worker.
Environment (in addition to vlm_bootstrap's): PREPARED_MANIFEST_SHA256, PREPARED_CONTENT_SHA256, CACHE_DIR,
CACHE_BACKING_DEVICE, PREPARED_FETCH_DEADLINE_S, CACHE_VERIFY_DEADLINE_S; with the release-bound SD profile also
CACHE_IO_PROFILE (json, must equal vlm_runtime_cache.SD_PROFILE) and CACHE_BACKING_DISK: the backing partition must then be
exactly CACHE_BACKING_DEVICE on that whole disk, and the fill follows its /proc/diskstats (AdaptiveWriter, /health
fetch_progress carries busy %, rate, pauses and fsync state).
"""
import json
import os
from pathlib import Path
import platform
import sys
import threading
import time
import urllib.request

import vlm_bootstrap as B
import vlm_runtime_cache as C


def main():
    sys.path.insert(0, B.CONFIG)
    import importlib
    worker_module = importlib.import_module(os.environ.get('WORKER_MODULE') or 'worker')
    import proc
    state = dict(phase='discovery', started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), events=[], mode='prepared')

    class Proxy:
        loaded = False

        def health(self):
            return dict(status=state['phase'], bootstrap=dict({k: v for k, v in state.items() if k != 'running'}, live=proc.live(state)),
                        node=os.environ.get('NODE_NAME'), pod_uid=os.environ.get('POD_UID'))

        def run(self, body):
            return 503, dict(status='error', error='not loaded', phase=state['phase'])
    proxy = Proxy()
    server = worker_module.serve(proxy, port=int(os.environ.get('WORKER_PORT', '8080')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        state['discovery'] = B.discover()
        recipe, blocker = B.choose(os.environ['RECIPE'], state['discovery'])
        state['recipe'] = recipe
        if blocker:
            state.update(phase='dependency_unavailable', blocker=blocker)
            return B.finish(proxy, state)
        cache, content = os.environ['CACHE_URL'], os.environ['PREPARED_CONTENT_SHA256']
        expect = dict(recipe=recipe, machine=platform.machine(), python='%d.%d' % sys.version_info[:2],
                      asset_manifest_sha256=os.environ['ASSET_MANIFEST_SHA256'], content_sha256=content)
        profile = json.loads(os.environ['CACHE_IO_PROFILE']) if os.environ.get('CACHE_IO_PROFILE') else None
        fetch_io = dict(io_profile=profile) if profile else dict(rate_cap_bps=B.FETCH_RATE_BPS, sync_every_bytes=B.SYNC_EVERY_BYTES)
        state.update(fetch_io=dict(fetch_io, verify_rate_bps=C.VERIFY_RATE_BPS), fetch_progress=None, phase='cache')
        started = time.perf_counter()

        def progress(io):
            state['fetch_progress'] = dict(io, elapsed_s=round(time.perf_counter()-started, 3))

        def open_tar():
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            return opener.open(cache+'/prepared/runtime.tar', timeout=120)
        event, manifest, final = C.ensure(
            os.environ['CACHE_DIR'], os.environ['PREPARED_MANIFEST_SHA256'], expect, os.environ.get('CACHE_BACKING_DEVICE'),
            lambda: B.fetch(cache+'/prepared/prepared-manifest.json', os.environ['PREPARED_MANIFEST_SHA256'], limit=C.MANIFEST_MAX),
            open_tar, B.FETCH_RATE_BPS, B.SYNC_EVERY_BYTES, float(os.environ['PREPARED_FETCH_DEADLINE_S']),
            float(os.environ['CACHE_VERIFY_DEADLINE_S']), os.environ['POD_UID'], progress, io_profile=profile,
            backing_disk=os.environ.get('CACHE_BACKING_DISK'))
        state['events'].append(dict(event, ms=(time.perf_counter()-started)*1000, content_sha256=content))
        tree, model = final/'tree', manifest['model']['key']
        contract = (tree/'contract.json').read_bytes()
        if C.sha256(contract) != manifest['frozen']['contract.json']:
            raise ValueError('cached contract.json differs from the prepared manifest')
        (B.WORK/'contract.json').write_bytes(contract)
        (B.WORK/'models').mkdir(parents=True, exist_ok=True)
        os.symlink(tree/'models'/model, B.WORK/'models'/model)
        sys.path.insert(0, str(tree/'site'))
        os.environ.update(MODEL_ROOT=str(B.WORK/'models'), JELLYBEAN_SOURCE=str(B.WORK/'jellybean'))
        state['phase'] = 'loading'
        loaded = worker_module.Worker(os.environ['BACKEND'], os.environ['STAGES'].split(','), model_root=str(B.WORK/'models'),
                                      source_root=str(B.WORK/'jellybean'), onnx={}, onnx_sha256={})
        state['phase'] = 'loaded'
        proxy.health = lambda: dict(loaded.health(), bootstrap={k: v for k, v in state.items() if k != 'running'})
        proxy.run = loaded.run
    except C.CacheError as error:
        state.update(phase=error.phase, error=str(error.detail)[:1500])
    except Exception as error:
        state.update(phase='bootstrap_failed', error=repr(error)[:1500])
    B.finish(proxy, state)


if __name__ == '__main__':
    main()
