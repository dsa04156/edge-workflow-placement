"""HTTP seams agreed in cluster plan G3; diagnostic adapters, never AI results."""
import base64
from contextlib import ExitStack
import unittest
import json
import urllib.request
import threading
from concurrent.futures import ThreadPoolExecutor

from http_runtime import serve, request


def encode(data):
    return base64.b64encode(data).decode()


class RuntimeTests(unittest.TestCase):
    def test_acknowledged_workflows_do_not_consume_inflight_capacity(self):
        with serve('fusion', lambda inputs: b'answer', capacity=1) as fusion:
            for i in range(70):
                identity = {'run_id': 'ack-test', 'request_id': str(i)}
                request(fusion + '/open', identity)
                for task in ('vision', 'speech'):
                    from http_runtime import digest
                    request(fusion + '/join', dict(identity, task=task, data=b'x', receipt={
                        'task': task, 'output_sha256': digest(b'x'), 'output_bytes': 1}))
                self.assertEqual(request(fusion + '/result', identity)['status'], 'ok')
                self.assertEqual(request(fusion + '/ack', identity)['status'], 'released')

    def test_running_fusion_does_not_block_unrelated_admission(self):
        started, release = threading.Event(), threading.Event()
        def adapter(inputs):
            started.set()
            release.wait(5)
            return b'answer'
        with serve('fusion', adapter) as fusion, ThreadPoolExecutor() as pool:
            key = {'run_id': 'lock-test', 'request_id': 'first'}
            request(fusion + '/open', key)
            from http_runtime import digest
            def join(task):
                return request(fusion + '/join', dict(key, task=task, data=b'x', receipt={
                    'task': task, 'output_sha256': digest(b'x'), 'output_bytes': 1}))
            join('vision')
            pending = pool.submit(join, 'speech')
            self.assertTrue(started.wait(2))
            try:
                reply = request(fusion + '/open', {'run_id': 'lock-test', 'request_id': 'second'}, timeout=.5)
                self.assertEqual(reply['status'], 'registered')
            finally:
                release.set()
                pending.result()

    def test_binary_input_arrives_byte_exact_without_base64_expansion(self):
        payload = bytes(range(256)) * 4096
        with serve('vision', lambda data: str(len(data)).encode()) as url:
            reply = request(url + '/infer', {'run_id': 'binary', 'request_id': 'one', 'data': payload})
            self.assertEqual(base64.b64decode(reply['data']), b'1048576')
            self.assertEqual(reply['receipt']['input_bytes'], {'source': 1048576})

    def test_readiness_is_available_after_operator_initialization(self):
        with serve('vision', lambda data: data) as url:
            with urllib.request.urlopen(url + '/health') as response:
                self.assertEqual(json.load(response)['status'], 'ready')

    def test_isolated_operator_profile_returns_output_without_forwarding(self):
        with serve('vision', lambda data: data.upper()) as url:
            reply = request(url + '/infer', {'run_id': 'test', 'request_id': 'probe', 'data': encode(b'image')})
            self.assertEqual(base64.b64decode(reply['data']), b'IMAGE')
            self.assertEqual(reply['receipt']['input_bytes'], {'source': 5})

    def test_two_branches_forward_to_fusion_and_return_three_receipts(self):
        with ExitStack() as stack:
            fusion = stack.enter_context(serve('fusion', lambda inputs: inputs['vision'] + inputs['speech']))
            vision = stack.enter_context(serve('vision', lambda data: data.upper()))
            speech = stack.enter_context(serve('speech', lambda data: data.lower()))
            identity = {'run_id': 'test', 'request_id': 'r1'}
            request(fusion + '/open', dict(identity))
            for url, data in [(vision, b'img'), (speech, b'QUESTION')]:
                request(url + '/execute', dict(identity, data=encode(data), fusion_url=fusion))
            result = request(fusion + '/result', dict(identity))
            self.assertEqual(base64.b64decode(result['data']), b'IMGquestion')
            self.assertEqual([r['task'] for r in result['receipts']], ['vision', 'speech', 'fusion'])
            self.assertTrue(all(r['scope'] == 'protocol_fixture_not_ai' for r in result['receipts']))


if __name__ == '__main__':
    unittest.main()
