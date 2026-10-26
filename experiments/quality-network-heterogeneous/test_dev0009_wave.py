"""Targeted tests for the revalidated dev0009 CUDA13 wave (dev0009-wave-spec.md). Selection/catalog/plan plumbing only;
actual import, CUDA execution and quality come only from the released live run."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import catalog  # noqa: E402
import qualify  # noqa: E402

AUDIT = catalog.load_audit()
EXTRA = qualify.ROOT/'.scratch/quality-network-heterogeneous/live-extra'


class Dev0009Selection(unittest.TestCase):
    def test_dev0009_cu130_is_the_only_jetson_worker_with_revalidation_provenance(self):
        jet = [w for w in qualify.workers_for(AUDIT) if w['backend'] == 'cuda-torch']
        self.assertEqual([(w['key'], w['node'], w['recipe'], w['image']) for w in jet],
                         [('jgpu-dev0009-cu130', 'etri-dev0009-jetorn', 'cu130-aarch64-cp311', qualify.IMAGE)])
        sel = jet[0]['recipe_selection']
        self.assertEqual((sel['driver_api'], sel['l4t']), (13020, 'R39.2.1'))
        reval = json.loads((EXTRA/'dev0009-revalidation-0954.json').read_text())
        conditions = {c['type']: c['status'] for c in reval['conditions']}
        self.assertEqual((conditions['Ready'], conditions['DiskPressure'], conditions['MemoryPressure'], reval['taints']), ('True', 'False', 'False', None))
        self.assertIn(qualify.sha(EXTRA/'dev0009-revalidation-0954.json')[:8], sel['evidence'])
        self.assertIn(qualify.sha(EXTRA/'dev0009-storage-budget-0955.json')[:8], sel['evidence'])

    def test_dev0001_jp61_is_held_as_runtime_failed_not_removed_or_unsupported(self):
        self.assertNotIn('etri-dev0001-jetorn', qualify.JETSON_RECIPES)
        self.assertIn('etri-dev0001-jetorn', qualify.JETSON_HELD)
        rows = [r for r in catalog.build(AUDIT)['rows'] if r['node'] == 'etri-dev0001-jetorn' and r['backend'] == 'cuda-torch']
        self.assertEqual(len(rows), 3)
        self.assertEqual({(r['execution'], r['failure_phase'], r['status']) for r in rows}, {('runtime_failed', 'load', 'runtime_failed')})
        self.assertTrue(all('torchvision::nms' in r['execution_evidence'] for r in rows))
        health = qualify.ROOT/qualify.RUN2/'health-jgpu-dev0001-jp61-002.json'
        self.assertIn('operator torchvision::nms does not exist', health.read_text())
        self.assertFalse(any(r['status'] == 'unsupported' for r in catalog.build(AUDIT)['rows']))

    def test_dev0007_stays_blocked_and_all_families_remain(self):
        cat = catalog.build(AUDIT)
        dev7 = {r['status'] for r in cat['rows'] if r['node'] == 'etri-dev0007-jetagx' and r['backend'] == 'cuda-torch'}
        self.assertEqual(dev7, {'deployment_blocked'})
        dev9 = [r for r in cat['rows'] if r['node'] == 'etri-dev0009-jetorn' and r['backend'] == 'cuda-torch']
        self.assertTrue(dev9 and not any('deployment_blocker' in r for r in dev9))
        self.assertEqual(cat['nodes'], 12)
        self.assertEqual({r['family'] for r in cat['rows'] if r['resource']},
                         {'nvidia-jetson-gpu', 'nvidia-server-gpu', 'intel-npu', 'hailo-8', 'mobilint', 'rockchip-rk3399pro'})

    def test_cu130_pair_is_an_official_matched_release_per_package_metadata(self):
        meta = json.loads((qualify.ROOT/'.scratch/quality-network-heterogeneous/cu130-pair-metadata.json').read_text())
        self.assertEqual(meta['pair_check']['torchvision_requires_torch'], ['torch (==2.9.1)'])
        torch = next(v for k, v in meta.items() if k.startswith('torch-'))
        self.assertEqual(torch['version'], '2.9.1+cu130')
        self.assertIn("cuda: Optional[str] = '13.0'", torch['version_py'])
        wheels = qualify.OUT_ROOT/'deps-20261023T080028Z/cu130-aarch64-cp311'
        for name, info in meta.items():
            if name != 'pair_check':
                self.assertEqual(qualify.sha(wheels/name), info['sha256'])  # the reused, already-prepared wheel


class Dev0009Plan(unittest.TestCase):
    def test_plan_budget_matches_root_accounting_and_mixed_composition_is_planned(self):
        with tempfile.TemporaryDirectory() as tmp:
            wave = qualify.plan(Path(tmp)/'p', qualify.OUT_ROOT/'export-20261023T075427Z',
                                [qualify.OUT_ROOT/'deps-20261023T075919Z', qualify.OUT_ROOT/'deps-20261023T080028Z'], hash_files=False)
            assets = json.loads((Path(tmp)/'p/assets-manifest.json').read_text())
        w = next(w for w in wave['workers'] if w['key'] == 'jgpu-dev0009-cu130')
        root = json.loads((EXTRA/'dev0009-storage-budget-0955.json').read_text())
        self.assertEqual(w['storage']['total_bytes'], root['total_bytes'])
        self.assertEqual(w['storage']['quantity'], '13Gi')
        groups = {e['group'] for e in assets['files']}
        self.assertIn('cu130-aarch64-cp311', groups)
        self.assertNotIn('jp61-aarch64-cp310', groups)  # no unchanged heavy JP6 retry
        self.assertFalse(any(e['path'].startswith('sysroot/') for e in assets['files']))
        names = [c[0] for c in qualify.compositions_for(wave)]
        self.assertIn('npu-c/jgpu-dev0009-cu130/cpu-c', names)
        ordered = sorted(qualify.compositions_for(wave), key=lambda c: (0 if c[0].startswith('npu-c/jgpu-') else 1, c[0]))
        self.assertEqual(ordered[0][0], 'npu-c/jgpu-dev0009-cu130/cpu-c')  # fresh mixed requests first
        self.assertEqual(wave['qmin'], 0.40)


if __name__ == '__main__':
    unittest.main()
