"""Local offline export of derived VQA stage graphs + CPU ONNX Runtime parity (reference/export validation only).

vision-static-448.onnx : ResNet18 trunk + spatial mean, static 1x3x448x448 -> 1x512 (OpenVINO NPU / vendor compilers)
speech-dynamic.onnx    : Wav2Vec2ForCTC logits, input_values [1, T] dynamic T (exact original length; no crop/pad)
fusion-dynamic.onnx    : MUTAN scores softmax, feature [1,512] + token ids [1, L] dynamic L (compilation probe)

Parity uses the frozen 60-pair corpus (raw .npy inputs, no filtering): torch CPU reference vs ORT CPU on the same
host tensors. Export/parity success is not accelerator execution or service-quality success. Writes a new
immutable directory artifacts/quality-network-heterogeneous/export-<UTC>/ (refused if present).
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import stages  # noqa: E402

LIVE = ROOT/'artifacts/topology-profile-live'
CORPUS = LIVE/'load-cache/corpus'
OUT_ROOT = ROOT/'artifacts/quality-network-heterogeneous'
OPSET = 17


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export(module, args, path, names, dynamic):
    import torch
    config = dict(exporter='torch.onnx.export (TorchScript, dynamo=False)', opset=OPSET, do_constant_folding=True,
                  input_names=names[0], output_names=names[1], dynamic_axes=dynamic, torch=torch.__version__)
    started = time.perf_counter()
    torch.onnx.export(module, args, str(path), opset_version=OPSET, input_names=names[0], output_names=names[1],
                      dynamic_axes=dynamic, do_constant_folding=True, dynamo=False)
    config['export_ms'] = (time.perf_counter()-started)*1000
    import onnx
    model = onnx.load(str(path))
    onnx.checker.check_model(str(path))
    config.update(sha256=sha(path), bytes=path.stat().st_size, onnx=onnx.__version__,
                  graph_inputs=[(i.name, [d.dim_value or d.dim_param for d in i.type.tensor_type.shape.dim]) for i in model.graph.input],
                  graph_outputs=[(o.name, [d.dim_value or d.dim_param for d in o.type.tensor_type.shape.dim]) for o in model.graph.output],
                  ops=sorted({n.op_type for n in model.graph.node}))
    return config


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--stages', default='vision,speech,fusion')
    ap.add_argument('--out', default=None)
    args = ap.parse_args(argv)
    import numpy as np
    import onnxruntime as ort
    import torch
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = Path(args.out) if args.out else OUT_ROOT/f'export-{stamp}'
    out.mkdir(parents=True, exist_ok=False)
    pairs = json.loads((LIVE/'load-pairs.json').read_text())['pairs']
    raw = lambda p, k: (CORPUS/Path(p['raw_'+k+'_path']).name).read_bytes()
    refs = {r['question_id']: r for r in map(json.loads, (LIVE/'reference-v1/results.jsonl').read_text().splitlines())}
    manifest = dict(created_utc=stamp, scope='local offline export + ORT CPU parity; not accelerator execution, not service quality',
                    host=dict(machine=platform.machine(), python=platform.python_version()), source=stages.legacy_check(),
                    model_root=str(Path(os.environ['MODEL_ROOT'])), jellybean_source=str(Path(os.environ['JELLYBEAN_SOURCE'])),
                    inputs=dict(pairs=str((LIVE/'load-pairs.json').relative_to(ROOT)), pairs_sha256=sha(LIVE/'load-pairs.json'),
                                count=len(pairs), filtering='none'),
                    ort=ort.__version__, ort_providers=['CPUExecutionProvider'], stages={})
    selected = args.stages.split(',')

    if 'vision' in selected:
        ref = stages.load('vision', 'cpu-torch')

        class VisionGraph(torch.nn.Module):
            def __init__(self, trunk):
                super().__init__()
                self.trunk = trunk

            def forward(self, x):
                f = self.trunk(x)
                return f.sum(3).sum(2).div(f.size(2) * f.size(3))
        path = out/'vision-static-448.onnx'
        dummy = torch.zeros(1, 3, 448, 448)
        config = export(VisionGraph(ref.module).eval(), (dummy,), path, (['image'], ['feature']), None)
        session = ort.InferenceSession(str(path), providers=['CPUExecutionProvider'])
        worst, worst_ref, rows = 0.0, 0.0, []
        for p in pairs:
            x = stages.vision_tensor_torchvision(stages.decode_image(raw(p, 'image')))
            with torch.inference_mode():
                f = ref.module(x)
                torch_feature = f.sum(3).sum(2).div(f.size(2) * f.size(3)).squeeze(0).numpy()
            onnx_feature = session.run(None, {'image': x.numpy()})[0].reshape(-1)
            reference = np.load(LIVE/f"reference-v1/{p['question_id']}.npy")
            d, dr = float(abs(onnx_feature - torch_feature).max()), float(abs(onnx_feature - reference).max())
            worst, worst_ref = max(worst, d), max(worst_ref, dr)
            rows.append(dict(question_id=p['question_id'], split=p['split'], max_abs_vs_torch_cpu=d, max_abs_vs_reference_v1=dr,
                             within_reference_rule=bool(np.allclose(onnx_feature, reference, rtol=1e-4, atol=1e-5))))
        try:
            import openvino as ov
            model = ov.Core().read_model(str(path))
            ov_check = dict(openvino=ov.__version__, read_model='ok', inputs=[str(i.get_partial_shape()) for i in model.inputs],
                            outputs=[str(o.get_partial_shape()) for o in model.outputs],
                            note='local read_model only; no compile, not NPU evidence')
        except Exception as error:
            ov_check = dict(read_model='failed', error=repr(error)[:400])
        manifest['stages']['vision'] = dict(export=config, original_model_sha256=ref.model_sha256, static_shape=[1, 3, 448, 448],
                                            parity=dict(n=len(rows), max_abs_vs_torch_cpu=worst, max_abs_vs_reference_v1=worst_ref,
                                                        all_within_reference_rule=all(r['within_reference_rule'] for r in rows), rows=rows),
                                            openvino_read=ov_check)
        del ref

    if 'speech' in selected:
        ref = stages.load('speech', 'cpu-torch')

        class SpeechGraph(torch.nn.Module):
            def __init__(self, model):
                super().__init__()
                self.model = model

            def forward(self, input_values):
                return self.model(input_values).logits
        path = out/'speech-dynamic.onnx'
        audio = stages.decode_audio(raw(pairs[0], 'audio'))
        dummy = ref.processor(audio, sampling_rate=16000, return_tensors='pt', padding='longest').input_values
        config = export(SpeechGraph(ref.module).eval(), (dummy,), path, (['input_values'], ['logits']),
                        {'input_values': {1: 'samples'}, 'logits': {1: 'frames'}})
        session = ort.InferenceSession(str(path), providers=['CPUExecutionProvider'])
        rows = []
        for p in pairs:
            audio = stages.decode_audio(raw(p, 'audio'))
            x = ref.processor(audio, sampling_rate=16000, return_tensors='pt', padding='longest').input_values
            with torch.inference_mode():
                logits = ref.module(x).logits.squeeze(0)
            torch_text = ref.processor.decode(torch.argmax(logits, dim=-1), skip_special_tokens=True)
            onnx_logits = session.run(None, {'input_values': x.numpy()})[0][0]
            onnx_text = ref.processor.decode(torch.from_numpy(onnx_logits.argmax(-1)), skip_special_tokens=True)
            rows.append(dict(question_id=p['question_id'], split=p['split'], samples=int(x.shape[1]),
                             max_abs_logit_vs_torch_cpu=float(abs(onnx_logits - logits.numpy()).max()),
                             transcript_equal_torch_cpu=onnx_text == torch_text,
                             transcript_equal_reference_v1=onnx_text == refs[p['question_id']]['transcript']))
        manifest['stages']['speech'] = dict(export=config, original_model_sha256=ref.model_sha256,
                                            shape_policy='dynamic samples axis; each request keeps its original length (no crop/pad). '
                                                         'Static-shape runtimes need exact-length reshape/compile per length; buckets would change semantics',
                                            parity=dict(n=len(rows), transcripts_equal_torch_cpu=sum(r['transcript_equal_torch_cpu'] for r in rows),
                                                        transcripts_equal_reference_v1=sum(r['transcript_equal_reference_v1'] for r in rows),
                                                        max_abs_logit=max(r['max_abs_logit_vs_torch_cpu'] for r in rows),
                                                        distinct_lengths=len({r['samples'] for r in rows}), rows=rows))
        del ref

    if 'fusion' in selected:
        ref = stages.load('fusion', 'cpu-torch')

        class FusionGraph(torch.nn.Module):
            def __init__(self, model):
                super().__init__()
                self.model = model

            def forward(self, feature, ids):
                return self.model(feature, ids).softmax(-1)
        path = out/'fusion-dynamic.onnx'
        rows, config = [], None
        try:
            ids0 = torch.tensor([[1, 2, 3, 4, 5]], dtype=torch.long)
            config = export(FusionGraph(ref.module).eval(), (torch.zeros(1, 512), ids0), path, (['feature', 'ids'], ['scores']),
                            {'ids': {1: 'tokens'}})
            session = ort.InferenceSession(str(path), providers=['CPUExecutionProvider'])
            wid = ref.vocab['word_to_wid']
            for p in pairs:
                q = p['question_id']
                feature = np.load(LIVE/f'reference-v1/{q}.npy')
                tokens = ref.namespace['tokenize_mcb'](refs[q]['transcript'])[:26]
                ids = torch.tensor([[wid.get(w, wid['UNK']) for w in tokens]], dtype=torch.long)
                with torch.inference_mode():
                    t_scores = ref.module(torch.from_numpy(feature).unsqueeze(0), ids).softmax(-1)[0].numpy()
                o_scores = session.run(None, {'feature': feature[None], 'ids': ids.numpy()})[0][0]
                rows.append(dict(question_id=q, split=p['split'], tokens=len(tokens), max_abs_score_vs_torch_cpu=float(abs(o_scores - t_scores).max()),
                                 top5_ids_equal=list(np.argsort(-o_scores)[:5]) == list(np.argsort(-t_scores)[:5])))
            status = 'exported_and_parity_checked'
        except Exception as error:
            status, rows = 'export_or_parity_failed', [dict(error=repr(error)[:600])]
        manifest['stages']['fusion'] = dict(status=status, export=config, original_model_sha256=ref.model_sha256,
                                            note='compilation probe artifact; the fusion serving paths are torch CPU/CUDA with strict load',
                                            parity=dict(n=len(rows), top5_equal=sum(r.get('top5_ids_equal', False) for r in rows),
                                                        max_abs_score=max((r.get('max_abs_score_vs_torch_cpu', 0) for r in rows), default=None),
                                                        distinct_token_lengths=len({r.get('tokens') for r in rows}), rows=rows))
    with (out/'manifest.json').open('x') as handle:
        json.dump(manifest, handle, indent=1)
    summary = {k: {kk: vv for kk, vv in v['parity'].items() if kk != 'rows'} for k, v in manifest['stages'].items()}
    print(json.dumps(dict(out=str(out), summary=summary), indent=1))


if __name__ == '__main__':
    sys.exit(main())
