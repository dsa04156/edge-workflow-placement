"""HTTP seams agreed in cluster plan G3; diagnostic adapters, never AI results."""
import base64
from contextlib import ExitStack
import unittest
import json
import urllib.request

from http_runtime import serve, request


def encode(data):
    return base64.b64encode(data).decode()


class RuntimeTests(unittest.TestCase):
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
