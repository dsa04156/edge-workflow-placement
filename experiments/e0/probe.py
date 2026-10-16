"""E0 CPU compatibility probe; three sequential tasks inside one Pod.

This measures execution feasibility, not distributed placement performance.
"""

import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import time
import urllib.request


MODEL_URL = (
    "https://huggingface.co/onnxmodelzoo/squeezenet1.1-7/resolve/"
    "61e525224ad479521059f4586bcacf50ad3627ca/squeezenet1.1-7.onnx"
)
MODEL_SHA256 = "1eeff551a67ae8d565ca33b572fc4b66e3ef357b0eb2863bb9ff47a918cc4088"
IMAGE_URL = (
    "https://raw.githubusercontent.com/pytorch/hub/"
    "c3beaae7d32fca2a23fec30aa7938ef5c9b6e5d5/images/dog.jpg"
)
IMAGE_SHA256 = "f3f87bb8ab3c26c7ecfd3ac60421d7f32b0503d1d6c5baf8bac42ed93d86351a"


def emit(kind, **data):
    print(json.dumps({"kind": kind, **data}, allow_nan=False), flush=True)


def fetch(url, expected):
    started = time.perf_counter()
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("Asset exceeds E0 download limit")
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected:
        raise ValueError(f"Asset SHA256 mismatch: {digest}")
    emit("asset", url=url, bytes=len(data), sha256=digest,
         download_ms=(time.perf_counter() - started) * 1000)
    return data


def main():
    import numpy as np
    import onnxruntime as ort
    from PIL import Image

    started = time.perf_counter()
    emit("environment", node=os.environ.get("NODE_NAME"),
         architecture=platform.machine(), python=platform.python_version(),
         packages={name: importlib.metadata.version(name) for name in
                   ("numpy", "onnxruntime", "Pillow")},
         available_providers=ort.get_available_providers())
    work = Path(os.environ.get("E0_WORKDIR", "/work"))
    work.mkdir(parents=True, exist_ok=True)
    model_bytes = fetch(MODEL_URL, MODEL_SHA256)
    image_bytes = fetch(IMAGE_URL, IMAGE_SHA256)
    model_path = work / "squeezenet1.1-7.onnx"
    model_path.write_bytes(model_bytes)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    loaded = time.perf_counter()
    session = ort.InferenceSession(str(model_path), sess_options=options,
                                   providers=["CPUExecutionProvider"])
    emit("model", providers=session.get_providers(),
         load_ms=(time.perf_counter() - loaded) * 1000,
         inputs=[{"name": x.name, "shape": x.shape, "type": x.type}
                 for x in session.get_inputs()],
         outputs=[{"name": x.name, "shape": x.shape, "type": x.type}
                  for x in session.get_outputs()])

    pipeline_started = time.perf_counter()
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = image.size
    scale = 256 / min(width, height)
    image = image.resize((round(width * scale), round(height * scale)),
                         Image.Resampling.BILINEAR)
    left, top = (image.width - 224) // 2, (image.height - 224) // 2
    image = image.crop((left, top, left + 224, top + 224))
    tensor = np.asarray(image, dtype=np.float32).transpose(2, 0, 1) / 255
    tensor = (tensor - np.array([.485, .456, .406], dtype=np.float32)[:, None, None])
    tensor /= np.array([.229, .224, .225], dtype=np.float32)[:, None, None]
    tensor = np.ascontiguousarray(tensor[None], dtype=np.float32)
    if tensor.shape != (1, 3, 224, 224) or not np.isfinite(tensor).all():
        raise ValueError("Invalid preprocessed tensor")
    emit("task", task="T1_preprocess", bytes=tensor.nbytes,
         tensor_sha256=hashlib.sha256(tensor.tobytes()).hexdigest(),
         elapsed_ms=(time.perf_counter() - pipeline_started) * 1000)

    inference_started = time.perf_counter()
    outputs = session.run(None, {session.get_inputs()[0].name: tensor})
    scores = np.asarray(outputs[0]).reshape(-1)
    if scores.size != 1000 or not np.isfinite(scores).all():
        raise ValueError("Invalid classifier output")
    emit("task", task="T2_inference", bytes=scores.nbytes,
         elapsed_ms=(time.perf_counter() - inference_started) * 1000)

    postprocess_started = time.perf_counter()
    top5 = np.argsort(scores)[-5:][::-1]
    result = [{"class_index": int(i), "raw_score": float(scores[i])} for i in top5]
    emit("task", task="T3_postprocess", top5=result,
         elapsed_ms=(time.perf_counter() - postprocess_started) * 1000)
    if int(top5[0]) != 258:
        raise ValueError(f"Dog fixture smoke check failed: expected Samoyed (258), got {int(top5[0])}")
    emit("result", status="fixture_smoke_passed", node=os.environ.get("NODE_NAME"),
         architecture=platform.machine(), top1=int(top5[0]), top5=result,
         pipeline_ms=(time.perf_counter() - pipeline_started) * 1000,
         total_after_import_ms=(time.perf_counter() - started) * 1000,
         scope="single_pod_cpu_feasibility_not_placement_benchmark")


if __name__ == "__main__":
    main()
