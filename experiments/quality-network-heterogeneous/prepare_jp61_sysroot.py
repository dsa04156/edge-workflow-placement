"""JP6.1 runtime dependency closure for the NVIDIA redist torch on nvcr.io/nvidia/l4t-jetpack r36.4 (no apt, no host change).

Observed live (qnh-20261023t090355): the pinned l4t-jetpack image starts on dev0001 (L4T R36.4.7, driver API 12060) but
`import torch` fails with libopenblas.so.0 missing; the wheel's ELF NEEDED list also contains libcusparseLt.so.0.
This script, run on the collector host only:
  1. downloads pinned official artifacts (sha256 checked): jammy arm64 libopenblas0-pthread + libgfortran5 .debs
     (ports.ubuntu.com) and cuSPARSELt 0.6.2.3 linux-aarch64 (developer.download.nvidia.com redist, JSON metadata hash);
  2. extracts only their shared libraries (dpkg-deb -x / tar) into <out>/sysroot/lib, recording per-file sha256;
  3. inventories the pinned image's files by streaming its registry layers (names only; nothing executed);
  4. resolves every external NEEDED soname of the torch/torchvision wheel files and of the added libraries against
     image library dirs + sysroot, and reports any unresolved soname BEFORE another live wave.
The Pod preloads the sysroot libraries in dependency order with ctypes RTLD_GLOBAL before importing torch.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import proc  # noqa: E402

ARTIFACTS = {
    'libopenblas0-pthread': dict(url='http://ports.ubuntu.com/ubuntu-ports/pool/universe/o/openblas/libopenblas0-pthread_0.3.20+ds-1_arm64.deb',
                                 index='jammy universe binary-arm64 Packages.xz', kind='deb'),
    'libgfortran5': dict(url='http://ports.ubuntu.com/ubuntu-ports/pool/main/g/gcc-12/libgfortran5_12.3.0-1ubuntu1~22.04.3_arm64.deb',
                         index='jammy-updates main binary-arm64 Packages.xz', kind='deb'),
    'cusparselt': dict(url='https://developer.download.nvidia.com/compute/cusparselt/redist/libcusparse_lt/linux-aarch64/'
                           'libcusparse_lt-linux-aarch64-0.6.2.3-archive.tar.xz',
                       sha256='b081a82a884754fd7ab1f53aa9cd1a943d782b87268c3657991c287af3179811',
                       index='https://developer.download.nvidia.com/compute/cusparselt/redist/redistrib_0.6.2.json', kind='tar.xz'),
}
IMAGE = dict(registry='https://nvcr.io', repo='nvidia/l4t-jetpack', digest='sha256:34ccf0f3b63c6da9eee45f2e79de9bf7fdf3beda9abfd72bbf285ae9d40bb673')
LIB_DIRS = ('usr/lib/aarch64-linux-gnu', 'lib/aarch64-linux-gnu', 'usr/local/cuda/lib64', 'usr/local/cuda-12.6/targets/aarch64-linux/lib',
            'usr/lib/aarch64-linux-gnu/nvidia', 'usr/lib/aarch64-linux-gnu/tegra', 'usr/local/cuda/targets/aarch64-linux/lib', 'usr/lib', 'lib')
# Provided at runtime by the NVIDIA container runtime (CSV/host mounts), not by the image.
HOST_PROVIDED = ('libcuda.so.1', 'libnvrm_gpu.so', 'libnvrm_mem.so')
PRELOAD_ORDER = ('libgfortran.so.5', 'libopenblas.so.0', 'libcusparseLt.so.0')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def packages_sha(name, index_json):
    entries = json.loads(Path(index_json).read_text())['packages'][name]
    return entries[-1]['sha256'], entries[-1]['filename']


def download(url, dest, expected):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'qnh-prep'}), timeout=120) as r, dest.open('wb') as f:
        for block in iter(lambda: r.read(1 << 20), b''):
            f.write(block)
    actual = sha(dest)
    if actual != expected:
        raise ValueError(f'{url}: sha256 {actual} != {expected}')
    return actual


def needed(path):
    out = subprocess.run(['readelf', '-d', str(path)], capture_output=True, text=True, timeout=60).stdout
    return sorted(set(re.findall(r'\(NEEDED\)\s+Shared library: \[(.+)\]', out))), sorted(set(re.findall(r'\(SONAME\)\s+Library soname: \[(.+)\]', out)))


def image_inventory(out_path):
    """Stream every layer of the pinned image (registry v2, anonymous token) and record file/symlink names only."""
    token = json.load(urllib.request.urlopen(f"https://nvcr.io/proxy_auth?scope=repository:{IMAGE['repo']}:pull", timeout=30)).get('token')
    accept = 'application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, ' \
             'application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'
    h = {'Authorization': 'Bearer '+token, 'Accept': accept}
    get = lambda path: urllib.request.urlopen(urllib.request.Request(IMAGE['registry']+f"/v2/{IMAGE['repo']}/{path}", headers=h), timeout=120)
    index = json.load(get('manifests/'+IMAGE['digest']))
    if 'manifests' in index:
        arm = next(m for m in index['manifests'] if m.get('platform', {}).get('architecture') == 'arm64')
        manifest = json.load(get('manifests/'+arm['digest']))
    else:
        manifest = index
    names, links, layers = set(), {}, []
    for layer in manifest['layers']:
        with get('blobs/'+layer['digest']) as blob, tarfile.open(fileobj=blob, mode='r|*') as tar:
            count = 0
            for member in tar:
                name = member.name.lstrip('./')
                if name.split('/')[-1].startswith('.wh.'):
                    names.discard(name.rsplit('/', 1)[0]+'/'+name.split('/')[-1][4:])
                    continue
                if member.issym() or member.islnk():
                    links[name] = member.linkname
                names.add(name)
                count += 1
        layers.append(dict(digest=layer['digest'], size=layer['size'], entries=count))
    inventory = dict(image=IMAGE, manifest_layers=layers, files=sorted(n for n in names if '/lib' in n and '.so' in n), symlinks=links)
    Path(out_path).write_text(json.dumps(inventory, indent=1))
    return inventory


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--out', required=True, help='new directory')
    ap.add_argument('--packages-index', default=str(ROOT/'.scratch/quality-network-heterogeneous/live-extra/jammy-arm64-openblas-packages.json'))
    ap.add_argument('--wheelhouse', default=str(ROOT/'artifacts/quality-network-heterogeneous/deps-20261023T075919Z/jp61-aarch64-cp310'))
    ap.add_argument('--skip-image', action='store_true')
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    (out/'downloads').mkdir()
    lib = out/'sysroot/lib'
    lib.mkdir(parents=True)
    record = dict(created_utc=datetime.now(timezone.utc).isoformat(), artifacts={}, sysroot={})
    for name, spec in ARTIFACTS.items():
        expected = spec.get('sha256') or packages_sha(name, args.packages_index)[0]
        dest = out/'downloads'/Path(spec['url']).name.replace('%2B', '+')
        record['artifacts'][name] = dict(url=spec['url'], sha256=download(spec['url'], dest, expected), bytes=dest.stat().st_size,
                                         hash_source=spec['index'])
        staging = out/'staging'/name
        staging.mkdir(parents=True)
        if spec['kind'] == 'deb':
            proc.run(['dpkg-deb', '-x', str(dest), str(staging)], out/'logs', f'extract-{name}', 300)
        else:
            with tarfile.open(dest) as tar:
                tar.extractall(staging, filter='data')
        for path in sorted(staging.rglob('*.so*')):
            if path.is_file() and not path.is_symlink():
                target = lib/path.name
                target.write_bytes(path.read_bytes())
                needs, soname = needed(target)
                record['sysroot'][path.name] = dict(from_artifact=name, original_path=str(path.relative_to(staging)), sha256=sha(target),
                                                    bytes=target.stat().st_size, soname=soname, needed=needs)
    # soname links (loader resolves NEEDED by soname)
    for name, info in list(record['sysroot'].items()):
        for soname in info['soname']:
            if soname != name and not (lib/soname).exists():
                (lib/soname).symlink_to(name)
                info.setdefault('soname_links', []).append(soname)
    # wheel NEEDED sonames
    wheel_needed = {}
    for wheel in sorted(Path(args.wheelhouse).glob('torch*.whl')):
        with zipfile.ZipFile(wheel) as z:
            for member in z.namelist():
                if member.endswith('.so') or '.so.' in member:
                    tmp = out/'staging'/'wheel-elf'/Path(member).name
                    tmp.parent.mkdir(parents=True, exist_ok=True)
                    tmp.write_bytes(z.read(member))
                    needs, soname = needed(tmp)
                    wheel_needed[f'{wheel.name}:{member}'] = dict(needed=needs, soname=soname)
    record['wheel_needed'] = wheel_needed
    inventory = None if args.skip_image else image_inventory(out/'image-inventory.json')
    provided_in_wheels = {s for v in wheel_needed.values() for s in v['soname']} | {Path(k.split(':')[1]).name for k in wheel_needed}
    available = set(record['sysroot']) | {l for v in record['sysroot'].values() for l in v.get('soname_links', [])} | provided_in_wheels
    if inventory:
        available |= {Path(f).name for f in inventory['files'] if any(f.startswith(d+'/') for d in LIB_DIRS)}
    required = {s for v in list(wheel_needed.values()) + list(record['sysroot'].values()) for s in v['needed']}
    record['closure'] = dict(required=sorted(required), host_provided=list(HOST_PROVIDED), image_checked=inventory is not None,
                             unresolved=sorted(s for s in required if s not in available and s not in HOST_PROVIDED),
                             preload_order=[n for n in PRELOAD_ORDER if n in available])
    (out/'sysroot-manifest.json').write_text(json.dumps(record, indent=1))
    print(json.dumps(dict(out=str(out), sysroot=sorted(record['sysroot']), unresolved=record['closure']['unresolved'],
                          image_checked=record['closure']['image_checked']), indent=1))
    return 0 if not record['closure']['unresolved'] else 1


if __name__ == '__main__':
    sys.exit(main())
