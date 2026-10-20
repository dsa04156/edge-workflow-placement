"""Derive byte-exact RGB/PCM fixtures; archive provenance before deployment."""
import hashlib
import io
import json
from pathlib import Path
import tarfile

import numpy as np
from PIL import Image
import soundfile as sf

from run_smoke import ASSETS


def main():
    original = ASSETS / 'selected-data/pairs.json'
    source = json.loads(original.read_text())
    target = ASSETS / 'load-cache'
    target.mkdir(exist_ok=False)
    corpus = target / 'corpus'
    corpus.mkdir()
    pairs = []
    for pair in source['pairs']:
        row = dict(pair)
        for kind in ('image', 'audio'):
            old = Path(pair[kind + '_path'])
            body = old.read_bytes()
            assert hashlib.sha256(body).hexdigest() == pair[kind + '_sha256']
            (corpus / old.name).write_bytes(body)
            row[kind + '_path'] = '/work/corpus/' + old.name
            if kind == 'image':
                array = np.asarray(Image.open(io.BytesIO(body)).convert('RGB'), dtype=np.uint8)
            else:
                array, rate = sf.read(io.BytesIO(body), dtype='float32')
                assert rate == 16000 and array.ndim == 1
            raw_path = corpus / f"{pair['question_id']}-{kind}.npy"
            np.save(raw_path, array, allow_pickle=False)
            raw = raw_path.read_bytes()
            row.update({f'raw_{kind}_path': '/work/corpus/' + raw_path.name,
                f'raw_{kind}_bytes': len(raw), f'raw_{kind}_sha256': hashlib.sha256(raw).hexdigest(),
                f'raw_{kind}_shape': list(array.shape), f'raw_{kind}_dtype': str(array.dtype),
                f'raw_{kind}_array_bytes': array.nbytes})
        pairs.append(row)
    manifest = dict(pairs=pairs, scope='decoded fixture replay from owned edge Pod; no live sensor',
        source_manifest_sha256=hashlib.sha256(original.read_bytes()).hexdigest(),
        decode_boundary='offline fixture derivation before deployment; clock starts at input ready',
        numpy=np.__version__, fixture_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (corpus / 'pairs.json').write_text(json.dumps(manifest, indent=2))
    (ASSETS / 'load-pairs.json').write_text(json.dumps(manifest, indent=2))
    (corpus / 'reference.jsonl').write_bytes((ASSETS / 'reference-v1/results.jsonl').read_bytes())
    rawtar = target / 'source-raw.tar'
    with tarfile.open(rawtar, 'w') as archive:
        archive.add(corpus, arcname='corpus')
    bundles = json.loads((ASSETS / 'bundles.json').read_text())
    bundles['source.tar'] = dict(bytes=rawtar.stat().st_size, sha256=hashlib.sha256(rawtar.read_bytes()).hexdigest())
    wheel = next((ASSETS / 'network-wheels').glob('*.whl'))
    bundles[wheel.name] = dict(bytes=wheel.stat().st_size, sha256=hashlib.sha256(wheel.read_bytes()).hexdigest())
    assert bundles[wheel.name]['sha256'] == '3334091326e560a506635449af03b26920d22d4e5a7996aed354363d106fcef8'
    (ASSETS / 'load-bundles.json').write_text(json.dumps(bundles, indent=2))
    with tarfile.open(ASSETS / 'load-cache-upload.tar', 'w') as archive:
        for name in json.loads((ASSETS / 'bundles.json').read_text()):
            archive.add(rawtar if name == 'source.tar' else ASSETS / 'cache-bundles' / name, arcname=name)
        archive.add(wheel, arcname=wheel.name)
    print(json.dumps(dict(pairs=len(pairs), source_tar_bytes=rawtar.stat().st_size,
        upload_bytes=(ASSETS / 'load-cache-upload.tar').stat().st_size)))


if __name__ == '__main__':
    main()
