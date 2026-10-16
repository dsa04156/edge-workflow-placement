"""Exercise real CPU sessions through the existing workflow HTTP boundary."""

import hashlib
import http.client
import json

from test_dag_profile import DAGContract


class ModelLifecycleHTTP(DAGContract):
    extra_env={'DYNAMIC_LIFECYCLE':'1'}

    def test_evicted_model_is_prepared_inside_first_request_then_reused(self):
        port=int(self.peers['a']['url'].rsplit(':',1)[1])
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=15)
        connection.request('GET','/healthz')
        health=json.loads(connection.getresponse().read())
        self.assertTrue(health['model_ready'])
        generation=health['generation']
        connection.request('POST','/evict',b'')
        self.assertFalse(json.loads(connection.getresponse().read())['model_ready'])
        for index in range(2):
            connection.request('POST','/stage/0',self.body,{
                'X-Plan':json.dumps(['a','a','a']),'X-Request-ID':f'lifecycle-{index}',
                'X-Payload-SHA256':hashlib.sha256(self.body).hexdigest()})
            response=connection.getresponse();result=json.loads(response.read())
            self.assertEqual(response.status,200)
            self.assertEqual(result['top5'],[258,259,157,279,152])
            device=result['trace'][1]['device']
            self.assertEqual(device['generation'],generation+1)
            self.assertEqual(device['was_cold'],index==0)
            if index==0:self.assertGreater(device['preparation_ms'],0)
            else:self.assertEqual(device['preparation_ms'],0)
        connection.close()


if __name__=='__main__':
    import unittest
    unittest.main()
