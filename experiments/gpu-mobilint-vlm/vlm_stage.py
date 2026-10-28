"""In-Pod VLM stage for the unchanged quality-network-heterogeneous Worker (`loader=` seam): one image+prompt -> caption.

The model runs in a spawned child process (same fail-closed pattern as mobilint-dag-pilot BackendProcess): the parent
enforces a load deadline and a per-call deadline by terminating the child; a child that failed or missed a deadline is
never reused and never replaced by another backend. The child's stdout/stderr go to a log file, so partial output
survives a kill and is returned in the failure record.

Backends (no fallback between them):
  mobilint  vendor transformers_mblt Qwen3-VL (vision MXQ + text MXQ via qbruntime on ARIES); host keeps the processor,
            tokenizer, embed_tokens lookup, vision pos_embed interpolation, rope tables and sampling.
  cuda      upstream transformers Qwen3-VL, BF16, every parameter on cuda:0 (asserted).
Output: canonical JSON {text, token_ids, prompt_tokens, processor_sha256, image_grid_thw, timing, host, device}.
"""
import io
import json
import math
import multiprocessing
import os
from pathlib import Path
import sys
import threading
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protocol as P  # noqa: E402

LOG_TAIL = 4000


# Pinned transformers-mblt 0.6.1 Qwen3-VL: count_npu_time defaults to False, and the selected non-batch deepstack path
# never calls _record_npu_timing, so get_npu_timing() would return reset zeros. Reported as unavailable, never as 0.
NPU_TIMING_UNAVAILABLE = dict(
    available=False, scope='vendor aggregate counters (text decoder prefill/decode; vision MXQ never included)',
    reason='pinned transformers-mblt 0.6.1 Qwen3-VL non-batch path: count_npu_time defaults False and the per-forward npu_time '
           'is not aggregated into get_npu_timing(); counters stay at reset values, so no measured NPU time is claimed. '
           'Coarse generate_ms/ttft_ms/token offsets and host cpu_s_* are the measured timing.')


class LoadFailed(RuntimeError):
    def __init__(self, message, record):
        super().__init__(message)
        self.record = record


class DeviceMismatch(RuntimeError):
    pass


def _tail(path):
    try:
        return Path(path).read_bytes()[-LOG_TAIL:].decode(errors='replace')
    except OSError as error:
        return f'log unreadable: {error!r}'


class TokenClock:
    """Streamer: the first put() is the prompt (upstream and vendor generate both do this), then one per new token."""

    def __init__(self):
        self.stamps = []

    def put(self, value):
        self.stamps.append(time.perf_counter())

    def end(self):
        pass


class Engine:
    """Child-side model owner. Third-party entrypoints are imported here only."""

    def __init__(self, backend, model_dir, contract):
        import torch
        torch.set_num_threads(contract['host_threads'])
        self.backend, self.contract, self.model_dir = backend, contract, Path(model_dir)
        self.inputs = {item['image_sha256']: item for item in contract['inputs']}
        started = time.perf_counter()
        expected = contract['models'][self.model_dir.name]['weights']
        self.weights = {name: P.file_sha(self.model_dir/name) for name in sorted(expected)}
        if self.weights != expected:
            raise RuntimeError(f'weight sha256 differs from the frozen contract: {self.weights}')
        hashed = time.perf_counter()
        if backend == 'mobilint':
            from transformers_mblt.models.qwen3_vl.modeling_qwen3_vl import MobilintQwen3VLForConditionalGeneration
            from transformers_mblt.models.qwen3_vl.processing_qwen3_vl import MobilintQwen3VLProcessor
            self.processor = MobilintQwen3VLProcessor.from_pretrained(str(self.model_dir))
            self.model = MobilintQwen3VLForConditionalGeneration.from_pretrained(str(self.model_dir))
            self.input_device = 'cpu'  # host tensors; the MXQs execute on the NPU through qbruntime
            evidence = self._mobilint_evidence()
        elif backend == 'cuda':
            from transformers import AutoProcessor, Qwen3VLForConditionalGeneration
            if not torch.cuda.is_available():
                raise RuntimeError('torch.cuda.is_available() is False; no other device is tried')
            self.processor = AutoProcessor.from_pretrained(str(self.model_dir))
            self.model = Qwen3VLForConditionalGeneration.from_pretrained(str(self.model_dir), dtype=getattr(torch, contract['cuda_dtype']),
                                                                         device_map={'': 'cuda:0'})
            self.input_device = 'cuda:0'
            evidence = self._cuda_evidence()
        else:
            raise ValueError(f'unknown backend {backend!r}')
        self.model.eval()
        self.evidence = dict(evidence, backend=backend, model=self.model_dir.name, weights_sha256=self.weights, host_threads=contract['host_threads'],
                             hash_ms=(hashed-started)*1000, load_ms=(time.perf_counter()-hashed)*1000, pid=os.getpid())

    def _host_parameters(self):
        rows = {}
        for name, param in self.model.named_parameters():
            rows[name] = dict(device=str(param.device), dtype=str(param.dtype), numel=param.numel())
        return rows

    def _mobilint_evidence(self):
        import qbruntime
        components = {}
        for role, module in (('vision', self.model.model.visual), ('text', self.model.model.language_model)):
            backend = module.npu_backend
            models = list(backend.mxq_models)
            if not models:
                raise DeviceMismatch(f'{role}: no launched MXQ model slot')
            components[role] = dict(target_device=str(backend.target_device), core_mode=str(backend.core_mode),
                                    mxq=os.path.basename(str(backend.mxq_path)), slots=len(models), dev_no=[int(d) for d in backend.model_dev_no],
                                    input_shapes=[str(s) for s in models[0].get_model_input_shape()],
                                    output_shapes=[str(s) for s in models[0].get_model_output_shape()])
            if components[role]['target_device'] != 'aries-rb':
                raise DeviceMismatch(f'{role} MXQ backend targets {components[role]["target_device"]}')
        try:
            dev = sorted(e for e in os.listdir('/dev') if e.startswith('aries'))
        except OSError as error:
            dev = repr(error)
        return dict(device='npu:aries-rb', runtime='qbruntime', runtime_version=str(getattr(qbruntime, '__version__', None)),
                    components=components, dev_nodes=dev, host_parameters=self._host_parameters())

    def _cuda_evidence(self):
        import torch
        params = self._host_parameters()
        devices = sorted({row['device'] for row in params.values()})
        if devices != ['cuda:0']:
            raise DeviceMismatch(f'parameters on {devices}, expected only cuda:0')
        return dict(device='cuda:0', runtime='torch', runtime_version=torch.__version__, cuda=torch.version.cuda,
                    device_name=torch.cuda.get_device_name(0), capability=list(torch.cuda.get_device_capability(0)),
                    parameter_devices=devices, buffer_devices=sorted({str(b.device) for b in self.model.buffers()}),
                    parameters=sum(r['numel'] for r in params.values()), dtypes=sorted({r['dtype'] for r in params.values()}),
                    memory_allocated=torch.cuda.memory_allocated(0))

    def generate(self, data):
        import hashlib
        import resource
        import numpy as np
        import torch
        from PIL import Image
        from transformers import GenerationConfig
        cpu0, wall0 = time.process_time(), time.perf_counter()
        item = self.inputs.get(hashlib.sha256(data).hexdigest())
        if item is None:
            raise ValueError('input image is not one of the frozen contract inputs')
        array = np.load(io.BytesIO(data), allow_pickle=False)
        if array.dtype != np.uint8 or list(array.shape) != item['shape']:
            raise ValueError(f'input array {array.dtype} {list(array.shape)} differs from the frozen {item["shape"]} uint8')
        image = Image.fromarray(array)  # uint8 HxWx3 -> RGB, the Intel smoke input contract
        messages = json.loads(json.dumps(self.contract['messages_template']))
        messages[0]['content'][1]['text'] = item['prompt']
        text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        batch = self.processor(text=[text], images=[image], images_kwargs=dict(size=self.contract['image_size']), return_tensors='pt')
        digest = hashlib.sha256()
        for key in ('input_ids', 'pixel_values', 'image_grid_thw'):
            digest.update(batch[key].numpy().tobytes())
        prompt_tokens = int(batch['input_ids'].shape[1])
        batch = batch.to(self.input_device)
        cuda = self.backend == 'cuda'
        if cuda:
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats(0)
        cpu1, wall1 = time.process_time(), time.perf_counter()
        clock = TokenClock()
        with torch.no_grad():
            out = self.model.generate(**batch, generation_config=GenerationConfig(**self.contract['generation']), streamer=clock)
        if cuda:
            torch.cuda.synchronize()
            if out.device.type != 'cuda':
                raise DeviceMismatch(f'generated output tensor on {out.device}, expected cuda')
        cpu2, wall2 = time.process_time(), time.perf_counter()
        new = [int(t) for t in out[0, prompt_tokens:].tolist()]
        caption = self.processor.decode(new, skip_special_tokens=True)
        wall3 = time.perf_counter()
        tokens = clock.stamps[1:]
        timing = dict(preprocess_ms=(wall1-wall0)*1000, generate_ms=(wall2-wall1)*1000, decode_text_ms=(wall3-wall2)*1000,
                      total_ms=(wall3-wall0)*1000, ttft_ms=(tokens[0]-wall1)*1000 if tokens else None,
                      token_offsets_ms=[(t-wall1)*1000 for t in tokens])
        host = dict(cpu_s_preprocess=cpu1-cpu0, cpu_s_generate=cpu2-cpu1, max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    streamer_token_puts=len(tokens))
        if cuda:
            device = dict(max_memory_allocated=torch.cuda.max_memory_allocated(0), output_device=str(out.device))
        else:
            device = dict(npu_timing=NPU_TIMING_UNAVAILABLE, output_tensor_device=str(out.device), output_tensor_note=(
                'host tensor expected: generate returns token ids on the host; the vision/text MXQ computation runs on the NPU '
                'and is evidenced by the load-time backend components, not by this tensor'))
        return dict(input_id=item['id'], text=caption, token_ids=new, prompt_tokens=prompt_tokens, processor_sha256=digest.hexdigest(),
                    image_grid_thw=batch['image_grid_thw'].tolist(), timing=timing, host=host, device=device)


def child_main(backend, model_dir, contract, log_path, conn):
    log = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(log, 1)
    os.dup2(log, 2)
    sys.stdout = os.fdopen(1, 'w', buffering=1)
    sys.stderr = os.fdopen(2, 'w', buffering=1)
    try:
        engine = Engine(backend, model_dir, contract)
        conn.send(dict(ok=True, evidence=engine.evidence))
    except BaseException as error:
        conn.send(dict(ok=False, error=repr(error)[:2000], trace=traceback.format_exc()[-3000:]))
        return
    while True:
        try:
            data = conn.recv_bytes()
        except EOFError:
            return
        try:
            conn.send(dict(ok=True, result=engine.generate(data)))
        except BaseException as error:
            conn.send(dict(ok=False, error=repr(error)[:2000], trace=traceback.format_exc()[-3000:]))


class VlmStage:
    """Parent side: Worker stage interface run(bytes) -> (bytes, timing) and describe()."""

    def __init__(self, backend, model_root, load_s=None, call_s=None, log_dir=None):
        self.backend, self.dead, self.calls = backend, None, 0
        self.model_dir = Path(model_root)/P.ARMS[backend]['model']
        contract_path = Path(model_root).parent/'contract.json'
        contract = json.loads(contract_path.read_text())
        self.contract_sha256 = P.file_sha(contract_path)
        self.call_s = call_s or P.DEADLINES['call_s']
        log_dir = Path(log_dir or Path(model_root).parent/'logs')
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir/f'vlm-{backend}-child.log'
        self.record = dict(backend=backend, model=self.model_dir.name, contract_sha256=self.contract_sha256,
                           load_deadline_s=load_s or P.DEADLINES['load_s'][backend], call_deadline_s=self.call_s,
                           log=str(self.log_path), started_at=time.time())
        ctx = multiprocessing.get_context('spawn')
        self.conn, child = ctx.Pipe()
        self.proc = ctx.Process(target=child_main, args=(backend, str(self.model_dir), contract, str(self.log_path), child), daemon=True)
        start = time.perf_counter()
        self.proc.start()
        child.close()
        reason, reply = 'load_timeout', None
        if self.conn.poll(self.record['load_deadline_s']):
            try:
                reply = self.conn.recv()
                reason = 'load_failed'
            except (EOFError, OSError):
                reason = 'load_eof'  # the child exited before replying
        self.record['load_wall_ms'] = (time.perf_counter()-start)*1000
        if reply is None or not reply.get('ok'):
            self.kill(reason, None if reply is None else reply.get('error'))
            self.record['trace'] = None if reply is None else reply.get('trace')
            raise LoadFailed(f"{reason}: {self.record.get('error') or 'no reply within %.1fs' % self.record['load_deadline_s']}", self.record)
        self.evidence = reply['evidence']
        self.record.update(state='ready', child_pid=self.proc.pid)

    def kill(self, reason, error=None):
        self.dead = reason
        self.record.update(state='dead', reason=reason, error=error, killed_at=time.time())
        if self.proc.is_alive():
            self.proc.terminate()
            self.proc.join(3)
            if self.proc.is_alive():
                self.proc.kill()
                self.proc.join(3)
        self.record['child_alive_after_kill'] = self.proc.is_alive()
        self.record['output_tail'] = _tail(self.log_path)

    def fail(self, reason, error, trace=None):
        """Any failed call: the child is killed and never reused; its error/trace and output tail stay in the record."""
        self.kill(reason, error)
        self.record['trace'] = trace
        raise RuntimeError(f'{reason}: {error}; child terminated, not reused; output tail: {self.record["output_tail"][-500:]}')

    def describe(self):
        return dict(backend=self.backend, device=(self.evidence or {}).get('device') if not self.dead else None,
                    model_sha256=self.evidence['weights_sha256'], contract_sha256=self.contract_sha256,
                    evidence=self.evidence, process=self.record)

    def run(self, data):
        if self.dead:
            raise RuntimeError(f'backend dead ({self.dead}); not reused')
        self.calls += 1
        try:
            self.conn.send_bytes(data)
            replied = self.conn.poll(self.call_s)
            reply = self.conn.recv() if replied else None
        except (EOFError, OSError) as error:
            self.fail('call_eof', repr(error))
        if not replied:
            self.kill('call_timeout')
            raise TimeoutError(f'VLM call exceeded {self.call_s}s; child terminated; output tail: {self.record["output_tail"][-500:]}')
        if not reply.get('ok'):
            self.fail('call_failed', reply.get('error'), reply.get('trace'))
        try:
            result = reply['result']
            timing, host, device = result['timing'], result['host'], result['device']
            values = [v for k, v in timing.items() if k != 'token_offsets_ms' and v is not None] + timing['token_offsets_ms']
            valid = all(math.isfinite(v) and v >= 0 for v in values) and timing['token_offsets_ms'] == sorted(timing['token_offsets_ms'])
        except (KeyError, TypeError, AttributeError) as error:
            valid, timing = False, repr(error)
        if not valid:
            self.fail('invalid_result', f'non-finite, unordered or missing timing: {timing}'[:2000])
        extra = {'output_device': device['output_device']} if 'output_device' in device else {}  # CUDA only
        return json.dumps(result, sort_keys=True, separators=(',', ':')).encode(), dict(timing, host=host, device_stats=device, **extra)


def load(name, backend, model_root=None, source_root=None, onnx_path=None, expected_onnx_sha256=None):
    """quality-network-heterogeneous Worker loader signature; only the 'vlm' stage exists."""
    if name != P.STAGE:
        raise ValueError(f'unknown stage {name!r}')
    return VlmStage(backend, model_root)
