"""Install hash-bound offline wheels and role assets inside owned emptyDir."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import urllib.request

root = Path('/work')
config = Path(os.environ.get('CONFIG_ROOT', '/config'))
manifest = json.loads((config / 'bundles.json').read_text())
role = os.environ['TASK_ID']
arch = platform.machine()
names = ['source.tar'] if role == 'source' else ['wheels-' + arch + '.tar', role + '.tar']
for name in names:
    path = root / name
    hasher = hashlib.sha256()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(os.environ['ASSET_URL'] + '/' + name, timeout=120) as response, path.open('xb') as output:
        while block := response.read(1024 * 1024):
            output.write(block)
            hasher.update(block)
    if hasher.hexdigest() != manifest[name]['sha256']:
        raise RuntimeError('Bundle hash mismatch: ' + name)
    with tarfile.open(path) as archive:
        archive.extractall(root, filter='data')
    path.unlink()
if role != 'source':
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-compile',
                    '--no-cache-dir', '--no-index', '--require-hashes', '--find-links', '/work/wheels',
                    '--target', '/work/site', '-r', '/work/requirements.txt'], check=True)
    os.environ.update(PYTHONPATH='/work/site:'+str(config), MODEL_ROOT='/work/models',
                      JELLYBEAN_SOURCE='/work/jellybean', ADAPTER_MODULE='model_adapter')
    os.execv(sys.executable, [sys.executable, '-u', str(config / 'http_runtime.py')])
os.execv(sys.executable, [sys.executable, '-u', str(config / 'edge_client.py'), '--root', '/work/corpus'])
