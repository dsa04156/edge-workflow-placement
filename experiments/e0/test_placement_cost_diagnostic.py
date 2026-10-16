"""State-check-aware placement must reach the selected real HTTP route."""

from test_policy_runner import PolicyWorkflowHTTP
from policy_runner import PolicyRunner


class PlacementCostHTTP(PolicyWorkflowHTTP):
    def test_state_check_cost_changes_route_without_changing_execution_contract(self):
        from placement_cost_diagnostic import dag_with_state
        from fresh_placement import dag_enumeration

        profile=self.profile()
        # Worked example: BBB workflow=3, AAA workflow=6; state checks 10 vs 1.
        profile['cost_ms']['ingress']={'a':0,'b':0}
        profile['cost_ms']['task']={f'{i}|{a}':cost for i in range(3) for a,cost in [('a',2),('b',1)]}
        profile['cost_ms']['edges']=[{'a|a':0,'a|b':20,'b|a':20,'b|b':0} for _ in range(2)]
        def choose(snapshot):
            self.assertEqual(dag_enumeration(snapshot),['b','b','b'])
            return dag_with_state(snapshot,{'a':1,'b':10})
        with PolicyRunner(self.peers) as runner:
            row=runner.run(request_id='state-aware-worked-example',input_id='dog',body=self.body,
                           source='a',profile=profile,policy_name='workflow-plus-state',policy=choose)
        self.assertEqual(row['status'],'ok',row.get('error'))
        self.assertEqual(row['plan'],['a','a','a'])
        self.assertEqual([t['alias'] for t in row['result']['trace']],['a','a','a'])
        self.assertEqual(row['result']['top5'],[258,259,157,279,152])
