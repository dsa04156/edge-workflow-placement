"""Corpus IDs stay bound to image/tensor/reference across the actual HTTP DAG."""

import hashlib
import http.client
import json
from pathlib import Path

from test_dag_profile import DAGContract


class CorpusHTTP(DAGContract):
    extra_env={'DYNAMIC_LIFECYCLE':'1','CORPUS_FILE':str(Path('artifacts/e3-input-replication/corpus.json').resolve()),
               'CORPUS_ASSET_DIR':str(Path('artifacts/e3-input-replication/assets/corpus').resolve())}

    def test_gray_image_is_forwarded_with_its_own_reference(self):
        corpus=json.loads(Path(self.extra_env['CORPUS_FILE']).read_text())
        body=(Path(self.extra_env['CORPUS_ASSET_DIR'])/'camera.png').read_bytes()
        connection=http.client.HTTPConnection('127.0.0.1',int(self.peers['a']['url'].rsplit(':',1)[1]),timeout=15)
        connection.request('POST','/stage/0',body,{'X-Plan':json.dumps(['a','b','a']),
            'X-Request-ID':'camera-http','X-Input-ID':'camera','X-Payload-SHA256':hashlib.sha256(body).hexdigest()})
        response=connection.getresponse();data=json.loads(response.read());connection.close()
        self.assertEqual(response.status,200)
        self.assertEqual(data['input_id'],'camera')
        self.assertEqual(data['top5'],corpus['camera']['reference_top5'])
        self.assertEqual(data['trace'][0]['output_sha256'],corpus['camera']['tensor_sha256'])
        self.assertTrue(data['trace'][1]['device']['top1_agrees_with_cpu'])
        self.assertEqual(data['trace'][1]['device']['top5_overlap_with_cpu'],5)


if __name__=='__main__':
    import unittest
    unittest.main()
