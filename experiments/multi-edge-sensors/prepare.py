"""Freeze models, wheels, MediaMTX, fixture images and generated inputs into a NEW prepared directory.

Every source is checked against its pinned sha256 (sources-*.json) before any
output is created; --fetch downloads only missing URL sources into the cache.
Bundles are deterministic tars (sorted, mtime 0, uid/gid 0) served to Pods by
the owned cache; the output directory must not exist and is never overwritten.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import urllib.request

HERE = Path(__file__).resolve().parent
ARCHES = ('x86_64', 'aarch64')
WHEELS = ('numpy', 'onnxruntime', 'pillow', 'tokenizers', 'av')
DEFAULT_MODELS = dict(V=dict(onnx='yolo11n-onnx', weights='yolo11n-pt', provenance='yolo11n-export-provenance', label='yolo11n-onnx'),
                      T=dict(onnx='minilm-onnx/model.onnx', tokenizer='minilm-tokenizer.json', label='all-MiniLM-L6-v2-onnx'))
CODE = ('runtime.py', 'models.py', 'rtsp.py', 'scenario.py', 'bootstrap.py')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fetch(entry, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(entry['url'], timeout=300) as response:
        data = response.read()
    if sha(data) != entry['sha256']:
        raise ValueError(f"{entry['id']}: downloaded bytes do not match the pinned sha256")
    with path.open('xb') as handle:
        handle.write(data)


def verify(manifest, cache, allow_fetch):
    """id -> bytes of every source, all hashes checked; ValueError naming every bad source."""
    data, errors = {}, []
    for entry in manifest['sources']:
        path = cache/entry['path']
        if not path.is_file() and allow_fetch and entry.get('url'):
            try:
                fetch(entry, path)
            except Exception as error:
                errors.append(f"{entry['id']}: fetch failed {error!r}")
                continue
        if not path.is_file():
            errors.append(f"{entry['id']}: missing {path}" + ('' if entry.get('url') else ' (derived; rerun its export command)'))
            continue
        raw = path.read_bytes()
        if sha(raw) != entry['sha256']:
            errors.append(f"{entry['id']}: sha256 mismatch for {path}")
            continue
        data[entry['id']] = raw
    if errors:
        raise ValueError('; '.join(errors))
    return data


def tar(members):
    """Deterministic uncompressed tar of {name: (bytes, mode)}."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for name in sorted(members):
            payload, mode = members[name]
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime, info.uid, info.gid, info.uname, info.gname = len(payload), mode, 0, 0, 0, '', ''
            archive.addfile(info, io.BytesIO(payload))
    return out.getvalue()


def mediamtx_members(raw):
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(raw))) as archive:
        binary = archive.extractfile('mediamtx').read()
        license_text = archive.extractfile('LICENSE').read()
    return {'mediamtx/mediamtx': (binary, 0o755), 'mediamtx/LICENSE': (license_text, 0o644)}


def class_names(onnx_bytes):
    import ast
    import onnxruntime
    session = onnxruntime.InferenceSession(onnx_bytes, providers=['CPUExecutionProvider'])
    names = ast.literal_eval(session.get_modelmeta().custom_metadata_map['names'])
    return [names[i] for i in range(len(names))]


def build(manifest, data, scenario_path):
    sys.path.insert(0, str(HERE))
    import scenario
    entries = {e['id']: e for e in manifest['sources']}
    bundles = {}
    for arch in ARCHES:
        wheels = [e for e in manifest['sources'] if e['kind'] == 'wheel' and e['arch'] == arch]
        if sorted(e['id'].split('-')[1].lower() for e in wheels) != sorted(WHEELS):
            raise ValueError(f'Expected exactly one wheel each of {WHEELS} for {arch}')
        members = {'wheels/'+Path(e['path']).name: (data[e['id']], 0o644) for e in wheels}
        requirements = ''.join(f"{Path(e['path']).name.split('-')[0].lower()}=={Path(e['path']).name.split('-')[1]} "
                               f"--hash=sha256:{e['sha256']}\n" for e in sorted(wheels, key=lambda e: e['id']))
        members['requirements.txt'] = (requirements.encode(), 0o644)
        bundles[f'wheels-{arch}.tar'] = members
        bundles[f'mediamtx-{arch}.tar'] = mediamtx_members(data['mediamtx-'+arch])
    models = {e['member']: (data[e['id']], 0o644) for e in manifest['sources'] if e['kind'] == 'model'}
    chosen = manifest.get('models', DEFAULT_MODELS)
    vm, tm = chosen['V'], chosen['T']
    names = class_names(data[vm['onnx']])
    models['models/class-names.json'] = (json.dumps(names).encode(), 0o644)
    t_dir = str(Path(entries[tm['onnx']]['member']).parent.parent.relative_to('models'))
    layout = dict(V=dict(file=str(Path(entries[vm['onnx']]['member']).relative_to('models')), label=vm['label']),
                  T=dict(dir=t_dir, label=tm['label']))
    models['models/manifest.json'] = (json.dumps(layout, sort_keys=True).encode(), 0o644)
    bundles['models.tar'] = models
    bundles['fixtures.tar'] = {e['member']: (data[e['id']], 0o644) for e in manifest['sources'] if e['kind'] == 'image'}
    frozen = scenario.load(scenario_path)
    images = {Path(e['member']).name: e['path'] for e in manifest['sources'] if e['kind'] == 'image'}
    seqs = sorted(frozen['inputs']['calibration'] + frozen['inputs']['evaluation'])
    inputs = dict(video={}, text={})
    for source, spec in frozen['video_sources'].items():
        image = Path(manifest['_cache'])/images[spec['image']]
        inputs['video'][source] = {str(k): dict(generated_rgb_sha256=sha(scenario.frame(image, spec['seed'], k).tobytes()))
                                   for k in seqs}
    for source, spec in frozen['text_sources'].items():
        inputs['text'][source] = {str(k): dict(text=(t := scenario.sentence(spec['seed'], k)), sha256=sha(t.encode())) for k in seqs}
    model_info = dict(V=dict(label=vm['label'], member=entries[vm['onnx']]['member'], sha256=entries[vm['onnx']]['sha256'],
                             derived_from=dict(id=vm['weights'], sha256=entries[vm['weights']]['sha256'], url=entries[vm['weights']]['url']),
                             provenance=dict(id=vm['provenance'], sha256=entries[vm['provenance']]['sha256'],
                                             export=json.loads(data[vm['provenance']]))),
                      T=dict(label=tm['label'], member=entries[tm['onnx']]['member'], sha256=entries[tm['onnx']]['sha256'],
                             tokenizer_sha256=entries[tm['tokenizer']]['sha256'], origin=entries[tm['onnx']]['origin']),
                      class_names_sha256=sha(models['models/class-names.json'][0]))
    return bundles, inputs, model_info, names


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--sources', type=Path, default=HERE/'sources-20261022.json')
    parser.add_argument('--scenario', type=Path, default=HERE/'scenario-20261022.json')
    parser.add_argument('--cache', type=Path, required=True, help='dedicated cache root holding every pinned source path')
    parser.add_argument('--output', type=Path, required=True, help='new directory; must not exist')
    parser.add_argument('--fetch', action='store_true', help='download missing URL sources into the cache (hash-checked)')
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.exit(2, f'refusing to overwrite existing {args.output}\n')
    manifest = json.loads(args.sources.read_text())
    try:
        data = verify(manifest, args.cache, args.fetch)
    except ValueError as error:
        parser.exit(3, f'source verification failed: {error}\n')
    manifest['_cache'] = str(args.cache)
    bundles, inputs, model_info, names = build(manifest, data, args.scenario)
    args.output.mkdir(parents=True)
    (args.output/'bundles').mkdir()
    recorded = {}
    for name, members in bundles.items():
        blob = tar(members)
        with (args.output/'bundles'/name).open('xb') as handle:
            handle.write(blob)
        recorded[name] = dict(sha256=sha(blob), bytes=len(blob), members={m: sha(v[0]) for m, v in sorted(members.items())})
    code = {name: sha((HERE/name).read_bytes()) for name in CODE}
    prepared = dict(name=args.output.name, sources=dict(path=str(args.sources), sha256=sha(args.sources.read_bytes()),
                    entries=[{k: v for k, v in e.items()} for e in manifest['sources']]),
                    scenario=dict(path=str(args.scenario), sha256=sha(args.scenario.read_bytes()), content=json.loads(args.scenario.read_text())),
                    bundles=recorded, models=model_info, class_names=names, inputs=inputs, code=code,
                    scope='frozen assets and generated inputs; decoded RTSP frames are frozen later per source Pod')
    with (args.output/'prepared.json').open('x') as handle:
        json.dump(prepared, handle, indent=1)
    print(json.dumps(dict(output=str(args.output), bundles={k: v['sha256'] for k, v in recorded.items()})))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
