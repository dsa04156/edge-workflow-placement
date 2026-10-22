import copy
import unittest
import tempfile
import subprocess
from pathlib import Path
from unittest.mock import patch
from run_budget_placement import BudgetPilot
from test_placement_policies import fixture
from placement_policies import select_policies
from budget_placement import with_server_budget, validate_placement

class BudgetTests(unittest.TestCase):
    def test_read_only_probe_can_use_same_pod_worker_after_three_netctl_eofs(self):
        with tempfile.TemporaryDirectory() as temp:
            pilot=BudgetPilot.__new__(BudgetPilot);pilot.run='fixture';pilot.out=Path(temp);pilot.counter=0
            pilot.actual={'source-e1':{'metadata':{'uid':'fixture-uid'}}}
            attempts=[]
            def external_command(command,**kwargs):
                attempts.append(command)
                if command[command.index('-c')+1]=='netctl':
                    kwargs['stderr'].write(b'error sending request: unexpected EOF\n')
                    return subprocess.CompletedProcess(command,1)
                self.assertIn("os.environ['POD_UID']=='fixture-uid'",command[-1])
                self.assertIn('/work/config',command[-1])
                kwargs['stdout'].write(b'{"status":"ready"}\n')
                return subprocess.CompletedProcess(command,0)
            with patch('subprocess.run',side_effect=external_command),patch('time.sleep'):
                result=pilot.execute('source-e1',"import sys;sys.path.insert(0,'/config');print('probe')",'restore-source-e1')
            self.assertIn('ready',result)
            self.assertEqual(len(attempts),4)

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


    def test_read_only_control_eof_is_preserved_then_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            pilot=BudgetPilot.__new__(BudgetPilot);pilot.run='fixture';pilot.out=Path(temp);pilot.counter=0;pilot.actual={'source-e1':{'metadata':{'uid':'fixture-uid'}}}
            events=iter([False,True])
            def external_command(command,**kwargs):
                success=next(events)
                if success:kwargs['stdout'].write(b'{"blocked":true}\n')
                else:kwargs['stderr'].write(b'error: Internal error occurred: error sending request: unexpected EOF\n')
                return subprocess.CompletedProcess(command,0 if success else 1)
            with patch('subprocess.run',side_effect=external_command):
                self.assertIn('true',pilot.execute('source-e1',"print('probe')",'bypass-source-e1'))
            self.assertEqual(len(list(pilot.out.glob('*.event.json'))),2)
            self.assertIn('unexpected EOF',(pilot.out/'001-bypass-source-e1.stderr').read_text())

    def test_network_mutation_eof_is_not_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            pilot=BudgetPilot.__new__(BudgetPilot);pilot.run='fixture';pilot.out=Path(temp);pilot.counter=0;pilot.actual={'source-e1':{'metadata':{'uid':'fixture-uid'}}}
            def external_command(command,**kwargs):
                kwargs['stderr'].write(b'error sending request: unexpected EOF\n')
                return subprocess.CompletedProcess(command,1)
            with patch('subprocess.run',side_effect=external_command) as boundary:
                with self.assertRaises(RuntimeError):
                    pilot.execute('source-e1',"print('mutation')",'guard-True-source-e1',container='netctl',stdin='{}')
                self.assertEqual(boundary.call_count,1)

if __name__=='__main__':unittest.main()
