"""Bounded E0 profiling: actual received tensors, common HTTP/1.1 contract."""

import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import threading
import time
import uuid
from urllib.parse import urlparse

from probe import MODEL_URL, MODEL_SHA256, IMAGE_URL, IMAGE_SHA256, fetch, emit
from transport import preprocess, TENSOR_BYTES, TENSOR_SHA256, OUTPUT_BYTES


def serve():
    import numpy as np
    import onnxruntime as ort

    backend, node = os.environ["BACKEND"], os.environ["NODE_NAME"]
    assert backend in {"cpu", "gpu", "intel_npu"}
    work = Path(os.environ.get("WORKDIR", "/work"))
    model_path = work / "model.onnx"
    model_path.write_bytes(fetch(MODEL_URL, MODEL_SHA256))
    fixture = preprocess(fetch(IMAGE_URL, IMAGE_SHA256))
    assert hashlib.sha256(fixture.tobytes()).hexdigest() == TENSOR_SHA256
    options = ort.SessionOptions()
    options.intra_op_num_threads = options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
    reference = session.run(None, {session.get_inputs()[0].name: fixture})[0].reshape(-1)
    if backend == "gpu":
        from accelerator_inference import gpu_backend
        infer = gpu_backend(model_path, fixture)
    elif backend == "intel_npu":
        from accelerator_inference import npu_backend
        infer = npu_backend(model_path, fixture)
    else:
        def infer(tensor):
            return session.run(None, {session.get_inputs()[0].name: tensor})[0].reshape(-1), {"execution_device": "CPU"}
    for _ in range(2):
        scores, evidence = infer(fixture)
        assert np.argsort(scores)[-5:][::-1].tolist() == [258, 259, 157, 279, 152]
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection.settimeout(15)
            self.connection_id = uuid.uuid4().hex

        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path != "/healthz":
                self.send_error(404)
                return
            body = json.dumps({"node": node, "backend": backend,
                               "model_sha256": MODEL_SHA256, "evidence": evidence}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/infer":
                self.send_error(404)
                return
            try:
                nodelay = int(self.headers.get("X-TCP-NoDelay", "1"))
                if nodelay not in {0, 1} or int(self.headers.get("Content-Length", "0")) != TENSOR_BYTES:
                    raise ValueError("Invalid transport setting or payload length")
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, nodelay)
                actual_nodelay = self.connection.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
                read_started = time.perf_counter()
                data = self.rfile.read(TENSOR_BYTES)
                read_ms = (time.perf_counter() - read_started) * 1000
                validated = time.perf_counter()
                digest = hashlib.sha256(data).hexdigest()
                if len(data) != TENSOR_BYTES or digest != TENSOR_SHA256 or self.headers.get("X-Tensor-SHA256") != digest:
                    raise ValueError("Tensor integrity check failed")
                tensor = np.frombuffer(data, dtype="<f4").reshape(1, 3, 224, 224).copy()
                if not np.isfinite(tensor).all():
                    raise ValueError("Non-finite tensor")
                decode_ms = (time.perf_counter() - validated) * 1000
                queued = time.perf_counter()
                with lock:
                    queue_ms = (time.perf_counter() - queued) * 1000
                    started = time.perf_counter()
                    scores, device_evidence = infer(tensor)
                    infer_ms = (time.perf_counter() - started) * 1000
                encoded = time.perf_counter()
                scores = np.asarray(scores, dtype="<f4").reshape(-1)
                assert scores.shape == (1000,) and np.isfinite(scores).all()
                assert np.argsort(scores)[-5:][::-1].tolist() == [258, 259, 157, 279, 152]
                difference = scores.astype(np.float64) - reference
                body = scores.tobytes()
                output_digest = hashlib.sha256(body).hexdigest()
                encode_ms = (time.perf_counter() - encoded) * 1000
                metrics = {"read_ms": read_ms, "decode_validate_ms": decode_ms,
                           "queue_ms": queue_ms, "backend_blocking_ms": infer_ms,
                           "encode_validate_ms": encode_ms, **device_evidence}
                request_id = self.headers.get("X-Request-ID", "")
                headers = {"Content-Length": str(len(body)), "Content-Type": "application/octet-stream",
                           "X-Request-ID": request_id, "X-Server-Node": node, "X-Backend": backend,
                           "X-Model-SHA256": MODEL_SHA256, "X-Input-SHA256": digest,
                           "X-Output-SHA256": output_digest, "X-Connection-ID": self.connection_id,
                           "X-Server-NoDelay": str(actual_nodelay), "X-Metrics": json.dumps(metrics),
                           "X-Max-Error": str(float(np.max(np.abs(difference))))}
                self.send_response(200)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)
                emit("served", request_id=request_id, connection_id=self.connection_id,
                     node=node, backend=backend, server_nodelay=actual_nodelay,
                     input_sha256=digest, output_sha256=output_digest,
                     input_bytes=len(data), output_bytes=len(body), metrics=metrics)
            except (ValueError, TimeoutError) as error:
                emit("request_error", error=str(error))
                self.send_error(400, "Invalid fixture request")

    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler)
    emit("server_ready", node=node, backend=backend, port=server.server_address[1],
         model_sha256=MODEL_SHA256, evidence=evidence)
    server.serve_forever()


def client():
    import numpy as np

    targets = {t["alias"]: t for t in json.loads(os.environ["TARGETS_JSON"])}
    schedule = json.loads(os.environ["SCHEDULE_JSON"])
    image = fetch(IMAGE_URL, IMAGE_SHA256)
    node, block = os.environ["NODE_NAME"], int(os.environ["BLOCK"])
    emit("client_environment", node=node, block=block, numpy=np.__version__,
         schedule=schedule, interval_seconds=0.2, warmup=2, measured=10)
    for target in targets.values():
        url = urlparse(target["url"])
        connection = http.client.HTTPConnection(url.hostname, url.port, timeout=15)
        connection.request("GET", "/healthz", headers={"Connection": "close"})
        response = connection.getresponse()
        health = json.loads(response.read())
        connection.close()
        assert response.status == 200 and health["node"] == target["node"]
        assert health["backend"] == target["backend"] and health["model_sha256"] == MODEL_SHA256
        emit("route", target=target, health=health)
    total = 0
    for condition in schedule:
        target = targets[condition["target"]]
        reuse, nodelay = condition["reuse"], condition["nodelay"]
        url = urlparse(target["url"])
        connection, reused_id, previous_id = None, None, None
        for iteration in range(12):
            request_id = f"{os.environ['RUN_ID']}-b{block}-{condition['id']}-{iteration}"
            started = time.perf_counter()
            tensor = preprocess(image)
            preprocessed = time.perf_counter()
            body = tensor.tobytes()
            digest = hashlib.sha256(body).hexdigest()
            assert digest == TENSOR_SHA256 and len(body) == TENSOR_BYTES
            serialized = time.perf_counter()
            connect_ms = 0.0
            if connection is None:
                connecting = time.perf_counter()
                connection = http.client.HTTPConnection(url.hostname, url.port, timeout=15)
                connection.connect()
                connection.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, nodelay)
                connect_ms = (time.perf_counter() - connecting) * 1000
            active_socket = connection.sock
            actual_nodelay = active_socket.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
            local_address, peer_address = active_socket.getsockname(), active_socket.getpeername()
            assert actual_nodelay == nodelay
            rpc_started = time.perf_counter()
            connection.request("POST", "/infer", body,
                               {"X-Request-ID": request_id, "X-Tensor-SHA256": digest,
                                "X-TCP-NoDelay": str(nodelay),
                                "Connection": "keep-alive" if reuse else "close"})
            response = connection.getresponse()
            assert response.status == 200 and int(response.getheader("Content-Length")) == OUTPUT_BYTES
            output = response.read()
            rpc_ended = time.perf_counter()
            assert len(output) == OUTPUT_BYTES
            assert response.getheader("X-Request-ID") == request_id
            assert response.getheader("X-Server-Node") == target["node"]
            assert response.getheader("X-Backend") == target["backend"]
            assert response.getheader("X-Model-SHA256") == MODEL_SHA256
            assert response.getheader("X-Input-SHA256") == digest
            assert response.getheader("X-Output-SHA256") == hashlib.sha256(output).hexdigest()
            assert int(response.getheader("X-Server-NoDelay")) == nodelay
            connection_id = response.getheader("X-Connection-ID")
            assert connection_id
            if reuse:
                assert connection.sock is active_socket
                reused_id = reused_id or connection_id
                assert connection_id == reused_id
            else:
                assert connection_id != previous_id
            previous_id = connection_id
            scores = np.frombuffer(output, dtype="<f4")
            assert np.isfinite(scores).all()
            top5 = np.argsort(scores)[-5:][::-1].tolist()
            assert top5 == [258, 259, 157, 279, 152]
            metrics = json.loads(response.getheader("X-Metrics"))
            finished = time.perf_counter()
            emit("request", request_id=request_id, block=block, condition=condition,
                 iteration=iteration, warmup=iteration < 2, source_node=node,
                 inference_node=target["node"], backend=target["backend"],
                 input_bytes=len(body), output_bytes=len(output), input_sha256=digest,
                 output_sha256=hashlib.sha256(output).hexdigest(), top5=top5,
                 client_nodelay=actual_nodelay, server_nodelay=int(response.getheader("X-Server-NoDelay")),
                 connection_id=connection_id, local_address=local_address, peer_address=peer_address,
                 preprocess_ms=(preprocessed-started)*1000,
                 serialization_hash_ms=(serialized-preprocessed)*1000,
                 connect_ms=connect_ms, rpc_wall_ms=(rpc_ended-rpc_started)*1000,
                 postprocess_validate_ms=(finished-rpc_ended)*1000,
                 workflow_wall_ms=(finished-started)*1000, server_metrics=metrics,
                 max_abs_error_vs_server_cpu=float(response.getheader("X-Max-Error")),
                 pacing_overrun=(finished-started)>0.2)
            total += 1
            if not reuse:
                connection.close()
                connection = None
            time.sleep(max(0, 0.2-(time.perf_counter()-started)))
        if connection is not None:
            connection.close()
    emit("client_complete", block=block, requests=total, measured=total*10//12,
         scope="paced_single_fixture_transport_profile_not_policy_benchmark")


if __name__ == "__main__":
    if os.environ["MODE"] == "server":
        serve()
    elif os.environ["MODE"] == "client":
        client()
    else:
        raise ValueError("Invalid MODE")
