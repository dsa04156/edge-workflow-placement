"""Public plan CLI: task-fastest is taskwise fastest-feasible, not a joint critical-path optimizer."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


def profile(compute, capacity, cpu=1000):
    nodes = sorted(capacity)
    pairs = {f'{a}|{b}': 0 for a in nodes for b in nodes}
    tasks = []
    for task, inputs in (('V', [dict(source_node='a', bytes=1, transfer_ms={f'a|{n}': 0 for n in nodes})]),
                         ('T', [dict(source_node='a', bytes=1, transfer_ms={f'a|{n}': 0 for n in nodes})]),
                         ('F', [dict(task='V', bytes=1, transfer_ms=dict(pairs)), dict(task='T', bytes=1, transfer_ms=dict(pairs))])):
        tasks.append(dict(id=task, candidates=nodes, compute_ms=compute[task], image='fixture@sha256:test', cpu_m=cpu, memory_mib=100,
                          inputs=inputs))
    return dict(provenance='fixture', evidence_id='hand-written counterexample', nodes={n: dict(cpu_free_m=c, memory_free_mib=10000)
                for n, c in capacity.items()}, tasks=tasks, outputs=[dict(task='F', destination_node='a', transfer_ms={f'{n}|a': 0 for n in nodes})])


def plans(value):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)/'profile.json'
        path.write_text(json.dumps(value))
        result = subprocess.run([sys.executable, str(HERE/'plan.py'), '--profile', path, '--video-node', 'a', '--text-node', 'a',
                                 '--destination-node', 'a', '--server', 'b', '--allow-fixture'], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class TaskFastest(unittest.TestCase):
    def test_review_counterexample_picks_each_tasks_fastest_node(self):
        # V a=100/b=1, T=200 on either, F=1: both critical paths tie at 201, the taskwise baseline still takes V=b.
        got = plans(profile(dict(V=dict(a=100, b=1), T=dict(a=200, b=200), F=dict(a=1, b=1)), dict(a=8000, b=8000)))
        self.assertEqual(got['task-fastest']['placement'], dict(V='b', T='a', F='a'))
        self.assertEqual(got['task-fastest']['status'], 'ok')

    def test_reserved_resources_push_a_later_task_to_its_next_fastest_node(self):
        got = plans(profile(dict(V=dict(a=100, b=1), T=dict(a=200, b=150), F=dict(a=1, b=1)), dict(a=8000, b=1000)))
        self.assertEqual(got['task-fastest']['placement'], dict(V='b', T='a', F='a'))
        self.assertEqual(got['task-fastest']['selection'][1]['skipped_for_resources'], ['b'])

    def test_no_remaining_capacity_is_reported_unavailable_not_replanned(self):
        got = plans(profile(dict(V=dict(a=100, b=1), T=dict(a=200, b=150), F=dict(a=1, b=1)), dict(a=1000, b=1000)))
        self.assertEqual(got['task-fastest']['status'], 'unavailable')
        self.assertIn('F', got['task-fastest']['error'])
        self.assertNotIn('placement', got['task-fastest'])


if __name__ == '__main__':
    unittest.main()
