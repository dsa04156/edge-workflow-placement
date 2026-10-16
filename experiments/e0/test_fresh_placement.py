"""Warm placement choices are applied through real HTTP workers."""

import itertools
import json
from pathlib import Path

from policy_runner import PolicyRunner
from test_policy_runner import PolicyWorkflowHTTP


class FreshPlacementHTTP(PolicyWorkflowHTTP):
    def test_dag_avoids_the_worked_greedy_trap_and_executes_the_chosen_route(self):
        from fresh_placement import data_greedy,dag_enumeration

        profile=self.profile()
        # Worked costs: greedy A,B,B=13ms; global B,B,B=4ms.
        profile['cost_ms']['task']={'0|a':1,'0|b':2,'1|a':20,'1|b':1,'2|a':1,'2|b':1}
        profile['cost_ms']['ingress']={'a':0,'b':0}
        profile['cost_ms']['edges']=[{'a|a':0,'a|b':10,'b|a':10,'b|b':0} for _ in range(2)]

        def policy(snapshot):
            self.assertEqual(data_greedy(snapshot),['a','b','b'])
            self.assertEqual(dag_enumeration(snapshot),['b','b','b'])
            return dag_enumeration(snapshot)

        with PolicyRunner(self.peers) as runner:
            row=runner.run(request_id='dag-worked-trap',input_id='dog',body=self.body,source='a',
                           profile=profile,policy_name='dag-worked-example',policy=policy)
        self.assertEqual(row['status'],'ok',row.get('error'))
        self.assertEqual(row['plan'],['b','b','b'])
        self.assertEqual(row['result']['top5'],[258,259,157,279,152])

    def test_http_calibration_freezes_candidates_and_refuses_an_unmet_quality_contract(self):
        from fresh_placement import freeze_profiles,dag_enumeration

        rows=[]
        with PolicyRunner(self.peers) as runner:
            for index,plan in enumerate(itertools.product(['a','b'],repeat=3)):
                row=runner.run(request_id=f'quality-cal-{index}',input_id='dog',body=self.body,source='a',
                               profile=self.profile(),policy_name='calibration',policy=lambda s,p=plan:p)
                self.assertEqual(row['status'],'ok',row.get('error'))
                row.update(source='a',warmup=False);rows.append(row)
            sample=json.loads(Path(self.extra_env['CORPUS_FILE']).read_text())['dog']
            profiles,quality=freeze_profiles(rows,{'dog':sample},['a','b'],'http-calibration-evidence')
            profile=profiles['a|dog']
            self.assertEqual(profile['candidates'],[['a','b'],['a','b'],['a','b']])
            self.assertTrue(quality['dog']['b']['eligible'])
            evaluated=runner.run(request_id='quality-heldout',input_id='dog',body=self.body,source='a',
                                 profile=profile,policy_name='calibrated-dag',policy=dag_enumeration)
            self.assertEqual(evaluated['status'],'ok',evaluated.get('error'))
            self.assertEqual(evaluated['result']['top5'],[258,259,157,279,152])
        unmet={'dog':{**sample,'reference_top5':[0,1,2,3,4]}}
        with self.assertRaisesRegex(ValueError,'quality'):
            freeze_profiles(rows,unmet,['a','b'],'unmet-quality-fixture')
