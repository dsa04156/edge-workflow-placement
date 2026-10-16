"""Keep client health connections active while only nested forwarding becomes idle."""

import time

from policy_runner import PolicyRunner
import test_policy_runner as fixtures


class IdleForwardingHTTP(fixtures.DAGContract):
    extra_env=fixtures.PolicyWorkflowHTTP.extra_env
    profile=fixtures.PolicyWorkflowHTTP.profile

    def test_nested_route_survives_idle_without_retrying_the_post(self):
        with PolicyRunner(self.peers) as runner:
            def run(name,plan):
                return runner.run(request_id=name,input_id='dog',body=self.body,source='a',
                                  profile=self.profile(),policy_name='idle-route',policy=lambda s:plan)
            self.assertEqual(run('idle-before',['a','b','a'])['status'],'ok')
            for index in range(4):
                time.sleep(4)
                row=run(f'idle-parent-active-{index}',['a','b','b'])
                self.assertEqual(row['status'],'ok',row.get('error'))
            after=run('idle-after',['a','b','a'])
            self.assertEqual(after['status'],'ok',after.get('error'))
            self.assertEqual(after['result']['top5'],[258,259,157,279,152])
            self.assertTrue(after['result']['trace'][0]['next_connection_reused'])
            self.assertFalse(after['result']['trace'][1]['next_connection_reused'])
