"""Export pinned ultralytics YOLO weights (e.g. yolo11n.pt, yolo11m.pt) to ONNX and cross-check the ONNX pipeline against the original weights.

Run in the isolated export env (ultralytics + torch CPU). Writes into a NEW
output directory: <weights stem>.onnx, export-provenance.json (weight hash, export
args, tool versions, exported hash) and original-vs-onnx detection comparison
on the given RGB frames. Never overwrites.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
EXPORT_ARGS = dict(format='onnx', imgsz=640, opset=17, dynamic=False, simplify=False, nms=False, half=False, device='cpu')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--weights', type=Path, required=True)
    parser.add_argument('--weights-sha256', required=True)
    parser.add_argument('--weights-url', default='https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt')
    parser.add_argument('--images', type=Path, nargs='+', required=True, help='fixture images; frames are rendered with scenario.frame')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if sha(args.weights) != args.weights_sha256:
        raise SystemExit('weights hash mismatch')
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault('YOLO_CONFIG_DIR', str(args.output/'ultralytics-config'))
    os.environ.update(YOLO_OFFLINE='1', YOLO_AUTOINSTALL='False')
    import numpy
    import onnx
    import onnxruntime
    import torch
    import ultralytics
    from ultralytics import YOLO
    sys.path.insert(0, str(HERE))
    import models
    import scenario
    work = args.output/args.weights.name
    shutil.copyfile(args.weights, work)
    exported = Path(YOLO(str(work)).export(**EXPORT_ARGS))
    detector = models.Detector(exported)
    original, packaged = YOLO(str(work)), YOLO(str(exported), task='detect')

    def listed(result, r=1.0, left=0, top=0, w=scenario.WIDTH, h=scenario.HEIGHT):
        # Same clip to the frame as models.Detector; the tensor-input path is otherwise unclipped at the frame edge.
        return [dict(class_id=int(c), name=result.names[int(c)], score=float(s),
                     box=[float(min(max(v, 0), lim)) for v, lim in zip(((b[0]-left)/r, (b[1]-top)/r, (b[2]-left)/r, (b[3]-top)/r), (w, h, w, h))])
                for c, s, b in zip(result.boxes.cls, result.boxes.conf, result.boxes.xyxy.tolist())]
    rows = []
    for image in args.images:
        for seq in range(3):
            frame = scenario.frame(image, 20261022, seq)
            got = detector(frame)
            tensor, r, left, top = detector.preprocess(frame)
            # Same 640x640 letterboxed tensor into the original .pt: weight/graph equivalence only.
            same_input = listed(original.predict(torch.from_numpy(tensor), conf=models.CONF, iou=models.IOU, device='cpu',
                                                 verbose=False)[0], r, left, top)
            # ultralytics' own ONNX predict on the BGR frame: pre/post-processing equivalence.
            ultra_onnx = listed(packaged.predict(frame[:, :, ::-1].copy(), imgsz=640, conf=models.CONF, iou=models.IOU,
                                                 device='cpu', verbose=False)[0])
            # Default .pt predict uses rectangular (stride-padded 640x480) inference: a different input, diagnostic only.
            rect = listed(original.predict(frame[:, :, ::-1].copy(), imgsz=640, conf=models.CONF, iou=models.IOU,
                                           device='cpu', verbose=False)[0])
            rows.append(dict(image=image.name, seq=seq, frame_sha256=hashlib.sha256(frame.tobytes()).hexdigest(), onnx=got,
                             original_same_tensor=same_input, ultralytics_onnx=ultra_onnx, original_default_rect=rect,
                             violations=models.compare('V', got, same_input),
                             pipeline_violations=models.compare('V', got, ultra_onnx),
                             rect_mode_differences=models.compare('V', got, rect)))
    provenance = dict(weights_url=args.weights_url,
        weights_sha256=args.weights_sha256, export_args=EXPORT_ARGS, onnx_sha256=sha(exported), onnx_bytes=exported.stat().st_size,
        versions=dict(python=sys.version.split()[0], torch=torch.__version__, ultralytics=ultralytics.__version__, onnx=onnx.__version__,
                      onnxruntime=onnxruntime.__version__, numpy=numpy.__version__),
        note='ONNX metadata includes an export date, so re-export bytes differ; the pinned onnx_sha256 is the artifact used',
        comparison=dict(scope='models.Detector (ORT CPU) vs original .pt on the same letterboxed tensor (gate), vs ultralytics '
                        'ONNX predict (pipeline gate), vs default rectangular .pt predict (diagnostic; different input shape)',
                        tolerance=models.TOLERANCE, frames=len(rows),
                        frames_with_violations=sum(bool(r['violations']) for r in rows),
                        frames_with_pipeline_violations=sum(bool(r['pipeline_violations']) for r in rows),
                        frames_differing_from_rect_mode=sum(bool(r['rect_mode_differences']) for r in rows)))
    with (args.output/'export-provenance.json').open('x') as f:
        json.dump(provenance, f, indent=1)
    with (args.output/'original-vs-onnx.json').open('x') as f:
        json.dump(rows, f, indent=1)
    print(json.dumps(provenance['comparison']))
    gate = provenance['comparison']
    return 0 if not gate['frames_with_violations'] and not gate['frames_with_pipeline_violations'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
