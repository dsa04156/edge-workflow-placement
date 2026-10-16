"""Historical measured costs cross the actual HTTP seam; local workers remain CPU."""

import json
from pathlib import Path

from policy_runner import PolicyRunner
from test_dag_profile import DAGContract


class MeasuredE2HTTP(DAGContract):
    aliases=('arm','intel_npu')
    extra_env={'DYNAMIC_LIFECYCLE':'1',
               'CORPUS_FILE':str(Path('artifacts/e3-input-replication/corpus.json').resolve()),
               'CORPUS_ASSET_DIR':str(Path('artifacts/e3-input-replication/assets/corpus').resolve())}

    def test_measured_profile_preserves_the_known_warm_dag_choice_through_http(self):
        from measured_policy_bridge import load_e2_contract,E2Policy

        contract=load_e2_contract(Path('artifacts/e2-dynamic'))
        profile=contract['profiles']['arm']
        self.assertEqual(profile['cost_ms']['task']['0|arm'],36.874249461106956)
        self.assertEqual(profile['cost_ms']['service_prepare']['arm'],None)
        self.assertEqual(profile['cost_ms']['service_prepare']['intel_npu'],27.011536993086338)
        self.assertNotIn('source_phases',json.dumps(contract))
        with PolicyRunner(self.peers) as runner:
            row=runner.run(request_id='measured-profile-http',input_id='dog',body=self.body,source='arm',
                           profile=profile,policy_name='e2-warm-dag',policy=E2Policy('warm_dag',contract))
        self.assertEqual(row['status'],'ok',row.get('error'))
        self.assertEqual(row['plan'],['arm','arm','arm'])
        self.assertEqual(row['result']['top5'],[258,259,157,279,152])
        self.assertEqual([t['worker_backend'] for t in row['result']['trace']],['cpu']*3)

    def test_task_fastest_does_not_treat_an_unknown_task_cost_as_a_valid_minimum(self):
        from measured_policy_bridge import load_e2_contract,task_fastest

        profile=load_e2_contract(Path('artifacts/e2-dynamic'))['profiles']['arm']
        profile['provenance']='Unknown-cost contract test, not a measured policy comparison'
        profile['candidates'][0]=['arm'];profile['cost_ms']['task']['0|arm']=None
        with PolicyRunner(self.peers) as runner:
            row=runner.run(request_id='unknown-task-cost',input_id='dog',body=self.body,source='arm',
                           profile=profile,policy_name='task-fastest',policy=task_fastest)
        self.assertEqual(row['status'],'error')
        self.assertEqual(row['error']['phase'],'decision')
        self.assertNotIn('result',row)

    def test_task_fastest_keeps_the_known_mixed_plan_and_actual_data_path(self):
        from measured_policy_bridge import load_e2_contract,task_fastest

        contract=load_e2_contract(Path('artifacts/e2-dynamic'))
        with PolicyRunner(self.peers) as runner:
            row=runner.run(request_id='measured-fastest-http',input_id='dog',body=self.body,source='intel_npu',
                           profile=contract['profiles']['intel_npu'],policy_name='task-fastest',policy=task_fastest)
        self.assertEqual(row['status'],'ok',row.get('error'))
        self.assertEqual(row['plan'],['intel_npu','intel_npu','arm'])
        self.assertEqual([t['alias'] for t in row['result']['trace']],['intel_npu','intel_npu','arm'])
        self.assertEqual(row['result']['top5'],[258,259,157,279,152])
