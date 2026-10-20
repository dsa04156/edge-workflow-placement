"""Source HTTP seam: async trace survives polling and retries, no exec stream."""
from contextlib import ExitStack
import json
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from edge_client import source_server
from http_runtime import request, serve


class SourceControlTests(unittest.TestCase):
    def test_repeated_profile_identity_returns_same_observation(self):
        os.environ.setdefault('NODE_NAME', 'protocol-fixture')
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            root = Path(folder)
            (root/'image.bin').write_bytes(b'image')
            (root/'pairs.json').write_text(json.dumps({'pairs':[dict(question_id=1,split='calibration',
                image_path=str(root/'image.bin'),image_sha256=hashlib.sha256(b'image').hexdigest())]}))
            (root/'reference.jsonl').write_text('{"question_id":1}\n')
            source = source_server(root, host='127.0.0.1', port=0)
            thread = threading.Thread(target=source.serve_forever, daemon=True); thread.start()
            outputs=iter([b'first observation', b'additional observation'])
            operator=stack.enter_context(serve('vision', lambda _:next(outputs)))
            body=dict(run_id='profile', request_id='one', question_id=1, kind='image', operator_url=operator)
            try:
                url='http://127.0.0.1:'+str(source.server_port)+'/profile'
                first=request(url,body)
                second=request(url,body)
                self.assertEqual(first,second)
            finally:
                source.shutdown();source.server_close();thread.join()

    def test_transfer_profile_preserves_payload_and_retry_observation(self):
        os.environ.setdefault('NODE_NAME', 'protocol-fixture')
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            root = Path(folder)
            (root/'pairs.json').write_text('{"pairs":[]}')
            (root/'reference.jsonl').write_text('')
            source = source_server(root, host='127.0.0.1', port=0)
            thread = threading.Thread(target=source.serve_forever, daemon=True); thread.start()
            target = stack.enter_context(serve('vision', lambda _:b'unused'))
            body = dict(run_id='transfer', request_id='one', target_url=target,
                        data=b'actual calibration payload', count=2)
            try:
                url = 'http://127.0.0.1:'+str(source.server_port)+'/transfer_profile'
                first = request(url, body)
                second = request(url, body)
                self.assertEqual(first, second)
                self.assertEqual(first['payload_bytes'], 26)
                self.assertEqual(len(first['samples_ms']), 2)
                self.assertEqual(first['sha256'], hashlib.sha256(body['data']).hexdigest())
                self.assertTrue(all(value >= 0 for value in first['samples_ms']))
            finally:
                source.shutdown();source.server_close();thread.join()

    def test_started_trace_is_idempotent_and_preserves_one_terminal_per_release(self):
        os.environ.setdefault('NODE_NAME', 'protocol-fixture')
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            root = Path(folder)
            (root/'image.bin').write_bytes(b'image')
            (root/'pairs.json').write_text(json.dumps({'pairs':[dict(question_id=1,split='evaluation',
                image_path=str(root/'image.bin'),image_sha256=hashlib.sha256(b'image').hexdigest())]}))
            (root/'reference.jsonl').write_text('{"question_id":1}\n')
            source = source_server(root, host='127.0.0.1', port=0)
            thread = threading.Thread(target=source.serve_forever, daemon=True); thread.start()
            fusion = stack.enter_context(serve('fusion',lambda x:b'fixture'))
            url = 'http://127.0.0.1:'+str(source.server_port)
            config = dict(run_id='control',phase='one',question_ids=[1,1],offsets_s=[0,.05],
                endpoints={'fusion':fusion},remote_source='',representation='compressed',deadline_s=1,max_inflight=4)
            try:
                first=request(url+'/start_trace',config)
                again=request(url+'/start_trace',config)
                self.assertEqual(first['phase'],again['phase'])
                for _ in range(40):
                    result=request(url+'/trace_status',{'phase':'one'})
                    if result['status']=='complete':break
                    time.sleep(.05)
                self.assertEqual(result['status'],'complete')
                terminals=[r for r in result['events'] if r['event']=='terminal']
                self.assertEqual(len(terminals),2)
                self.assertEqual(sorted(r['index'] for r in terminals),[0,1])
                self.assertEqual(len(list(root.glob('trace-*.jsonl'))),1)
            finally:
                source.shutdown();source.server_close();thread.join()
