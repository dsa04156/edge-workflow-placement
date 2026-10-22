"""Slice C: public CLI --prepare on the real discovery history and local-only --dry-run."""
import json
import os
import shutil
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
HISTORY = ROOT / 'artifacts/sensehat-placement/discovery-20261021/history'


def cli(*args, env=None):
    return subprocess.run([sys.executable, str(HERE / 'run.py'), *map(str, args)], capture_output=True, text=True,
                          timeout=120, env=env)


def trap_path(tmp):
    """PATH whose kubectl/rtk only record that they were invoked."""
    bindir = Path(tmp) / 'bin'
    bindir.mkdir()
    marker = Path(tmp) / 'invoked'
    for name in ('kubectl', 'rtk'):
        tool = bindir / name
        tool.write_text(f'#!/bin/sh\necho "$0 $@" >> {marker}\nexit 3\n')
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    return dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ['PATH']), marker


@unittest.skipUnless(HISTORY.exists(), 'discovery history not present')
class PrepareRealHistory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.prepared = Path(cls.tmp.name) / 'prepared'
        cls.result = cli('--prepare', HISTORY, '--output', cls.prepared)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def load(self, name):
        return json.loads((self.prepared / name).read_text())

    def test_prepare_freezes_split_model_and_target_free_windows(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)
        model = self.load('model.json')
        self.assertEqual(sorted(model['channels']), ['humidity', 'pressure', 'temp_humidity'])
        manifest = self.load('source-manifest.json')
        for name, channel in manifest['channels'].items():
            self.assertEqual(channel['split_counts'], {'training': 540, 'calibration': 180, 'evaluation': 180})
            self.assertEqual(model['channels'][name]['training_rows'], 540)
            self.assertEqual(model['channels'][name]['training_last_sequence'], channel['split_sequences']['training'][1])
        windows = self.load('windows.json')['windows']
        targets = self.load('targets.json')['targets']
        self.assertEqual(len(windows), 12)
        self.assertEqual(sum(w['kind'] == 'evaluation' for w in windows.values()), 9)
        for wid, window in windows.items():
            sequences = [r['sequence'] for r in window['payload']['rows']]
            self.assertEqual(targets[wid]['sequence'], sequences[-1] + 1)
            self.assertNotIn('target', json.dumps(window))
        evaluation = self.load('evaluation.json')['channels']
        for name, channel in evaluation.items():
            self.assertEqual(len(channel['rows']), 180 - 8)
            self.assertEqual(channel['model']['count'], 172)
            self.assertIn('truth', channel['rows'][0])
        for name in ('temp_humidity', 'humidity', 'pressure'):
            original = (HISTORY / (name + '.json')).read_bytes()
            self.assertEqual((self.prepared / 'source/history' / (name + '.json')).read_bytes(), original)

    def test_prepare_refuses_existing_output(self):
        again = cli('--prepare', HISTORY, '--output', self.prepared)
        self.assertNotEqual(again.returncode, 0)

    def test_dry_run_is_local_only_and_reports_plan(self):
        env, marker = trap_path(self.tmp.name)
        result = cli('--dry-run', '--prepared', self.prepared, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists(), 'dry-run invoked kubectl/rtk')
        report = json.loads(result.stdout)
        self.assertEqual(report['mode'], 'dry-run-local-only')
        self.assertEqual(sorted(b['label'] for b in report['plan']['blocks']),
                         ['default-scheduler', 'edge-all', 'mixed', 'server-all'])
        self.assertEqual(report['plan']['max_workflows'], 48)
        self.assertTrue(report['reference_reproduced'])
        for pod in report['pods']:
            spec = pod['spec']
            self.assertNotIn('hostNetwork', spec)
            self.assertFalse(any(v.get('hostPath') for v in spec['volumes']))
            ctx = spec['containers'][0]['securityContext']
            self.assertEqual((ctx['readOnlyRootFilesystem'], ctx['allowPrivilegeEscalation']), (True, False))
        self.assertNotIn('targets.json', report['configmap_keys'])

    def test_dry_run_rejects_tampered_prepared_file(self):
        copy = Path(self.tmp.name) / 'tampered'
        subprocess.run(['cp', '-r', str(self.prepared), str(copy)], check=True)
        model = copy / 'model.json'
        model.write_text(model.read_text().replace('"a":', '"a" :', 1))
        result = cli('--dry-run', '--prepared', copy)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('changed after freeze', result.stderr)

    def test_execute_refuses_source_edited_after_prepare(self):
        code = Path(self.tmp.name) / 'repo' / 'experiments' / 'code'
        code.mkdir(parents=True)
        for name in ('forecast.py', 'stage_runtime.py', 'run.py', 'verify.py'):
            shutil.copyfile(HERE / name, code / name)
        prepared = Path(self.tmp.name) / 'prepared-for-edit'
        first = subprocess.run([sys.executable, str(code / 'run.py'), '--prepare', HISTORY, '--output', prepared],
                               capture_output=True, text=True, timeout=120)
        self.assertEqual(first.returncode, 0, first.stderr)
        with (code / 'stage_runtime.py').open('a') as handle:
            handle.write('# edited after preparation\n')
        (Path(self.tmp.name) / 'edit-trap').mkdir()
        env, marker = trap_path(Path(self.tmp.name) / 'edit-trap')
        result = subprocess.run([sys.executable, str(code / 'run.py'), '--execute', '--prepared', prepared],
                                capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('stage_runtime.py changed after preparation', result.stderr)
        self.assertFalse(marker.exists(), 'execute invoked kubectl/rtk with stale preparation')
        self.assertFalse((code.parents[1] / 'artifacts').exists())


class StagePodAffinity(unittest.TestCase):
    """Server dry-run rejected matchFields metadata.name In with two values (sensehat-20261021t114403z)."""

    def terms(self, pod):
        return pod['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution'][
            'nodeSelectorTerms']

    def test_every_term_holds_one_field_value_and_candidates_unchanged(self):
        sys.path.insert(0, str(HERE))
        import forecast
        import run
        expected = [forecast.NODES['edge'], forecast.NODES['server']]
        for pod in run.scheduler_pods('r'):
            terms = self.terms(pod)
            self.assertEqual(len(terms), 2)
            for term in terms:
                self.assertEqual(len(term['matchFields']), 1)
                field = term['matchFields'][0]
                self.assertEqual((field['key'], field['operator'], len(field['values'])), ('metadata.name', 'In', 1))
            self.assertEqual([t['matchFields'][0]['values'][0] for t in terms], expected)
        for pod in run.pool_pods('r'):
            terms = self.terms(pod)
            self.assertEqual(len(terms), 1)
            self.assertEqual(len(terms[0]['matchFields'][0]['values']), 1)


if __name__ == '__main__':
    unittest.main()
