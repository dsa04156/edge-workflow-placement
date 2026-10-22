"""In-Pod init: fetch hash-pinned bundles from the owned cache over HTTP, install wheels offline, exec the runtime.

No `kubectl exec -i` upload. Bundles per role: video-source = wheels + mediamtx + fixtures,
worker = wheels + models, text-source/controller = none (stdlib only). Every bundle sha256 is checked
against bundles.json from the run ConfigMap before extraction into the emptyDir.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import time
import urllib.request

ROLE_BUNDLES = {'video-source': ['wheels-{arch}.tar', 'mediamtx-{arch}.tar', 'fixtures.tar'],
                'worker': ['wheels-{arch}.tar', 'models.tar'], 'text-source': [], 'controller': []}


def main():
    config = Path(__file__).resolve().parent
    root = Path(os.environ.get('ASSET_ROOT', '/work'))
    manifest = json.loads((config/'bundles.json').read_text())
    arch = platform.machine()
    events = []
    for template in ROLE_BUNDLES[os.environ['ROLE']]:
        name = template.format(arch=arch)
        start = time.monotonic()
        path, hasher = root/name, hashlib.sha256()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(os.environ['ASSET_URL']+'/'+name, timeout=300) as response, path.open('xb') as output:
            while block := response.read(1 << 20):
                output.write(block)
                hasher.update(block)
        if hasher.hexdigest() != manifest[name]['sha256']:
            raise RuntimeError('Bundle hash mismatch: '+name)
        with tarfile.open(path) as archive:
            archive.extractall(root, filter='data')
        path.unlink()
        events.append(dict(bundle=name, sha256=hasher.hexdigest(), fetch_extract_ms=(time.monotonic()-start)*1000))
    if (root/'requirements.txt').exists():
        start = time.monotonic()
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-compile', '--no-cache-dir',
                        '--no-index', '--no-deps', '--require-hashes', '--find-links', str(root/'wheels'), '--target', str(root/'site'),
                        '-r', str(root/'requirements.txt')], check=True)
        events.append(dict(step='pip-install', ms=(time.monotonic()-start)*1000))
        os.environ['PYTHONPATH'] = str(root/'site')
    if (root/'mediamtx/mediamtx').exists():
        (root/'mediamtx/mediamtx').chmod(0o755)
    print(json.dumps(dict(event='bootstrap', arch=arch, steps=events)), flush=True)
    os.execv(sys.executable, [sys.executable, '-u', str(config/'runtime.py'), 'serve'])


if __name__ == '__main__':
    main()
