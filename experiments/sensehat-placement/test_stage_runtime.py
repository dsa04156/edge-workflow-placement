"""Slice B: real multi-process stdlib HTTP chain source -> T1 -> T2 -> T3 with receipts."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

import forecast
import stage_runtime as sr
from test_forecast import make_rows, ramp, write_root

HERE = Path(__file__).resolve().parent


def synthetic_prepared(tmp):
    root = Path(tmp) / 'history'
    write_root(root, {'temp_humidity': make_rows(ramp(200, 0.01, 40.0), 'temp_humidity', '°C', uid='u-t'),
                      'humidity': make_rows(ramp(200, -0.02, 25.0), 'humidity', '%RH', uid='u-h'),
                      'pressure': make_rows([1024.5 + (i % 7) * 0.01 for i in range(200)], 'pressure', 'hPa', uid='u-p')})
    return forecast.prepare(root, Path(tmp) / 'prepared')


def free_port(host):
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def post(url, body, headers=None, timeout=10):
    req = urllib.request.Request(url, body, headers or {'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b'{}')


class Servers:
    """Each role in its own OS process on its own loopback address, like separate Pods."""

    def __init__(self, config_root, roles):
        self.procs, self.urls = [], {}
        for i, role in enumerate(roles):
            host = f'127.0.0.{10 + i}'
            port = free_port(host)
            env = dict(os.environ, TASK_ID=role, NODE_NAME='node-' + role, POD_UID='uid-' + role,
                       CONFIG_ROOT=str(config_root), BIND_HOST=host, PORT=str(port))
            self.procs.append(subprocess.Popen([sys.executable, str(HERE / 'stage_runtime.py'), 'serve'], env=env,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            self.urls[role] = f'http://{host}:{port}'
        for url in self.urls.values():
            deadline = time.monotonic() + 10
            while True:
                try:
                    if post(url + '/health', b'{}')[0] == 200:
                        break
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    self.close()
                    raise RuntimeError('server did not start: ' + url)
                time.sleep(0.05)

    def close(self):
        for proc in self.procs:
            proc.kill()
            proc.wait()


class StageHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.prepared = synthetic_prepared(cls.tmp.name)
        cls.servers = Servers(cls.prepared, ['source', 't1', 't2', 't3'])
        cls.windows = json.loads((cls.prepared / 'windows.json').read_text())['windows']
        cls.reference = json.loads((cls.prepared / 'reference.json').read_text())['windows']
        cls.model_sha = json.loads((cls.prepared / 'prepared-manifest.json').read_text())['model_sha256']

    @classmethod
    def tearDownClass(cls):
        cls.servers.close()
        cls.tmp.cleanup()

    def run_window(self, wid, chain=None):
        u = self.servers.urls
        control = {'run_id': 'test-run', 'request_id': 'r-' + wid, 'window_id': wid,
                   'chain': chain or [u['t1'], u['t2'], u['t3']]}
        out = subprocess.run([sys.executable, str(HERE / 'stage_runtime.py'), 'client', '--url', u['source'] + '/run',
                              '--timeout', '20'], input=json.dumps(control).encode(), capture_output=True, timeout=30)
        return out.returncode, json.loads(out.stdout)

    def test_chain_matches_local_reference_with_hash_linked_receipts(self):
        for wid, window in self.windows.items():
            code, reply = self.run_window(wid)
            self.assertEqual((code, reply['status']), (0, 'ok'), reply)
            receipts = {r['task']: r for r in reply['receipts']}
            self.assertEqual(set(receipts), {'t1', 't2', 't3'})
            self.assertEqual(receipts['t1']['input_sha256'], window['payload_sha256'])
            self.assertEqual(receipts['t2']['input_sha256'], receipts['t1']['output_sha256'])
            self.assertEqual(receipts['t3']['input_sha256'], receipts['t2']['output_sha256'])
            self.assertEqual(receipts['t3']['output_sha256'], self.reference[wid]['t3']['output_sha256'])
            for task, receipt in receipts.items():
                self.assertEqual(receipt['node'], 'node-' + task)
                self.assertEqual(receipt['pod_uid'], 'uid-' + task)
                self.assertEqual(receipt['model_sha256'], self.model_sha)
                self.assertEqual(receipt['source_sha256'], self.reference[wid]['t1']['output']['source_sha256'])
                self.assertGreaterEqual(receipt['handler_total_ms'], receipt['compute_ms'] + receipt['downstream_rpc_ms'])
            self.assertEqual(receipts['t3']['downstream_rpc_ms'], 0)
            self.assertGreater(receipts['t1']['downstream_rpc_ms'], receipts['t2']['downstream_rpc_ms'])
            self.assertEqual(reply['source']['node'], 'node-source')
            self.assertEqual(reply['source']['payload_sha256'], window['payload_sha256'])
            self.assertGreaterEqual(reply['source']['e2e_ms'], receipts['t1']['handler_total_ms'])
            self.assertEqual(reply['result'], self.reference[wid]['t3']['output'])
            self.assertNotIn('target', json.dumps(reply))

    def test_downstream_failure_is_reported_once_without_retry(self):
        u = self.servers.urls
        dead = 'http://127.0.0.1:' + str(free_port('127.0.0.1'))
        wid = next(iter(self.windows))
        code, reply = self.run_window(wid, [u['t1'], dead, u['t3']])
        self.assertEqual(reply['status'], 'error')
        self.assertEqual(reply['attempts'], 1)
        self.assertEqual([r['task'] for r in reply['receipts']], ['t1'])
        self.assertIn('downstream', reply['error'])

    def test_target_field_in_payload_is_rejected(self):
        wid, window = next(iter(self.windows.items()))
        rows = [dict(r) for r in window['payload']['rows']]
        rows[-1]['target'] = 1.0
        body = json.dumps({'run_id': 'x', 'request_id': 'y', 'chain': [], 'data': {'rows': rows}}).encode()
        code, reply = post(self.servers.urls['t1'] + '/stage', body)
        self.assertEqual(code, 400)
        self.assertIn('extra', reply['error'])

    def test_gap_window_is_rejected(self):
        wid, window = next(iter(self.windows.items()))
        rows = list(window['payload']['rows'])
        rows[4] = self.windows[[w for w in self.windows if w != wid][0]]['payload']['rows'][4]
        body = json.dumps({'run_id': 'x', 'request_id': 'y', 'chain': [], 'data': {'rows': rows}}).encode()
        code, reply = post(self.servers.urls['t1'] + '/stage', body)
        self.assertEqual(code, 400)

    def test_oversized_body_is_refused(self):
        body = b'{"pad":"' + b'x' * (sr.MAX_BODY + 10) + b'"}'
        code, reply = post(self.servers.urls['t1'] + '/stage', body)
        self.assertEqual(code, 413)

    def test_health_reports_backend_identity(self):
        code, reply = post(self.servers.urls['t2'] + '/health', b'{}')
        self.assertEqual(code, 200)
        self.assertEqual(reply['task'], 't2')
        self.assertEqual(reply['backend']['impl'], 'python-stdlib-cpu')
        self.assertEqual(reply['model_sha256'], self.model_sha)


if __name__ == '__main__':
    unittest.main()
