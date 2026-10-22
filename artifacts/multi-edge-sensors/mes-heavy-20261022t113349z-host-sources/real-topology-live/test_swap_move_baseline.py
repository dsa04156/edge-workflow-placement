import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from budget_placement import with_server_budget
from placement_policies import prepare
from swap_move_baseline import greedy_swap_move, plan

HERE = Path(__file__).resolve().parent


def three_node(fusion_edge_ms=100, vision_server_ms=1):
    """Hand-checked case: E0 image source, E1 audio source and sink, server C with two slots.

    greedy C/C/E1=131ms; best pair swap E1/C/C=95ms; moving vision E1->E0 gives 55ms.
    """
    nodes = dict(e0=dict(cpu_free_m=3000, memory_free_mib=8192),
                 e1=dict(cpu_free_m=3000, memory_free_mib=8192),
                 c=dict(cpu_free_m=2000, memory_free_mib=8192))
    delay = {frozenset(['e0', 'e1']): 40, frozenset(['e0', 'c']): 15, frozenset(['e1', 'c']): 15}
    matrix = {a+'|'+b: 0 if a == b else delay[frozenset([a, b])] for a in nodes for b in nodes}
    compute = dict(vision=dict(e0=20, e1=20, c=vision_server_ms), speech=dict(e0=80, e1=80, c=1),
                   fusion=dict(e0=fusion_edge_ms, e1=fusion_edge_ms, c=5))
    inputs = dict(vision=[dict(source_node='e0')], speech=[dict(source_node='e1')],
                  fusion=[dict(task='vision'), dict(task='speech')])
    tasks = []
    for name in ('vision', 'speech', 'fusion'):
        for edge in inputs[name]:
            edge.update(bytes=100, transfer_ms=dict(matrix))
        tasks.append(dict(id=name, candidates=['e0', 'e1', 'c'], compute_ms=compute[name], cpu_m=1000,
                          memory_mib=1024, image='fixture@sha256:test', inputs=inputs[name]))
    return dict(provenance='measured', evidence_id='hand-checked local fixture', nodes=nodes, tasks=tasks,
                outputs=[dict(task='fusion', destination_node='e1',
                              transfer_ms={n+'|e1': matrix[n+'|e1'] for n in nodes})])


class SwapMoveTests(unittest.TestCase):
    def test_swap_leaves_one_beneficial_relocation(self):
        result = greedy_swap_move(three_node())
        self.assertEqual(result['greedy_placement'], dict(vision='c', speech='c', fusion='e1'))
        self.assertEqual(result['greedy_proxy_ms'], 131)
        self.assertEqual(result['swap_placement'], dict(vision='e1', speech='c', fusion='c'))
        self.assertEqual(result['swap_proxy_ms'], 95)
        self.assertEqual(result['placement'], dict(vision='e0', speech='c', fusion='c'))
        self.assertEqual(result['predicted_e2e_proxy_ms'], 55)
        self.assertEqual(result['move'], dict(task='vision', source='e1', destination='e0'))
        # Pairs (vision,speech) duplicates greedy; both others satisfy the unchanged slot multiset.
        self.assertEqual(result['candidates']['swap'], dict(
            proposals=3, rejected_unqualified=0, rejected_capacity=0, validated_distinct=3))
        # Six one-task proposals; vision->c would put three tasks on two server slots.
        self.assertEqual(result['candidates']['move'], dict(
            proposals=6, rejected_unqualified=0, rejected_capacity=1, validated_distinct=6))

    def test_equal_costs_use_sorted_placement_and_keep_zero_move(self):
        profile = three_node()
        for task in profile['tasks']:
            task['compute_ms'] = dict(e0=10, e1=10, c=10)
            for edge in task['inputs']:
                edge['transfer_ms'] = {k: 0 for k in edge['transfer_ms']}
        profile['outputs'][0]['transfer_ms'] = {k: 0 for k in profile['outputs'][0]['transfer_ms']}
        first, second = greedy_swap_move(profile), greedy_swap_move(profile)
        # Every placement costs 30ms; greedy breaks node ties by name, repair by sorted items.
        self.assertEqual(first['greedy_placement'], dict(vision='c', speech='c', fusion='e0'))
        self.assertEqual(first['swap_placement'], dict(vision='e0', speech='c', fusion='c'))
        self.assertEqual(first['placement'], first['swap_placement'])
        self.assertIsNone(first['move'])
        for name in ('placement', 'predicted_e2e_proxy_ms', 'candidates', 'move'):
            self.assertEqual(first[name], second[name])

    def test_no_improvement_returns_greedy_without_move(self):
        profile = three_node()
        profile['nodes']['c']['cpu_free_m'] = 3000
        result = greedy_swap_move(profile)
        self.assertEqual(result['greedy_placement'], dict(vision='c', speech='c', fusion='c'))
        self.assertEqual(result['placement'], result['greedy_placement'])
        self.assertEqual(result['predicted_e2e_proxy_ms'], result['greedy_proxy_ms'])
        self.assertEqual(result['predicted_e2e_proxy_ms'], 36)
        self.assertIsNone(result['move'])
        self.assertEqual(result['candidates']['swap']['validated_distinct'], 1)
        self.assertEqual(result['candidates']['move'], dict(
            proposals=6, rejected_unqualified=0, rejected_capacity=0, validated_distinct=7))

    def test_unqualified_swap_is_rejected_and_single_move_can_leave_residual(self):
        profile = three_node()
        by_id = {t['id']: t for t in profile['tasks']}
        by_id['vision']['candidates'] = ['e0', 'c']
        by_id['fusion']['candidates'] = ['e1', 'c']
        result = greedy_swap_move(profile)
        self.assertEqual(result['greedy_placement'], dict(vision='c', speech='c', fusion='e1'))
        # Swapping vision/fusion would put vision on unqualified E1.
        self.assertEqual(result['candidates']['swap'], dict(
            proposals=3, rejected_unqualified=1, rejected_capacity=0, validated_distinct=2))
        self.assertEqual(result['swap_placement'], dict(vision='c', speech='e1', fusion='c'))
        self.assertEqual(result['candidates']['move'], dict(
            proposals=4, rejected_unqualified=0, rejected_capacity=1, validated_distinct=4))
        # vision->E0 ties the swap at 115ms; the sorted-items key keeps the zero move.
        self.assertIsNone(result['move'])
        self.assertEqual(result['predicted_e2e_proxy_ms'], 115)
        # The qualified exact optimum E0/C/C (55ms) needs two relocations; no convergence iteration.
        self.assertEqual(prepare.cost(profile, dict(vision='e0', speech='c', fusion='c'), 'topology_profile'), 55)

    def test_inputs_are_not_mutated(self):
        profile = three_node()
        before = copy.deepcopy(profile)
        greedy_swap_move(profile)
        plan(profile, 'c')
        self.assertEqual(profile, before)


class PlanTests(unittest.TestCase):
    def test_plan_adds_fifth_policy_without_live_contract(self):
        result = plan(with_server_budget(three_node(), 'c', 2), 'c')
        self.assertIs(result['execution_ready'], False)
        self.assertEqual(list(result['policies']), ['greedy', 'heft', 'ours', 'greedy-swap', 'greedy-swap-move'])
        policies = result['policies']
        self.assertEqual(policies['greedy-swap-move']['placement'], policies['ours']['placement'])
        self.assertEqual(policies['greedy-swap-move']['greedy_placement'], policies['greedy']['placement'])
        self.assertEqual(policies['greedy-swap-move']['swap_placement'], policies['greedy-swap']['placement'])


RUN = HERE.parents[1]/'artifacts/topology-profile-live/wan-20261020t082011'
DIAGNOSTIC = HERE.parents[1]/'artifacts/budget-completion/fresh-single-move.json'


@unittest.skipUnless(RUN.is_dir() and DIAGNOSTIC.is_file(), 'retained frozen run is local-only')
class FrozenRunCompatibilityTests(unittest.TestCase):
    def test_reproduces_post_hoc_single_move_selection(self):
        frozen = json.loads((RUN/'plans-frozen.json').read_text())
        diagnostic = json.loads(DIAGNOSTIC.read_text())
        for condition in ('b0-compressed-10', 'b0-compressed-2'):
            path = RUN/('profile-'+condition+'.json')
            expected = diagnostic['conditions'][condition]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected['profile_sha256'])
            profile = json.loads(path.read_text())
            result = plan(profile, profile['admission_budget']['server'])['policies']
            for policy in ('greedy', 'heft', 'ours', 'greedy-swap'):
                self.assertEqual(result[policy]['placement'], frozen[condition][policy]['placement'])
            self.assertEqual(result['greedy-swap-move']['placement'], expected['selected'])
            self.assertEqual(result['greedy-swap-move']['predicted_e2e_proxy_ms'], expected['selected_proxy_ms'])
            self.assertEqual(result['greedy-swap-move']['candidates']['move']['validated_distinct'],
                             expected['feasible_candidates'])


class CommandTests(unittest.TestCase):
    def command(self, directory, profile, server, out):
        path = Path(directory)/'profile.json'
        path.write_text(json.dumps(profile))
        process = subprocess.run([sys.executable, str(HERE/'swap_move_baseline.py'), str(path),
                                  '--server', server, '--out', str(out)], capture_output=True, text=True)
        return path, process

    def test_writes_provenance_exclusively(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)/'plan.json'
            path, process = self.command(directory, three_node(), 'c', out)
            self.assertEqual(process.returncode, 0, process.stderr)
            written = out.read_bytes()
            result = json.loads(written)
            self.assertIs(result['execution_ready'], False)
            self.assertEqual(result['contract'], 'swap-move-baseline-offline-v1')
            source = result['provenance']
            self.assertEqual(source['profile_file_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(source['profile_sha256'], prepare.digest(three_node()))
            self.assertEqual(source['server'], 'c')
            for name in ('swap_move_baseline.py', 'budget_placement.py', 'placement_policies.py', 'prepare.py'):
                self.assertRegex(source['implementations_sha256'][name], '^[0-9a-f]{64}$')
            self.assertEqual(result['policies']['greedy-swap-move']['placement'],
                             dict(vision='e0', speech='c', fusion='c'))
            _, again = self.command(directory, three_node(), 'c', out)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(out.read_bytes(), written)

    def test_invalid_inputs_leave_no_output(self):
        unmeasured = three_node()
        unmeasured['provenance'] = 'synthetic'
        for profile, server in [(three_node(), 'missing'), (with_server_budget(three_node(), 'c', 2), 'e0'),
                                (unmeasured, 'c')]:
            with tempfile.TemporaryDirectory() as directory:
                out = Path(directory)/'plan.json'
                _, process = self.command(directory, profile, server, out)
                self.assertNotEqual(process.returncode, 0)
                self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
