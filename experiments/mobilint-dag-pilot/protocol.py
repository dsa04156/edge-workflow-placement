"""Frozen protocol of the Mobilint real-inference DAG pilot (stdlib only; shipped into every Pod).

DAG (one request): edge origin -JPEG-> preprocess (edge, Pillow) -uint8 HWC tensor-> infer (ser0004) -logits-> postprocess
(edge, softmax/top-5) -> origin. Two inference arms share the same edge Pod, inputs, preprocessed tensor and executor:
  mobilint  official Mobilint MXQ (quantized derivative of torchvision ResNet50 IMAGENET1K_V1) on ARIES via qbruntime
  cpu-fp32  the original ONNX of the same repository, ONNX Runtime CPU EP, FP32, 1 thread
The arms differ in precision (quantized MXQ vs FP32); they are NOT the same model numerically. Accuracy against
ImageNetV2 labels is the ground-truth diagnostic; agreement between the arms is CPU-reference parity only.
Closed loop, concurrency 1: no capacity/goodput claim. Selection and order are frozen here before any model output.
"""
import hashlib
import random

SEED = 20261026
EXPERIMENT = 'mobilint-dag-pilot'
NS = 'topology-profile-lab'

HF_REPO = 'mobilint/ResNet50.tv1_in1k'
HF_REVISION = '8adb91ca7578fae9395cb4e8e2311b2afc7041c0'
ASSETS = {
    'resnet50_IMAGENET1K_V1.mxq': dict(
        sha256='48b1ca4e669ac9d293148a748c06c352a7f542c5999f5f8eb2d1f5a31cf21e2d', bytes=26018637,
        source=f'https://huggingface.co/{HF_REPO}/resolve/{HF_REVISION}/aries-rb/resnet50_IMAGENET1K_V1.mxq',
        license='BSD-3-Clause (repository card)', role='mobilint'),
    'resnet50_IMAGENET1K_V1.onnx': dict(
        sha256='3a742b2337fc640cae0ebff4c00fee264379a03275a5b192e707cb3a3e62d58a', bytes=102146392,
        source=f'https://huggingface.co/{HF_REPO}/resolve/{HF_REVISION}/resnet50_IMAGENET1K_V1.onnx',
        license='BSD-3-Clause (repository card); weights torchvision IMAGENET1K_V1 (BSD-3-Clause)', role='cpu-fp32'),
    'onnxruntime-1.23.2-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl': dict(
        sha256='da44b99206e77734c5819aa2142c69e64f3b46edc3bd314f6a45a932defc0b3e', bytes=17372080,
        source='https://pypi.org/project/onnxruntime/1.23.2/', license='MIT', role='cpu-fp32'),
}
IMAGENETV2 = dict(
    file='imagenetv2-matched-frequency.tar.gz', sha256='f0c37fdf925916b19ea1323cd9a2208cdb6959ba2c32eef2a7fc393835c9ca7c',
    bytes=1264079360, format='POSIX tar (despite .gz name)', license='MIT (dataset card); images from Flickr',
    source='https://huggingface.co/datasets/vaishaal/ImageNetV2/resolve/d626240be2538720e83103a0e1178d24aca8b12c/'
           'imagenetv2-matched-frequency.tar.gz',
    prefix='imagenetv2-matched-frequency-format-val/')
ASSETS_BY_ARM = {'mobilint': 'resnet50_IMAGENET1K_V1.mxq', 'cpu-fp32': 'resnet50_IMAGENET1K_V1.onnx'}
# Existing wheelhouse (cp311) used by earlier real-topology-live/multi-edge-sensors Pods.
WHEELS = {'aarch64': ['numpy-1.26.4-cp311-cp311-manylinux_2_17_aarch64.manylinux2014_aarch64.whl',
                      'pillow-11.3.0-cp311-cp311-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl'],
          'x86_64': ['numpy-1.26.4-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl',
                     'onnxruntime-1.23.2-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl']}

MOBILINT_IMAGE = '10.254.192.47:5000/ra-npu-mobilint@sha256:3e1760cdef820206d262488dc60a5047acd551b95e81f914e1f516fda1a496da'
PY_IMAGE = '10.254.192.47:5000/node-inventory-python@sha256:370a8339507905053693d178e1cda8fbf77937f045ead58a3c32d8cead2ca67f'
QBRUNTIME_VERSION = '1.4.0'  # the image's runtime; the MXQ card reports evaluation with 1.5.0 (compatibility probed first)
SERVER = 'etri-ser0004-cgnms0'
EDGE_CANDIDATES = ('etri-dev0003-raspi5', 'etri-dev0002-raspi5', 'etri-dev0008-raspi5')  # first Ready one that fits
PORT = 8080

# requests == limits for CPU (threads 1 for the CPU arm); memory request/limit.
RESOURCES = {
    'cache': dict(cpu='250m', memory='256Mi', memory_limit='512Mi', disk='1Gi'),
    'mobilint': dict(cpu='1', memory='1Gi', memory_limit='2Gi', disk='256Mi', npu=1),
    'cpu-fp32': dict(cpu='1', memory='1Gi', memory_limit='2Gi', disk='512Mi'),
    'edge': dict(cpu='750m', memory='384Mi', memory_limit='768Mi', disk='512Mi'),
}
DEADLINES = dict(pod_ready_s=600, install_s=600, load_s=120, request_s=30, probe_s=180, trace_s=1200,
                 active_deadline_s=3600, delete_s=120)

ARMS = ('mobilint', 'cpu-fp32')
N_EVAL = 200
N_WARMUP_PER_ARM = 5
INPUT = dict(resize_shorter=256, crop=224, interpolation='PIL bilinear (antialiased)', layout='HWC uint8 RGB')
MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)  # applied only for the FP32 ONNX arm (MXQ fuses normalization)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def imagenetv2_members(names):
    """-> sorted [(class_index, member)] of the 10,000 JPEGs (class directory = ImageNet-1k class index)."""
    out = []
    for name in names:
        if not name.startswith(IMAGENETV2['prefix']) or not name.endswith('.jpeg'):
            continue
        parts = name[len(IMAGENETV2['prefix']):].split('/')
        if len(parts) != 2 or not parts[0].isdigit() or not 0 <= int(parts[0]) < 1000:
            raise ValueError(f'unexpected ImageNetV2 member {name!r}')
        out.append((int(parts[0]), name))
    if len(out) != 10000 or len({c for c, _ in out}) != 1000:
        raise ValueError(f'expected 10000 JPEGs in 1000 classes, got {len(out)}')
    return sorted(out)


def select(members, seed=SEED, n_eval=N_EVAL, n_warmup=N_WARMUP_PER_ARM * len(ARMS)):
    """Seeded, output-independent selection: n_eval distinct evaluation samples and disjoint warmup samples."""
    picked = random.Random(seed).sample(range(len(members)), n_eval + n_warmup)
    evaluation = [dict(id=f'v2-{i:05d}', label=members[i][0], member=members[i][1]) for i in sorted(picked[:n_eval])]
    warmup = [dict(id=f'w2-{i:05d}', label=members[i][0], member=members[i][1]) for i in picked[n_eval:]]
    return evaluation, warmup


def schedule(evaluation, warmup, parity=(), seed=SEED):
    """Frozen closed-loop order. Warmup alternates arms; each evaluation sample runs on both arms back to back
    (paired) in a seeded shuffled sample order with a seeded arm order per pair, so slow drift hits both arms alike.
    COCO parity samples follow the same pairing after the labeled set."""
    rng = random.Random(seed + 1)
    rows = [dict(phase='warmup', sample=s['id'], arm=ARMS[i % len(ARMS)]) for i, s in enumerate(warmup)]
    for name, samples in (('eval', evaluation), ('parity', parity)):
        order = [s['id'] for s in samples]
        rng.shuffle(order)
        for sid in order:
            arms = list(ARMS)
            rng.shuffle(arms)
            rows += [dict(phase=name, sample=sid, arm=arm) for arm in arms]
    return [dict(row, seq=i) for i, row in enumerate(rows)]


def center_crop_box(width, height, crop=INPUT['crop']):
    """torchvision center_crop offsets: top = int(round((h - crop) / 2)), left likewise."""
    top, left = int(round((height - crop) / 2.0)), int(round((width - crop) / 2.0))
    return left, top, left + crop, top + crop


def resize_shape(width, height, shorter=INPUT['resize_shorter']):
    """torchvision resize(int): shorter side -> `shorter`, longer side int(shorter * long / short)."""
    if width <= height:
        return shorter, int(shorter * height / width)
    return int(shorter * width / height), shorter
