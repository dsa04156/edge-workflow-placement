"""In-Pod program of the Mobilint DAG pilot. ROLE = edge | mobilint | cpu-fp32 (the cache Pod is plain http.server).

Every role serves GET /health on :8080 at once (state starting -> installing -> loading -> ready | failed, error, log
tail, evidence), so failures are readable over application HTTP without exec/logs. Assets come from the owned cache Pod
(sha256 of every file checked against the frozen protocol, absolute fetch deadline); wheels are unpacked into
/work/site (no pip, no network beyond the cache).

Model roles run the backend in a spawned child process (BackendProcess). The parent enforces the frozen load deadline
and a per-call deadline by terminating the child; the failure and phase timestamps are persisted to
/work/backend-evidence.json and /health. A child that missed a deadline is never reused (fail closed).
mobilint  qbruntime only (no ONNX Runtime anywhere in the process tree; a load/infer failure is a failure, never a CPU
          substitute). Single core Cluster0/Core0; runtime version, target cores, device name and /dev/aries* nodes.
cpu-fp32  ONNX Runtime CPUExecutionProvider only, intra/inter threads 1, FP32, normalization (x/255-mean)/std, NCHW.
  POST /infer  binary framing {run_id, request_id, arm, shape, dtype, sha256, data=uint8 HWC} -> receipt + logits.
  The receipt echoes run/request ids, the server-computed input sha256, the model file sha256 computed at load, runtime
  version, frozen protocol sha256, Pod UID and node, so the edge can bind every success to the expected execution.
edge      origin + preprocess + postprocess. Holds every frozen JPEG (sha-checked); runs the frozen schedule closed loop
          (concurrency 1, no retries) against the configured arm URLs; data never comes from the collector.
  POST /configure {run_id, urls{arm: url}, identities{arm: {pod_uid, node, model_sha256, runtime_version, frozen_sha256}}}
  POST /probe {arm, sample}   POST /trace {label}   GET /status   GET /ledger?label=L[&since=N]
  A reply whose identity differs from the configured one is status identity_mismatch, never ok.
Timing: e2e on the edge monotonic clock from the request start (JPEG already in memory) to the top-5 result or to the
failure. rpc_ms is the edge wall time of the infer POST and CONTAINS the server time; rpc_ms - server_ms is
transfer/HTTP residual, not pure network time.
"""
import base64
import importlib
import importlib.metadata
import io
import json
import multiprocessing
import os
from pathlib import Path
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bounded_http  # noqa: E402
from http_runtime import decode_body, encode_body  # noqa: E402
import protocol as P  # noqa: E402

ROLE = os.environ.get('ROLE', '')
WORK = Path(os.environ.get('WORK_DIR', '/work'))
CACHE_URL = os.environ.get('CACHE_URL', '')
FROZEN_SHA = os.environ.get('FROZEN_SHA256', '')
TENSOR_SHAPE = (P.INPUT['crop'], P.INPUT['crop'], 3)
TENSOR_BYTES = TENSOR_SHAPE[0]*TENSOR_SHAPE[1]*TENSOR_SHAPE[2]
MAX_ASSET = 160*1024*1024
IDENTITY_FIELDS = ('pod_uid', 'node', 'model_sha256', 'runtime_version', 'frozen_sha256')


class State:
    def __init__(self, role):
        self.role, self.state, self.error, self.log, self.evidence = role, 'starting', None, [], {}

    def note(self, message):
        self.log = (self.log + [f'{time.time():.3f} {message}'])[-60:]

    def health(self):
        return dict(role=self.role, state=self.state, error=self.error, log=self.log, evidence=self.evidence,
                    node=os.environ.get('NODE_NAME'), pod_uid=os.environ.get('POD_UID'), frozen_sha256=FROZEN_SHA)


# ------------------------------------------------------------------ assets
def fetch(name, expected, max_body=MAX_ASSET, deadline_s=None):
    status, body, _ = bounded_http.exchange(CACHE_URL.rstrip('/')+'/'+name, None, None,
                                            deadline_s or P.DEADLINES['install_s'], max_body)
    if status != 200:
        raise RuntimeError(f'cache GET {name}: HTTP {status}')
    if P.sha256(body) != expected:
        raise ValueError(f'{name}: sha256 mismatch')
    return body


def frozen_protocol():
    return json.loads(fetch('frozen.json', FROZEN_SHA, max_body=8*1024*1024))


def install(frozen, arch, state):
    site = WORK/'site'
    site.mkdir(parents=True, exist_ok=True)
    for name in P.WHEELS[arch]:
        data = fetch('wheels/'+name, frozen['wheels'][name])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(site)
        state.note(f'unpacked {name}')
    sys.path.insert(0, str(site))
    return site


# ------------------------------------------------------------------ stages (pure; also used by tests)
def preprocess(jpeg):
    """JPEG bytes -> uint8 HWC 224x224x3 (resize shorter side 256, PIL bilinear, torchvision center-crop offsets)."""
    import numpy as np
    from PIL import Image
    img = Image.open(io.BytesIO(jpeg))
    img = img.convert('RGB')
    img = img.resize(P.resize_shape(*img.size), Image.BILINEAR)
    img = img.crop(P.center_crop_box(*img.size))
    arr = np.ascontiguousarray(np.asarray(img, dtype=np.uint8))
    if arr.shape != TENSOR_SHAPE:
        raise ValueError(f'preprocess produced {arr.shape}')
    return arr


def postprocess(logits, k=5):
    import numpy as np
    x = np.asarray(logits, dtype=np.float64).reshape(-1)
    if x.shape != (1000,) or not np.isfinite(x).all():
        raise ValueError('logits must be 1000 finite values')
    p = np.exp(x - x.max())
    p /= p.sum()
    top = np.argsort(-p, kind='stable')[:k]
    return [int(i) for i in top], [float(p[i]) for i in top]


def normalize_hwc_vendor(arr):
    """Official Mobilint input contract of this MXQ (mblt-vision-python 0.0.6 models/ResNet50.yaml IMAGENET1K_V1:
    Reader pil, Resize 256 bilinear, CenterCrop 224, SetOrder HWC, Normalize torch; utils/preprocess/normalize.py math:
    float32 /255, float64 mean/std, cast to float32). The MXQ declares Float32 input; qbruntime 1.4.0 rejected uint8
    with Model_DtypeMismatched (probe mdp-20261026t091641)."""
    import numpy as np
    x = arr.astype(np.float32) / 255.0
    x = (x - np.array(P.MEAN)) / np.array(P.STD)
    return np.ascontiguousarray(x.astype(np.float32))


def normalize_nchw(arr):
    import numpy as np
    x = arr.astype(np.float32) / 255.0
    x = (x - np.asarray(P.MEAN, dtype=np.float32)) / np.asarray(P.STD, dtype=np.float32)
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None])


# ------------------------------------------------------------------ backends (constructed inside the child process)
class MobilintBackend:
    """qbruntime on the allocated ARIES device; any deviation from the frozen contract raises."""
    name = 'mobilint'

    def __init__(self, model_path):
        import qbruntime as qb
        self.version = importlib.metadata.version('mobilint-qb-runtime')
        if self.version != P.QBRUNTIME_VERSION:
            raise RuntimeError(f'qbruntime {self.version} != frozen {P.QBRUNTIME_VERSION}')
        target = qb.CoreId(qb.Cluster.Cluster0, qb.Core.Core0)
        cfg = qb.ModelConfig()
        if not cfg.set_single_core_mode(core_ids=[target]):
            raise RuntimeError('single-core request refused')
        self.accelerator = qb.Accelerator(0)
        self.model = qb.Model(str(model_path), cfg)
        self.model.launch(self.accelerator)
        cores = [str(c) for c in self.model.get_target_cores()]
        if cores != [str(target)]:
            raise RuntimeError(f'target cores {cores} != {[str(target)]}')
        shape = tuple(self.model.get_model_input_shape()[0])
        if shape != TENSOR_SHAPE:
            raise RuntimeError(f'MXQ input shape {shape} != {TENSOR_SHAPE}')
        self.evidence = dict(runtime='qbruntime', runtime_version=self.version, target_cores=cores, input_shape=list(shape),
                             dev_nodes=sorted(n for n in os.listdir('/dev') if 'aries' in n or 'mobilint' in n))
        for key, call in (('device_name', lambda: self.accelerator.get_device_name()),
                          ('input_dtype', lambda: str(self.model.get_model_input_data_type())),
                          ('output_dtype', lambda: str(self.model.get_model_output_data_type())),
                          ('output_shape', lambda: [list(s) for s in self.model.get_model_output_shape()]),
                          ('core_mode', lambda: str(self.model.get_core_mode()))):
            try:
                self.evidence[key] = call()
            except Exception as error:  # optional getters: record, never substitute
                self.evidence[key+'_error'] = repr(error)[:200]

    def infer(self, arr):
        import numpy as np
        out = self.model.infer([normalize_hwc_vendor(arr)])
        if not out:
            raise RuntimeError('qbruntime returned no output')
        return np.asarray(out[0], dtype=np.float32).reshape(-1)


class OrtCpuBackend:
    name = 'cpu-fp32'

    def __init__(self, model_path):
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(str(model_path), options, providers=['CPUExecutionProvider'])
        if self.session.get_providers() != ['CPUExecutionProvider']:
            raise RuntimeError(f'providers {self.session.get_providers()}')
        self.input = self.session.get_inputs()[0].name
        self.evidence = dict(runtime='onnxruntime', runtime_version=ort.__version__, providers=self.session.get_providers(),
                             intra_op_threads=1, inter_op_threads=1, precision='fp32')

    def infer(self, arr):
        import numpy as np
        out = self.session.run(None, {self.input: normalize_nchw(arr)})[0]
        return np.asarray(out, dtype=np.float32).reshape(-1)


BACKENDS = {'mobilint': ('worker:MobilintBackend', 'resnet50_IMAGENET1K_V1.mxq', None),
            'cpu-fp32': ('worker:OrtCpuBackend', 'resnet50_IMAGENET1K_V1.onnx', 'x86_64')}


def backend_child(factory, model_path, extra_paths, conn):
    """Child process: construct the backend, report evidence, then serve tensors from the pipe until closed."""
    try:
        for path in extra_paths:
            sys.path.insert(0, path)
        module, _, name = factory.partition(':')
        backend = getattr(importlib.import_module(module), name)(model_path)
        conn.send(dict(ok=True, evidence=backend.evidence, pid=os.getpid()))
    except BaseException as error:
        conn.send(dict(ok=False, error=repr(error)[:2000], trace=traceback.format_exc()[-3000:]))
        return
    import numpy as np
    while True:
        try:
            data = conn.recv_bytes()
        except EOFError:
            return
        try:
            # qbruntime rejects read-only buffers ('array is not writeable', probe mdp-20261026t091406): same values, own copy.
            arr = np.frombuffer(data, dtype=np.uint8).reshape(TENSOR_SHAPE).copy()
            t0 = time.perf_counter()
            logits = backend.infer(arr)
            infer_ms = (time.perf_counter()-t0)*1000
            conn.send(dict(ok=True, infer_ms=infer_ms, logits=np.asarray(logits, dtype='<f4').tobytes()))
        except BaseException as error:
            conn.send(dict(ok=False, error=repr(error)[:2000]))


class BackendProcess:
    """Terminable boundary around a native backend: load and each call have deadlines; a missed deadline kills the
    child and marks the backend dead (no reuse, no substitute)."""

    def __init__(self, name, factory, model_path, extra_paths=(), load_s=None, call_s=None, evidence_path=None):
        self.name, self.dead, self.lock = name, None, threading.Lock()
        self.call_s = call_s or P.DEADLINES['request_s']
        self.evidence_path = evidence_path
        self.record = dict(name=name, factory=factory, load_deadline_s=load_s or P.DEADLINES['load_s'], started_at=time.time())
        ctx = multiprocessing.get_context('spawn')
        self.conn, child = ctx.Pipe()
        self.proc = ctx.Process(target=backend_child, args=(factory, str(model_path), list(extra_paths), child), daemon=True)
        start = time.perf_counter()
        self.proc.start()
        child.close()
        reply = self.conn.recv() if self.conn.poll(self.record['load_deadline_s']) else None
        self.record['load_ms'] = (time.perf_counter()-start)*1000
        if reply is None or not reply.get('ok'):
            reason = 'load_timeout' if reply is None else 'load_failed'
            self.kill(reason, None if reply is None else reply.get('error'))
            self.record['trace'] = None if reply is None else reply.get('trace')
            self.persist()
            raise RuntimeError(f"{reason}: {self.record.get('error') or 'no reply within %.1fs' % self.record['load_deadline_s']}")
        self.evidence = reply['evidence']
        self.record.update(state='ready', child_pid=reply['pid'], evidence=self.evidence)
        self.persist()

    def persist(self):
        if self.evidence_path:
            Path(self.evidence_path).write_text(json.dumps(self.record, indent=1))

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
        self.persist()

    def infer(self, arr):
        if self.dead:
            raise RuntimeError(f'backend dead ({self.dead}); not reused')
        self.conn.send_bytes(arr.tobytes())
        if not self.conn.poll(self.call_s):
            self.kill('call_timeout')
            raise TimeoutError(f'backend call exceeded {self.call_s}s; child terminated')
        reply = self.conn.recv()
        if not reply['ok']:
            raise RuntimeError(reply['error'])
        import numpy as np
        return np.frombuffer(reply['logits'], dtype='<f4'), reply['infer_ms']


class InferService:
    def __init__(self, state, backend, model_sha256):
        self.state, self.backend, self.model_sha256, self.lock, self.served = state, backend, model_sha256, threading.Lock(), 0

    def handle(self, body):
        import numpy as np
        start = time.perf_counter()
        if body.get('arm') != self.backend.name:
            raise ValueError(f"arm {body.get('arm')!r} sent to {self.backend.name}")
        data = body.get('data')
        if (not isinstance(data, bytes) or len(data) != TENSOR_BYTES or body.get('dtype') != 'uint8'
                or list(body.get('shape') or []) != list(TENSOR_SHAPE) or P.sha256(data) != body.get('sha256')):
            raise ValueError('tensor must be uint8 224x224x3 with matching sha256')
        arr = np.frombuffer(data, dtype=np.uint8).reshape(TENSOR_SHAPE)
        if not self.lock.acquire(blocking=False):
            raise BlockingIOError('busy')
        try:
            logits, infer_ms = self.backend.infer(arr)
            self.served += 1
            seq = self.served
        finally:
            self.lock.release()
        if logits.shape != (1000,) or not np.isfinite(logits).all():
            raise RuntimeError(f'invalid logits shape {logits.shape} or non-finite values')
        raw = logits.astype('<f4').tobytes()
        return dict(arm=self.backend.name, run_id=body.get('run_id'), request_id=body.get('request_id'), input_sha256=P.sha256(data),
                    served_seq=seq, node=os.environ.get('NODE_NAME'), pod_uid=os.environ.get('POD_UID'), frozen_sha256=FROZEN_SHA,
                    model_sha256=self.model_sha256, runtime=self.backend.evidence.get('runtime'),
                    runtime_version=self.backend.evidence.get('runtime_version'),
                    infer_ms=infer_ms, server_ms=(time.perf_counter()-start)*1000,
                    logits_sha256=P.sha256(raw), logits_b64=base64.b64encode(raw).decode())


def identity_problems(receipt, expected, run_id, request_id, tensor_sha):
    """-> list of mismatches between a reply and the configured execution identity (empty = bound)."""
    problems = [f'{k}={receipt.get(k)!r}' for k in IDENTITY_FIELDS if receipt.get(k) != expected.get(k)]
    for key, want in (('run_id', run_id), ('request_id', request_id), ('input_sha256', tensor_sha)):
        if receipt.get(key) != want:
            problems.append(f'{key}={receipt.get(key)!r}')
    return problems


# ------------------------------------------------------------------ edge origin
class Edge:
    def __init__(self, state, frozen, images):
        self.state, self.frozen, self.images = state, frozen, images
        self.samples = {s['id']: s for s in frozen['evaluation'] + frozen['warmup'] + frozen['parity']}
        self.urls, self.identities, self.run_id, self.traces, self.ledgers = None, None, None, {}, {}
        self.busy = threading.Lock()

    def configure(self, body):
        urls, identities = body.get('urls') or {}, body.get('identities') or {}
        if self.urls is not None:
            raise ValueError('already configured')
        if set(urls) != set(P.ARMS) or not isinstance(body.get('run_id'), str):
            raise ValueError('run_id and one url per arm required')
        if set(identities) != set(urls) or any(set(v) != set(IDENTITY_FIELDS) for v in identities.values()):
            raise ValueError(f'one identity per arm with fields {IDENTITY_FIELDS} required')
        self.urls, self.identities, self.run_id = dict(urls), dict(identities), body['run_id']
        return dict(configured=self.urls, identities=self.identities)

    def request(self, row, deadline_s=None):
        rec = dict(seq=row.get('seq'), phase=row['phase'], sample=row['sample'], arm=row['arm'],
                   label=self.samples[row['sample']]['label'], started_at=time.time(), status='error')
        t0 = time.perf_counter()
        try:
            self._request(rec, row, t0, deadline_s)
        except Exception as error:
            rec.update(status='error', error=repr(error)[:500])
        rec['e2e_ms'] = (time.perf_counter()-t0)*1000
        return rec

    def _request(self, rec, row, t0, deadline_s):
        import numpy as np
        jpeg = self.images[row['sample']]
        rec['jpeg_bytes'] = len(jpeg)
        arr = preprocess(jpeg)
        t1 = time.perf_counter()
        rec['pre_ms'] = (t1-t0)*1000
        data = arr.tobytes()
        tensor_sha = P.sha256(data)
        request_id = f"{self.run_id}-{row.get('seq')}-{row['arm']}"
        raw, ctype = encode_body(dict(run_id=self.run_id, request_id=request_id, arm=row['arm'], shape=list(TENSOR_SHAPE),
                                      dtype='uint8', sha256=tensor_sha, data=data))
        rec.update(tensor_sha256=tensor_sha, request_id=request_id, request_body_bytes=len(raw))
        try:
            status, reply, _ = bounded_http.exchange(self.urls[row['arm']]+'/infer', raw, ctype,
                                                     deadline_s or P.DEADLINES['request_s'], 1 << 20)
        except bounded_http.DeadlineExceeded as error:
            rec.update(status='rpc_timeout', error=str(error), rpc_ms=(time.perf_counter()-t1)*1000)
            return
        t2 = time.perf_counter()
        rec.update(rpc_ms=(t2-t1)*1000, response_body_bytes=len(reply), http_status=status)
        body = json.loads(reply)
        if status != 200:
            rec.update(status='rpc_error', error=str(body.get('error'))[:500])
            return
        receipt = {k: v for k, v in body.items() if k != 'logits_b64'}
        rec['receipt'] = receipt
        problems = identity_problems(receipt, self.identities[row['arm']], self.run_id, request_id, tensor_sha)
        if body.get('arm') != row['arm']:
            problems.append(f"arm={body.get('arm')!r}")
        logits_raw = base64.b64decode(body['logits_b64'], validate=True)
        if P.sha256(logits_raw) != body.get('logits_sha256'):
            problems.append('logits_sha256')
        if problems:
            rec.update(status='identity_mismatch', error='; '.join(problems)[:500])
            return
        top, prob = postprocess(np.frombuffer(logits_raw, dtype='<f4'))
        t3 = time.perf_counter()
        rec.update(status='ok', post_ms=(t3-t2)*1000, top5=top, top5_prob=prob, logits_b64=body['logits_b64'])

    def probe(self, body):
        if body.get('arm') not in P.ARMS or body.get('sample') not in self.samples:
            raise ValueError('known arm and sample required')
        with self.busy:
            return self.request(dict(seq=-1, phase='probe', sample=body['sample'], arm=body['arm']), P.DEADLINES['probe_s'])

    def start_trace(self, body):
        label = body.get('label')
        if not isinstance(label, str) or label in self.traces or self.urls is None:
            raise ValueError('configured edge and a new label required')
        if not self.busy.acquire(blocking=False):
            raise BlockingIOError('another trace or probe is running')
        rows = [r for r in self.frozen['schedule'] if body.get('phases') is None or r['phase'] in body['phases']]
        self.traces[label], self.ledgers[label] = dict(state='running', total=len(rows), done=0), []
        threading.Thread(target=self.run_trace, args=(label, rows), daemon=True).start()
        return dict(label=label, rows=len(rows))

    def run_trace(self, label, rows):
        deadline = time.monotonic() + P.DEADLINES['trace_s']
        try:
            for row in rows:
                if time.monotonic() > deadline:
                    self.ledgers[label].append(dict(seq=row['seq'], phase=row['phase'], sample=row['sample'], arm=row['arm'],
                                                    status='not_run_trace_deadline'))
                else:
                    self.ledgers[label].append(self.request(row, min(P.DEADLINES['request_s'], max(0.1, deadline-time.monotonic()))))
                self.traces[label]['done'] += 1
            self.traces[label]['state'] = 'done'
        except Exception:
            self.traces[label].update(state='crashed', error=traceback.format_exc()[-2000:])
        finally:
            self.busy.release()


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, value):
        data = json.dumps(value, separators=(',', ':')).encode()
        try:
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client gave up (its deadline); the outcome is in its own record

    def do_GET(self):
        app = self.server.app
        if self.path == '/health':
            return self.reply(200, app['state'].health())
        edge = app.get('edge')
        if edge and self.path == '/status':
            return self.reply(200, dict(traces=edge.traces))
        if edge and self.path.startswith('/ledger?label='):
            query = dict(p.split('=', 1) for p in self.path.split('?', 1)[1].split('&') if '=' in p)
            label, since = query.get('label'), int(query.get('since', '0'))
            rows = edge.ledgers.get(label)
            return self.reply(200 if rows is not None else 404, dict(label=label, since=since, total=None if rows is None else len(rows),
                                                                     rows=None if rows is None else rows[since:]))
        self.reply(404, dict(error='not found'))

    def do_POST(self):
        app = self.server.app
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4*1024*1024:
                raise ValueError('body must be 1 B .. 4 MiB')
            body = decode_body(self.rfile.read(length), self.headers.get('Content-Type'))
            if app['state'].state != 'ready':
                return self.reply(503, dict(error=f"not ready: {app['state'].state}"))
            if self.path == '/infer' and app.get('infer'):
                return self.reply(200, app['infer'].handle(body))
            edge = app.get('edge')
            routes = dict(configure=edge.configure, probe=edge.probe, trace=edge.start_trace) if edge else {}
            if self.path.lstrip('/') in routes:
                return self.reply(200, routes[self.path.lstrip('/')](body))
            self.reply(404, dict(error='not found'))
        except BlockingIOError as error:
            self.reply(503, dict(error=str(error)))
        except ValueError as error:
            self.reply(400, dict(error=str(error)))
        except TimeoutError as error:
            app['state'].state, app['state'].error = 'failed', str(error)
            self.reply(504, dict(error=str(error)))
        except Exception as error:
            self.reply(500, dict(error=repr(error)[:500]))


def boot(app):
    state = app['state']
    try:
        state.state = 'installing'
        frozen = frozen_protocol()
        if ROLE == 'edge':
            install(frozen, 'aarch64', state)
            images = {}
            for s in frozen['evaluation'] + frozen['warmup'] + frozen['parity']:
                images[s['id']] = fetch(s['file'], s['sha256'], max_body=16*1024*1024, deadline_s=60)
            state.note(f'{len(images)} images verified')
            import numpy
            import PIL
            state.evidence = dict(numpy=numpy.__version__, pillow=PIL.__version__, images=len(images))
            app['edge'] = Edge(state, frozen, images)
        else:
            factory, model_name, arch = BACKENDS[ROLE]
            extra = [str(install(frozen, arch, state))] if arch else []
            data = fetch(model_name, frozen['assets'][model_name]['sha256'])
            path = WORK/model_name
            path.write_bytes(data)
            model_sha = P.sha256(path.read_bytes())
            state.state = 'loading'
            backend = BackendProcess(ROLE, factory, path, [str(Path(__file__).resolve().parent)] + extra,
                                     evidence_path=WORK/'backend-evidence.json')
            state.evidence = dict(backend.evidence, model=model_name, model_sha256=model_sha, load_ms=backend.record['load_ms'],
                                  child_pid=backend.record['child_pid'])
            app['infer'] = InferService(state, backend, model_sha)
        state.state = 'ready'
    except BaseException as error:
        state.state, state.error = 'failed', repr(error)[:2000]
        state.note(traceback.format_exc()[-3000:])
        evidence = WORK/'backend-evidence.json'
        if evidence.exists():
            state.evidence = dict(backend=json.loads(evidence.read_text()))


def main():
    if ROLE not in ('edge', 'mobilint', 'cpu-fp32'):
        raise SystemExit(f'unknown ROLE {ROLE!r}')
    app = dict(state=State(ROLE))
    server = ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', P.PORT))), Handler)
    server.app = app
    threading.Thread(target=boot, args=(app,), daemon=True).start()
    server.serve_forever()


if __name__ == '__main__':
    main()
