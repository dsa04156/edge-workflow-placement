"""In-Pod NumPy MLP inference (shipped verbatim; needs only numpy + stdlib).

model.bin layout: little-endian float32 [mean, std, W1, b1, W2, b2, W3, b3] for LAYERS.
`run` prints one JSON document: identity, one untimed warmup, then one timed forward per evaluation window.
Timing: perf_counter_ns / thread_time_ns around forward() only; file loading and JSON are outside the span.
"""
import hashlib
import json
import os
import platform
import sys
import time

import numpy as np

LAYERS = (64, 512, 512, 16)


def shapes():
    out = []
    for fan_in, fan_out in zip(LAYERS, LAYERS[1:]):
        out += [(fan_in, fan_out), (fan_out,)]
    return out


def unpack(raw):
    flat = np.frombuffer(raw, dtype='<f4')
    expected = 2 + sum(int(np.prod(s)) for s in shapes())
    if flat.size != expected:
        raise ValueError(f'model has {flat.size} floats, expected {expected}')
    mean, std, offset, params = flat[0], flat[1], 2, []
    for shape in shapes():
        size = int(np.prod(shape))
        params.append(flat[offset:offset + size].reshape(shape))
        offset += size
    return float(mean), float(std), params


def pack(mean, std, params):
    parts = [np.array([mean, std], dtype='<f4')] + [np.ascontiguousarray(p, dtype='<f4').ravel() for p in params]
    return b''.join(p.tobytes() for p in parts)


def forward(params, x):
    """x: (n, 64) float32 normalized -> (n, 16) float32 normalized."""
    h = x
    for i in range(0, len(params) - 2, 2):
        h = np.maximum(h @ params[i] + params[i + 1], np.float32(0))
    return h @ params[-2] + params[-1]


def predict(mean, std, params, window):
    x = ((np.asarray(window, dtype=np.float32) - np.float32(mean)) / np.float32(std)).reshape(1, -1)
    return forward(params, x)


def denormalize(mean, std, out):
    return (out * np.float32(std) + np.float32(mean)).astype(np.float32)


def run(model_path, inputs_path):
    raw = open(model_path, 'rb').read()
    mean, std, params = unpack(raw)
    doc = json.load(open(inputs_path))
    report = {'node': os.environ.get('NODE_NAME'), 'pod_uid': os.environ.get('POD_UID'),
              'python': platform.python_version(), 'numpy': np.__version__, 'machine': platform.machine(),
              'threads_env': {k: os.environ.get(k) for k in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS')},
              'model_sha256': hashlib.sha256(raw).hexdigest(), 'model_bytes': len(raw), 'requests': []}
    for item in [doc['warmup'], *doc['evaluation']]:
        x = ((np.asarray(item['window'], dtype=np.float32) - np.float32(mean)) / np.float32(std)).reshape(1, -1)
        wall0, cpu0 = time.perf_counter_ns(), time.thread_time_ns()
        out = forward(params, x)
        wall1, cpu1 = time.perf_counter_ns(), time.thread_time_ns()
        pred = denormalize(mean, std, out)[0]
        report['requests'].append({'window_id': item['window_id'], 'kind': item['kind'],
                                   'forward_wall_ns': wall1 - wall0, 'forward_cpu_ns': cpu1 - cpu0,
                                   'prediction': [float(v) for v in pred],
                                   'prediction_f32_sha256': hashlib.sha256(pred.astype('<f4').tobytes()).hexdigest()})
    return report


if __name__ == '__main__':
    print(json.dumps(run(sys.argv[1], sys.argv[2])))
