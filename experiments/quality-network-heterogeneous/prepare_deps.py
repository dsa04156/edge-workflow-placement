"""Download official public wheels into isolated, hash-recorded wheelhouses for the live qualification wave.

Recipes (one per runtime family; which applies to a node is decided by live discovery, never by node name):
  cu130-aarch64-cp311 : PyTorch cu130 aarch64 (proven on GB10 in docs/experiments/e0-accelerators.md) for CUDA-13
                        drivers (server GB10, JetPack 7 class Jetson). Pure-Python/abi3 deps from PyPI.
  jp61-aarch64-cp310  : NVIDIA redist torch for JetPack 6.1 (developer.download.nvidia.com/compute/redist/jp/v61) +
                        PyPI cp310 deps; torchvision installed --no-deps (only transforms + resnet18 constructor are used).
  npu-x86_64-cp312    : Pillow for the existing OpenVINO 2026.3.1 Intel NPU image (python 3.12, numpy 1.26.4 present).
No license prompt is accepted and nothing is installed on any host. Output: <out>/<recipe>/ wheels + manifest.json.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import os
import sys
import urllib.parse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import proc  # noqa: E402
ROOT = HERE.parents[1]
OUT_ROOT = ROOT/'artifacts/quality-network-heterogeneous'
STEP_TIMEOUT_S = float(os.environ.get('DEPS_STEP_TIMEOUT_S', '3600'))
PIP = ['uvx', '--from', 'pip==25.2', 'pip', 'download', '--no-cache-dir', '--only-binary=:all:', '--disable-pip-version-check']
COMMON = ['transformers==4.57.1', 'tokenizers==0.22.2', 'safetensors==0.8.0', 'huggingface_hub==0.36.2', 'pyyaml==6.0.3', 'regex==2026.9.29',
          'soundfile==0.13.1', 'pillow==11.3.0', 'numpy==1.26.4']
RECIPES = {
    'cu130-aarch64-cp311': dict(platform='manylinux_2_28_aarch64', python='3.11', glibc=36, steps=[
        dict(index='https://download.pytorch.org/whl/cu130', extra='https://pypi.org/simple', packages=['torch==2.9.1+cu130', 'torchvision==0.24.1']),
        dict(index='https://pypi.org/simple', packages=COMMON)]),
    'jp61-aarch64-cp310': dict(platform='manylinux_2_28_aarch64', python='3.10', glibc=35, steps=[
        dict(url='https://developer.download.nvidia.com/compute/redist/jp/v61/pytorch/'
                 'torch-2.5.0a0+872d972e41.nv24.08.17622132-cp310-cp310-linux_aarch64.whl'),
        dict(index='https://pypi.org/simple', packages=['torchvision==0.20.0'], no_deps=True),
        dict(index='https://pypi.org/simple', packages=COMMON + ['filelock', 'typing-extensions', 'sympy', 'networkx', 'jinja2', 'fsspec'])]),
    'npu-x86_64-cp312': dict(platform='manylinux_2_28_x86_64', python='3.12', glibc=39, steps=[
        dict(index='https://pypi.org/simple', packages=['pillow==11.3.0'], no_deps=True)]),
}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def run_step(recipe, step, dest, log):
    if 'url' in step:
        command = ['curl', '-fsSL', '--retry', '2', '-o', str(dest/Path(urllib.parse.unquote(step['url'])).name), step['url']]
    else:
        arch = recipe['platform'].split('manylinux_2_28_')[1]
        # pip --platform matches tags exactly: list every manylinux level the base image glibc accepts (recipe['glibc']).
        tags = [f'manylinux_2_{v}_{arch}' for v in range(17, recipe['glibc'] + 1)] + [f'manylinux2014_{arch}', f'linux_{arch}']
        command = PIP + ['--dest', str(dest), *[x for t in tags for x in ('--platform', t)], '--python-version', recipe['python'],
                         '--implementation', 'cp', '--index-url', step['index']]
        if step.get('extra'):
            command += ['--extra-index-url', step['extra']]
        if step.get('no_deps'):
            command += ['--no-deps']
        command += step['packages']
    # Finite deadline, incremental durable logs in <dest>/logs, owned process group killed on timeout (proc.run).
    event = proc.run(command, dest/'logs', f'step-{len(log)}', STEP_TIMEOUT_S)
    log.append(event)
    return 1 if event['timed_out'] else event['returncode']


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--recipes', default=','.join(RECIPES))
    ap.add_argument('--out', default=None)
    args = ap.parse_args(argv)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = Path(args.out) if args.out else OUT_ROOT/f'deps-{stamp}'
    out.mkdir(parents=True, exist_ok=False)
    summary = {}
    for name in args.recipes.split(','):
        recipe, dest, log = RECIPES[name], out/name, []
        dest.mkdir()
        codes = [run_step(recipe, step, dest, log) for step in recipe['steps']]
        files = sorted(p for p in dest.iterdir() if p.is_file() and p.suffix == '.whl')
        manifest = dict(recipe=name, created_utc=stamp, platform=recipe['platform'], python=recipe['python'], steps=recipe['steps'],
                        status='complete' if not any(codes) else 'incomplete', commands=log,
                        files=[dict(name=p.name, bytes=p.stat().st_size, sha256=sha(p)) for p in files],
                        note='downloaded only; installation/compatibility is verified in the live Pod, not here')
        with (dest/'manifest.json').open('x') as handle:
            json.dump(manifest, handle, indent=1)
        summary[name] = dict(status=manifest['status'], files=len(files), bytes=sum(f['bytes'] for f in manifest['files']))
    print(json.dumps(dict(out=str(out), summary=summary), indent=1))
    return 0 if all(v['status'] == 'complete' for v in summary.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
