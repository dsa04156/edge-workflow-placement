"""Offline freeze (no kubectl): contract.json + assets-manifest.json for the unchanged heterogeneous bootstrap.py.

  python3 prepare.py freeze --assets <models dir> --vendor-wheels <dir> --out <new dir>
  <env python> prepare.py parity --frozen <out dir> --assets <models dir> --out <new json>   (processors only, no inference)

The contract pins model weights (sha256 from protocol.MODELS, re-hashed), the four Intel-smoke inputs (sha256-checked),
prompts, pixel budget, greedy generation and the seeded schedule. Manifest groups follow bootstrap.py: 'common' (every
worker) and one group per recipe (that recipe's worker only). Nothing existing is overwritten.
"""
import argparse
import json
from pathlib import Path
import random
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import protocol as P  # noqa: E402

WHEEL_DIRS = {'mobilint-x86_64-cp311': ROOT/'artifacts/topology-profile-live/wheelhouse/x86_64',
              'cu130-aarch64-cp311': ROOT/'artifacts/quality-network-heterogeneous/deps-20261023T080028Z/cu130-aarch64-cp311'}
VENDOR_WHEELS = {  # task-owned downloads (vendor-src/wheels), pinned
    'mobilint-x86_64-cp311': {
        'opencv_python_headless-4.11.0.86-cp37-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl':
            '0e0a27c19dd1f40ddff94976cfe43066fbbe9dfbb2ec1907d66c19caef42a57b',
        'transformers_mblt-0.6.1-py3-none-any.whl': 'a83d1f769d38b8f477288e0ea2d6cefa58cf39e0d6dffa2a196f30b377d0071c',
        'mblt_npu_python-0.1.0-py3-none-any.whl': '31f95516002f9b77d54ec3dd310046e4a02ff77217c7832f4e915d7cd3f8b9c3'},
    'cu130-aarch64-cp311': {
        'accelerate-1.10.1-py3-none-any.whl': '3621cff60b9a27ce798857ece05e2b9f56fcc71631cfb31ccf71f0359c311f11',
        'psutil-7.1.0-cp36-abi3-manylinux_2_17_aarch64.manylinux2014_aarch64.whl':
            '7d4a113425c037300de3ac8b331637293da9be9713855c4fc9d2d97436d7259d'},
}


def entry(group, path, source):
    source = Path(source)
    return dict(group=group, path=path, source=str(source), bytes=source.stat().st_size, sha256=P.file_sha(source))


def verify_manifest(manifest):
    """Problems (empty = every source still has the frozen size and sha256)."""
    problems = []
    for item in manifest['files']:
        source = Path(item['source'])
        if not source.is_file() or source.stat().st_size != item['bytes'] or P.file_sha(source) != item['sha256']:
            problems.append(f"{item['path']}: source {source} differs from the frozen manifest")
    return problems


def frozen_inputs(corpus):
    rows = []
    for item in P.INPUTS:
        path = Path(corpus)/f"{item['question_id']}-image.npy"
        if P.file_sha(path) != item['image_sha256']:
            raise ValueError(f'{path}: sha256 differs from the pinned Intel-smoke input')
        rows.append(dict(item, prompt=item['question']+P.PROMPT_SUFFIX, source=str(path)))
    return rows


def schedule():
    """Probe: the smoke input on both arms. Qualification: inputs 1..3, both arms each, seeded arm order per input."""
    rng = random.Random(P.SEED)
    out = {}
    for stage, items in (('probe', P.INPUTS[:1]), ('qualification', P.INPUTS[1:])):
        rows = []
        for item in items:
            arms = sorted(P.ARMS)
            rng.shuffle(arms)
            rows += [dict(input=item['id'], arm=arm) for arm in arms]
        out[stage] = rows
    return out


def freeze(out, assets, vendor_wheels, corpus):
    out = Path(out)
    if out.exists():
        raise FileExistsError(f'{out} exists; prepare never overwrites a frozen output')
    models = {}
    for key, model in P.MODELS.items():
        got = {name: P.file_sha(Path(assets)/key/name) for name in sorted(model['weights'])}
        if got != model['weights']:
            raise ValueError(f'{key}: weight sha256 differs from protocol.MODELS: {got}')
        models[key] = dict(repo=model['repo'], revision=model['revision'], weights=model['weights'])
    inputs = frozen_inputs(corpus)
    contract = dict(P.CONTRACT, experiment=P.EXPERIMENT, seed=P.SEED, models=models,
                    inputs=[{k: v for k, v in row.items() if k != 'source'} for row in inputs], schedule=schedule(),
                    arms={arm: dict(node=a['node'], recipe=a['recipe'], model=a['model'], image=a['image']) for arm, a in P.ARMS.items()})
    out.mkdir(parents=True)
    (out/'contract.json').write_text(json.dumps(contract, indent=1, sort_keys=True)+'\n')
    files = [entry('common', 'contract.json', out/'contract.json')]
    for arm in P.ARMS.values():
        recipe, key = arm['recipe'], arm['model']
        for name in sorted(P.MODEL_FILES + tuple(P.MODELS[key]['weights'])):
            files.append(entry(recipe, f'models/{key}/{name}', Path(assets)/key/name))
        for wheel in sorted(WHEEL_DIRS[recipe].glob('*.whl')):
            files.append(entry(recipe, f'wheels/{recipe}/{wheel.name}', wheel))
        for name, sha in sorted(VENDOR_WHEELS[recipe].items()):
            files.append(entry(recipe, f'wheels/{recipe}/{name}', Path(vendor_wheels)/name))
            if files[-1]['sha256'] != sha:
                raise ValueError(f'{name}: sha256 differs from the pinned vendor wheel')
    footprint = {}
    for item in files:
        footprint[item['group']] = footprint.get(item['group'], 0) + item['bytes']
    manifest = dict(files=files, recipes={r: P.RECIPES[r] for r in sorted({a['recipe'] for a in P.ARMS.values()})},
                    contract_sha256=P.file_sha(out/'contract.json'), footprint_bytes=dict(footprint, total=sum(footprint.values())),
                    inputs=[dict(id=r['id'], source=r['source'], sha256=r['image_sha256']) for r in inputs])
    (out/'assets-manifest.json').write_text(json.dumps(manifest, indent=1, sort_keys=True)+'\n')
    return manifest


def parity(frozen, assets, out):
    """Both arms' processors on the four frozen inputs (no model load, no inference); reports actual differences."""
    import hashlib
    import io
    import numpy as np
    from PIL import Image
    from transformers import AutoProcessor
    from transformers_mblt.models.qwen3_vl.processing_qwen3_vl import MobilintQwen3VLProcessor
    out = Path(out)
    if out.exists():
        raise FileExistsError(out)
    contract = json.loads((Path(frozen)/'contract.json').read_text())
    manifest = json.loads((Path(frozen)/'assets-manifest.json').read_text())
    processors = {'cuda': AutoProcessor.from_pretrained(str(Path(assets)/P.ARMS['cuda']['model'])),
                  'mobilint': MobilintQwen3VLProcessor.from_pretrained(str(Path(assets)/P.ARMS['mobilint']['model']))}
    sources = {row['id']: row['source'] for row in manifest['inputs']}
    rows = []
    for item in contract['inputs']:
        data = Path(sources[item['id']]).read_bytes()
        image = Image.fromarray(np.load(io.BytesIO(data), allow_pickle=False))
        messages = json.loads(json.dumps(contract['messages_template']))
        messages[0]['content'][1]['text'] = item['prompt']
        seen = {}
        for arm, processor in processors.items():
            text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            batch = processor(text=[text], images=[image], images_kwargs=dict(size=contract['image_size']), return_tensors='pt')
            seen[arm] = dict(processor=type(processor).__name__, image_processor=type(processor.image_processor).__name__,
                             text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                             **{k+'_sha256': hashlib.sha256(batch[k].numpy().tobytes()).hexdigest() for k in ('input_ids', 'pixel_values', 'image_grid_thw')},
                             input_ids_len=int(batch['input_ids'].shape[1]), image_grid_thw=batch['image_grid_thw'].tolist(),
                             pre_merge_patches=int(batch['pixel_values'].shape[0]))
        keys = [k for k in seen['cuda'] if k.endswith('_sha256')]
        rows.append(dict(input=item['id'], arms=seen, differences=[k for k in keys if seen['cuda'][k] != seen['mobilint'][k]]))
    report = dict(contract_sha256=P.file_sha(Path(frozen)/'contract.json'), rows=rows, identical=all(not r['differences'] for r in rows),
                  scope='processor/tokenizer outputs only; no model load or inference')
    out.write_text(json.dumps(report, indent=1)+'\n')
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    f = sub.add_parser('freeze')
    f.add_argument('--assets', required=True)
    f.add_argument('--vendor-wheels', required=True)
    f.add_argument('--corpus', default=str(ROOT/P.CORPUS))
    f.add_argument('--out', required=True)
    p = sub.add_parser('parity')
    p.add_argument('--frozen', required=True)
    p.add_argument('--assets', required=True)
    p.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    if a.cmd == 'freeze':
        manifest = freeze(a.out, a.assets, a.vendor_wheels, a.corpus)
        print(json.dumps(dict(contract_sha256=manifest['contract_sha256'], footprint_bytes=manifest['footprint_bytes'], files=len(manifest['files']))))
    else:
        report = parity(a.frozen, a.assets, a.out)
        print(json.dumps(dict(identical=report['identical'], differences={r['input']: r['differences'] for r in report['rows']})))


if __name__ == '__main__':
    main()
