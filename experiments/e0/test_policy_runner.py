"""Policy decisions are checked against actual HTTP workflow receipts."""

import http.client
import json
from pathlib import Path

from probe import MODEL_SHA256
from test_dag_profile import DAGContract


class PolicyWorkflowHTTP(DAGContract):
    extra_env={'DYNAMIC_LIFECYCLE':'1',
               'CORPUS_FILE':str(Path('artifacts/e3-input-replication/corpus.json').resolve()),
               'CORPUS_ASSET_DIR':str(Path('artifacts/e3-input-replication/assets/corpus').resolve())}

    def worker_request(self,alias,method,path,body=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',int(self.peers[alias]['url'].rsplit(':',1)[1]),timeout=15)
        try:
            connection.request(method,path,body,headers or {})
            response=connection.getresponse();result=json.loads(response.read())
            self.assertEqual(response.status,200)
            return result
        finally:
            connection.close()

    def setUp(self):
        # Each case starts from known service-object state through the public HTTP API.
        from probe import IMAGE_SHA256
        for alias in self.peers:
            self.worker_request(alias,'POST','/stage/0',self.body,{
                'X-Plan':json.dumps([alias]*3),'X-Request-ID':'setup-'+alias,
                'X-Input-ID':'dog','X-Payload-SHA256':IMAGE_SHA256})

    def profile(self):
        # Deliberately synthetic costs for an interface test, not a latency fit.
        return {'id':'http-fixture-v1','provenance':'synthetic interface fixture',
                'input_id':'dog','source':'a','model_sha256':MODEL_SHA256,
                'candidates':[['a','b'],['a','b'],['a','b']],
                'cost_ms':{'task':{f'{i}|{a}':1 for i in range(3) for a in ['a','b']},
                           'ingress':{'a':0,'b':2},
                           'edges':[{f'{a}|{b}':0 if a==b else 2
                                     for a in ['a','b'] for b in ['a','b']} for _ in range(2)],
                           'service_prepare':{'a':None,'b':None}}}

    def test_policy_snapshot_drives_real_forwarding_without_mutable_or_future_fields(self):
        from policy_runner import PolicyRunner

        def policy(snapshot):
            self.assertEqual(set(snapshot),{'schema_version','snapshot_id','request','profile','workers'})
            self.assertEqual(set(snapshot['request']),{'request_id','input_id','source','bytes','sha256'})
            self.assertEqual(snapshot['profile']['id'],'http-fixture-v1')
            self.assertTrue(snapshot['workers']['b']['service_object_ready'])
            self.assertEqual(snapshot['workers']['b']['weight_memory_resident'],'unknown')
            with self.assertRaises(TypeError):snapshot['workers']['b']['service_object_ready']=False
            with self.assertRaises(TypeError):snapshot['profile']['candidates'][1][0]='b'
            return ['a','b','a']

        with PolicyRunner(self.peers) as runner:
            record=runner.run(request_id='policy-forward',input_id='dog',body=self.body,source='a',
                              profile=self.profile(),policy_name='http-route-fixture',policy=policy)
        self.assertEqual(record['status'],'ok',record.get('error'))
        self.assertEqual(record['result']['input_id'],'dog')
        self.assertEqual(record['result']['top5'],[258,259,157,279,152])
        self.assertEqual([t['alias'] for t in record['result']['trace']],['a','b','a'])
        self.assertEqual([t['node'] for t in record['result']['trace']],['local-a','local-b','local-a'])
        self.assertEqual(len(record['snapshot_id']),64)
        self.assertGreaterEqual(record['total_ms'],record['timings_ms']['workflow'])
        json.dumps(record,allow_nan=False)

    def test_excluded_candidate_is_rejected_before_cold_model_execution(self):
        from policy_runner import PolicyRunner

        before=self.worker_request('b','POST','/evict',b'')
        profile=self.profile();profile['candidates'][1]=['a']
        with PolicyRunner(self.peers) as runner:
            record=runner.run(request_id='policy-excluded',input_id='dog',body=self.body,source='a',
                              profile=profile,policy_name='invalid-route-fixture',
                              policy=lambda snapshot:['a','b','a'])
        self.assertEqual(record['status'],'error')
        self.assertEqual(record['error']['phase'],'plan_validation')
        self.assertNotIn('result',record)
        after=self.worker_request('b','GET','/healthz')
        self.assertFalse(after['model_ready'])
        self.assertEqual(after['generation'],before['generation'])

    def test_state_change_after_decision_is_rejected_before_workflow(self):
        from policy_runner import PolicyRunner

        def policy(snapshot):
            self.assertTrue(snapshot['workers']['b']['service_object_ready'])
            self.worker_request('b','POST','/evict',b'')
            return ['a','b','a']

        with PolicyRunner(self.peers) as runner:
            record=runner.run(request_id='policy-stale',input_id='dog',body=self.body,source='a',
                              profile=self.profile(),policy_name='eviction-race-fixture',policy=policy)
        self.assertEqual(record['status'],'error')
        self.assertEqual(record['error']['phase'],'state_validation')
        self.assertNotIn('result',record)
        self.assertFalse(self.worker_request('b','GET','/healthz')['model_ready'])
        with PolicyRunner(self.peers) as runner:
            cold=runner.run(request_id='policy-cold',input_id='dog',body=self.body,source='a',
                            profile=self.profile(),policy_name='fresh-route-fixture',policy=lambda s:['a','b','a'])
            warm=runner.run(request_id='policy-warm',input_id='dog',body=self.body,source='a',
                            profile=self.profile(),policy_name='fresh-route-fixture',policy=lambda s:['a','b','a'])
        self.assertEqual(cold['status'],'ok',cold.get('error'))
        self.assertFalse(cold['snapshot']['workers']['b']['service_object_ready'])
        self.assertTrue(cold['result']['trace'][1]['device']['was_cold'])
        self.assertGreater(cold['result']['trace'][1]['device']['preparation_ms'],0)
        self.assertEqual(warm['status'],'ok',warm.get('error'))
        self.assertTrue(warm['snapshot']['workers']['b']['service_object_ready'])
        self.assertFalse(warm['result']['trace'][1]['device']['was_cold'])

    def test_profiles_for_other_inputs_sources_or_future_fields_are_rejected(self):
        from policy_runner import PolicyRunner

        for changes in [{'input_id':'camera'},{'source':'b'},{'future_sources':['b','a']},
                        {'cost_ms':{'future_latencies':[0,1]}}]:
            with self.subTest(changes=changes):
                profile=self.profile();profile.update(changes)
                with PolicyRunner(self.peers) as runner:
                    record=runner.run(request_id='policy-profile-scope',input_id='dog',body=self.body,source='a',
                                      profile=profile,policy_name='route-fixture',policy=lambda s:['a','a','a'])
                self.assertEqual(record['status'],'error')
                self.assertEqual(record['error']['phase'],'validation')
                self.assertNotIn('result',record)

    def test_malformed_policy_output_still_produces_a_serializable_error_record(self):
        from policy_runner import PolicyRunner

        with PolicyRunner(self.peers) as runner:
            record=runner.run(request_id='policy-malformed',input_id='dog',body=self.body,source='a',
                              profile=self.profile(),policy_name='malformed-fixture',
                              policy=lambda s:[object(),'a','a'])
        self.assertEqual(record['status'],'error')
        self.assertEqual(record['error']['phase'],'plan_validation')
        self.assertNotIn('result',record)
        json.dumps(record,allow_nan=False)


if __name__=='__main__':
    import unittest
    unittest.main()
