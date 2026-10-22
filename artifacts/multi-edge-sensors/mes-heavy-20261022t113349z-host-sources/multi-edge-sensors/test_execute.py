"""Collector --execute and verify/reference CLIs through the fake kubectl (real local MediaMTX/PyAV/ONNX processes).

One golden offline run per test process; mutation tests copy it. These numbers are local
and offline (environment 'local-fake-kubectl'), never cluster evidence.
"""
import json
import os
from pathlib import Path
import statistics
from types import SimpleNamespace
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_collect import CACHE, HAVE_CACHE, HERE, collect, fake_env

FAILED_NODE = 'etri-ser0004-cgnms0'
# Fake-only: this node's worker Pods get permuted class names, so its T and F outputs are numerically wrong.
CORRUPT_NODE = 'etri-ser0003-cg0ms0'
RUN = {}


def setUpModule():
    if not HAVE_CACHE:
        return
    reuse = os.environ.get('MES_TEST_REUSE')
    if reuse:
        # Read-only re-check of the Collection assertions on a retained golden run (no new collection).
        # Audit tests need that run's prepared bundle and are skipped in this mode.
        run = Path(reuse)/'runs/mes-fake'
        RUN.update(run=run, reuse=True, result=SimpleNamespace(
            returncode=0 if (run/'completed.json').is_file() and not (run/'failure.json').exists() else 1,
            stderr=(Path(reuse)/'result.stderr').read_text()))
        return
    tmp = tempfile.TemporaryDirectory()
    RUN['tmp'] = tmp
    prepared = Path(tmp.name)/'prepared'
    subprocess.run([sys.executable, str(HERE/'prepare.py'), '--cache', CACHE, '--output', prepared], check=True, capture_output=True)
    env = fake_env(tmp.name, fail={FAILED_NODE: 'ImagePullBackOff'},
                   sched={'scheduler': 'etri-ser0001-cg0msb'})
    env['FAKE_CORRUPT_NODE'] = CORRUPT_NODE
    result = collect(env, '--execute', '--prepared', prepared, '--output-root', Path(tmp.name)/'runs', '--run-id', 'mes-fake',
                     '--environment', 'local-fake-kubectl')
    RUN.update(prepared=prepared, env=env, result=result, run=Path(tmp.name)/'runs/mes-fake')
    if result.returncode == 0:
        RUN['reference'] = subprocess.run([sys.executable, str(HERE/'reference.py'), '--run', RUN['run'], '--prepared', prepared],
                                          capture_output=True, text=True, timeout=900)


def tearDownModule():
    if 'tmp' in RUN and not RUN.get('reuse'):
        # MES_TEST_RETAIN=<new dir>: keep this golden run's evidence before the TemporaryDirectory is removed.
        retain = os.environ.get('MES_TEST_RETAIN')
        if retain:
            shutil.copytree(Path(RUN['tmp'].name)/'runs', Path(retain)/'runs')
            for name in ('result', 'reference'):
                if name in RUN:
                    (Path(retain)/f'{name}.stdout').write_text(RUN[name].stdout)
                    (Path(retain)/f'{name}.stderr').write_text(RUN[name].stderr)
        RUN['tmp'].cleanup()


def read(name, run=None):
    return json.loads(((run or RUN['run'])/name).read_text())


def attempts(run=None):
    return [json.loads(line) for line in ((run or RUN['run'])/'attempts.jsonl').read_text().splitlines()]


def verify(run):
    result = subprocess.run([sys.executable, str(HERE/'verify.py'), '--run', run, '--prepared', RUN['prepared']],
                            capture_output=True, text=True, timeout=300)
    return result, (json.loads(result.stdout) if result.stdout.strip().startswith('{') else None)


@unittest.skipUnless(HAVE_CACHE, 'dedicated model/wheel cache not present')
class Collection(unittest.TestCase):
    def test_collection_completes_and_cleans_up_owned_resources(self):
        self.assertEqual(RUN['result'].returncode, 0, RUN['result'].stderr[-3000:])
        self.assertEqual(read('cleanup.json')['remaining_owned_pods_and_configmaps'], 0)
        self.assertEqual(read('design.json')['environment'], 'local-fake-kubectl')

    def test_every_source_publishes_from_its_own_edge(self):
        evidence = read('sources-evidence.json')
        self.assertEqual({s: e['node'] for s, e in evidence.items()},
                         {'cam-e0': 'etri-dev0003-raspi5', 'cam-e6': 'etri-dev0006-raspi5', 'cam-e8': 'etri-dev0008-raspi5',
                          'txt-e1': 'etri-dev0002-raspi5', 'txt-e9': 'etri-dev0009-jetorn'})
        for sid in ('cam-e0', 'cam-e6', 'cam-e8'):
            ev = evidence[sid]['evidence']
            self.assertTrue(ev['mediamtx']['alive'] and ev['mediamtx']['cmdline'][0].endswith('mediamtx'))
            self.assertIn('publish', ev['publisher']['cmdline'])
            path = next(p for p in ev['mediamtx_paths'] if p['name'] == sid)
            self.assertTrue(path['ready'])
            self.assertGreater(path['bytes_received'], 0)
            self.assertEqual(ev['freeze']['codec']['name'], 'h264')
            self.assertTrue(all(f['loop_consistent'] for f in ev['freeze']['frozen'].values()))

    def test_failed_node_is_recorded_and_excluded_not_filled(self):
        nodes = read('qualification.json')['nodes']
        for task in ('V', 'T', 'F'):
            self.assertIn('ImagePullBackOff', json.dumps(nodes[task][FAILED_NODE]['ready_problem']))
        for flow in read('profiles-frozen.json')['candidates'].values():
            for task in ('V', 'T', 'F'):
                self.assertNotIn(FAILED_NODE, flow['candidates'][task])

    def test_numerically_wrong_node_is_excluded_before_planning_with_reasons(self):
        quality = read('candidate-quality.json')
        expected = dict(V='model', T='highest-similarity', F='fus')
        for flow_id, flow in read('profiles-frozen.json')['candidates'].items():
            for task in ('V', 'T', 'F'):
                self.assertNotIn(CORRUPT_NODE, flow['candidates'][task])
                self.assertEqual(len(flow['candidates'][task]), 7)
                reasons = json.dumps(quality['workflows'][flow_id][task]['excluded'][CORRUPT_NODE])
                self.assertIn(expected[task], reasons)
            # kube-default observes among exactly the same qualified candidates as the profile policies.
            self.assertEqual(read(f'scheduler-{flow_id}.json')['candidates'], flow['candidates'])
        self.assertEqual(sorted(read('calibration-reference.json')['seqs']), [0, 1, 2])
        failed_rows = [r for r in quality['rows'] if r['node'] == CORRUPT_NODE and r['task'] == 'T']
        self.assertTrue(failed_rows and all(r['problems'] for r in failed_rows))

    def test_v_planning_cost_is_decode_plus_inference_per_row(self):
        qual = read('qualification.json')
        for flow_id, profile in read('profiles-frozen.json')['profiles'].items():
            video = next(f['video'] for f in read('design.json')['workflows'] if f['id'] == flow_id)
            v = next(t for t in profile['tasks'] if t['id'] == 'V')
            for node, cost in v['compute_ms'].items():
                rows = [r['reply']['receipt'] for r in qual['rows'] if r['task'] == 'V' and r['node'] == node
                        and r['tag'].startswith(video+'/') and r['tag'].endswith('/cal')]
                self.assertEqual(len(rows), 3)
                self.assertAlmostEqual(cost, statistics.median(r['decode_ms']+r['compute_ms'] for r in rows))
                self.assertGreater(cost, statistics.median(r['compute_ms'] for r in rows))

    def test_f_transfer_bytes_are_actual_run_bodies_including_receipts(self):
        sizes = read('profiles-frozen.json')['payload_sizes']
        run_rows = read('run-response-sizes.json')['rows']
        evaluation = [r for r in attempts() if r.get('status') == 'ok' and not r['warmup']]
        for flow_id, size in sizes.items():
            measured = [r['reply_bytes'] for r in run_rows if r['workflow'] == flow_id and r['tag'].endswith('/cal')]
            self.assertEqual(size['F'], int(statistics.median(measured)))
            self.assertGreater(size['categories']['F_receipt_envelope_bytes'], size['categories']['F_task_bytes'])
            observed = statistics.median(r['result_bytes'] for r in evaluation if r['workflow'] == flow_id)
            self.assertLess(abs(size['F']-observed)/observed, 0.1)

    def test_every_scheduled_attempt_is_recorded_once(self):
        jobs = read('schedule-frozen.json')['jobs']
        rows = attempts()
        self.assertEqual(len(rows), 4*len(jobs))
        self.assertEqual(sorted(r['job'] for r in rows), sorted(j['label'] for j in jobs for _ in range(4)))
        policies = [p for j in jobs for p in j['policies']]
        self.assertEqual(len(policies), len(set((j['workflow'], p) for j in jobs for p in j['policies'])))

    def test_protected_pod_env_is_never_persisted(self):
        for path in RUN['run'].rglob('*'):
            if path.is_file() and path.suffix in ('.json', '.stdout', '.stderr', '.jsonl'):
                self.assertNotIn('do-not-store', path.read_text(errors='replace'), path.name)


@unittest.skipUnless(HAVE_CACHE, 'dedicated model/wheel cache not present')
@unittest.skipIf(os.environ.get('MES_TEST_REUSE'), 'audit mutations need the run prepared bundle; not in reuse mode')
class Audit(unittest.TestCase):
    def mutated(self, change):
        tmp = tempfile.mkdtemp(dir=RUN['tmp'].name)
        run = Path(tmp)/'mes-fake'
        shutil.copytree(RUN['run'], run)
        change(run)
        return verify(run)

    def rewrite_attempts(self, run, edit):
        rows = attempts(run)
        rows = edit(rows)
        (run/'attempts.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))

    def test_reference_is_written_and_golden_run_passes_as_offline(self):
        self.assertEqual(RUN['reference'].returncode, 0, RUN['reference'].stderr[-3000:])
        result, summary = verify(RUN['run'])
        self.assertEqual(result.returncode, 0, result.stdout[-3000:]+result.stderr[-2000:])
        self.assertEqual(summary['status'], 'passed')
        self.assertTrue(summary['offline'])
        self.assertGreater(summary['quality']['checked_rows'], 0)

    def test_source_replaced_by_server_is_rejected(self):
        def edit(rows):
            row = next(r for r in rows if r.get('status') == 'ok')
            row['receipts'][0]['source']['x-source-node'] = 'etri-ser0002-cgnmsb'
            return rows
        result, summary = self.mutated(lambda run: self.rewrite_attempts(run, edit))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any('source' in e for e in summary['errors']))

    def test_model_mismatch_is_rejected(self):
        def edit(rows):
            row = next(r for r in rows if r.get('status') == 'ok')
            row['receipts'][0]['model_sha256'] = '0'*64
            return rows
        result, summary = self.mutated(lambda run: self.rewrite_attempts(run, edit))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any('model' in e for e in summary['errors']))

    def test_input_mismatch_is_rejected(self):
        def edit(rows):
            row = next(r for r in rows if r.get('status') == 'ok')
            row['receipts'][1]['input_sha256'] = '1'*64
            return rows
        result, summary = self.mutated(lambda run: self.rewrite_attempts(run, edit))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any('input' in e for e in summary['errors']))

    def test_dropped_sample_is_rejected(self):
        result, summary = self.mutated(lambda run: self.rewrite_attempts(run, lambda rows: rows[:-1]))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any('attempt' in e for e in summary['errors']))

    def test_same_placement_counted_twice_is_rejected(self):
        def change(run):
            schedule = read('schedule-frozen.json', run)
            first = schedule['jobs'][0]
            twin = dict(first, label=first['label']+'x', policies=[first['policies'][0]+'-copy'])
            schedule['jobs'].append(twin)
            (run/'schedule-frozen.json').write_text(json.dumps(schedule))
            rows = [dict(r, job=twin['label'], policies=twin['policies']) for r in attempts(run) if r['job'] == first['label']]
            with (run/'attempts.jsonl').open('a') as handle:
                handle.write(''.join(json.dumps(r)+'\n' for r in rows))
        result, summary = self.mutated(change)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any('duplicate' in e for e in summary['errors']))


if __name__ == '__main__':
    unittest.main()
