import copy
import unittest
from test_placement_policies import fixture
from placement_policies import select_policies
from budget_placement import with_server_budget, validate_placement

class BudgetTests(unittest.TestCase):
    def test_two_slots_preserve_measured_costs_and_reserve_join_with_joint_plan(self):
        original=fixture()
        for n in original['nodes'].values():n.update(cpu_free_m=9000,memory_free_mib=16000)
        for task,edge_time in zip(original['tasks'],[8,30,20]):
            task['compute_ms']={'edge':edge_time,'cloud':5}
            for edge in task['inputs']:edge['transfer_ms']={key:0 for key in edge['transfer_ms']}
        original['outputs'][0]['transfer_ms']={'edge|edge':0,'cloud|edge':0}
        before=copy.deepcopy(original)
        constrained=with_server_budget(original,'cloud',2)
        self.assertEqual(original,before)
        self.assertEqual(constrained['tasks'],original['tasks'])
        results=select_policies(constrained,'cloud')
        self.assertEqual(results['greedy']['placement'],{'vision':'cloud','speech':'cloud','fusion':'edge'})
        self.assertEqual(results['ours']['placement'],{'vision':'edge','speech':'cloud','fusion':'cloud'})
        self.assertEqual(results['greedy']['predicted_e2e_proxy_ms'],25)
        self.assertEqual(results['ours']['predicted_e2e_proxy_ms'],13)
        validate_placement(constrained,results['ours']['placement'])
        with self.assertRaises(ValueError):
            validate_placement(constrained,{'vision':'cloud','speech':'cloud','fusion':'cloud'})

if __name__=='__main__':unittest.main()
