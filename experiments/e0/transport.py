"""Three-task CPU fixture across two Pods using an internal HTTP Service.

HTTP wall time is an application round trip, never one-way network latency.
"""

import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
from pathlib import Path
import socket
import time
import urllib.parse
import urllib.request

from probe import MODEL_URL, MODEL_SHA256, IMAGE_URL, IMAGE_SHA256, fetch, emit


TENSOR_BYTES = 602112
OUTPUT_BYTES = 4000
TENSOR_SHA256 = "791a0f0caae0b9e4a81d7ad9fe3db67514bc2f67fbff71fb4d29f6cd66ff011d"
NODE = os.environ.get("NODE_NAME", "unknown")


def preprocess(image_bytes):
    import numpy as np
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    scale = 256 / min(image.size)
    image = image.resize((round(image.width * scale), round(image.height * scale)),
                         Image.Resampling.BILINEAR)
    left, top = (image.width - 224) // 2, (image.height - 224) // 2
    image = image.crop((left, top, left + 224, top + 224))
    tensor = np.asarray(image, dtype=np.float32).transpose(2, 0, 1) / 255
    tensor = (tensor - np.array([.485, .456, .406], dtype=np.float32)[:, None, None])
    tensor /= np.array([.229, .224, .225], dtype=np.float32)[:, None, None]
    return np.ascontiguousarray(tensor[None], dtype="<f4")


def serve():
    import numpy as np
    import onnxruntime as ort

    model = Path("/work/model.onnx")
    model.write_bytes(fetch(MODEL_URL, MODEL_SHA256))
    options = ort.SessionOptions()
    options.intra_op_num_threads = options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(model), sess_options=options,
                                   providers=["CPUExecutionProvider"])
    assert session.get_providers() == ["CPUExecutionProvider"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path != "/healthz":
                self.send_error(404)
                return
            body = json.dumps({"node": NODE, "model_sha256": MODEL_SHA256,
                               "providers": session.get_providers()}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/infer":
                self.send_error(404)
                return
            self.connection.settimeout(15)
            try:
                if int(self.headers.get("Content-Length", "0")) != TENSOR_BYTES:
                    raise ValueError("Unexpected payload length")
                read_started = time.perf_counter()
                data = self.rfile.read(TENSOR_BYTES)
                read_ms = (time.perf_counter() - read_started) * 1000
                validation_started = time.perf_counter()
                if len(data) != TENSOR_BYTES:
                    raise ValueError("Truncated tensor")
                digest = hashlib.sha256(data).hexdigest()
                if digest != TENSOR_SHA256 or self.headers.get("X-Tensor-SHA256") != digest:
                    raise ValueError("Tensor integrity check failed")
                tensor = np.frombuffer(data, dtype="<f4").reshape(1, 3, 224, 224)
                if not np.isfinite(tensor).all():
                    raise ValueError("Non-finite tensor")
                validation_ms = (time.perf_counter() - validation_started) * 1000
                inference_started = time.perf_counter()
                output = session.run(None, {session.get_inputs()[0].name: tensor})[0]
                inference_ms = (time.perf_counter() - inference_started) * 1000
                encode_started = time.perf_counter()
                scores = np.asarray(output, dtype="<f4").reshape(-1)
                if scores.size != 1000 or not np.isfinite(scores).all():
                    raise ValueError("Invalid inference output")
                body = scores.tobytes()
                output_digest = hashlib.sha256(body).hexdigest()
                encode_ms = (time.perf_counter() - encode_started) * 1000
                headers = {"Content-Type": "application/octet-stream",
                           "Content-Length": str(len(body)), "X-Server-Node": NODE,
                           "X-Request-ID": self.headers.get("X-Request-ID", ""),
                           "X-Model-SHA256": MODEL_SHA256, "X-Input-SHA256": digest,
                           "X-Output-SHA256": output_digest, "X-Read-Ms": str(read_ms),
                           "X-Validation-Ms": str(validation_ms),
                           "X-Infer-Ms": str(inference_ms), "X-Encode-Ms": str(encode_ms)}
                self.send_response(200)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)
                emit("served", request_id=self.headers.get("X-Request-ID"),
                     node=NODE, input_bytes=len(data), input_sha256=digest,
                     output_bytes=len(body), output_sha256=output_digest,
                     read_ms=read_ms, validation_ms=validation_ms,
                     inference_ms=inference_ms, encode_ms=encode_ms)
            except (ValueError, TimeoutError) as error:
                emit("request_error", error=str(error))
                self.send_error(400, "Invalid E0 request")

    server = HTTPServer(("0.0.0.0", 8080), Handler)
    emit("server_ready", node=NODE, model_sha256=MODEL_SHA256,
         providers=session.get_providers())
    server.serve_forever()


def client():
    import numpy as np

    # Internal Service traffic must not be redirected to an ambient HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    image_bytes = fetch(IMAGE_URL, IMAGE_SHA256)
    targets = json.loads(os.environ["TARGETS_JSON"])
    warmup, measured = 2, 5
    for target in targets:
        hostname = urllib.parse.urlparse(target["url"]).hostname
        addresses = sorted({x[4][0] for x in socket.getaddrinfo(hostname, 8080)})
        with opener.open(target["url"] + "/healthz", timeout=15) as response:
            health = json.load(response)
        assert health["node"] == target["node"]
        assert health["model_sha256"] == MODEL_SHA256
        assert health["providers"] == ["CPUExecutionProvider"]
        emit("route", source_node=NODE, target=target, resolved_addresses=addresses)
    for iteration in range(warmup + measured):
        order = targets if iteration % 2 == 0 else list(reversed(targets))
        for target in order:
            request_id = f"{os.environ['RUN_ID']}-{os.environ['CLIENT_ALIAS']}-{iteration}-{target['alias']}"
            started = time.perf_counter()
            tensor = preprocess(image_bytes)
            preprocessed = time.perf_counter()
            body = tensor.tobytes()
            digest = hashlib.sha256(body).hexdigest()
            assert len(body) == TENSOR_BYTES and digest == TENSOR_SHA256
            serialized = time.perf_counter()
            request = urllib.request.Request(
                target["url"] + "/infer", data=body, method="POST",
                headers={"Content-Type": "application/octet-stream",
                         "X-Tensor-SHA256": digest, "X-Request-ID": request_id})
            rpc_started = time.perf_counter()
            with opener.open(request, timeout=15) as response:
                output = response.read(OUTPUT_BYTES + 1)
                headers = response.headers
            rpc_finished = time.perf_counter()
            assert len(output) == OUTPUT_BYTES
            assert headers["X-Server-Node"] == target["node"]
            assert headers["X-Request-ID"] == request_id
            assert headers["X-Model-SHA256"] == MODEL_SHA256
            assert headers["X-Input-SHA256"] == digest
            assert hashlib.sha256(output).hexdigest() == headers["X-Output-SHA256"]
            scores = np.frombuffer(output, dtype="<f4")
            assert np.isfinite(scores).all()
            top5 = np.argsort(scores)[-5:][::-1].tolist()
            assert top5 == [258, 259, 157, 279, 152]
            finished = time.perf_counter()
            emit("request", request_id=request_id, iteration=iteration,
                 warmup=iteration < warmup, source_node=NODE,
                 inference_node=target["node"], same_node=NODE == target["node"],
                 input_bytes=len(body), input_sha256=digest,
                 output_bytes=len(output), output_sha256=headers["X-Output-SHA256"],
                 top5=top5, preprocess_ms=(preprocessed - started) * 1000,
                 serialization_and_hash_ms=(serialized - preprocessed) * 1000,
                 rpc_wall_ms=(rpc_finished - rpc_started) * 1000,
                 server_read_ms=float(headers["X-Read-Ms"]),
                 server_validation_ms=float(headers["X-Validation-Ms"]),
                 server_inference_ms=float(headers["X-Infer-Ms"]),
                 server_encode_ms=float(headers["X-Encode-Ms"]),
                 response_validation_and_postprocess_ms=(finished - rpc_finished) * 1000,
                 workflow_wall_ms=(finished - started) * 1000)
    emit("client_complete", node=NODE, warmup_requests=warmup * len(targets),
         measured_requests=measured * len(targets),
         scope="two_pod_cpu_service_transport_pilot")


if __name__ == "__main__":
    if os.environ["MODE"] == "server":
        serve()
    elif os.environ["MODE"] == "client":
        client()
    else:
        raise ValueError("Unknown E0 mode")
