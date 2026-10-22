"""Local CPU reference for one run: same ONNX bytes on the run's frozen decoded frames and prepared sentences.

Writes <run>/reference.json exclusively (never overwrites). Models are read from
the prepared models.tar and checked against the prepared hashes first.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import models  # noqa: E402


def compute(prepared_dir, run_dir, seqs, workflows):
    """CPU reference for the given seqs on the run's frozen frames and prepared sentences.

    Used by this CLI (all seqs, final evaluation audit) and by the collector before planning
    (calibration seqs only). Raises ValueError on any bundle or frozen-input hash mismatch.
    """
    prepared_dir, run_dir = Path(prepared_dir), Path(run_dir)
    prepared = json.loads((prepared_dir/'prepared.json').read_text())
    bundle = prepared_dir/'bundles/models.tar'
    if hashlib.sha256(bundle.read_bytes()).hexdigest() != prepared['bundles']['models.tar']['sha256']:
        raise ValueError('models.tar differs from prepared.json')
    frozen = json.loads((run_dir/'frozen-inputs.json').read_text())
    sc = prepared['scenario']['content']
    seqs = [str(s) for s in sorted(seqs)]
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(bundle) as archive:
            archive.extractall(tmp, filter='data')
        root = Path(tmp)/'models'
        names = json.loads((root/'class-names.json').read_text())
        detector = models.Detector(root/'yolo11n.onnx', 1)
        embedder = models.Embedder(str(root/'minilm'), 1, names)
        video, text = {}, {}
        for sid in sc['video_sources']:
            for seq in seqs:
                png = (run_dir/'frozen-inputs'/f'{sid}-{seq}.png').read_bytes()
                if hashlib.sha256(png).hexdigest() != frozen[sid][seq]['png_sha256']:
                    raise ValueError(f'frozen frame {sid}/{seq} differs from frozen-inputs.json')
                rgb = models.decode_png(png)
                video[f'{sid}/{seq}'] = dict(input_sha256=hashlib.sha256(rgb.tobytes()).hexdigest(),
                                             png_sha256=hashlib.sha256(png).hexdigest(), output=detector(rgb))
        for sid in sc['text_sources']:
            for seq in seqs:
                sentence = prepared['inputs']['text'][sid][seq]['text']
                text[f'{sid}/{seq}'] = dict(input_sha256=hashlib.sha256(sentence.encode()).hexdigest(), output=embedder(sentence))
        fused = {f"{f['id']}/{seq}": models.fuse(video[f"{f['video']}/{seq}"]['output'], text[f"{f['text']}/{seq}"]['output'], names)
                 for f in workflows for seq in seqs}
    import numpy
    import onnxruntime
    return dict(scope='local CPU reference (ORT CPUExecutionProvider, 1 thread) on the run frozen inputs; same ONNX bytes as the Pods',
                seqs=[int(s) for s in seqs],
                host=dict(machine=platform.machine(), python=platform.python_version(), onnxruntime=onnxruntime.__version__,
                          numpy=numpy.__version__),
                models=dict(V=prepared['models']['V']['sha256'], T=prepared['models']['T']['sha256']),
                tolerance=models.TOLERANCE, V=video, T=text, F=fused)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--prepared', type=Path, required=True)
    args = parser.parse_args(argv)
    out = args.run/'reference.json'
    if out.exists():
        parser.exit(2, f'refusing to overwrite {out}\n')
    sc = json.loads((args.prepared/'prepared.json').read_text())['scenario']['content']
    design = json.loads((args.run/'design.json').read_text())
    try:
        result = compute(args.prepared, args.run, sc['inputs']['calibration'] + sc['inputs']['evaluation'], design['workflows'])
    except ValueError as error:
        parser.exit(3, f'{error}\n')
    with out.open('x') as handle:
        json.dump(result, handle, indent=1)
    print(json.dumps(dict(reference=str(out), V=len(result['V']), T=len(result['T']), F=len(result['F']))))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
