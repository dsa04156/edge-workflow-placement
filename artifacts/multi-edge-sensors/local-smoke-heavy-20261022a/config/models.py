"""CPU inference for V (YOLO11n ONNX), T (all-MiniLM-L6-v2 ONNX) and the deterministic fusion F.

V: ultralytics-style letterbox (pad 114), conf 0.25, class-aware NMS IoU 0.7, max 300.
T: tokenizer.json, no padding, truncation 256, attention-mask mean pooling, L2 normalize;
   cosine against fixed prompts 'a photo of a {class}' for the 80 model class names.
F: explicit arithmetic, not a learned model: fused_c = max_det_score_c * max(0, cosine_c).
Imports of numpy/onnxruntime/tokenizers stay inside functions so stdlib-only roles can import this module.
"""
import ast
import io
import json
import math
import os
from pathlib import Path
import time

CONF, IOU, MAX_DET, PAD = 0.25, 0.7, 300, 114
MAX_TOKENS = 256
PROMPT = 'a photo of a {}'
ALERT_THRESHOLD = 0.2
# Pre-agreed quality tolerances against the local CPU reference (same ONNX bytes).
TOLERANCE = dict(box_abs_px=1.0, score_abs=1e-3, embedding_cosine_min=0.9999, similarity_abs=1e-4, fused_abs=1e-3)


def bundle_models(root):
    """(V onnx path, T model dir, labels) from the prepared models/manifest.json."""
    manifest = json.loads((Path(root)/'manifest.json').read_text())
    return Path(root)/manifest['V']['file'], str(Path(root)/manifest['T']['dir']), dict(V=manifest['V']['label'], T=manifest['T']['label'])


def session(path, threads):
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(str(path), options, providers=['CPUExecutionProvider'])


class Detector:
    def __init__(self, path, threads=1):
        self.session = session(path, threads)
        meta = self.session.get_modelmeta().custom_metadata_map
        self.names = {int(k): v for k, v in ast.literal_eval(meta['names']).items()}
        self.size = ast.literal_eval(meta['imgsz'])

    def preprocess(self, rgb):
        import numpy as np
        from PIL import Image
        h, w = rgb.shape[:2]
        th, tw = self.size
        r = min(th/h, tw/w)
        nw, nh = round(w*r), round(h*r)
        image = rgb if (nw, nh) == (w, h) else np.asarray(Image.fromarray(rgb).resize((nw, nh), Image.Resampling.BILINEAR))
        dw, dh = (tw-nw)/2, (th-nh)/2
        top, left = round(dh-0.1), round(dw-0.1)
        canvas = np.full((th, tw, 3), PAD, dtype=np.uint8)
        canvas[top:top+nh, left:left+nw] = image
        tensor = (canvas.astype(np.float32)/255.0).transpose(2, 0, 1)[None]
        return np.ascontiguousarray(tensor), r, left, top

    def __call__(self, rgb):
        import numpy as np
        tensor, r, left, top = self.preprocess(rgb)
        raw = self.session.run(None, {self.session.get_inputs()[0].name: tensor})[0][0].T  # 8400x84
        scores = raw[:, 4:]
        cls = scores.argmax(1)
        conf = scores[np.arange(len(cls)), cls]
        keep = conf > CONF
        raw, cls, conf = raw[keep], cls[keep], conf[keep]
        xy, wh = raw[:, :2], raw[:, 2:4]
        boxes = np.concatenate([xy-wh/2, xy+wh/2], 1)
        order = nms(boxes + cls[:, None]*7680.0, conf)[:MAX_DET]
        h, w = rgb.shape[:2]
        out = []
        for i in order:
            x1, y1, x2, y2 = boxes[i]
            box = [min(max((x1-left)/r, 0), w), min(max((y1-top)/r, 0), h), min(max((x2-left)/r, 0), w), min(max((y2-top)/r, 0), h)]
            out.append(dict(class_id=int(cls[i]), name=self.names[int(cls[i])], score=float(conf[i]), box=[float(v) for v in box]))
        return out


def nms(boxes, scores):
    import numpy as np
    order, keep = scores.argsort()[::-1], []
    area = (boxes[:, 2]-boxes[:, 0]).clip(0)*(boxes[:, 3]-boxes[:, 1]).clip(0)
    while len(order):
        i = order[0]
        keep.append(int(i))
        rest = order[1:]
        ix1, iy1 = np.maximum(boxes[i, 0], boxes[rest, 0]), np.maximum(boxes[i, 1], boxes[rest, 1])
        ix2, iy2 = np.minimum(boxes[i, 2], boxes[rest, 2]), np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = (ix2-ix1).clip(0)*(iy2-iy1).clip(0)
        iou = inter/(area[i]+area[rest]-inter+1e-9)
        order = rest[iou <= IOU]
    return keep


class Embedder:
    def __init__(self, root, threads=1, class_names=None):
        from tokenizers import Tokenizer
        self.session = session(os.path.join(root, 'onnx/model.onnx'), threads)
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.tokenizer = Tokenizer.from_file(os.path.join(root, 'tokenizer.json'))
        self.tokenizer.no_padding()
        self.tokenizer.enable_truncation(MAX_TOKENS)
        self.names = list(class_names or [])
        self.bank = None
        if self.names:
            import numpy as np
            self.bank = np.stack([self.embed(PROMPT.format(n)) for n in self.names])

    def embed(self, text):
        import numpy as np
        encoding = self.tokenizer.encode(text)
        feed = dict(input_ids=np.array([encoding.ids], dtype=np.int64),
                    attention_mask=np.array([encoding.attention_mask], dtype=np.int64),
                    token_type_ids=np.array([encoding.type_ids], dtype=np.int64))
        hidden = self.session.run(None, {k: v for k, v in feed.items() if k in self.inputs})[0][0]
        mask = feed['attention_mask'][0][:, None].astype(np.float32)
        pooled = (hidden*mask).sum(0)/max(mask.sum(), 1e-9)
        return (pooled/max(float(np.linalg.norm(pooled)), 1e-12)).astype(np.float32)

    def __call__(self, text):
        vector = self.embed(text)
        sims = self.bank @ vector
        return dict(embedding=[float(v) for v in vector], similarities=[float(v) for v in sims],
                    text_top_class=self.names[int(sims.argmax())], tokens=len(self.tokenizer.encode(text).ids))


def fuse(detections, text, names):
    """Deterministic decision from V detections and T similarities; no learned parameters."""
    vis = {}
    for d in detections:
        vis[d['name']] = max(vis.get(d['name'], 0.0), d['score'])
    fused = sorted(((vis.get(n, 0.0)*max(0.0, s), n) for n, s in zip(names, text['similarities'])), reverse=True)
    top_score, top_class = fused[0]
    return dict(top_class=top_class if top_score > 0 else None, top_fused=top_score, alert=top_score >= ALERT_THRESHOLD,
                top3=[dict(name=n, fused=v) for v, n in fused[:3]], detections=len(detections),
                text_top_class=text['text_top_class'], rule='fused_c = max_det_score_c * max(0, cosine_c); alert >= %g' % ALERT_THRESHOLD)


def decode_png(data):
    import numpy as np
    from PIL import Image
    return np.asarray(Image.open(io.BytesIO(data)).convert('RGB'))


def timed(function, *args):
    """(result, wall_ms, thread_cpu_ms) around one call only."""
    wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
    result = function(*args)
    return result, (time.perf_counter_ns()-wall)/1e6, (time.thread_time_ns()-cpu)/1e6


EMBEDDING_DIM, CLASSES = 384, 80
NORM_TOLERANCE = 1e-3  # output contract: T embedding is L2-normalized


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _vector(value, length):
    return isinstance(value, list) and len(value) == length and all(_finite(v) for v in value)


def _int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def validate(task, out, names=None):
    """Shape/finiteness/contract violations of one stage output, each prefixed 'invalid:' (empty = well-formed).

    names: the model's class-name list; when given, every class name/id must be supported and consistent.
    """
    bad = []
    known = set(names) if names is not None else None
    if task == 'V':
        if not isinstance(out, list):
            return ['invalid: V output is not a detection list']
        if len(out) > MAX_DET:
            bad.append(f'invalid: {len(out)} detections > max {MAX_DET}')
        for i, d in enumerate(out):
            if not isinstance(d, dict):
                bad.append(f'invalid: detection {i} is not an object')
                continue
            cid = d.get('class_id')
            if not (_int(cid) and 0 <= cid < CLASSES) or not isinstance(d.get('name'), str):
                bad.append(f'invalid: detection {i} class_id/name')
            elif names is not None and names[cid] != d['name']:
                bad.append(f'invalid: detection {i} name {d["name"]!r} is not class {cid} ({names[cid]!r})')
            if not (_finite(d.get('score')) and CONF < d['score'] <= 1.0):
                bad.append(f'invalid: detection {i} score {d.get("score")!r} not finite in ({CONF}, 1]')
            box = d.get('box')
            if not _vector(box, 4):
                bad.append(f'invalid: detection {i} box must be 4 finite coordinates, got {box!r}')
            elif box[0] > box[2] or box[1] > box[3]:
                bad.append(f'invalid: detection {i} box corners inverted')
    elif task == 'T':
        if not isinstance(out, dict):
            return ['invalid: T output is not an object']
        if not _vector(out.get('embedding'), EMBEDDING_DIM):
            bad.append(f'invalid: embedding must be {EMBEDDING_DIM} finite values')
        elif abs(math.sqrt(sum(v*v for v in out['embedding'])) - 1.0) > NORM_TOLERANCE:
            bad.append('invalid: embedding is not L2-normalized (norm %.6g)' % math.sqrt(sum(v*v for v in out['embedding'])))
        sims = out.get('similarities')
        if not _vector(sims, CLASSES):
            bad.append(f'invalid: similarities must be {CLASSES} finite values')
        elif any(abs(s) > 1.0 + NORM_TOLERANCE for s in sims):
            bad.append('invalid: similarity outside [-1, 1]')
        if not isinstance(out.get('text_top_class'), str):
            bad.append('invalid: text_top_class missing')
        elif names is not None and _vector(sims, CLASSES) and out['text_top_class'] != names[max(range(CLASSES), key=sims.__getitem__)]:
            bad.append('invalid: text_top_class is not the highest-similarity class')
    elif task == 'F':
        if not isinstance(out, dict):
            return ['invalid: F output is not an object']
        top3, fused = out.get('top3'), out.get('top_fused')
        if not (_finite(fused) and 0.0 <= fused <= 1.0 + NORM_TOLERANCE):
            bad.append(f'invalid: top_fused {fused!r} not finite in [0, 1]')
        if not (isinstance(top3, list) and len(top3) == 3 and all(isinstance(t, dict) and isinstance(t.get('name'), str)
                                                                  and _finite(t.get('fused')) for t in top3)):
            bad.append('invalid: top3 must be 3 named finite scores')
        elif _finite(fused) and (top3[0]['fused'] != fused or any(a['fused'] < b['fused'] for a, b in zip(top3, top3[1:]))):
            bad.append('invalid: top3 not sorted or inconsistent with top_fused')
        if not isinstance(out.get('alert'), bool) or (_finite(fused) and out['alert'] != (fused >= ALERT_THRESHOLD)):
            bad.append('invalid: alert inconsistent with top_fused and threshold')
        if _finite(fused) and (out.get('top_class') is None) != (fused <= 0):
            bad.append('invalid: top_class inconsistent with top_fused')
        elif (_finite(fused) and fused > 0 and isinstance(top3, list) and top3 and isinstance(top3[0], dict)
              and out.get('top_class') != top3[0].get('name')):
            bad.append('invalid: top_class is not the first ranked top3 name')
        if not (_int(out.get('detections')) and 0 <= out['detections'] <= MAX_DET):
            bad.append(f'invalid: detections {out.get("detections")!r} must be a non-bool integer in [0, {MAX_DET}]')
        if not isinstance(out.get('text_top_class'), str):
            bad.append('invalid: text_top_class missing')
        if known is not None:
            used = [out.get('top_class'), out.get('text_top_class')] + [t.get('name') for t in top3 or [] if isinstance(t, dict)]
            unknown = sorted({str(n) for n in used if n is not None and n not in known})
            if unknown:
                bad.append(f'invalid: unsupported class names {unknown}')
    else:
        bad.append(f'invalid: unsupported task {task!r}')
    return bad


def compare(task, got, ref, names=None):
    """Quality check of one stage output against the CPU reference; list of violations (empty = ok).

    Fails closed: malformed, nonfinite or wrong-shape outputs (got or reference) return only
    'invalid:' diagnostics and are never compared element-wise by truncating zip. Labels/order
    and scores are reported as separate violations.
    """
    bad = validate(task, got, names) + [b.replace('invalid:', 'invalid reference:', 1) for b in validate(task, ref, names)]
    if bad:
        return bad
    errors = []
    if task == 'V':
        if len(got) != len(ref) or [(d['class_id'], d['name']) for d in got] != [(d['class_id'], d['name']) for d in ref]:
            return ['class sequence differs: %s vs %s' % ([d['name'] for d in got], [d['name'] for d in ref])]
        for a, b in zip(got, ref):
            if abs(a['score']-b['score']) > TOLERANCE['score_abs']:
                errors.append('score diff %.3g' % abs(a['score']-b['score']))
            if max(abs(x-y) for x, y in zip(a['box'], b['box'])) > TOLERANCE['box_abs_px']:
                errors.append('box diff')
    elif task == 'T':
        a, b = got['embedding'], ref['embedding']
        cosine = sum(x*y for x, y in zip(a, b))/(math.sqrt(sum(x*x for x in a))*math.sqrt(sum(y*y for y in b)))
        if cosine < TOLERANCE['embedding_cosine_min']:
            errors.append('embedding cosine %.6f' % cosine)
        if max(abs(x-y) for x, y in zip(got['similarities'], ref['similarities'])) > TOLERANCE['similarity_abs']:
            errors.append('similarity diff')
        if got['text_top_class'] != ref['text_top_class']:
            errors.append('text top class differs')
    else:
        if (got['top_class'], got['alert']) != (ref['top_class'], ref['alert']):
            errors.append('fusion decision differs')
        if [t['name'] for t in got['top3']] != [t['name'] for t in ref['top3']]:
            errors.append('fusion top3 ranking differs: %s vs %s' % ([t['name'] for t in got['top3']], [t['name'] for t in ref['top3']]))
        if got['detections'] != ref['detections']:
            errors.append(f"fusion detection count differs: {got['detections']} vs {ref['detections']}")
        if got['text_top_class'] != ref['text_top_class']:
            errors.append('fusion text class differs')
        if max(abs(x['fused']-y['fused']) for x, y in zip([dict(fused=got['top_fused'])]+got['top3'],
                                                           [dict(fused=ref['top_fused'])]+ref['top3'])) > TOLERANCE['fused_abs']:
            errors.append('fused score diff')
    return errors


def dumps(value):
    return json.dumps(value, separators=(',', ':'), sort_keys=True).encode()
