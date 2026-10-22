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
import os
import time

CONF, IOU, MAX_DET, PAD = 0.25, 0.7, 300, 114
MAX_TOKENS = 256
PROMPT = 'a photo of a {}'
ALERT_THRESHOLD = 0.2
# Pre-agreed quality tolerances against the local CPU reference (same ONNX bytes).
TOLERANCE = dict(box_abs_px=1.0, score_abs=1e-3, embedding_cosine_min=0.9999, similarity_abs=1e-4, fused_abs=1e-3)


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
        hidden = self.session.run(None, feed)[0][0]
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


def compare(task, got, ref):
    """Quality check of one stage output against the CPU reference; list of violations (empty = ok)."""
    errors = []
    if task == 'V':
        if [d['class_id'] for d in got] != [d['class_id'] for d in ref]:
            return ['class sequence differs: %s vs %s' % ([d['name'] for d in got], [d['name'] for d in ref])]
        for a, b in zip(got, ref):
            if abs(a['score']-b['score']) > TOLERANCE['score_abs']:
                errors.append('score diff %.3g' % abs(a['score']-b['score']))
            if max(abs(x-y) for x, y in zip(a['box'], b['box'])) > TOLERANCE['box_abs_px']:
                errors.append('box diff')
    elif task == 'T':
        dot = sum(x*y for x, y in zip(got['embedding'], ref['embedding']))
        if dot < TOLERANCE['embedding_cosine_min']:
            errors.append('embedding cosine %.6f' % dot)
        if max(abs(x-y) for x, y in zip(got['similarities'], ref['similarities'])) > TOLERANCE['similarity_abs']:
            errors.append('similarity diff')
        if got['text_top_class'] != ref['text_top_class']:
            errors.append('text top class differs')
    elif task == 'F':
        if (got['top_class'], got['alert']) != (ref['top_class'], ref['alert']):
            errors.append('fusion decision differs')
        if abs(got['top_fused']-ref['top_fused']) > TOLERANCE['fused_abs']:
            errors.append('fused score diff')
    return errors


def dumps(value):
    return json.dumps(value, separators=(',', ':'), sort_keys=True).encode()
