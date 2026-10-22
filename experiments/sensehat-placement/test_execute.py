"""Slice D: --execute through a fake kubectl I/O seam (real local processes), then read-only verify."""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import forecast
import run
import verify

HERE = Path(__file__).resolve().parent
HISTORY = HERE.parents[1] / 'artifacts/sensehat-placement/discovery-20261021/history'
FAKE = (sys.executable, str(HERE / 'fake_kubectl.py'))
EDGE, SERVER = forecast.NODES['edge'], forecast.NODES['server']
SECRET = 'SENTINEL-unrelated-pod-secret-7f3a'


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def failed(report):
    return {c['name'] for c in report['checks'] if not c['ok']}


class FakeCluster:
    def __init__(self, sched, fail_exec=0, admit=''):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.prepared = forecast.prepare(HISTORY, base / 'prepared')
        (base / 'state').mkdir()
        (base / 'artifacts').mkdir()
        self.state, self.artifacts, self.port = base / 'state', base / 'artifacts', free_port()
        env = {'FAKE_KUBE_STATE': str(self.state), 'FAKE_KUBE_PORT': str(self.port), 'FAKE_SCHED': json.dumps(sched),
               'FAKE_FAIL_EXEC': str(fail_exec), 'FAKE_PROTECTED_SECRET': SECRET, 'FAKE_ADMIT': admit}
        with mock.patch.dict(os.environ, env):
            try:
                self.returncode, self.error = run.execute(self.prepared, kubectl=FAKE, artifacts=self.artifacts,
                                                          port=self.port), None
            except Exception as error:
                self.returncode, self.error = None, error
        self.run_dir = next(self.artifacts.iterdir())

    def phases(self, run_dir=None):
        return [json.loads(line) for line in ((run_dir or self.run_dir) / 'phases.jsonl').read_text().splitlines()]

    def tampered(self, name, edit_row, line=5):
        """Copy of the run with attempts.jsonl row `line` edited in place by edit_row(row)."""
        copy = Path(self.tmp.name) / name
        shutil.copytree(self.run_dir, copy)
        lines = (copy / 'attempts.jsonl').read_text().splitlines()
        row = json.loads(lines[line])
        edit_row(row)
        lines[line] = json.dumps(row)
        (copy / 'attempts.jsonl').write_text('\n'.join(lines) + '\n')
        return copy

    def fake_state(self):
        return json.loads((self.state / 'state.json').read_text())

    def close(self):
        for pod in self.fake_state()['pods'].values():
            try:
                os.killpg(pod['fake_pid'], signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.tmp.cleanup()


@unittest.skipUnless(HISTORY.exists(), 'discovery history not present')
class SharedSchedulerPlacement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = FakeCluster({'t1': EDGE, 't2': SERVER, 't3': SERVER})

    @classmethod
    def tearDownClass(cls):
        cls.cluster.close()

    def test_run_completes_and_verifies(self):
        c = self.cluster
        self.assertIsNone(c.error)
        self.assertEqual(c.returncode, 0)
        report = verify.verify(c.prepared, c.run_dir)
        self.assertTrue(report['passed'], failed(report))
        placements = json.loads((c.run_dir / 'placements.json').read_text())
        self.assertEqual(placements['default_scheduler']['shares_with'], 'mixed')
        rows = [json.loads(line) for line in (c.run_dir / 'attempts.jsonl').read_text().splitlines()]
        self.assertEqual(len(rows), 3 * 12)
        self.assertEqual({r['label'] for r in rows}, {'edge-all', 'server-all', 'mixed'})
        ended = {e['phase']: e for e in c.phases() if e['event'] == 'end'}
        for label in ('edge-all', 'server-all', 'mixed'):
            for kind in ('warmup', 'evaluation'):
                self.assertEqual(ended[f'{kind}:{label}']['status'], 'ok')
                self.assertGreater(ended[f'{kind}:{label}']['duration_s'], 0)
        self.assertNotIn('warmup:default-scheduler', ended)
        self.assertEqual(ended['teardown']['status'], 'ok')
        summary = json.loads((c.run_dir / 'summary.json').read_text())
        self.assertGreater(summary['blocks']['mixed']['warmup_phase_s'], 0)
        self.assertGreater(summary['blocks']['mixed']['evaluation_phase_s'], 0)
        for row in rows:
            receipts = {r['task']: r['node'] for r in row['reply']['receipts']}
            sites = forecast.FIXED_PLACEMENTS[row['label']]
            self.assertEqual(receipts, {t: forecast.NODES[s] for t, s in sites.items()})

    def test_cleanup_deletes_only_manifest_resources(self):
        c = self.cluster
        state = c.fake_state()
        self.assertEqual(state['pods'], {})
        manifests = [json.loads(p.read_text()) for p in sorted(c.run_dir.glob('*.manifest.json'))]
        owned = sorted([i['kind'], i['metadata']['name']] for m in manifests for i in m['items'])
        self.assertEqual(sorted(state['deleted']), owned)
        cleanup = json.loads((c.run_dir / 'cleanup.json').read_text())
        self.assertEqual(cleanup['remaining_owned_pods_and_configmaps'], 0)
        self.assertEqual(json.loads((c.run_dir / 'protected-after.json').read_text())['changes'], [])

    def test_unrelated_pod_spec_never_reaches_artifacts(self):
        c = self.cluster
        leaked = [str(p.relative_to(c.run_dir)) for p in c.run_dir.rglob('*')
                  if p.is_file() and SECRET.encode() in p.read_bytes()]
        self.assertEqual(leaked, [])
        expected = {'name': 'mapper', 'namespace': 'edgeai-devices', 'phase': 'Running', 'ready': 'True'}
        before = json.loads((c.run_dir / 'protected-before.json').read_text())
        self.assertEqual(before['protected-1'], expected)
        self.assertEqual(json.loads((c.run_dir / 'protected-after.json').read_text())['protected_count'], len(before))
        for stem in ('protected-before', 'protected-after'):
            raw = next(c.run_dir.glob(f'*-{stem}.stdout')).read_text()
            self.assertIn('protected-1\tedgeai-devices\tmapper\tRunning\tTrue', raw.splitlines())

    def test_tampered_receipt_is_detected(self):
        c = self.cluster
        copy = Path(c.tmp.name) / 'tampered-run'
        shutil.copytree(c.run_dir, copy)
        lines = (copy / 'attempts.jsonl').read_text().splitlines()
        row = json.loads(lines[5])
        row['reply']['receipts'][1]['output_sha256'] = '0' * 64
        lines[5] = json.dumps(row)
        (copy / 'attempts.jsonl').write_text('\n'.join(lines) + '\n')
        report = verify.verify(c.prepared, copy)
        self.assertFalse(report['passed'])
        self.assertIn('attempts', failed(report))

    def test_edited_forecast_is_detected_against_reference(self):
        c = self.cluster
        copy = Path(c.tmp.name) / 'forecast-run'
        shutil.copytree(c.run_dir, copy)
        lines = (copy / 'attempts.jsonl').read_text().splitlines()
        row = json.loads(lines[-1])
        row['reply']['result']['forecast'] += 1e-6
        lines[-1] = json.dumps(row)
        (copy / 'attempts.jsonl').write_text('\n'.join(lines) + '\n')
        self.assertIn('attempts', failed(verify.verify(c.prepared, copy)))

    def test_verifier_rebuilds_from_source_copies(self):
        c = self.cluster
        copy = Path(c.tmp.name) / 'model-run'
        shutil.copytree(c.run_dir, copy)
        # Consistently re-frozen but wrong model: manifest hash updated, source rebuild must disagree.
        model = json.loads((copy / 'prepared/model.json').read_text())
        model['channels']['humidity']['a'] += 0.5
        forecast.write_json(copy / 'prepared/model2.json', model)
        (copy / 'prepared/model.json').unlink()
        (copy / 'prepared/model2.json').rename(copy / 'prepared/model.json')
        manifest = json.loads((copy / 'prepared/prepared-manifest.json').read_text())
        manifest['files']['model.json'] = forecast.sr.digest((copy / 'prepared/model.json').read_bytes())
        (copy / 'prepared/prepared-manifest.json').write_text(json.dumps(manifest))
        report = verify.verify(copy / 'prepared', copy)
        self.assertIn('rebuild_from_source', failed(report))


    def test_receipt_identity_and_byte_tampering_is_detected(self):
        c = self.cluster

        def paired_t1_t2_hash(row):
            r = row['reply']['receipts']
            r[0]['output_sha256'] = r[1]['input_sha256'] = 'f' * 64

        edits = {'request-id': lambda row: row['reply']['receipts'][1].update(request_id='WRONG'),
                 'run-id': lambda row: row['reply']['receipts'][2].update(run_id='WRONG'),
                 'input-bytes': lambda row: row['reply']['receipts'][0].update(input_bytes=-999),
                 'output-bytes': lambda row: row['reply']['receipts'][2].update(output_bytes=-999),
                 'payload-bytes': lambda row: row['reply']['source'].update(payload_bytes=-999),
                 'paired-intermediate-hash': paired_t1_t2_hash}
        for name, edit in edits.items():
            with self.subTest(name):
                self.assertIn('attempts', failed(verify.verify(c.prepared, c.tampered('id-' + name, edit))))

    def test_intermediate_output_uses_numeric_tolerance_not_float_bytes(self):
        c = self.cluster

        def drift(delta):
            def edit(row):
                t2, t3 = row['reply']['receipts'][1:]
                t2['output']['forecast'] += delta
                raw = forecast.sr.canonical(t2['output'])
                t2.update(output_sha256=forecast.sr.digest(raw), output_bytes=len(raw))
                t3.update(input_sha256=t2['output_sha256'], input_bytes=t2['output_bytes'])
            return edit

        # Self-consistent T2 receipt whose float bytes differ from the local reference.
        within = c.tampered('drift-within', drift(1e-12))
        self.assertNotIn('attempts', failed(verify.verify(c.prepared, within)))
        beyond = c.tampered('drift-beyond', drift(1e-3))
        self.assertIn('attempts', failed(verify.verify(c.prepared, beyond)))

    def test_collector_source_differing_from_frozen_code_is_detected(self):
        c = self.cluster
        copy = Path(c.tmp.name) / 'code-run'
        shutil.copytree(c.run_dir, copy)
        with (copy / 'collector-source' / 'stage_runtime.py').open('a') as handle:
            handle.write('# post-prepare edit\n')
        self.assertIn('code_provenance', failed(verify.verify(c.prepared, copy)))

    def test_missing_placement_phase_is_detected(self):
        c = self.cluster
        copy = Path(c.tmp.name) / 'phase-run'
        shutil.copytree(c.run_dir, copy)
        kept = [line for line in (copy / 'phases.jsonl').read_text().splitlines()
                if json.loads(line)['phase'] != 'warmup:mixed']
        (copy / 'phases.jsonl').write_text('\n'.join(kept) + '\n')
        self.assertIn('phases', failed(verify.verify(c.prepared, copy)))

    def test_observed_pod_with_extra_container_fails_pod_safety(self):
        c = self.cluster
        copy = Path(c.tmp.name) / 'admitted-run'
        shutil.copytree(c.run_dir, copy)
        pods = json.loads((copy / 'pool-pods.json').read_text())
        pod = next(iter(pods.values()))
        pod['security']['containers'].append({'kind': 'containers', 'name': 'sidecar', 'image': 'x',
                                              'securityContext': {'privileged': True}})
        (copy / 'pool-pods.json').write_text(json.dumps(pods))
        self.assertIn('pod_safety', failed(verify.verify(c.prepared, copy)))


@unittest.skipUnless(HISTORY.exists(), 'discovery history not present')
class DistinctSchedulerPlacementWithFailure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 7 pool health execs, then measurement; the 12th exec fails at the transport layer.
        cls.cluster = FakeCluster({'t1': SERVER, 't2': EDGE, 't3': EDGE}, fail_exec=12)

    @classmethod
    def tearDownClass(cls):
        cls.cluster.close()

    def test_failure_is_retained_cleanup_runs_and_verify_refuses(self):
        c = self.cluster
        self.assertIsNone(c.error)
        self.assertEqual(c.returncode, 1)
        rows = [json.loads(line) for line in (c.run_dir / 'attempts.jsonl').read_text().splitlines()]
        self.assertEqual(len(rows), 4 * 12)
        bad = [r for r in rows if r['status'] != 'ok']
        self.assertEqual(len(bad), 1)
        self.assertIn('unable to upgrade', bad[0]['stderr'])
        self.assertEqual(c.fake_state()['pods'], {})
        report = verify.verify(c.prepared, c.run_dir)
        self.assertFalse(report['passed'])
        self.assertIn('attempts', failed(report))
        placements = json.loads((c.run_dir / 'placements.json').read_text())
        self.assertIsNone(placements['default_scheduler']['shares_with'])

        ended = {e['phase']: e for e in c.phases() if e['event'] == 'end'}
        self.assertEqual(ended['teardown']['status'], 'ok')


@unittest.skipUnless(HISTORY.exists(), 'discovery history not present')
class AdmittedUnsafePod(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Admission adds a privileged sidecar (with its own env secret) to every owned Pod.
        cls.cluster = FakeCluster({'t1': EDGE, 't2': SERVER, 't3': SERVER}, admit='privileged-sidecar')

    @classmethod
    def tearDownClass(cls):
        cls.cluster.close()

    def test_execute_refuses_before_measurement_and_cleans_up(self):
        c = self.cluster
        self.assertIsInstance(c.error, RuntimeError)
        self.assertIn('admitted Pod', str(c.error))
        self.assertFalse((c.run_dir / 'attempts.jsonl').exists())
        self.assertEqual(c.fake_state()['pods'], {})
        phases = c.phases()
        self.assertEqual([e['status'] for e in phases if e['phase'] == 'scheduler-probe' and e['event'] == 'end'],
                         ['error'])
        self.assertEqual([e['event'] for e in phases if e['phase'] == 'teardown'], ['start', 'end'])
        leaked = [str(p.relative_to(c.run_dir)) for p in c.run_dir.rglob('*')
                  if p.is_file() and SECRET.encode() in p.read_bytes()]
        self.assertEqual(leaked, [])


class ProjectedCallFailureEvidence(unittest.TestCase):
    """Projected reads keep safe stdout evidence on nonzero/timeout, never raw secret bytes."""
    POD = {'metadata': {'name': 'p1', 'uid': 'u1', 'labels': {'role': 'src'}},
           'spec': {'nodeName': EDGE, 'containers': [{'name': 'c', 'env': [{'name': 'K', 'value': SECRET}]}]},
           'status': {}}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.rec = run.Recorder(self.tmp, ['kubectl'])

    def call(self, effect):
        def fake(cmd, stdout, stderr, **kwargs):
            stderr.write(b'server said no')
            return effect(cmd, kwargs['timeout'])
        project = lambda raw: json.dumps(run.project_pod(json.loads(raw)))
        with mock.patch.object(run.subprocess, 'run', side_effect=fake):
            with self.assertRaises(BaseException) as caught:
                self.rec.call('pod-state', ['get', 'pod'], timeout=5, project=project)
        event = json.loads((self.tmp / '001-pod-state.event.json').read_text())
        self.assertEqual((self.tmp / '001-pod-state.stderr').read_bytes(), b'server said no')
        self.assertEqual([p for p in self.tmp.iterdir() if SECRET.encode() in p.read_bytes()], [])
        return caught.exception, event, (self.tmp / '001-pod-state.stdout').read_text()

    def test_timeout_with_valid_json_keeps_projected_identity(self):
        raw = json.dumps(self.POD).encode()
        def timeout(cmd, t):
            raise subprocess.TimeoutExpired(cmd, t, output=raw)
        error, event, stdout = self.call(timeout)
        self.assertIsInstance(error, subprocess.TimeoutExpired)
        self.assertEqual(event['status'], 'timeout')
        self.assertEqual(event['projection'], {'status': 'projected', 'captured_bytes': len(raw)})
        self.assertEqual(json.loads(stdout)['uid'], 'u1')

    def test_nonzero_with_valid_json_keeps_projected_identity(self):
        raw = json.dumps(self.POD).encode()
        error, event, stdout = self.call(lambda cmd, t: subprocess.CompletedProcess(cmd, 1, raw))
        self.assertIsInstance(error, RuntimeError)
        self.assertIn('rc=1', str(error))
        self.assertEqual((event['status'], event['returncode']), ('completed', 1))
        self.assertEqual(event['projection'], {'status': 'projected', 'captured_bytes': len(raw)})
        self.assertEqual(json.loads(stdout)['name'], 'p1')

    def test_partial_invalid_json_records_unavailable_without_secret(self):
        raw = json.dumps(self.POD).encode()[:-20]
        self.assertIn(SECRET.encode(), raw)
        for effect, kind in ((lambda cmd, t: subprocess.CompletedProcess(cmd, 1, raw), RuntimeError),
                             (lambda cmd, t: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd, t, output=raw)),
                              subprocess.TimeoutExpired)):
            with self.subTest(kind=kind.__name__):
                self.setUp()
                error, event, stdout = self.call(effect)
                self.assertIsInstance(error, kind)  # projection failure never masks the original outcome
                self.assertEqual(stdout, '')
                self.assertEqual(event['projection'], {'status': 'unavailable', 'captured_bytes': len(raw),
                                                       'reason': 'JSONDecodeError; raw stdout omitted'})


if __name__ == '__main__':
    unittest.main()
