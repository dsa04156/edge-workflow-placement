"""Collector resource ownership through the public CLI and the fake kubectl seam.

Create-only creation, UIDs of actually created objects, UID-preconditioned deletion: an existing
foreign object with a run name, an object replaced mid-run, and a name raced in between server
dry-run and create are all preserved, while every object this run created is cleaned up.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest

from test_collect import CACHE, HAVE_CACHE, HERE, collect, fake_env

NS = 'topology-profile-lab'


def foreign_pod(name, labels=None):
    return dict(apiVersion='v1', kind='Pod', metadata=dict(name=name, namespace=NS, labels=labels or dict(experiment='someone-else')),
                spec=dict(nodeName='etri-ser0002-cgnmsb'))


@unittest.skipUnless(HAVE_CACHE, 'dedicated model/wheel cache not present')
class Ownership(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.prepared = Path(cls.tmp.name)/'prepared'
        subprocess.run([sys.executable, str(HERE/'prepare.py'), '--cache', CACHE, '--output', cls.prepared], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        # Preserved (not ours) fake objects keep their local processes; stop them after the assertions.
        for state in Path(cls.tmp.name).glob('*/kube/state.json'):
            for pod in json.loads(state.read_text())['pods'].values():
                if pod.get('fake', {}).get('pid'):
                    try:
                        os.killpg(pod['fake']['pid'], signal.SIGTERM)
                    except ProcessLookupError:
                        pass
        cls.tmp.cleanup()

    def run_case(self, name, preseed=(), on_create=None, truncate=None):
        root = Path(self.tmp.name)/name
        root.mkdir()
        env = fake_env(root)
        env.update(FAKE_PRESEED=json.dumps(list(preseed)), FAKE_ON_CREATE=json.dumps(on_create or {}),
                   FAKE_TRUNCATE_CREATE=json.dumps(dict(trigger=truncate) if truncate else {}))
        result = collect(env, '--execute', '--prepared', self.prepared, '--output-root', root/'runs', '--run-id', name,
                         '--environment', 'local-fake-kubectl', timeout=900)
        state = json.loads((root/'kube/state.json').read_text())
        run = root/'runs'/name
        cleanup = json.loads((run/'cleanup.json').read_text()) if (run/'cleanup.json').exists() else None
        return result, state, cleanup, run

    def test_reused_run_label_refuses_before_any_create(self):
        squatter = foreign_pod('unrelated', dict(experiment='multi-edge-sensors', **{'run-id': 'mes-own-reuse'}))
        result, state, cleanup, run = self.run_case('mes-own-reuse', preseed=[squatter])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unrelated', state['pods'])
        self.assertEqual(set(state['pods']), {'unrelated'})
        self.assertEqual(state['configmaps'], {})
        self.assertEqual(cleanup['created'], [])
        self.assertTrue(cleanup['foreign_with_run_label'])

    def test_existing_and_replaced_objects_are_preserved_while_own_objects_are_deleted(self):
        run_id = 'mes-own-replace'
        existing = foreign_pod(run_id+'-ctl')
        result, state, cleanup, run = self.run_case(run_id, preseed=[existing],
            on_create=dict(trigger=run_id+'-ctl', target=run_id+'-cache', action='replace'))
        self.assertNotEqual(result.returncode, 0)
        # Existing foreign object with a run name: refused, untouched.
        self.assertEqual(state['pods'][run_id+'-ctl']['metadata']['labels'], dict(experiment='someone-else'))
        # Run-created cache replaced by someone else before cleanup: UID precondition preserves it.
        self.assertIn(run_id+'-cache', state['pods'])
        created = {r['name']: r for r in cleanup['created']}
        self.assertNotEqual(state['pods'][run_id+'-cache']['metadata']['uid'], created[run_id+'-cache']['uid'])
        self.assertEqual(created[run_id+'-cache']['delete'], 'refused-uid-mismatch')
        # Run-created ConfigMap: deleted.
        self.assertNotIn(run_id+'-code', state['configmaps'])
        self.assertEqual(created[run_id+'-code']['delete'], 'deleted')
        self.assertEqual(cleanup['remaining_owned_pods_and_configmaps'], 0)
        self.assertEqual(len(created), 2)

    def test_partial_create_records_created_subset_and_preserves_raced_name(self):
        run_id = 'mes-own-race'
        raced = run_id+'-src-txt-e9'
        result, state, cleanup, run = self.run_case(run_id, on_create=dict(trigger=raced, target=raced, action='inject', real_only=True))
        self.assertNotEqual(result.returncode, 0)
        created = {r['name']: r for r in cleanup['created']}
        sources = sorted(n for n in created if '-src-' in n)
        self.assertEqual(sources, sorted(f'{run_id}-src-{s}' for s in ('cam-e0', 'cam-e6', 'cam-e8', 'txt-e1')))
        self.assertTrue(all(r['uid'] and r['delete'] == 'deleted' for r in created.values()))
        self.assertEqual(set(state['pods']), {raced})
        self.assertEqual(state['pods'][raced]['metadata']['labels'], dict(experiment='someone-else'))
        self.assertEqual(state['configmaps'], {})
        self.assertEqual(cleanup['remaining_owned_pods_and_configmaps'], 0)
        self.assertIn('AlreadyExists', (run/'failure.json').read_text())

    def test_interrupted_create_output_keeps_confirmed_uids_and_reports_the_rest_unresolved(self):
        run_id = 'mes-own-trunc'
        result, state, cleanup, run = self.run_case(run_id, truncate=run_id+'-src-cam-e0')
        self.assertNotEqual(result.returncode, 0)
        created = {r['name']: r for r in cleanup['created']}
        # The complete first object keeps its UID and is deleted by that UID.
        self.assertEqual(created[run_id+'-src-cam-e0']['delete'], 'deleted')
        self.assertNotIn(run_id+'-src-cam-e0', state['pods'])
        # The other four were created but their identity never came back: reported, not deleted, not adopted.
        others = sorted(f'{run_id}-src-{s}' for s in ('cam-e6', 'cam-e8', 'txt-e1', 'txt-e9'))
        self.assertEqual(sorted(u['name'] for u in cleanup['unresolved_creation']), others)
        self.assertTrue(all(u['present'] for u in cleanup['unresolved_creation']))
        self.assertTrue(set(others) <= set(state['pods']))
        self.assertFalse(set(others) & set(created))
        self.assertFalse(cleanup['cleanup_verified'])
        # Confirmed run objects created before the interruption are cleaned up.
        self.assertEqual(state['configmaps'], {})
        self.assertNotIn(run_id+'-cache', state['pods'])


if __name__ == '__main__':
    unittest.main()
