"""Offline prepared CUDA runtime (no kubectl, no download, no aarch64 code execution):

  python3 prepare_runtime.py --frozen <frozen dir> --out <new dir> [--python <cp311 interpreter>]

Installs the frozen cu130-aarch64-cp311 wheels (hash-checked, exactly the frozen manifest group) into <out>/build/site
with pip --target for the foreign platform (--platform manylinux_2_{28,27,25,17}/2014_aarch64 --python-version 3.11 --implementation cp
--abi cp311 --only-binary=:all: --no-index --no-compile). pip only unpacks wheels; it runs under a host CPython 3.11 so
python_version markers resolve as on the cp311 node. The tree is then bound file-by-file to the wheels (every file is
listed in its distribution's RECORD and, when RECORD carries a hash, equals it; pip-written metadata without a RECORD hash
is labeled pip-generated) and to the frozen contract/model entries. Console scripts (<target>/bin, host shebangs) are not
shipped. Writes prepared-manifest.json (vlm_runtime_cache format), runtime.tar (deterministic) and build-log.json.
"""
import argparse
import base64
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import protocol as P  # noqa: E402
import prepare  # noqa: E402
import vlm_runtime_cache as C  # noqa: E402

ARM = P.ARMS['cuda']
RECIPE, MODEL = ARM['recipe'], ARM['model']
# pip 26 does not expand one manylinux tag to older glibc levels: every tag used by the frozen wheels is listed (glibc <= 2.28,
# as torch's manylinux_2_28 wheel already required on the node image).
PLATFORMS = ('manylinux_2_28_aarch64', 'manylinux_2_27_aarch64', 'manylinux_2_25_aarch64', 'manylinux_2_17_aarch64', 'manylinux2014_aarch64')
RUNTIME = dict(machine='aarch64', python='3.11', implementation='cp', abi='cp311', platforms=list(PLATFORMS))


def frozen_entries(frozen):
    manifest = json.loads((Path(frozen)/'assets-manifest.json').read_text())
    if manifest.get('onnx'):
        raise ValueError('the prepared CUDA path carries no onnx assets')
    entries = [i for i in manifest['files'] if i['group'] in ('common', RECIPE)]
    problems = prepare.verify_manifest(dict(files=entries))
    if problems:
        raise ValueError('; '.join(problems))
    return manifest, entries


def pip_command(python, wheels, site, report, requirements):
    return [python, '-I', '-m', 'pip', 'install', '--no-index', '--no-cache-dir', '--disable-pip-version-check', '--no-compile',
            '--only-binary=:all:', *(x for p in PLATFORMS for x in ('--platform', p)), '--python-version', RUNTIME['python'],
            '--implementation', RUNTIME['implementation'], '--abi', RUNTIME['abi'], '--find-links', str(wheels),
            '--target', str(site), '--report', str(report), *requirements]


def bind_distributions(report, wheel_entries, requirements):
    """Every installed distribution came from one frozen wheel (same file name and sha256); every pin is installed."""
    by_name = {Path(e['path']).name: e for e in wheel_entries}
    dists = []
    for item in report['install']:
        name = urllib.parse.unquote(Path(item['download_info']['url']).name)
        entry = by_name.get(name)
        sha = (item['download_info'].get('archive_info') or {}).get('hashes', {}).get('sha256')
        if entry is None or sha != entry['sha256']:
            raise ValueError(f'{name}: not a frozen wheel of {RECIPE} (sha256 {sha})')
        dists.append(dict(name=item['metadata']['name'], version=item['metadata']['version'], wheel=name, wheel_sha256=sha,
                          frozen_path=entry['path'], requested=item.get('requested', False)))
    versions = {d['name'].lower().replace('_', '-'): d['version'] for d in dists}
    for requirement in requirements:
        name, version = requirement.split('==')
        if versions.get(name.lower().replace('_', '-')) != version:
            raise ValueError(f'{requirement} not installed (got {versions.get(name.lower())})')
    return sorted(dists, key=lambda d: d['name'].lower())


def record_index(site):
    """normalized target-relative path -> (distribution dir, RECORD sha256 hex or None). pip --target writes RECORD for
    its temporary prefix, so wheel .data scripts/data appear as ../../bin/... and ../../share/... and land at the target
    root; they are mapped there. Any other escaping entry is reported."""
    index, outside = {}, []
    for record in sorted(site.glob('*.dist-info/RECORD')):
        for row in csv.reader(record.read_text().splitlines()):
            path = os.path.normpath(row[0])
            if path.startswith('../../') and not os.path.normpath(path[len('../../'):]).startswith('..'):
                path = os.path.normpath(path[len('../../'):])
            elif path.startswith('..'):
                outside.append(row[0])
                continue
            digest = None
            if row[1:2] and row[1].startswith('sha256='):
                raw = row[1][len('sha256='):]
                digest = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)).hex()
            index[path] = (record.parent.name, digest)
    return index, outside


def scan_site(site):
    index, outside = record_index(site)
    files, excluded = [], []
    for directory, subdirs, names in os.walk(site):
        rel_dir = Path(directory).relative_to(site)
        if rel_dir == Path('.') and 'bin' in subdirs:
            subdirs.remove('bin')
            excluded.append('bin/ (console scripts with host shebangs; not used by the worker)')
        for name in subdirs + names:
            if (Path(directory)/name).is_symlink():
                raise ValueError(f'symlink in the prepared tree: {rel_dir/name}')
        for name in names:
            rel = str(rel_dir/name) if rel_dir != Path('.') else name
            path = Path(directory)/name
            sha = P.file_sha(path)
            if rel not in index:
                raise ValueError(f'{rel}: not listed in any installed RECORD')
            dist, digest = index[rel]
            if digest is not None and digest != sha:
                raise ValueError(f'{rel}: differs from its RECORD hash ({dist})')
            files.append(dict(path='site/'+rel, size=path.stat().st_size, sha256=sha, mode=0o755 if path.stat().st_mode & 0o111 else 0o644,
                              origin=dist if digest else f'pip-generated:{dist}'))
    return files, excluded + [f'RECORD entry outside site: {p}' for p in outside]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--frozen', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--python', default=str(Path.home()/'.local/bin/python3.11'))
    a = ap.parse_args(argv)
    frozen, out = Path(a.frozen), Path(a.out)
    started = time.time()
    asset_manifest, entries = frozen_entries(frozen)
    group_bytes = sum(e['bytes'] for e in entries)
    out.mkdir(parents=True, exist_ok=False)
    free = os.statvfs(out)
    meminfo = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    resources = dict(free_bytes=free.f_bavail*free.f_frsize, need_bytes=3*group_bytes, mem_available=meminfo['MemAvailable'].strip())
    if resources['free_bytes'] < resources['need_bytes']:
        raise SystemExit(f'not enough disk: {resources}')
    wheels = out/'wheels'
    wheels.mkdir()
    for e in entries:
        if e['path'].startswith('wheels/'):
            (wheels/Path(e['path']).name).symlink_to(Path(e['source']).resolve())
    spec = asset_manifest['recipes'][RECIPE]
    if spec.get('no_deps') or spec.get('preload'):
        raise ValueError('the prepared recipe expects dependency resolution and no preload')
    site, report_path = out/'build'/'site', out/'pip-report.json'
    command = pip_command(a.python, wheels, site, report_path, spec['requirements'])
    versions = subprocess.run([a.python, '-I', '-c', 'import sys,pip;print(sys.version.split()[0], pip.__version__)'], capture_output=True,
                              text=True, check=True).stdout.split()
    if not versions[0].startswith(RUNTIME['python']+'.'):
        raise SystemExit(f'{a.python} is CPython {versions[0]}, not {RUNTIME["python"]}')
    pip = subprocess.run(command, capture_output=True, text=True, env={**os.environ, 'PIP_CONFIG_FILE': os.devnull}, timeout=3600)
    (out/'pip.stdout').write_text(pip.stdout)
    (out/'pip.stderr').write_text(pip.stderr)
    if pip.returncode:
        raise SystemExit(f'pip failed ({pip.returncode}); see {out}/pip.stderr')
    wheel_entries = [e for e in entries if e['path'].startswith('wheels/')]
    dists = bind_distributions(json.loads(report_path.read_text()), wheel_entries, spec['requirements'])
    site_files, excluded = scan_site(site)
    frozen_files = [dict(path=e['path'], size=e['bytes'], sha256=e['sha256'], mode=0o644, origin='frozen:'+e['path'], source=e['source'])
                    for e in entries if e['path'] == 'contract.json' or e['path'].startswith(f'models/{MODEL}/')]
    files = sorted(site_files + frozen_files, key=lambda f: f['path'])
    sources = {f['path']: f.pop('source', None) or str(out/'build'/f['path']) for f in files}
    contract = json.loads((frozen/'contract.json').read_text())
    manifest = dict(format=C.FORMAT, recipe=RECIPE, runtime=RUNTIME, model=dict(key=MODEL, weights=contract['models'][MODEL]['weights']),
                    frozen={n: P.file_sha(frozen/n) for n in ('contract.json', 'assets-manifest.json')}, requirements=spec['requirements'],
                    builder=dict(python=versions[0], pip=versions[1], command=pip_command('python3.11', 'wheels', 'build/site', 'pip-report.json', [])[1:]), distributions=dists,
                    excluded=excluded, file_count=len(files), total_bytes=sum(f['size'] for f in files), content_sha256=C.content_sha256(files),
                    files=files)
    tar_path = out/'runtime.tar'
    C.write_tar(tar_path, files, lambda f: sources[f['path']])
    manifest['tar'] = dict(name='runtime.tar', bytes=tar_path.stat().st_size, sha256=P.file_sha(tar_path))
    problems = C.manifest_problems(manifest, dict(recipe=RECIPE, machine=RUNTIME['machine'], python=RUNTIME['python'],
                                                  asset_manifest_sha256=manifest['frozen']['assets-manifest.json'],
                                                  content_sha256=manifest['content_sha256']))
    if problems:
        raise SystemExit(f'prepared manifest rejected: {problems}')
    (out/'prepared-manifest.json').write_text(json.dumps(manifest, indent=1, sort_keys=True)+'\n')
    log = dict(command=command, resources_before=resources, pip_returncode=pip.returncode, elapsed_s=round(time.time()-started, 1),
               manifest_sha256=P.file_sha(out/'prepared-manifest.json'), content_sha256=manifest['content_sha256'], tar=manifest['tar'],
               file_count=manifest['file_count'], total_bytes=manifest['total_bytes'], site_files=len(site_files),
               versions={d['name']: d['version'] for d in dists}, excluded=excluded)
    (out/'build-log.json').write_text(json.dumps(log, indent=1)+'\n')
    print(json.dumps({k: v for k, v in log.items() if k != 'versions'}, indent=1))


if __name__ == '__main__':
    main()
