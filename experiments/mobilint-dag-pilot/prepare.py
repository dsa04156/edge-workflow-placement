"""Offline preparation (no cluster): verify downloads, freeze selection/order, build the cache bundle.

  python3 prepare.py --downloads <dir> --wheelhouse <dir> --coco <dir> --out <new dir> --frozen <new json>

<downloads> holds hf-resnet50/{*.mxq,*.onnx}, wheels/onnxruntime-*.whl and imagenetv2/<tar> (hashes in protocol.ASSETS).
The frozen JSON (selection, order, every hash; no image or weight bytes) is written BEFORE any model output exists and
is never overwritten; code hashes are bound later by the release file (live.py --print-release-draft), not here. <out>/bundle is what the owned cache Pod serves; it is local-only (images/weights not published).
"""
import argparse
import json
from pathlib import Path
import shutil
import tarfile

import protocol as P

HERE = Path(__file__).resolve().parent
REUSED = {'bounded_http.py': HERE.parent/'quality-network-baselines/bounded_http.py',
          'http_runtime.py': HERE.parent/'real-topology-live/http_runtime.py'}


def file_sha(path):
    h = P.hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def verify(path, expected_sha, expected_bytes=None):
    path = Path(path)
    if expected_bytes is not None and path.stat().st_size != expected_bytes:
        raise ValueError(f'{path}: {path.stat().st_size} B != {expected_bytes} B')
    got = file_sha(path)
    if got != expected_sha:
        raise ValueError(f'{path}: sha256 {got} != {expected_sha}')
    return got


def prepare(downloads, wheelhouse, coco_dir, out, frozen_path):
    downloads, wheelhouse, out, frozen_path = Path(downloads), Path(wheelhouse), Path(out), Path(frozen_path)
    if frozen_path.exists():
        raise FileExistsError(f'{frozen_path} exists; a frozen protocol is never rewritten')
    bundle = out/'bundle'
    (bundle/'images').mkdir(parents=True, exist_ok=False)
    (bundle/'wheels').mkdir()
    for name, a in P.ASSETS.items():
        src = downloads/('wheels' if name.endswith('.whl') else 'hf-resnet50')/name
        verify(src, a['sha256'], a['bytes'])
        shutil.copyfile(src, bundle/('wheels' if name.endswith('.whl') else '.')/name)
    wheels = {}
    for arch, names in P.WHEELS.items():
        for name in names:
            if name in P.ASSETS:
                wheels[name] = P.ASSETS[name]['sha256']
                continue
            shutil.copyfile(wheelhouse/arch/name, bundle/'wheels'/name)
            wheels[name] = file_sha(bundle/'wheels'/name)
    tar_path = downloads/'imagenetv2'/P.IMAGENETV2['file']
    verify(tar_path, P.IMAGENETV2['sha256'], P.IMAGENETV2['bytes'])
    with tarfile.open(tar_path, 'r:') as archive:
        members = P.imagenetv2_members(archive.getnames())
        evaluation, warmup = P.select(members)
        for s in evaluation + warmup:
            data = archive.extractfile(s['member']).read()
            (bundle/'images'/f"{s['id']}.jpeg").write_bytes(data)
            s.update(file=f"images/{s['id']}.jpeg", sha256=P.sha256(data), bytes=len(data))
    parity = []
    for path in sorted(Path(coco_dir).glob('COCO_val2014_*.jpg')):
        data = path.read_bytes()
        sid = 'coco-'+path.stem.rsplit('_', 1)[-1]
        (bundle/'images'/f'{sid}.jpeg').write_bytes(data)
        parity.append(dict(id=sid, label=None, member=path.name, file=f'images/{sid}.jpeg', sha256=P.sha256(data), bytes=len(data)))
    frozen = dict(
        experiment=P.EXPERIMENT, seed=P.SEED, arms=list(P.ARMS), input=P.INPUT, mean=P.MEAN, std=P.STD,
        hf=dict(repo=P.HF_REPO, revision=P.HF_REVISION), assets=P.ASSETS, imagenetv2=P.IMAGENETV2, wheels=wheels,
        images=dict(mobilint=P.MOBILINT_IMAGE, python=P.PY_IMAGE), qbruntime=P.QBRUNTIME_VERSION,
        server=P.SERVER, edge_candidates=list(P.EDGE_CANDIDATES), resources=P.RESOURCES, deadlines=P.DEADLINES,
        evaluation=evaluation, warmup=warmup, parity=parity,
        parity_source=dict(path=str(coco_dir), note='COCO val2014 JPEGs of the earlier VQA corpus; no ImageNet labels'),
        schedule=P.schedule(evaluation, warmup, parity),
        claims=dict(allowed=['Mobilint execution with device receipts', 'per-stage/e2e latency (closed loop, c=1)',
                             'top-1/top-5 vs ImageNetV2 labels on 200 samples', 'MXQ vs FP32 ONNX reference parity'],
                    excluded=['quality superiority', 'capacity/goodput', 'same numeric model', 'energy']))
    frozen_path.parent.mkdir(parents=True, exist_ok=True)
    with frozen_path.open('x') as f:
        json.dump(frozen, f, indent=1)
        f.write('\n')
    files = {str(p.relative_to(bundle)): dict(sha256=file_sha(p), bytes=p.stat().st_size) for p in sorted(bundle.rglob('*')) if p.is_file()}
    (out/'bundle-manifest.json').write_text(json.dumps(dict(frozen_sha256=file_sha(frozen_path), files=files), indent=1)+'\n')
    return frozen


def main(argv=None):
    ap = argparse.ArgumentParser()
    for name in ('downloads', 'wheelhouse', 'coco', 'out', 'frozen'):
        ap.add_argument('--'+name, required=True)
    a = ap.parse_args(argv)
    frozen = prepare(a.downloads, a.wheelhouse, a.coco, a.out, a.frozen)
    print(json.dumps(dict(evaluation=len(frozen['evaluation']), warmup=len(frozen['warmup']), parity=len(frozen['parity']),
                          schedule=len(frozen['schedule']))))


if __name__ == '__main__':
    main()
