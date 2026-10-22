"""Public collector CLI. Dry-run: design only, zero kubectl calls. Execute: through a fake kubectl on PATH
that starts real local runtime processes (MediaMTX, PyAV, ONNX Runtime); offline, never cluster evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCES = HERE/'sources-20261022.json'
CACHE = Path(json.loads(SOURCES.read_text())['cache_hint'])
INVENTORY = ROOT/'.scratch/multi-edge-sensors/inventory-before.json'
READY9 = ['etri-dev0003-raspi5', 'etri-dev0002-raspi5', 'etri-ser0002-cgnmsb', 'etri-dev0006-raspi5', 'etri-dev0008-raspi5',
          'etri-dev0009-jetorn', 'etri-ser0001-cg0msb', 'etri-ser0003-cg0ms0', 'etri-ser0004-cgnms0']
HAVE_CACHE = (CACHE/'export-20261022c/yolo11n.onnx').is_file()


def fake_env(tmp, fail=None, sched=None):
    bin_dir = Path(tmp)/'bin'
    bin_dir.mkdir(exist_ok=True)
    (bin_dir/'kubectl').write_text(f'#!/bin/sh\nexec {sys.executable} {HERE/"fake_kubectl.py"} "$@"\n')
    (bin_dir/'kubectl').chmod(0o755)
    nodes = [dict(metadata=dict(name=n, labels={'kubernetes.io/arch': 'amd64' if 'ser000' in n and n != 'etri-ser0003-cg0ms0' else 'arm64'}),
                  spec={}, status=dict(conditions=[dict(type='Ready', status='True')], allocatable=dict(cpu='4', memory='8Gi'),
                                       nodeInfo=dict(architecture='arm64')))
             for n in READY9 + ['etri-dev0001-jetorn']]
    nodes[-1]['status']['conditions'] = [dict(type='Ready', status='Unknown')]
    (Path(tmp)/'nodes.json').write_text(json.dumps(nodes))
    return dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}', FAKE_KUBE_STATE=str(Path(tmp)/'kube'), FAKE_PYTHON=sys.executable,
                FAKE_NODES=str(Path(tmp)/'nodes.json'), FAKE_FAIL=json.dumps(fail or {}), FAKE_SCHED=json.dumps(sched or {}),
                MES_POD_PORT='18080')


def collect(env, *args, timeout=1800):
    return subprocess.run([sys.executable, str(HERE/'collect.py'), *map(str, args)], capture_output=True, text=True, env=env, timeout=timeout)


@unittest.skipUnless(HAVE_CACHE, 'dedicated model/wheel cache not present')
class DryRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.prepared = Path(cls.tmp.name)/'prepared'
        subprocess.run([sys.executable, str(HERE/'prepare.py'), '--cache', CACHE, '--output', cls.prepared], check=True, capture_output=True)
        cls.env = fake_env(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def dry(self, *extra):
        result = collect(self.env, '--dry-run', '--prepared', self.prepared, *extra)
        return result, (json.loads(result.stdout) if result.stdout.strip().startswith('{') else None)

    def test_design_maps_sources_to_five_edges_and_nine_candidates_without_kubectl(self):
        result, design = self.dry()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({k: v['node'] for k, v in design['sources'].items()},
                         {'cam-e0': 'etri-dev0003-raspi5', 'cam-e6': 'etri-dev0006-raspi5', 'cam-e8': 'etri-dev0008-raspi5',
                          'txt-e1': 'etri-dev0002-raspi5', 'txt-e9': 'etri-dev0009-jetorn'})
        self.assertEqual(sorted(design['candidates']), sorted(READY9))
        self.assertEqual(design['topology']['excluded'], ['etri-dev0001-jetorn', 'etri-dev0004-tedger', 'etri-dev0007-jetagx'])
        self.assertEqual(design['resources']['V'], dict(cpu='2', memory='1536Mi', threads=2))
        self.assertLessEqual(design['workload']['max_eval_workflows'], 72)
        self.assertFalse((Path(self.tmp.name)/'kube/state.json').exists(), 'dry-run must not call kubectl')

    def test_saved_inventory_preflight_passes_for_ready9(self):
        result, design = self.dry('--inventory', INVENTORY)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(design['preflight']['status'], 'passed')
        self.assertEqual(len(design['preflight']['candidates']), 9)

    def test_source_on_the_server_is_rejected(self):
        scenario = json.loads((HERE/'scenario-20261022.json').read_text())
        scenario['text_sources']['txt-e9']['node_alias'] = 'c'
        path = Path(self.tmp.name)/'server-source.json'
        path.write_text(json.dumps(scenario))
        result, _ = self.dry('--scenario', path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('edge-role', result.stderr)


if __name__ == '__main__':
    unittest.main()
