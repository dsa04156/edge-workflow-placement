"""Pod runtime: video/text source, V/T/F worker, workflow driver and probe caller.

  serve              ROLE=video-source|text-source|worker (TASK_ID=V|T|F) HTTP on BIND_IP:PORT (8080)
  publish            RTSP publisher process started by a video source (own pid/cmdline evidence)
  drive '<json>'     run one workflow against F from the calling process (local smoke)
  remote URL ['<json>']  controller Pod: one GET/POST to another Pod, reply printed as JSON lines
  frames URL '<seqs>'  controller Pod: a video source's frozen frames as base64 JSON
HTTP POST /batch (self-calls in order) and /drive (workflows from this Pod) let the collector
drive edge Pods from one controller Pod on the server without kubectl exec into edges.

Durations are monotonic on one host; no cross-host timestamp is subtracted.
Every receipt carries node, Pod UID, model/input/output sha256 and byte counts.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rtsp  # noqa: E402
import scenario  # noqa: E402

ENV = os.environ
PORT = int(ENV.get('PORT', '8080'))
WORK = Path(ENV.get('ASSET_ROOT', '/work'))
BIND = ENV.get('BIND_IP', '0.0.0.0')
SELF = ENV.get('POD_IP', '127.0.0.1')
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def identity():
    return dict(node=ENV.get('NODE_NAME'), pod_uid=ENV.get('POD_UID'), pod_ip=SELF, role=ENV.get('ROLE'),
                task=ENV.get('TASK_ID'), source_id=ENV.get('SOURCE_ID'))


def http(url, body=None, timeout=60):
    """(bytes, headers, monotonic ms) of one GET/POST."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'} if data else {})
    start = time.monotonic()
    with OPENER.open(request, timeout=timeout) as response:
        payload = response.read()
        headers = dict(response.headers)
    return payload, headers, (time.monotonic()-start)*1000


def frozen_scenario():
    return scenario.validate(json.loads((HERE/'scenario.json').read_text()))


class State:
    ready, error, info = False, None, {}
    lock = threading.Lock()
    outputs = {}


def file_sha(path):
    return sha256(Path(path).read_bytes())


# ---------------- video source ----------------

def video_source():
    sc = frozen_scenario()
    spec = sc['video_sources'][ENV['SOURCE_ID']]
    stream = sc['stream']
    seqs = sorted(sc['inputs']['calibration'] + sc['inputs']['evaluation'])
    binary = WORK/'mediamtx/mediamtx'
    (WORK/'mediamtx.yml').write_text(rtsp.mediamtx_config(SELF, stream['port']))
    log = (WORK/'mediamtx.log').open('ab')
    server = subprocess.Popen([str(binary), str(WORK/'mediamtx.yml')], cwd=str(WORK), stdout=log, stderr=subprocess.STDOUT)
    publisher = subprocess.Popen([sys.executable, '-u', str(HERE/'runtime.py'), 'publish'], cwd=str(WORK),
                                 stdout=(WORK/'publisher.log').open('ab'), stderr=subprocess.STDOUT)
    url = f"rtsp://{SELF}:{stream['port']}/{spec['path']}"
    State.info = dict(rtsp_url=url, image=spec['image'], image_sha256=file_sha(WORK/'fixtures'/spec['image']), seed=spec['seed'],
                      stream=stream, mediamtx=dict(pid=server.pid, sha256=file_sha(binary)), publisher=dict(pid=publisher.pid))
    frames = {}
    deadline = time.monotonic() + 60
    record = None
    while time.monotonic() < deadline and record is None:
        try:
            record = rtsp.read(url, wanted=seqs, max_frames=4*stream['loop_frames'], timeout_s=30,
                                             keep=lambda s, rgb: frames.setdefault(s, rgb))
        except Exception as error:  # Publisher may not be online yet; retried until the deadline.
            State.info.setdefault('freeze_retries', []).append(repr(error))
            time.sleep(1)
    if record is None:
        raise RuntimeError('RTSP freeze failed before deadline')
    from PIL import Image
    frozen = {}
    for seq in seqs:
        if seq not in frames:
            raise RuntimeError(f'Frame seq {seq} never decoded from RTSP')
        out = io.BytesIO()
        Image.fromarray(frames[seq]).save(out, format='PNG', compress_level=6)
        png = out.getvalue()
        frozen[seq] = png
        record.setdefault('frozen', {})[str(seq)] = dict(rgb_sha256=sha256(frames[seq].tobytes()), png_sha256=sha256(png),
                                                          png_bytes=len(png), loop_consistent=record['loop_consistent'].get(str(seq)))
    State.frames = frozen
    State.info['freeze'] = record
    return url


def process(pid):
    try:
        cmdline = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        return dict(pid=pid, alive=True, cmdline=[c.decode() for c in cmdline if c])
    except OSError:
        return dict(pid=pid, alive=False)


def video_evidence():
    info = dict(State.info)
    info['mediamtx'] = dict(info['mediamtx'], **process(info['mediamtx']['pid']))
    info['publisher'] = dict(info['publisher'], **process(info['publisher']['pid']))
    try:
        body, _, _ = http(f'http://{SELF}:9997/v3/paths/list', timeout=5)
        paths = json.loads(body)
        info['mediamtx_paths'] = [dict(name=p.get('name'), ready=p.get('ready'), tracks=p.get('tracks'),
                                       source=(p.get('source') or {}).get('type'), bytes_received=p.get('bytesReceived'),
                                       readers=len(p.get('readers') or [])) for p in paths.get('items', [])]
    except Exception as error:
        info['mediamtx_paths_error'] = repr(error)
    return info


def publish_main():
    sc = frozen_scenario()
    spec = sc['video_sources'][ENV['SOURCE_ID']]
    image = WORK/'fixtures'/spec['image']
    frames = [scenario.frame(image, spec['seed'], k) for k in range(sc['stream']['loop_frames'])]
    print(json.dumps(dict(event='frames', sha256=[sha256(f.tobytes()) for f in frames])), flush=True)
    url = f"rtsp://{SELF}:{sc['stream']['port']}/{spec['path']}"
    deadline = time.monotonic() + 30
    while True:  # MediaMTX may still be starting; bounded retry, then the error ends this process.
        try:
            rtsp.publish(url, frames, sc['stream'], log=lambda e: print(json.dumps(e), flush=True))
        except Exception as error:
            print(json.dumps(dict(event='publish-error', error=repr(error))), flush=True)
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


# ---------------- text source ----------------

def text_source():
    sc = frozen_scenario()
    spec = sc['text_sources'][ENV['SOURCE_ID']]
    seqs = sorted(sc['inputs']['calibration'] + sc['inputs']['evaluation'])
    State.texts = {k: scenario.sentence(spec['seed'], k) for k in seqs}
    State.info = dict(seed=spec['seed'], generator='scenario.sentence (seeded templates)',
                      text_sha256={str(k): sha256(v.encode()) for k, v in State.texts.items()})


# ---------------- workers ----------------

def load_worker():
    import models
    task = ENV['TASK_ID']
    threads = int(ENV.get('THREADS', '1'))
    root = WORK/'models'
    names = json.loads((root/'class-names.json').read_text())
    start = time.perf_counter()
    if task == 'V':
        State.model = models.Detector(root/'yolo11n.onnx', threads)
        State.info = dict(model='yolo11n-onnx', model_sha256=file_sha(root/'yolo11n.onnx'))
    elif task == 'T':
        State.model = models.Embedder(str(root/'minilm'), threads, names)
        State.info = dict(model='all-MiniLM-L6-v2-onnx', model_sha256=file_sha(root/'minilm/onnx/model.onnx'),
                          tokenizer_sha256=file_sha(root/'minilm/tokenizer.json'))
    elif task == 'F':
        State.info = dict(model='deterministic-fusion', model_sha256=file_sha(HERE/'models.py'))
    else:
        raise ValueError('Unknown task '+task)
    State.names = names
    State.info.update(load_ms=(time.perf_counter()-start)*1000, threads=threads)


def infer(body):
    import models
    task = ENV['TASK_ID']
    payload, headers, fetch_ms = http(f"{body['source_url']}{'/frame' if task == 'V' else '/text'}?seq={body['seq']}")
    source = {k.lower(): v for k, v in headers.items() if k.lower().startswith('x-source-')}
    if task == 'V':
        rgb, decode_ms, _ = models.timed(models.decode_png, payload)
        output, compute_ms, cpu_ms = models.timed(State.model, rgb)
        input_sha = sha256(rgb.tobytes())
    else:
        text = json.loads(payload)['text']
        decode_ms = 0.0
        output, compute_ms, cpu_ms = models.timed(State.model, text)
        input_sha = sha256(text.encode())
    encoded = models.dumps(output)
    with State.lock:
        State.outputs[body['request_id']+'/'+task] = output
    receipt = dict(identity(), request_id=body['request_id'], seq=body['seq'], model_sha256=State.info['model_sha256'],
                   input_bytes=len(payload), input_payload_sha256=sha256(payload), input_sha256=input_sha, source=source,
                   fetch_ms=fetch_ms, decode_ms=decode_ms, compute_ms=compute_ms, compute_cpu_ms=cpu_ms,
                   output_sha256=sha256(encoded), output_bytes=len(encoded), device='cpu')
    return dict(receipt=receipt, output=output if task == 'V' else dict(similarities=output['similarities'],
                text_top_class=output['text_top_class']))


def run(body):
    """F: call V and T concurrently, then fuse. Call durations are monotonic on F's host."""
    import models

    def call(task):
        part = body[{'V': 'video', 'T': 'text'}[task]]
        raw, _, ms = http(part['endpoint']+'/infer', dict(seq=body['seq'], source_url=part['source_url'],
                                                         request_id=body['request_id']), timeout=120)
        return task, json.loads(raw), ms, len(raw)
    with ThreadPoolExecutor(2) as pool:
        replies = {task: (reply, ms, size) for task, reply, ms, size in pool.map(call, ['V', 'T'])}
    detections, text = replies['V'][0]['output'], replies['T'][0]['output']
    result, fuse_ms, cpu_ms = models.timed(models.fuse, detections, text, State.names)
    encoded = models.dumps(result)
    with State.lock:
        State.outputs[body['request_id']+'/F'] = result
    receipt = dict(identity(), request_id=body['request_id'], seq=body['seq'], model_sha256=State.info['model_sha256'],
                   input_sha256={t: replies[t][0]['receipt']['output_sha256'] for t in 'VT'},
                   call_ms={t: replies[t][1] for t in 'VT'}, response_bytes={t: replies[t][2] for t in 'VT'},
                   compute_ms=fuse_ms, compute_cpu_ms=cpu_ms, output_sha256=sha256(encoded), output_bytes=len(encoded), device='cpu')
    return dict(result=result, receipts=[replies['V'][0]['receipt'], replies['T'][0]['receipt'], receipt])


def fuse(body):
    """F qualification: timed fusion of recorded V/T outputs (no upstream calls)."""
    import models
    result, ms, cpu_ms = models.timed(models.fuse, body['detections'], body['text'], State.names)
    return dict(receipt=dict(identity(), request_id=body['request_id'], model_sha256=State.info['model_sha256'], compute_ms=ms,
                             compute_cpu_ms=cpu_ms, output_sha256=sha256(models.dumps(result)), device='cpu'), output=result)


def probe(body):
    """Pull timing at this receiver: repeated GETs of a URL; bytes and monotonic ms per attempt."""
    rows = []
    for _ in range(body.get('repeats', 3)):
        try:
            payload, _, ms = http(body['url'], timeout=body.get('timeout_s', 30))
            rows.append(dict(status='ok', ms=ms, bytes=len(payload), sha256=sha256(payload)))
        except Exception as error:
            rows.append(dict(status='error', error=repr(error)))
    return dict(identity(), url=body['url'], rows=rows)


def rtsp_probe(body):
    start = time.monotonic()
    try:
        return dict(identity(), status='ok', **rtsp.read(body['url'], wanted=body.get('wanted'), max_frames=body.get('max_frames', 40),
                                                       timeout_s=body.get('timeout_s', 20)))
    except Exception as error:
        return dict(identity(), status='error', url=body['url'], error=repr(error), elapsed_ms=(time.monotonic()-start)*1000)


# ---------------- HTTP ----------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def reply(self, code, body, kind='application/json', headers=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        query = dict(urllib.parse.parse_qsl(url.query))
        role = ENV['ROLE']
        try:
            if url.path == '/health':
                return self.reply(200 if State.ready else 503, dict(identity(), ready=State.ready, error=State.error, info=State.info))
            if not State.ready:
                return self.reply(503, dict(error='not ready'))
            if url.path == '/payload':
                return self.reply(200, bytes(int(query['bytes'])), 'application/octet-stream')
            if url.path == '/evidence':
                return self.reply(200, dict(identity(), **(video_evidence() if role == 'video-source' else State.info)))
            if url.path == '/frame' and role == 'video-source':
                png = State.frames[int(query['seq'])]
                return self.reply(200, png, 'image/png', {'X-Source-Node': ENV.get('NODE_NAME'), 'X-Source-Pod-Uid': ENV.get('POD_UID'),
                                                          'X-Source-Id': ENV['SOURCE_ID'], 'X-Source-Seq': query['seq']})
            if url.path == '/text' and role == 'text-source':
                seq = int(query['seq'])
                text = State.texts[seq]
                return self.reply(200, dict(text=text, seq=seq, sha256=sha256(text.encode())), headers={
                    'X-Source-Node': ENV.get('NODE_NAME'), 'X-Source-Pod-Uid': ENV.get('POD_UID'),
                    'X-Source-Id': ENV['SOURCE_ID'], 'X-Source-Seq': str(seq)})
            if url.path == '/outputs':
                with State.lock:
                    return self.reply(200, {k: v for k, v in State.outputs.items() if k.startswith(query['request_id']+'/')})
            return self.reply(404, dict(error='unknown path'))
        except Exception as error:
            return self.reply(500, dict(error=repr(error)))

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or b'{}')
        routes = dict(infer=infer, run=run, fuse=fuse, probe=probe, batch=batch, drive=drive_many)
        routes['rtsp-probe'] = rtsp_probe
        name = self.path.strip('/')
        try:
            if not State.ready or name not in routes or (ENV['ROLE'] != 'worker' and name not in ('probe', 'batch', 'drive')):
                return self.reply(404 if State.ready else 503, dict(error='unavailable'))
            return self.reply(200, routes[name](body))
        except Exception as error:
            return self.reply(500, dict(identity(), error=repr(error)))


def serve():
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    role = ENV['ROLE']
    try:
        {'video-source': video_source, 'text-source': text_source, 'worker': load_worker, 'controller': lambda: None}[role]()
        State.ready = True
    except Exception as error:
        State.error = repr(error)
        print(json.dumps(dict(event='startup-error', error=repr(error))), flush=True)
    print(json.dumps(dict(event='serving', ready=State.ready, **identity())), flush=True)
    threading.Event().wait()


def drive(control):
    """One workflow from this Pod: POST F/run, then fetch stage outputs untimed. Returns one row."""
    row = dict(identity(), request_id=control['request_id'], seq=control['seq'])
    try:
        raw, _, e2e_ms = http(control['F']+'/run', dict(request_id=control['request_id'], seq=control['seq'],
            video=dict(endpoint=control['V'], source_url=control['video_source']),
            text=dict(endpoint=control['T'], source_url=control['text_source'])), timeout=control.get('timeout_s', 120))
        reply = json.loads(raw)
        if 'error' in reply:
            raise RuntimeError(reply['error'])
        outputs = {}
        for task in 'VTF':
            body, _, _ = http(f"{control[task]}/outputs?request_id={urllib.parse.quote(control['request_id'])}", timeout=30)
            outputs.update(json.loads(body))
        row.update(status='ok', e2e_ms=e2e_ms, result_bytes=len(raw), result=reply['result'], receipts=reply['receipts'],
                   outputs=outputs, scope='E2E monotonic on the driver host: F request to F response, includes V/T fetch')
    except Exception as error:
        row.update(status='error', error=repr(error))
    return row


def drive_many(controls):
    """POST /drive: sequential workflows driven from this Pod (the destination edge)."""
    return [drive(c) for c in controls]


def batch(calls):
    """POST /batch: several POSTs to this Pod's own endpoints, in order; errors kept in place."""
    rows = []
    for call in calls:
        try:
            body, _, ms = http(f"http://{SELF}:{PORT}{call['path']}", call['body'], timeout=call.get('timeout_s', 120))
            rows.append(dict(tag=call.get('tag'), status='ok', call_ms=ms, reply_bytes=len(body), reply=json.loads(body)))
        except Exception as error:
            rows.append(dict(tag=call.get('tag'), status='error', error=repr(error)))
    return rows


def main(argv):
    command = argv[0] if argv else 'serve'
    if command == 'serve':
        serve()
    elif command == 'publish':
        publish_main()
    elif command == 'drive':
        print(json.dumps(drive(json.loads(argv[1]))), flush=True)
    elif command == 'remote':
        # Controller: one HTTP call to another Pod; a list reply becomes one JSON line per item.
        body, _, ms = http(argv[1], json.loads(argv[2]) if len(argv) > 2 else None, timeout=float(ENV.get('REMOTE_TIMEOUT_S', '900')))
        reply = json.loads(body)
        for item in reply if isinstance(reply, list) else [reply]:
            print(json.dumps(item), flush=True)
    elif command == 'frames':
        # Controller: frozen decoded PNGs of one video source, base64 JSON, for the local CPU reference.
        out = {}
        for seq in json.loads(argv[2]):
            body, _, _ = http(f'{argv[1]}/frame?seq={seq}', timeout=60)
            out[str(seq)] = base64.b64encode(body).decode()
        print(json.dumps(out), flush=True)
    else:
        raise SystemExit('unknown command')


if __name__ == '__main__':
    main(sys.argv[1:])
