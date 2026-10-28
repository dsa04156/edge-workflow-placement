"""Frozen constants: the same base VLM (Qwen3-VL-2B-Instruct) on a CUDA GPU and on the Mobilint ARIES NPU.

Mobilint arm: vendor-compiled W4V8 MXQs (vision + text) of mobilint/Qwen3-VL-2B-Instruct (card: base_model
Qwen/Qwen3-VL-2B-Instruct, base_model_relation quantized) on the existing qbruntime 1.4.0 image of ser0004.
GPU arm: the original BF16 Qwen/Qwen3-VL-2B-Instruct on the dev0009 Orin with the reviewed cu130 recipe.
The two arms are the same model family, NOT numerically identical models (quantization is part of the arm).
Host CPU work (decode, processor, tokenizer, embedding lookup, pos-embed interpolation, rope tables, sampling,
host<->device copies) stays on the host in both arms and is recorded, not hidden.
"""
import hashlib
from pathlib import Path

EXPERIMENT = 'gpu-mobilint-vlm'
NS = 'topology-profile-lab'
SEED = 20261028
PORT = 8080
SERVER = 'etri-ser0004-cgnms0'
JETSON = 'etri-dev0009-jetorn'
MOBILINT_IMAGE = '10.254.192.47:5000/ra-npu-mobilint@sha256:3e1760cdef820206d262488dc60a5047acd551b95e81f914e1f516fda1a496da'
PY_IMAGE = '10.254.192.47:5000/node-inventory-python@sha256:370a8339507905053693d178e1cda8fbf77937f045ead58a3c32d8cead2ca67f'
STAGE = 'vlm'

MODELS = {
    'mobilint-qwen3vl-2b-f4be563': dict(
        repo='mobilint/Qwen3-VL-2B-Instruct', revision='f4be56382079d7553d1d175d9063e689cf0d8a0c',
        weights={'Qwen3-VL-2B-Instruct_vision-W4V8.mxq': '3a2edc0439928689b047c14e20c48e6a58c605e403e1dc436b94ded001b40a23',
                 'Qwen3-VL-2B-Instruct_text-W4V8.mxq': '56bea0e80aeb4c79441a203357987a063d37d625f1bde36a6a885fa2c01b36f8',
                 # host tables only: language_model.embed_tokens [151936,2048] + visual.pos_embed [2304,1024], F32
                 'model.safetensors': '5ba86da957b1803375b061061827db1af7d186e0418f4e6cd55ae607b0de9af7'}),
    'qwen3vl-2b-89644892': dict(
        repo='Qwen/Qwen3-VL-2B-Instruct', revision='89644892e4d85e24eaac8bacfd4f463576704203',
        weights={'model.safetensors': '7de1838c87a5349b016c26a1c3f7d2bc400a3d485f95ef39a7059ffd734977a0'}),
}
MODEL_FILES = ('config.json', 'chat_template.json', 'generation_config.json', 'merges.txt', 'preprocessor_config.json',
               'tokenizer.json', 'tokenizer_config.json', 'video_preprocessor_config.json', 'vocab.json')

# Wheel sets per bootstrap recipe (installed offline by the unchanged quality-network-heterogeneous bootstrap.py).
# Mobilint: frozen x86_64 cp311 reference wheelhouse + opencv-headless (vendor processor imports cv2) + the two vendor
# wheels; qbruntime 1.4.0 comes from the image and is NOT installed. --no-deps: the vendor packages' unused optional
# dependencies (mblt-tracker, matplotlib, torchcodec, transformers[serving]) are not shipped (offline import gate).
RECIPES = {
    'mobilint-x86_64-cp311': dict(no_deps=True, requirements=[
        'torch==2.8.0+cpu', 'torchvision==0.23.0', 'transformers==4.57.1', 'tokenizers==0.22.2', 'safetensors==0.8.0',
        'huggingface_hub==0.36.2', 'hf_xet==1.7.0', 'filelock==4.2.4', 'fsspec==2026.9.0', 'packaging==26.3', 'pyyaml==6.0.3',
        'regex==2026.9.29', 'requests==2.34.2', 'certifi==2026.10.18', 'charset_normalizer==3.5.2', 'idna==3.20',
        'urllib3==2.8.0', 'tqdm==4.70.1', 'typing_extensions==4.16.0', 'sympy==1.14.0', 'mpmath==1.3.0', 'networkx==3.6.1',
        'jinja2==3.1.6', 'markupsafe==3.0.4', 'pillow==11.3.0', 'opencv-python-headless==4.11.0.86',
        'transformers-mblt==0.6.1', 'mblt-npu-python==0.1.0']),
    # Same requirement set as the reviewed qnh dev0009 cu130 wave (torch/torchvision official pair, transformers 4.57.1).
    'cu130-aarch64-cp311': dict(no_deps=False, requirements=[
        'torch==2.9.1+cu130', 'torchvision==0.24.1', 'transformers==4.57.1', 'numpy==1.26.4', 'pillow==11.3.0', 'accelerate==1.10.1']),
}

ARMS = {
    'mobilint': dict(node=SERVER, edge=False, image=MOBILINT_IMAGE, recipe='mobilint-x86_64-cp311', model='mobilint-qwen3vl-2b-f4be563',
                     device_resource={'mobilint.com/npu': 1}, cpu='4', memory='6Gi', memory_limit='8Gi', storage='12Gi',
                     image_user=True),  # image default user, as the 10/26 qbruntime Pods (device node access)
    'cuda': dict(node=JETSON, edge=True, image=PY_IMAGE, recipe='cu130-aarch64-cp311', model='qwen3vl-2b-89644892',
                 device_resource={'nvidia.com/gpu.shared': 1}, cpu='4', memory='5Gi', memory_limit='6656Mi', storage='20Gi',
                 image_user=False),  # runAsUser 0, as the reviewed qnh Jetson GPU Pods
}

# The same four pinned inputs, questions and prompt suffix as the 10/28 Intel NPU smoke (vp-20261028t-npu-smoke2 request):
# uint8 RGB HWC .npy arrays of the existing VQA corpus. Input 0 is the smoke; inputs 1..3 the qualification set.
CORPUS = 'artifacts/topology-profile-live/load-cache/corpus'
PROMPT_SUFFIX = '\nAnswer the question using a single word or phrase.'
INPUTS = (
    dict(id='smoke-1713820', question_id=1713820, role='smoke', shape=[426, 640, 3], question='What is this gentlemen jumping with?',
         image_sha256='55d53f1bd63ba04e083ac2794fe3205e88de8a10cd6657ea51cb3d1aae030196'),
    dict(id='cal-3733440', question_id=3733440, role='qualification', shape=[426, 640, 3], question='Is she laying on the grass?',
         image_sha256='46519e9b7f58875f52724607e99ff5199066fd1b50f10e9dab189b602950d550'),
    dict(id='cal-4556100', question_id=4556100, role='qualification', shape=[408, 640, 3],
         question='What kind of traffic marker is in the foreground near the red bike?',
         image_sha256='005d385d5195a4adcfb23986396a010fa5955dc2dac1e41fa3e932c575aba27b'),
    dict(id='cal-3355890', question_id=3355890, role='qualification', shape=[640, 640, 3], question='What color is the ribbon on the envelope?',
         image_sha256='d5fbd6dc6b5aca024643e02c1a001c3e6f125553245cd68a179f52d021182e45'),
)

# Frozen request/processor/generation contract. Every value is passed explicitly; nothing falls back to a repo default.
CONTRACT = dict(
    # Qwen3-VL chat message: image then question text (prompt = question + PROMPT_SUFFIX, from the frozen inputs).
    messages_template=[{'role': 'user', 'content': [{'type': 'image'}, {'type': 'text', 'text': None}]}],
    # Pixels (Qwen2VLImageProcessorFast size), shared by both arms; bounds Orin memory and stays far below the dynamic
    # vision MXQ maximum of 4096 pre-merge patches (a 426x640 input -> 24x32 = 768 patches, 192 merged tokens).
    image_size=dict(shortest_edge=65536, longest_edge=200704),
    generation=dict(do_sample=False, num_beams=1, max_new_tokens=64, eos_token_id=[151645, 151643], pad_token_id=151643,
                    temperature=None, top_p=None, top_k=None, repetition_penalty=1.0),
    host_threads=4,
    cuda_dtype='bfloat16',
)

DEADLINES = dict(load_s={'mobilint': 900, 'cuda': 1200}, call_s=300, install_s=1800, fetch_s=1800, cache_ready_s=180, upload_s=1800,
                 worker_ready_s=4200, pod_lifetime_s=7200, cache_lifetime_s=9300)
assert DEADLINES['worker_ready_s'] > DEADLINES['fetch_s'] + DEADLINES['install_s'] + max(DEADLINES['load_s'].values()) - 1200
assert DEADLINES['cache_lifetime_s'] > DEADLINES['upload_s'] + DEADLINES['cache_ready_s'] + DEADLINES['pod_lifetime_s']


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def sha256(data):
    return hashlib.sha256(data).hexdigest()
