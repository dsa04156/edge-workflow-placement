"""Cross-check the T pipeline (published ONNX + tokenizer.json, models.Embedder) against sentence-transformers.

Run in the isolated sentence-transformers env with a local model directory at
the pinned revision. Writes one new JSON file (never overwrites); nonzero exit
on any tolerance violation.
"""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--scenario', type=Path, default=HERE/'scenario-20261022.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    import numpy
    import sentence_transformers
    sys.path.insert(0, str(HERE))
    import models
    import scenario
    frozen = scenario.load(args.scenario)
    texts = [scenario.sentence(s['seed'], k) for s in frozen['text_sources'].values()
             for k in frozen['inputs']['calibration'] + frozen['inputs']['evaluation']]
    texts += [models.PROMPT.format(n) for n in ('person', 'bus', 'cat', 'tie', 'remote')]
    original = sentence_transformers.SentenceTransformer(str(args.model_dir), device='cpu')
    reference = original.encode(texts, batch_size=1, normalize_embeddings=True, convert_to_numpy=True)
    embedder = models.Embedder(str(args.model_dir))
    rows = []
    for text, ref in zip(texts, reference):
        got = embedder.embed(text)
        rows.append(dict(text=text, cosine=float(numpy.dot(got, ref)), max_abs=float(numpy.abs(got-ref).max())))
    worst = min(r['cosine'] for r in rows)
    result = dict(scope='models.Embedder (published onnx/model.onnx, ORT CPU) vs sentence-transformers %s (safetensors, torch CPU)'
                  % sentence_transformers.__version__, texts=len(rows), min_cosine=worst,
                  max_abs=max(r['max_abs'] for r in rows), tolerance_cosine_min=models.TOLERANCE['embedding_cosine_min'],
                  ok=worst >= models.TOLERANCE['embedding_cosine_min'], rows=rows)
    with args.output.open('x') as f:
        json.dump(result, f, indent=1)
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
