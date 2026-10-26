"""Mobilint vision compile PROBE (compile only; no NPU device, no model execution) inside the public, digest-pinned
docker.io/mobilint/qbcompiler:1.4-cpu-ubuntu22.04 image as a CPU-only owned Pod. No host devices, host IPC or
privilege; status and the artifact are served over HTTP on :8080 (no exec/logs needed).

The compile runs in a child process (`--child`) through proc.run: finite deadline COMPILE_TIMEOUT_S (default 5400 s),
stdout/stderr written incrementally to /work/logs (live tails in /health), the owned process group killed on timeout,
phase compile_timeout / compile_failed / compiled recorded. API usage follows the official tutorial
mobilint/mblt-sdk-tutorial compilation/image_classification/{model_compile,convert_img_to_tensor}.py (master; README
blob 9734db2975bf17c2e592585b3445d953a1a6cb3a): make_calib_man + mxq_compile with the tutorial's per-channel
maxpercentile CalibrationConfig. Deviations: input is our derived static vision graph (ResNet18 trunk + spatial mean,
1x3x448x448 -> 512) already normalized by stages.vision_array_numpy (== torchvision); preprocessing fusion and uint8
input are off; calibration uses ONLY the 20 calibration-split images. mxq_compile produces a quantized package; compile
success is not execution or quality evidence. Never run locally (no Docker access on the collector host).
Env: CACHE_URL, ASSET_MANIFEST_SHA256, MOBILINT_TARGET (default aries-rb), COMPILE_TIMEOUT_S, CONFIG_DIR, WORK_DIR.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

CONFIG = os.environ.get('CONFIG_DIR', '/config')
WORK = Path(os.environ.get('WORK_DIR', '/work'))
COMPILE_TIMEOUT_S = float(os.environ.get('COMPILE_TIMEOUT_S', '5400'))
sys.path.insert(0, CONFIG)
CALIBRATION = dict(method=1, output=0, mode=1, max_percentile={'percentile': 0.9999, 'topk_ratio': 0.01})


def run_compile_child(state, work, timeout_s, command=None):
    """Run the compile child with a finite deadline; result file written by the child decides 'compiled'."""
    import proc
    work = Path(work)
    result_path = work/'compile-result.json'
    state['phase'] = 'compiling'
    event = proc.run(command or [sys.executable, '-u', __file__, '--child'], work/'logs', 'mxq-compile', timeout_s, state=state)
    if event['timed_out']:
        state['phase'] = 'compile_timeout'
    elif event['returncode'] or not result_path.exists():
        state['phase'] = 'compile_failed'
    else:
        state.update(phase='compiled', **json.loads(result_path.read_text()))
    return state


def child():
    """Compile in this process (called only as the deadline-bounded child)."""
    import stages
    from qbcompiler import CalibrationConfig, PreprocessingConfig, Uint8InputConfig, mxq_compile
    from qbcompiler.calibration import make_calib_man
    manifest = json.loads((WORK/'assets-manifest.json').read_text())
    onnx = WORK/manifest['onnx']['vision']['path']
    pre = lambda path: stages.vision_array_numpy(stages.decode_image(Path(path).read_bytes()))[0].transpose(1, 2, 0)
    started = time.perf_counter()
    make_calib_man(pre_ftn=pre, data_dir=str(WORK/'calib'), save_dir=str(WORK/'calib-tensor'), save_name='vision448', remove_npy=True)
    calibrated = time.perf_counter()
    out = WORK/'vision-static-448.mxq'
    mxq_compile(model=str(onnx), calib_data_path=str(WORK/'calib-tensor'/'vision448'), save_path=str(out), image_channels=3,
                backend='onnx', device='cpu', target_device=os.environ.get('MOBILINT_TARGET', 'aries-rb'), inference_scheme='all',
                preprocessing_config=PreprocessingConfig(apply=False, auto_convert_format=False, pipeline=[], input_configs={}),
                uint8_input_config=Uint8InputConfig(apply=False, inputs=[]), calibration_config=CalibrationConfig(**CALIBRATION))
    (WORK/'compile-result.json').write_text(json.dumps(dict(
        mxq_path=str(out), mxq_sha256=hashlib.sha256(out.read_bytes()).hexdigest(), mxq_bytes=out.stat().st_size,
        source_onnx_sha256=manifest['onnx']['vision']['sha256'], calibration_ms=(calibrated-started)*1000,
        compile_ms=(time.perf_counter()-calibrated)*1000, calibration_config=CALIBRATION)))


def main():
    import bootstrap
    import proc
    import worker as worker_module
    state = dict(phase='starting', role='compile only; runtime execution is a separate, unimplemented step',
                 target=os.environ.get('MOBILINT_TARGET', 'aries-rb'), events=[])

    class Proxy:
        loaded = False

        def health(self):
            return dict(status=state['phase'], probe=dict({k: v for k, v in state.items() if k != 'running'}, live=proc.live(state)), node=os.environ.get('NODE_NAME'),
                        pod_uid=os.environ.get('POD_UID'))

        def run(self, body):
            if body.get('stage') != 'artifact' or 'mxq_path' not in state:
                return 404, dict(status='error', error='no artifact')
            import base64
            data = Path(state['mxq_path']).read_bytes()
            return 200, dict(status='ok', data=base64.b64encode(data).decode(), sha256=hashlib.sha256(data).hexdigest())
    proxy = Proxy()
    server = worker_module.serve(proxy, port=int(os.environ.get('WORKER_PORT', '8080')), max_active=2, max_body=1 << 20)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    proxy.loaded = True  # /ready = status server up; compile progress/terminal state is read from /health, never from readiness
    try:
        import importlib.metadata as md
        state['packages'] = {p: _version(md, p) for p in ('qbcompiler', 'torch', 'numpy', 'pillow', 'onnx')}
        cache = os.environ['CACHE_URL']
        raw = bootstrap.fetch(cache+'/assets-manifest.json', os.environ['ASSET_MANIFEST_SHA256'])
        manifest = json.loads(raw)
        WORK.mkdir(parents=True, exist_ok=True)
        (WORK/'assets-manifest.json').write_bytes(raw)
        state['phase'] = 'fetching'
        for item in manifest['files']:
            if item['group'] == 'calib' or (item['group'] == 'onnx' and item['path'].endswith('vision-static-448.onnx')):
                dest = WORK/item['path']
                dest.parent.mkdir(parents=True, exist_ok=True)
                bootstrap.fetch(cache+'/'+item['path'], item['sha256'], dest)
        if state['packages'].get('qbcompiler') is None:
            state.update(phase='dependency_unavailable', blocker='qbcompiler is not installed in this image')
        else:
            run_compile_child(state, WORK, COMPILE_TIMEOUT_S)
    except Exception as error:
        state.update(phase='compile_failed', error=repr(error)[:1500], traceback=traceback.format_exc()[-3000:])
    proxy.loaded = True
    threading.Event().wait()


def _version(md, name):
    try:
        return md.version(name)
    except md.PackageNotFoundError:
        return None


if __name__ == '__main__':
    child() if '--child' in sys.argv else main()
