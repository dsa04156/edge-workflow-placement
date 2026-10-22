import unittest
from placement_policies import select_policies


def fixture():
    nodes = {n: dict(cpu_free_m=2000, memory_free_mib=4096) for n in ('edge','cloud')}
    tasks=[]
    for task, inputs in [('vision',[{'source_node':'edge'}]),('speech',[{'source_node':'edge'}]),('fusion',[{'task':'vision'},{'task':'speech'}])]:
        for edge in inputs:
            edge.update(bytes=100,transfer_ms={a+'|'+b:0 if a==b else 100 for a in nodes for b in nodes})
        tasks.append(dict(id=task,candidates=list(nodes),compute_ms={'edge':30 if task=='fusion' else 10,'cloud':1},
                          cpu_m=1000,memory_mib=2048,image='fixture@sha256:test',inputs=inputs))
    return dict(provenance='measured',evidence_id='local interface fixture only',nodes=nodes,tasks=tasks,
                outputs=[dict(task='fusion',destination_node='edge',transfer_ms={'edge|edge':0,'cloud|edge':100})])

class PolicyTests(unittest.TestCase):
    def test_all_policies_respect_joint_capacity_and_share_identical_plans(self):
        results=select_policies(fixture(), server_node='cloud')
        self.assertEqual(results['server-only']['status'],'infeasible')
        for name in ('greedy','heft','ours','no-network','no-node-profile'):
            self.assertEqual(results[name]['status'],'ok')
            placement=results[name]['placement']
            self.assertLessEqual(list(placement.values()).count('edge'),2)
            self.assertLessEqual(list(placement.values()).count('cloud'),2)
        self.assertEqual(results['ours']['placement'], {'vision':'cloud','speech':'edge','fusion':'cloud'})

if __name__=='__main__':unittest.main()
