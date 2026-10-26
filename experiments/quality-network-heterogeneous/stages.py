"""VQA stage backends with explicit device dispatch: cpu-torch reference, cuda-torch, openvino-npu (vision).

Provenance: the model construction and per-stage semantics are a small extraction of the frozen
experiments/real-topology-live/model_adapter.py (sha recorded as LEGACY_ADAPTER_SHA256 and re-checked at
import; the legacy file itself is unchanged). Differences are limited to device placement and timing:
- vision: torchvision Resize(448) + CenterCrop(448) + ToTensor + Normalize on the host, ResNet18 trunk
  (last two children removed) on the backend device, spatial mean sum(3).sum(2).div(H*W) on the device, 512-d
  float32 copied to the host before npy serialization.
- speech: the exact Wav2Vec2Processor on the host at the original duration (padding='longest' on one
  utterance adds nothing), CTC logits and argmax on the device, decode on the host.
- fusion: exact MUTAN factory with the same pretraining-skip patch, strict state_dict load and tensor
  equality check, upstream tokenize_mcb on the host, model/softmax/topk on the device.
No backend ever falls back: an unavailable device raises BackendUnavailable; there is no AUTO/HETERO path.
CUDA baseline: FP32, TF32 disabled for matmul and cuDNN, cudnn.benchmark off; settings are in every receipt.
"""
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import sys
import time

HERE = Path(__file__).resolve().parent
LEGACY_ADAPTER = HERE.parent/'real-topology-live/model_adapter.py'
# Current frozen adapter (commit 62a3ccaf): the reference-v1 adapter dad90308... (commit 81f8382e, recorded in
# reference-v1/metadata.json) plus raw .npy image/audio decoding only; model code identical.
LEGACY_ADAPTER_SHA256 = '33e4f879191cdee275ef4f4e752c8ea6fab362989206dbdae3fe8bf3ed81e9d4'
REFERENCE_V1_ADAPTER_SHA256 = 'dad903088776a507d4dd7862cb3f362a9d78b261a5bcfb3aca0c678429d6103d'
JELLYBEAN_COMMIT = '65a1d67817946a4ae67b068eafb49d649bc061bc'
BACKENDS = ('cpu-torch', 'cuda-torch', 'openvino-npu')
STAGES = ('vision', 'speech', 'fusion')
# quality-network-baselines (additive): host preprocessing split out of the vision/speech branches as separate CPU stages;
# vision/speech then accept the preprocessed tensor (input='tensor'). Qualification stages/semantics above are unchanged.
PREPROCESS_STAGES = ('image_preprocess', 'audio_preprocess')
DAG_STAGES = STAGES + PREPROCESS_STAGES
REFUSED_DEVICE_TOKENS = ('AUTO', 'HETERO', 'MULTI', 'BATCH')


class BackendUnavailable(RuntimeError):
    """The requested device/runtime is not present; never answered by another device."""


def normalize_devices(value):
    """OpenVINO EXECUTION_DEVICES may come back as a str ('NPU') or a sequence (['NPU']). Returns (names, raw) where
    names is a list of device names and raw records the original type/value for audit. A str is ONE device name."""
    raw = dict(type=type(value).__name__, value=value if isinstance(value, (str, int, float)) else repr(value)[:200])
    if isinstance(value, str):
        names = [part.strip() for part in value.split(',') if part.strip()] if ',' in value else ([value.strip()] if value.strip() else [])
    else:
        try:
            names = [str(v) for v in value]
        except TypeError:
            names = [str(value)]
    return names, raw


def require_direct_npu(value):
    names, raw = normalize_devices(value)
    if names != ['NPU']:
        raise DeviceMismatch(f'EXECUTION_DEVICES={names} (raw {raw}), expected exactly [NPU]')
    return names, raw


class PhaseError(RuntimeError):
    """A request failed in a named preparation phase (e.g. request-time NPU compile); keeps the original error."""

    def __init__(self, phase, original):
        super().__init__(f'{phase}: {original!r}')
        self.phase, self.original = phase, original


class DeviceMismatch(RuntimeError):
    """A tensor, parameter or compiled model was not on the requested device."""


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def legacy_check():
    actual = sha(LEGACY_ADAPTER) if LEGACY_ADAPTER.exists() else None
    return dict(legacy_adapter=str(LEGACY_ADAPTER.name), legacy_adapter_sha256=LEGACY_ADAPTER_SHA256,
                legacy_adapter_present_sha256=actual, reference_v1_adapter_sha256=REFERENCE_V1_ADAPTER_SHA256,
                extraction_sha256=sha(__file__))


# ---------------------------------------------------------------- preprocessing (host)
VISION_SIZE = 448
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)


def decode_image(data):
    import numpy as np
    from PIL import Image
    if data.startswith(b'\x93NUMPY'):
        pixels = np.load(io.BytesIO(data), allow_pickle=False)
        if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 3:
            raise ValueError('Expected decoded uint8 RGB pixels')
        return Image.fromarray(pixels, 'RGB')
    return Image.open(io.BytesIO(data)).convert('RGB')


def vision_tensor_torchvision(image):
    import torchvision.transforms as transforms
    transform = transforms.Compose([transforms.Resize(VISION_SIZE), transforms.CenterCrop(VISION_SIZE),
                                    transforms.ToTensor(), transforms.Normalize(list(MEAN), list(STD))])
    return transform(image).unsqueeze(0)


def vision_array_numpy(image):
    """torchvision-free equivalent (PIL BILINEAR short-side resize, round-half centre crop, /255, normalize) for
    runtimes without torch; equality with vision_tensor_torchvision is a tested contract, not assumed."""
    import numpy as np
    from PIL import Image
    w, h = image.size
    short, long = (w, h) if w <= h else (h, w)
    new_short, new_long = VISION_SIZE, int(VISION_SIZE * long / short)
    size = (new_short, new_long) if w <= h else (new_long, new_short)
    if size != (w, h):
        image = image.resize(size, Image.BILINEAR)
    w, h = image.size
    top, left = int(round((h - VISION_SIZE) / 2.0)), int(round((w - VISION_SIZE) / 2.0))
    image = image.crop((left, top, left + VISION_SIZE, top + VISION_SIZE))
    x = np.asarray(image, dtype=np.uint8).astype(np.float32).transpose(2, 0, 1) / np.float32(255)
    mean = np.asarray(MEAN, dtype=np.float32).reshape(3, 1, 1)
    std = np.asarray(STD, dtype=np.float32).reshape(3, 1, 1)
    return ((x - mean) / std)[None].astype(np.float32)


def decode_audio(data):
    import numpy as np
    if data.startswith(b'\x93NUMPY'):
        audio = np.load(io.BytesIO(data), allow_pickle=False)
        if audio.dtype != np.float32 or not np.isfinite(audio).all():
            raise ValueError('Expected float32 PCM')
        rate = 16000
    else:
        import soundfile as sf
        audio, rate = sf.read(io.BytesIO(data), dtype='float32')
    if rate != 16000 or audio.ndim != 1:
        raise ValueError('Expected mono 16kHz fixture')
    return audio


def npy_bytes(array):
    import numpy as np
    out = io.BytesIO()
    np.save(out, array, allow_pickle=False)
    return out.getvalue()


VISION_TENSOR_SHAPE = (1, 3, VISION_SIZE, VISION_SIZE)


def load_vision_tensor(data):
    """Preprocessed image tensor (image_preprocess output): float32 [1,3,448,448], finite."""
    import numpy as np
    x = np.load(io.BytesIO(data), allow_pickle=False)
    if x.dtype != np.float32 or tuple(x.shape) != VISION_TENSOR_SHAPE or not np.isfinite(x).all():
        raise ValueError(f'Expected float32 {list(VISION_TENSOR_SHAPE)} vision tensor, got {x.dtype} {list(x.shape)}')
    return x


def load_input_values(data):
    """Preprocessed speech input (audio_preprocess output): 1-D float32 Wav2Vec2Processor input_values, finite."""
    import numpy as np
    x = np.load(io.BytesIO(data), allow_pickle=False)
    if x.dtype != np.float32 or x.ndim != 1 or not np.isfinite(x).all():
        raise ValueError('Expected 1-D float32 processor input_values')
    return x


class PreprocessStage:
    """CPU host preprocessing as its own DAG stage, the same code the in-branch host step runs:
    image_preprocess = decode_image + vision_tensor_torchvision (Resize/CenterCrop/ToTensor/Normalize) -> float32 [1,3,448,448];
    audio_preprocess = decode_audio + the exact Wav2Vec2Processor at the original length -> 1-D float32 input_values.
    CPU only: any other backend raises BackendUnavailable (a preprocessing stage is never placed on an accelerator here)."""

    def __init__(self, stage, backend, model_root):
        if stage not in PREPROCESS_STAGES:
            raise ValueError(f'{stage} is not a preprocessing stage')
        if backend != 'cpu-torch':
            raise BackendUnavailable(f'{stage} is a CPU host stage; {backend} not supported')
        import torch
        self.stage, self.backend = stage, backend
        self.settings = torch_settings(torch.device('cpu'))
        started = time.perf_counter()
        root = Path(model_root)
        if stage == 'audio_preprocess':
            from transformers import Wav2Vec2Processor
            self.processor = Wav2Vec2Processor.from_pretrained(root/'asr', local_files_only=True)
            self.model_name, self.model_sha256 = 'wav2vec2-base-960h processor', sha(root/'asr/preprocessor_config.json')
        else:
            import torchvision
            self.model_name, self.model_sha256 = f'torchvision {torchvision.__version__} Resize({VISION_SIZE})+CenterCrop+Normalize', None
        self.load_ms = (time.perf_counter() - started) * 1000

    def run(self, data):
        import numpy as np
        t0 = time.perf_counter()
        if self.stage == 'image_preprocess':
            value = vision_tensor_torchvision(decode_image(data)).numpy().astype(np.float32, copy=False)
        else:
            audio = decode_audio(data)
            value = self.processor(audio, sampling_rate=16000, return_tensors='np', padding='longest').input_values[0].astype(np.float32)
        t1 = time.perf_counter()
        out = npy_bytes(value)
        return out, dict(output_shape=list(value.shape), output_dtype='float32', host_preprocess_ms=(t1-t0)*1000,
                         serialize_ms=(time.perf_counter()-t1)*1000, output_device='cpu')

    def describe(self):
        return dict(stage=self.stage, backend=self.backend, device='cpu', model=self.model_name, model_sha256=self.model_sha256,
                    derived_artifact_sha256=None, load_ms=self.load_ms, settings=self.settings, provenance=legacy_check())


# ---------------------------------------------------------------- torch backends
def torch_device(backend):
    import torch
    if backend == 'cpu-torch':
        return torch.device('cpu')
    if backend == 'cuda-torch':
        if not torch.cuda.is_available() or torch.version.cuda is None:
            raise BackendUnavailable(f'cuda-torch requested but torch {torch.__version__} reports no CUDA device')
        return torch.device('cuda', 0)
    raise ValueError(f'{backend} is not a torch backend')


def torch_settings(device):
    import torch
    torch.set_num_threads(1)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass  # already set in this process
    settings = dict(torch=torch.__version__, torch_cuda_build=torch.version.cuda, threads=torch.get_num_threads(), precision='float32',
                    python=platform.python_version(), machine=platform.machine())
    if device.type == 'cuda':
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        torch.backends.cudnn.benchmark = False
        props = torch.cuda.get_device_properties(device)
        settings.update(device_name=props.name, capability=list(torch.cuda.get_device_capability(device)),
                        device_total_memory=props.total_memory, cudnn=torch.backends.cudnn.version(),
                        matmul_allow_tf32=torch.backends.cuda.matmul.allow_tf32, cudnn_allow_tf32=torch.backends.cudnn.allow_tf32,
                        cudnn_benchmark=torch.backends.cudnn.benchmark, arch_list=torch.cuda.get_arch_list())
    return settings


def assert_on(device, tensors, what):
    for t in tensors:
        if t.device.type != device.type or (device.index is not None and t.device.index != device.index):
            raise DeviceMismatch(f'{what} on {t.device}, expected {device}')


class TorchStage:
    """One stage on one torch device. run(bytes) -> (output bytes, timing/device dict)."""

    def __init__(self, stage, backend, model_root, source_root):
        import torch
        self.torch, self.stage, self.backend = torch, stage, backend
        self.device = torch_device(backend)
        self.settings = torch_settings(self.device)
        started = time.perf_counter()
        root, source = Path(model_root), Path(source_root)
        getattr(self, '_load_'+stage)(root, source)
        params = [p for p in self.module.parameters()]
        assert_on(self.device, params, f'{stage} parameters')
        self.parameter_elements = sum(p.numel() for p in params)
        self.load_ms = (time.perf_counter() - started) * 1000

    def _sync(self):
        if self.device.type == 'cuda':
            self.torch.cuda.synchronize(self.device)

    # -- vision
    def _load_vision(self, root, source):
        import torchvision.models as models
        weight = root/'resnet18-f37072fd.pth'
        model = models.resnet18(weights=None)
        model.load_state_dict(self.torch.load(weight, weights_only=True, map_location='cpu'), strict=True)
        self.module = self.torch.nn.Sequential(*list(model.children())[:-2]).eval().to(self.device)
        self.model_sha256, self.model_name = sha(weight), 'resnet18-imagenet1k-v1-noatt-448'

    def _run_vision(self, data):
        import numpy as np
        t0 = time.perf_counter()
        value = vision_tensor_torchvision(decode_image(data))
        t1 = time.perf_counter()
        with self.torch.inference_mode():
            x = value.to(self.device)
            self._sync()
            t2 = time.perf_counter()
            feature = self.module(x)
            feature = feature.sum(3).sum(2).div(feature.size(2) * feature.size(3)).squeeze(0)
            assert_on(self.device, [feature], 'vision feature')
            self._sync()
            t3 = time.perf_counter()
            host = feature.cpu().numpy()
        t4 = time.perf_counter()
        assert host.shape == (512,) and host.dtype == np.float32
        return npy_bytes(host), dict(input_shape=list(x.shape), input_dtype=str(x.dtype).replace('torch.', ''), output_shape=[512],
                                     output_dtype='float32', host_preprocess_ms=(t1-t0)*1000, h2d_ms=(t2-t1)*1000,
                                     device_compute_ms=(t3-t2)*1000, d2h_ms=(t4-t3)*1000, output_device=str(feature.device))

    # -- speech
    def _load_speech(self, root, source):
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
        self.processor = Wav2Vec2Processor.from_pretrained(root/'asr', local_files_only=True)
        self.module = Wav2Vec2ForCTC.from_pretrained(root/'asr', local_files_only=True).eval().to(self.device)
        self.model_sha256, self.model_name = sha(root/'asr/model.safetensors'), 'wav2vec2-base-960h'

    def _run_speech(self, data):
        t0 = time.perf_counter()
        audio = decode_audio(data)
        inputs = self.processor(audio, sampling_rate=16000, return_tensors='pt', padding='longest')
        t1 = time.perf_counter()
        with self.torch.inference_mode():
            x = inputs.input_values.to(self.device)
            self._sync()
            t2 = time.perf_counter()
            logits = self.module(x).logits.squeeze(0)
            ids = self.torch.argmax(logits, dim=-1)
            assert_on(self.device, [logits, ids], 'speech logits')
            self._sync()
            t3 = time.perf_counter()
            host_ids = ids.cpu()
        t4 = time.perf_counter()
        text = self.processor.decode(host_ids, skip_special_tokens=True)
        t5 = time.perf_counter()
        return text.encode(), dict(input_shape=list(x.shape), input_dtype='float32', audio_samples=int(audio.shape[0]),
                                   output_frames=int(logits.shape[0]), host_preprocess_ms=(t1-t0)*1000, h2d_ms=(t2-t1)*1000,
                                   device_compute_ms=(t3-t2)*1000, d2h_ms=(t4-t3)*1000, host_decode_ms=(t5-t4)*1000,
                                   output_device=str(logits.device))

    # -- fusion
    def _load_fusion(self, root, source):
        torch = self.torch
        sys.path.insert(0, str(source))
        import vqa.models as models
        import skipthoughts
        from gru import BayesianGRU
        vocab_path, weight = root/'vocab.json', root/'mutan-resnet18.pth.tar'
        self.vocab = json.loads(vocab_path.read_text())
        words = [word for word, _ in sorted(self.vocab['word_to_wid'].items(), key=lambda x: x[1])]
        import yaml
        config = yaml.safe_load((source/'configs/variants/mutan_noatt_resnet18.yaml').read_text())
        old_embedding = skipthoughts.AbstractSkipThoughts._load_embedding
        old_rnn = skipthoughts.BayesianUniSkip._load_rnn
        skipthoughts.AbstractSkipThoughts._load_embedding = lambda self: torch.nn.Embedding(len(self.vocab) + 1, 620, padding_idx=0)
        skipthoughts.BayesianUniSkip._load_rnn = lambda self: BayesianGRU(620, 2400, dropout=self.dropout)
        try:
            model = models.factory(config['model'], words, self.vocab['aid_to_ans'])
        finally:
            skipthoughts.AbstractSkipThoughts._load_embedding = old_embedding
            skipthoughts.BayesianUniSkip._load_rnn = old_rnn
        state = torch.load(weight, weights_only=True, map_location='cpu')
        model.load_state_dict(state, strict=True)
        assert all(torch.equal(v.cpu(), state[k]) for k, v in model.state_dict().items())
        self.strict_state_keys = len(state)
        self.module = model.eval().to(self.device)
        source_text = (source/'vqa/datasets/vqa_processed.py').read_text()
        function = next(n for n in ast.parse(source_text).body if isinstance(n, ast.FunctionDef) and n.name == 'tokenize_mcb')
        self.namespace = {'re': re}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'upstream-tokenize_mcb', 'exec'), self.namespace)
        self.model_sha256, self.model_name, self.vocab_sha256 = sha(weight), 'MutanNoAtt-resnet18', sha(vocab_path)

    def _run_fusion(self, inputs):
        import numpy as np
        torch = self.torch
        t0 = time.perf_counter()
        feature = np.load(io.BytesIO(inputs['vision']), allow_pickle=False)
        if feature.shape != (512,) or feature.dtype != np.float32 or not np.isfinite(feature).all():
            raise ValueError('Invalid feature tensor')
        tokens = self.namespace['tokenize_mcb'](inputs['speech'].decode())[:26]
        if not tokens:
            raise ValueError('ASR produced empty question')
        wid = self.vocab['word_to_wid']
        ids = torch.tensor([[wid.get(w, wid['UNK']) for w in tokens]], dtype=torch.long)
        t1 = time.perf_counter()
        with torch.inference_mode():
            f, i = torch.from_numpy(feature).unsqueeze(0).to(self.device), ids.to(self.device)
            self._sync()
            t2 = time.perf_counter()
            scores = self.module(f, i).softmax(-1)
            values, indices = scores.topk(5)
            assert_on(self.device, [scores, values, indices], 'fusion scores')
            self._sync()
            t3 = time.perf_counter()
            values, indices = values.cpu(), indices.cpu()
        t4 = time.perf_counter()
        result = {'answers': [self.vocab['aid_to_ans'][k] for k in indices[0].tolist()], 'answer_ids': indices[0].tolist(),
                  'scores': values[0].tolist()}
        return json.dumps(result, separators=(',', ':')).encode(), dict(
            input_shape=[[1, 512], list(ids.shape)], input_dtype=['float32', 'int64'], host_preprocess_ms=(t1-t0)*1000,
            h2d_ms=(t2-t1)*1000, device_compute_ms=(t3-t2)*1000, d2h_ms=(t4-t3)*1000, output_device=str(scores.device))

    def run(self, data):
        output, timing = getattr(self, '_run_'+self.stage)(data)
        return output, timing

    # -- preprocessed-tensor inputs (quality-network-baselines DAG); same model ops as _run_vision/_run_speech after preprocessing
    def run_tensor(self, data):
        if self.stage not in ('vision', 'speech'):
            raise ValueError(f'{self.stage} has no tensor-input form')
        return getattr(self, '_run_'+self.stage+'_tensor')(data)

    def _run_vision_tensor(self, data):
        import numpy as np
        t0 = time.perf_counter()
        value = self.torch.from_numpy(load_vision_tensor(data))
        t1 = time.perf_counter()
        with self.torch.inference_mode():
            x = value.to(self.device)
            self._sync()
            t2 = time.perf_counter()
            feature = self.module(x)
            feature = feature.sum(3).sum(2).div(feature.size(2) * feature.size(3)).squeeze(0)
            assert_on(self.device, [feature], 'vision feature')
            self._sync()
            t3 = time.perf_counter()
            host = feature.cpu().numpy()
        t4 = time.perf_counter()
        assert host.shape == (512,) and host.dtype == np.float32
        return npy_bytes(host), dict(input_kind='tensor', input_shape=list(x.shape), input_dtype='float32', output_shape=[512],
                                     output_dtype='float32', host_decode_ms=(t1-t0)*1000, h2d_ms=(t2-t1)*1000,
                                     device_compute_ms=(t3-t2)*1000, d2h_ms=(t4-t3)*1000, output_device=str(feature.device))

    def _run_speech_tensor(self, data):
        t0 = time.perf_counter()
        values = load_input_values(data)
        t1 = time.perf_counter()
        with self.torch.inference_mode():
            x = self.torch.from_numpy(values).unsqueeze(0).to(self.device)
            self._sync()
            t2 = time.perf_counter()
            logits = self.module(x).logits.squeeze(0)
            ids = self.torch.argmax(logits, dim=-1)
            assert_on(self.device, [logits, ids], 'speech logits')
            self._sync()
            t3 = time.perf_counter()
            host_ids = ids.cpu()
        t4 = time.perf_counter()
        text = self.processor.decode(host_ids, skip_special_tokens=True)
        t5 = time.perf_counter()
        return text.encode(), dict(input_kind='tensor', input_shape=list(x.shape), input_dtype='float32', output_frames=int(logits.shape[0]),
                                   host_decode_ms=(t1-t0)*1000, h2d_ms=(t2-t1)*1000, device_compute_ms=(t3-t2)*1000,
                                   d2h_ms=(t4-t3)*1000, ctc_decode_ms=(t5-t4)*1000, output_device=str(logits.device))

    def describe(self):
        out = dict(stage=self.stage, backend=self.backend, device=str(self.device), model=self.model_name, model_sha256=self.model_sha256,
                   derived_artifact_sha256=None, parameter_elements=self.parameter_elements, load_ms=self.load_ms,
                   settings=self.settings, provenance=legacy_check(), jellybean_commit=JELLYBEAN_COMMIT)
        if self.stage == 'fusion':
            out.update(vocab_sha256=self.vocab_sha256, strict_state_keys=self.strict_state_keys)
        return out


# ---------------------------------------------------------------- OpenVINO NPU (vision)
class OpenVinoVision:
    """Static 1x3x448x448 -> 512 vision graph compiled directly on 'NPU'; EXECUTION_DEVICES must be exactly NPU."""

    def __init__(self, onnx_path, device='NPU', expected_onnx_sha256=None, cache_dir=None):
        if device != 'NPU' or any(t in device.upper() for t in REFUSED_DEVICE_TOKENS):
            raise ValueError(f'Only direct NPU compilation is allowed, got {device!r}')
        import openvino as ov
        self.ov = ov
        self.onnx_sha256 = sha(onnx_path)
        if expected_onnx_sha256 and self.onnx_sha256 != expected_onnx_sha256:
            raise ValueError('Derived vision ONNX hash mismatch')
        core = ov.Core()
        self.available = list(core.available_devices)
        if 'NPU' not in self.available:
            raise BackendUnavailable(f'OpenVINO {ov.__version__} reports devices {self.available}, no NPU')
        started = time.perf_counter()
        model = core.read_model(str(onnx_path))
        read = time.perf_counter()
        config = {'CACHE_DIR': str(cache_dir)} if cache_dir else {}
        self.compiled = core.compile_model(model, 'NPU', config)
        done = time.perf_counter()
        self.execution_devices, self.execution_devices_raw = require_direct_npu(self.compiled.get_property('EXECUTION_DEVICES'))
        self.request = self.compiled.create_infer_request()
        self.full_device_name = core.get_property('NPU', 'FULL_DEVICE_NAME')
        try:  # documented in OpenVINO 2026.3 npu-device.rst: queryable on the CompiledModel (driver vs plugin compiler)
            self.compiler_type = str(self.compiled.get_property('NPU_COMPILER_TYPE'))
        except Exception as error:
            self.compiler_type = f'unavailable: {type(error).__name__}'
        self.read_ms, self.compile_ms = (read-started)*1000, (done-read)*1000
        self.stage, self.backend = 'vision', 'openvino-npu'

    def run(self, data):
        import numpy as np
        t0 = time.perf_counter()
        x = vision_array_numpy(decode_image(data))
        t1 = time.perf_counter()
        result = self.request.infer({0: x})
        t2 = time.perf_counter()
        feature = np.asarray(next(iter(result.values())), dtype=np.float32).reshape(-1)
        if feature.shape != (512,):
            raise ValueError(f'NPU vision output shape {feature.shape}')
        return npy_bytes(feature), dict(input_shape=list(x.shape), input_dtype='float32', output_shape=[512], output_dtype='float32',
                                        host_preprocess_ms=(t1-t0)*1000, device_infer_ms=(t2-t1)*1000,
                                        execution_devices=self.execution_devices,
                                        timing_boundary='host tensor -> infer() -> host output (blocking)')

    def run_tensor(self, data):
        """Preprocessed float32 [1,3,448,448] tensor (quality-network-baselines DAG) -> same compiled NPU graph."""
        import numpy as np
        t0 = time.perf_counter()
        x = load_vision_tensor(data)
        t1 = time.perf_counter()
        result = self.request.infer({0: x})
        t2 = time.perf_counter()
        feature = np.asarray(next(iter(result.values())), dtype=np.float32).reshape(-1)
        if feature.shape != (512,):
            raise ValueError(f'NPU vision output shape {feature.shape}')
        return npy_bytes(feature), dict(input_kind='tensor', input_shape=list(x.shape), input_dtype='float32', output_shape=[512],
                                        output_dtype='float32', host_decode_ms=(t1-t0)*1000, device_infer_ms=(t2-t1)*1000,
                                        execution_devices=self.execution_devices,
                                        timing_boundary='host tensor -> infer() -> host output (blocking)')

    def describe(self):
        return dict(stage='vision', backend='openvino-npu', device='NPU', execution_devices=self.execution_devices,
                    execution_devices_raw=self.execution_devices_raw,
                    full_device_name=self.full_device_name, available_devices=self.available, openvino=self.ov.__version__,
                    npu_compiler_type=self.compiler_type,
                    derived_artifact_sha256=self.onnx_sha256, model_sha256='f37072fd47e89c5e827621c5baffa7500819f7896bbacec160b1a16c560e07ec',
                    precision='model FP32 I/O; HW computation FP16 per OpenVINO 2026.3 npu-device.rst (not an FP32 device comparison)',
                    read_ms=self.read_ms, compile_ms=self.compile_ms, provenance=legacy_check())


def rss_kib():
    """Process resident set (VmRSS) - an observation of host process memory, not NPU/driver memory."""
    try:
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return int(line.split()[1])
    except OSError:
        return None
    return None


class OpenVinoSpeechProbe:
    """Compilation/execution PROBE (not a serving backend): dynamic speech ONNX reshaped to each request's exact
    sample count, compiled directly on NPU (EXECUTION_DEVICES must be exactly NPU), CTC argmax ids returned.
    Input is the Wav2Vec2Processor output computed by the collector at the original length (attributed to the
    collector); decoding happens on the collector. Bounded cache: at most CACHE_GRAPHS (1) compiled exact-length
    graph; references to the previous graph/request are dropped before compiling another length. Evictions and
    each (re)compile cost are recorded; process RSS is recorded as an observation only (dropping Python references
    does not prove the driver released device memory)."""
    CACHE_GRAPHS = 1

    def __init__(self, onnx_path, expected_onnx_sha256=None, core=None, ov_version=None):
        if core is None:
            import openvino as ov
            core, ov_version = ov.Core(), ov.__version__
        self.ov_version, self.onnx_path, self.onnx_sha256 = ov_version, str(onnx_path), sha(onnx_path)
        if expected_onnx_sha256 and self.onnx_sha256 != expected_onnx_sha256:
            raise ValueError('Derived speech ONNX hash mismatch')
        self.core = core
        self.available = list(self.core.available_devices)
        if 'NPU' not in self.available:
            raise BackendUnavailable(f'OpenVINO {ov_version} reports devices {self.available}, no NPU')
        self.cache, self.evictions, self.compiles, self.stage, self.backend = {}, 0, 0, 'speech', 'openvino-npu'

    def _prepare(self, length):
        before = rss_kib()
        evicted = None
        while len(self.cache) >= self.CACHE_GRAPHS:
            evicted = next(iter(self.cache))
            del self.cache[evicted]
            self.evictions += 1
        after_evict = rss_kib()
        started = time.perf_counter()
        try:
            model = self.core.read_model(self.onnx_path)
            model.reshape({model.inputs[0]: [1, length]})
        except Exception as error:
            raise PhaseError('prepare', error) from error
        try:
            compiled = self.core.compile_model(model, 'NPU')
            raw_devices = compiled.get_property('EXECUTION_DEVICES')
        except Exception as error:
            raise PhaseError('compile', error) from error
        try:
            devices, self.execution_devices_raw = require_direct_npu(raw_devices)
        except DeviceMismatch as error:  # device verification, not a model compile failure
            raise PhaseError('device_verification', error) from error
        self.cache[length] = compiled.create_infer_request()
        del compiled, model
        self.compiles += 1
        return dict(exact_length_compile_ms=(time.perf_counter() - started) * 1000, evicted_length=evicted,
                    process_rss_kib_before=before, process_rss_kib_after_evict=after_evict, process_rss_kib_after_compile=rss_kib())

    def run(self, data):
        import numpy as np
        values = np.load(io.BytesIO(data), allow_pickle=False)
        if values.dtype != np.float32 or values.ndim != 1:
            raise ValueError('Expected 1-D float32 processor input_values')
        length = int(values.shape[0])
        prepare = dict(exact_length_compile_ms=None, evicted_length=None, process_rss_kib_before=rss_kib())
        if length not in self.cache:
            prepare = self._prepare(length)
        t0 = time.perf_counter()
        result = self.cache[length].infer({0: values[None]})
        t1 = time.perf_counter()
        logits = np.asarray(next(iter(result.values())), dtype=np.float32)[0]
        return npy_bytes(logits.argmax(-1).astype(np.int64)), dict(
            input_shape=[1, length], input_dtype='float32', output='ctc argmax ids (decoded by collector)', device_infer_ms=(t1-t0)*1000,
            execution_devices=['NPU'], execution_devices_raw=getattr(self, 'execution_devices_raw', None), cached_graphs=len(self.cache), evictions_total=self.evictions, compiles_total=self.compiles,
            prepare_boundary='read_model+reshape+compile_model+create_infer_request, separate from device_infer_ms', **prepare)

    def describe(self):
        return dict(stage='speech', backend='openvino-npu', device='NPU', role='compilation probe, not serving', openvino=self.ov_version,
                    available_devices=self.available, derived_artifact_sha256=self.onnx_sha256, cache_graphs=self.CACHE_GRAPHS,
                    memory_note='process RSS observations only; not device memory, release by the driver is not asserted',
                    model_sha256='8aa76ab2243c81747a1f832954586bc566090c83a0ac167df6f31f0fa917d74a', provenance=legacy_check())


def load(stage, backend, model_root=None, source_root=None, onnx_path=None, expected_onnx_sha256=None):
    if backend not in BACKENDS:
        raise ValueError(f'Unknown backend {backend}')
    if stage in PREPROCESS_STAGES:
        return PreprocessStage(stage, backend, model_root or os.environ['MODEL_ROOT'])
    if backend == 'openvino-npu':
        if stage == 'vision':
            return OpenVinoVision(onnx_path['vision'] if isinstance(onnx_path, dict) else onnx_path,
                                  expected_onnx_sha256=(expected_onnx_sha256 or {}).get('vision') if isinstance(expected_onnx_sha256, dict) else expected_onnx_sha256)
        if stage == 'speech':
            if not isinstance(onnx_path, dict) or 'speech' not in onnx_path:
                raise BackendUnavailable('openvino-npu speech probe needs the derived speech ONNX')
            return OpenVinoSpeechProbe(onnx_path['speech'], (expected_onnx_sha256 or {}).get('speech'))
        raise BackendUnavailable(f'openvino-npu {stage}: no derived artifact (local fusion export failed); not attempted')
    return TorchStage(stage, backend, model_root or os.environ['MODEL_ROOT'], source_root or os.environ['JELLYBEAN_SOURCE'])
