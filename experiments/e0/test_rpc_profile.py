"""Integration checks at the previously used /healthz and /infer HTTP boundary."""

import hashlib
import http.client
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import unittest

from probe import IMAGE_URL, IMAGE_SHA256, fetch
from transport import preprocess


class RPCContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.stderr = open(Path(cls.directory.name) / "stderr.log", "w+")
        env = dict(os.environ, MODE="server", BACKEND="cpu", NODE_NAME="local",
                   WORKDIR=cls.directory.name, PORT="0", PYTHONDONTWRITEBYTECODE="1")
        cls.process = subprocess.Popen([sys.executable, "-u", str(Path(__file__).with_name("rpc_profile.py"))],
                                       env=env, stdout=subprocess.PIPE, stderr=cls.stderr, text=True)
        selector = selectors.DefaultSelector()
        selector.register(cls.process.stdout, selectors.EVENT_READ)
        while selector.select(timeout=60):
            line = cls.process.stdout.readline()
            if not line:
                break
            record = json.loads(line)
            if record["kind"] == "server_ready":
                cls.port = record["port"]
                cls.body = preprocess(fetch(IMAGE_URL, IMAGE_SHA256)).tobytes()
                selector.close()
                return
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.stderr.seek(0)
        raise AssertionError("Server did not become ready: " + cls.stderr.read()[-2000:])

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.process.stdout.close()
        cls.stderr.close()
        cls.directory.cleanup()

    def test_reused_connection_returns_valid_inference_receipts(self):
        import numpy as np

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        self.assertEqual(json.loads(response.read())["backend"], "cpu")
        receipts = []
        for request_id in ["first", "second"]:
            connection.request("POST", "/infer", self.body,
                               {"X-Tensor-SHA256": hashlib.sha256(self.body).hexdigest(),
                                "X-Request-ID": request_id, "X-TCP-NoDelay": "1"})
            response = connection.getresponse()
            output = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(len(output), 4000)
            self.assertEqual(response.getheader("X-Request-ID"), request_id)
            self.assertEqual(response.getheader("X-Server-NoDelay"), "1")
            self.assertEqual(np.argsort(np.frombuffer(output, dtype="<f4"))[-5:][::-1].tolist(),
                             [258, 259, 157, 279, 152])
            receipts.append(response.getheader("X-Connection-ID"))
        self.assertTrue(receipts[0])
        self.assertEqual(receipts[0], receipts[1])
        connection.close()

    def test_invalid_payloads_return_400_and_close_connection(self):
        for body, digest in [(b"x", "invalid"), (self.body, "0" * 64)]:
            with self.subTest(length=len(body)):
                connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
                connection.request("POST", "/infer", body, {"X-Tensor-SHA256": digest})
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 400)
                self.assertEqual(response.getheader("Connection"), "close")
                connection.close()

    def test_profile_client_checks_new_and_reused_transport(self):
        targets = [{"alias": "local", "node": "local", "backend": "cpu",
                    "url": f"http://127.0.0.1:{self.port}"}]
        schedule = [{"id": "new0", "target": "local", "reuse": False, "nodelay": 0},
                    {"id": "reuse1", "target": "local", "reuse": True, "nodelay": 1}]
        env = dict(os.environ, MODE="client", NODE_NAME="local", BLOCK="0", RUN_ID="integration",
                   TARGETS_JSON=json.dumps(targets), SCHEDULE_JSON=json.dumps(schedule))
        result = subprocess.run([sys.executable, "-u", str(Path(__file__).with_name("rpc_profile.py"))],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(records[-1]["kind"], "client_complete")
        self.assertEqual(records[-1]["requests"], 24)


if __name__ == "__main__":
    unittest.main()
