"""Collector CLI seam: --help, dry-run design without kubectl, and the owned-resource cleanup contract.

The live path is driven through the public CLI against fake_rtk.py (test-only kubectl stand-in): no Pod
ever becomes Ready, so the run aborts during deployment and must clean up only what it created, by UID.
"""
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
HAVE_ASSETS = (ROOT/'artifacts/topology-profile-assets/20261019T091725Z/selected-data/pairs.json').exists() and \
    (ROOT/'artifacts/topology-profile-live/load-pairs.json').exists()
NS = 'topology-profile-lab'
STREAM_VERBS = ('exec', 'attach', 'logs', 'cp', 'port-forward', 'top', 'debug')


def run(tmp, *args, env_extra=None):
    bin_dir = tmp/'bin'
    bin_dir.mkdir(exist_ok=True)
    shutil.copy(HERE/'fake_rtk.py', bin_dir/'rtk')
    (bin_dir/'rtk').chmod(0o755)
    env = dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}', FAKE_STATE=str(tmp/'state.json'), FAKE_LOG=str(tmp/'calls.jsonl'),
               PYTHONDONTWRITEBYTECODE='1', **(env_extra or {}))
    return subprocess.run([sys.executable, str(HERE/'run_pilot.py'), *args], capture_output=True, text=True, env=env, timeout=300)


def calls(tmp):
    path = tmp/'calls.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class Help(unittest.TestCase):
    def test_help_lists_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run(Path(tmp), '--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('--dry-run', result.stdout)
        self.assertIn('--execute', result.stdout)

    def test_a_mode_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertNotEqual(run(Path(tmp)).returncode, 0)
            self.assertEqual(calls(Path(tmp)), [])


@unittest.skipUnless(HAVE_ASSETS, 'frozen VQA assets not present')
class DryRun(unittest.TestCase):
    def test_dry_run_prints_bounds_estimate_and_never_calls_kubectl(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            result = run(tmp, '--dry-run', '--output-root', str(tmp/'runs'))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls(tmp), [])
            self.assertFalse((tmp/'runs').exists())
        design = json.loads(result.stdout)
        self.assertEqual(len(design['placements']), 4)
        self.assertEqual(len(design['condition_order']), 4)
        self.assertEqual(design['phases']['calibration_trace_phases'], 16)
        self.assertEqual(design['phases']['evaluation_unique_plan_phases'], dict(min=4, max=16))
        self.assertEqual(design['phases']['qualification_requests'], 240)
        self.assertLess(design['expected_runtime_s']['min'], design['expected_runtime_s']['max'])
        self.assertEqual(design['slo']['slo_s'], 2.0)
        self.assertEqual(design['slo']['cancel_timeout_s'], 10)
        self.assertEqual(design['quality']['qmin'], .4)
        self.assertEqual(len(design['inputs']['calibration_ids']), 20)
        self.assertEqual(len(design['inputs']['evaluation_ids']), 40)
        self.assertIn('not an unseen holdout', design['inputs']['selection'])
        self.assertIn('no statistical superiority', design['scope'])
        self.assertIn('goodput-optimal policy', design['scope'])
        # Frozen reference answers: calibration passes the exploratory threshold, evaluation does not.
        self.assertAlmostEqual(design['quality']['reference_quality']['calibration']['answered_mean'], .515)
        self.assertAlmostEqual(design['quality']['reference_quality']['evaluation']['answered_mean'], .355)


@unittest.skipUnless(HAVE_ASSETS, 'frozen VQA assets not present')
class OwnedCleanup(unittest.TestCase):
    def execute(self, tmp, run_id, **hooks):
        result = run(tmp, '--execute', '--output-root', str(tmp/'runs'), '--run-id', run_id,
                     env_extra={k: (v if isinstance(v, str) else json.dumps(v)) for k, v in hooks.items()})
        out = tmp/'runs'/run_id
        state = json.loads((tmp/'state.json').read_text())
        status = json.loads((out/'run-status.json').read_text()) if (out/'run-status.json').exists() else None
        cleanup = json.loads((out/'cleanup.json').read_text()) if (out/'cleanup.json').exists() else None
        return result, state, status, cleanup, out

    def test_failed_deployment_deletes_its_own_pod_by_uid_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            result, state, status, cleanup, out = self.execute(tmp, 'qnv-abort')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(state['objects'], {})
            self.assertEqual(status['status'], 'error')
            self.assertIn('timed out', next(out.glob('*-cache-ready.stderr')).read_text())
            self.assertEqual(cleanup['remaining_owned_pods_and_configmaps'], 0)
            self.assertTrue(cleanup['cleanup_verified'])
            self.assertEqual([r['delete'] for r in cleanup['created']], ['deleted'])
            deletes = [c for c in calls(tmp) if 'delete' in c]
            self.assertTrue(deletes and all('--raw' in c for c in deletes))
            self.assertEqual(status['audit'], 'complete')
            self.assertTrue((out/'phase-002-deploy.end.json').exists())
            self.assertEqual(json.loads((out/'phase-002-deploy.end.json').read_text())['status'], 'error')

    def test_replaced_same_name_object_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            result, state, status, cleanup, _ = self.execute(tmp, 'qnv-replaced', FAKE_REPLACE_ON_WAIT='1')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Pod/qnv-replaced-cache', state['objects'])
            self.assertEqual(cleanup['preserved_not_ours'][0]['reason'], 'refused-uid-mismatch')
            self.assertEqual(cleanup['remaining_owned_pods_and_configmaps'], 0)

    def execs(self, tmp):
        return [c for c in calls(tmp) if 'exec' in c]

    def streams(self, tmp):
        # Every kubelet stream verb (exec/attach/logs/cp/port-forward/top/debug) that reached kubectl.
        return [c for c in calls(tmp) if len(c) > 3 and c[3] in STREAM_VERBS]

    def test_ready_cache_replacement_on_an_edge_node_gets_no_stream_of_any_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            result, state, status, cleanup, out = self.execute(tmp, 'qnv-cachexn', FAKE_READY='1', FAKE_REPLACE_READY='cache',
                                                               FAKE_WRONG_NODE='cache')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(state['objects']['Pod/qnv-cachexn-cache']['spec']['nodeName'], 'etri-dev0003-raspi5')
            self.assertEqual(self.streams(tmp), [])
            self.assertTrue(list(out.glob('*-logs-*cache*.skipped.json')))
            self.assertEqual(cleanup['preserved_not_ours'][0]['reason'], 'refused-uid-mismatch')
            self.assertEqual(cleanup['remaining_owned_pods_and_configmaps'], 0)

    def test_ready_same_name_cache_replacement_gets_no_upload_and_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            result, state, status, cleanup, _ = self.execute(tmp, 'qnv-cachex', FAKE_READY='1', FAKE_REPLACE_READY='cache')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('not the UID this run created', status['error'])
            self.assertEqual(self.streams(tmp), [])
            self.assertEqual(state['objects']['Pod/qnv-cachex-cache']['metadata']['labels'], dict(experiment='someone-else'))
            self.assertEqual(cleanup['preserved_not_ours'][0]['reason'], 'refused-uid-mismatch')

    def test_own_cache_on_an_edge_node_gets_no_stream_and_is_deleted_by_uid(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            result, state, status, cleanup, _ = self.execute(tmp, 'qnv-cachen', FAKE_READY='1', FAKE_WRONG_NODE='cache')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('runs on etri-dev0003-raspi5, expected etri-ser0002-cgnmsb', status['error'])
            self.assertEqual(self.streams(tmp), [])
            self.assertEqual(state['objects'], {})
            self.assertEqual([r['delete'] for r in cleanup['created']], ['deleted'])

    def test_ready_gateway_replacement_gets_no_peer_file_or_shaping(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            result, state, status, cleanup, out = self.execute(tmp, 'qnv-gwx', FAKE_READY='1', FAKE_REPLACE_READY='gateway')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('gateway', status['error'])
            # Only the owned, node-checked cache received the guarded, logged upload.
            self.assertEqual([c[c.index('-n')+2] for c in self.execs(tmp)], ['qnv-gwx-cache'])
            # Diagnostic logs only reach owned, freshly verified server Pods; never the replaced gateway.
            targets = {c[c.index('-n')+2] for c in self.streams(tmp)}
            self.assertNotIn('qnv-gwx-gateway', targets)
            self.assertLessEqual(targets, {'qnv-gwx-cache', 'qnv-gwx-v-cloud', 'qnv-gwx-s-cloud', 'qnv-gwx-fusion'})
            upload = json.loads(next(out.glob('*-cache-upload.event.json')).read_text())
            self.assertEqual(upload['status'], 'completed')
            self.assertIn('Pod/qnv-gwx-gateway', state['objects'])
            self.assertEqual([p['name'] for p in cleanup['preserved_not_ours']], ['qnv-gwx-gateway'])
            self.assertEqual(cleanup['remaining_owned_pods_and_configmaps'], 0)

    def test_reused_run_label_refuses_before_any_create(self):
        squatter = dict(kind='Pod', metadata=dict(name='other', namespace=NS, labels={'experiment': 'x', 'run-id': 'qnv-reuse'}))
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            result, state, status, cleanup, _ = self.execute(tmp, 'qnv-reuse', FAKE_PRESEED=[squatter])
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(any(c[2:3] == ['create'] or 'create' in c for c in calls(tmp)))
            self.assertIn('Pod/other', state['objects'])
            self.assertEqual(cleanup['created'], [])

    def test_protected_workload_change_marks_the_audit_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _, _, status, cleanup, _ = self.execute(tmp, 'qnv-protected', FAKE_PROTECTED_CHANGE='1')
            self.assertEqual(status['audit'], 'partial')
            self.assertEqual(len(status['protected_changes']), 1)
            self.assertTrue(cleanup['cleanup_verified'])


@unittest.skipUnless(HAVE_ASSETS, 'frozen VQA assets not present')
class EdgeControlBoundary(unittest.TestCase):
    """No kubelet stream request may reach kubectl for an edge-node Pod (unpatched EdgeCore)."""

    def session(self, tmp):
        sys.path.insert(0, str(HERE))
        import run_pilot
        bin_dir = tmp/'bin'
        bin_dir.mkdir()
        shutil.copy(HERE/'fake_rtk.py', bin_dir/'rtk')
        (bin_dir/'rtk').chmod(0o755)
        env = dict(PATH=f'{bin_dir}:{os.environ["PATH"]}', FAKE_STATE=str(tmp/'state.json'), FAKE_LOG=str(tmp/'calls.jsonl'))
        patcher = unittest.mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        return run_pilot.VqaSession(tmp/'runs', 'qnv-edge'), run_pilot

    def test_exec_and_attach_to_edge_pods_are_refused_before_kubectl(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            session, _ = self.session(tmp)
            for verb, target in (('exec', 'qnv-edge-source-e0'), ('exec', 'qnv-edge-v-edge'), ('attach', 'qnv-edge-s-edge'),
                                 ('port-forward', 'qnv-edge-source-e1'), ('top', 'etri-dev0003-raspi5')):
                with self.assertRaisesRegex(RuntimeError, 'refused'):
                    session.call('probe', [verb, '-n', NS, target, '--', 'true'])
            self.assertEqual(calls(tmp), [])
            self.assertEqual(len(list(session.out.glob('*-probe.refused.json'))), 5)

    def test_edge_logs_are_skipped_explicitly_and_server_streams_still_pass_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            session, _ = self.session(tmp)
            self.assertEqual(session.call('logs-v', ['logs', '-n', NS, 'qnv-edge-v-edge', '-c', 'worker']), '')
            self.assertEqual(calls(tmp), [])
            self.assertTrue(list(session.out.glob('*-logs-v.skipped.json')))
            # A server Pod this run never recorded is skipped too (fresh GET finds no creation UID).
            self.assertEqual(session.call('logs-u', ['logs', '-n', NS, 'qnv-edge-fusion', '-c', 'worker']), '')
            self.assertFalse(any('logs' in c for c in calls(tmp)))
            self.assertTrue(list(session.out.glob('*-logs-u.skipped.json')))
            session.owned_state = lambda key, stem: dict(metadata=dict(uid='u'), spec=dict(nodeName='etri-ser0002-cgnmsb'))
            self.assertEqual(session.call('logs-f', ['logs', '-n', NS, 'qnv-edge-fusion', '-c', 'worker']), '')
            self.assertEqual(calls(tmp)[-1][2:3], ['--request-timeout=30s'])
            self.assertIn('qnv-edge-fusion', calls(tmp)[-1])

    def test_edge_pods_have_no_netctl_sidecar_and_server_pods_keep_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            session, _ = self.session(Path(tmp))
            for key, zone, task in (('source-e0', 'e0', 'source'), ('v-edge', 'e0', 'vision'), ('s-edge', 'e1', 'speech')):
                pod = session.pod(key, zone, task, 'http://cache')
                self.assertEqual([c['name'] for c in pod['spec']['containers']], ['worker'])
                self.assertFalse(any('NET_ADMIN' in json.dumps(c) for c in pod['spec']['containers']))
            pod = session.pod('fusion', 'c', 'fusion', 'http://cache')
            self.assertEqual([c['name'] for c in pod['spec']['containers']], ['worker', 'netctl'])


@unittest.skipUnless(HAVE_ASSETS, 'frozen VQA assets not present')
class SourceTransferEndpoint(unittest.TestCase):
    """The reused source handler (edge_client /transfer_profile) appends /sink to target_url itself."""

    def setUp(self):
        sys.path.insert(0, str(ROOT/'experiments/real-topology-live'))
        sys.path.insert(0, str(HERE))
        import run_pilot
        from edge_client import source_server
        from http_runtime import decode_body, digest, payload
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        patcher = unittest.mock.patch.dict(os.environ, dict(NODE_NAME='source-fixture'))
        patcher.start()
        self.addCleanup(patcher.stop)
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        (tmp/'pairs.json').write_text('{"pairs":[]}')
        (tmp/'reference.jsonl').write_text('')
        self.paths, self.blocked = [], [False]
        test = self

        class Sink(BaseHTTPRequestHandler):
            def do_POST(self):
                test.paths.append(self.path)
                direct = self.path == '/sink'
                relay = re.fullmatch(r'/to/[a-z0-9-]+/sink', self.path)
                if not (direct or relay) or (direct and test.blocked[0]):
                    self.send_error(503 if direct else 404)
                    return
                body = payload(decode_body(self.rfile.read(int(self.headers['Content-Length'])), self.headers.get('Content-Type'))['data'])
                raw = json.dumps(dict(sha256=digest(body), bytes=len(body))).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass
        servers = [source_server(tmp, host='127.0.0.1', port=0), ThreadingHTTPServer(('127.0.0.1', 0), Sink)]
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        source, sink = (f'http://127.0.0.1:{s.server_port}' for s in servers)
        self.session = run_pilot.VqaSession(tmp/'runs', 'qnv-xfer')
        self.session.zones = run_pilot.ZONES
        self.session.urls = {k: (source if k.startswith('source-') else sink) for k in [*run_pilot.ZONES, 'gateway']}
        self.session.actual = {k: dict(metadata=dict(name='qnv-xfer-'+k, uid=k), spec=dict(nodeName='source-fixture'))
                               for k in [*run_pilot.ZONES, 'gateway']}
        self.session.edge = ['10.0.0.1']
        self.run_pilot = run_pilot

    def test_guard_proofs_reach_the_real_sink_handler_directly_and_through_the_relay(self):
        session = self.session
        session.net = lambda key, spec, stem: self.blocked.__setitem__(0, spec['action'] == 'block') or {}
        session.execute = lambda key, code, stem, timeout=60, **kw: json.dumps(dict(direct_ok=not self.blocked[0]))
        session.pod_rpc = lambda *args, **kw: dict(status='ready')
        session.guard(True)
        session.guard(False)
        self.assertEqual(sorted(set(self.paths)), ['/sink', '/to/fusion/sink'])
        proof = json.loads((session.out/'guard-block-proof.json').read_text())['proofs']
        self.assertFalse(proof['source-e0']['edge_direct_to_fusion_ok'])
        self.assertTrue(json.loads((session.out/'guard-unblock-proof.json').read_text())['proofs']['source-e1']['edge_direct_to_fusion_ok'])

    def test_calibration_transfer_profile_posts_each_source_hop_once_to_sink(self):
        session = self.session
        live = str(ROOT/'experiments/real-topology-live')
        # Server-origin hops: the exact in-Pod code, run locally with /config replaced by the reused module directory.
        session.execute = lambda key, code, stem, timeout=60, **kw: subprocess.check_output(
            [sys.executable, '-c', code.replace("sys.path.insert(0,'/config')", f'sys.path.insert(0,{live!r})')], text=True, timeout=timeout)
        cal = [p['question_id'] for p in session.pairs if p['split'] == 'calibration']
        rows = session.transfer_profile(2, [dict(question_id=q, answers=['yes']) for q in cal])
        self.assertEqual(len(rows), len(self.run_pilot.HOPS)*len(cal))
        self.assertEqual(sorted(set(self.paths)), ['/sink', '/to/fusion/sink', '/to/s-cloud/sink', '/to/source-e0/sink', '/to/v-cloud/sink'])
        self.assertEqual(len(self.paths), len(rows))

    def test_a_sink_suffixed_target_is_refused_before_any_request(self):
        with self.assertRaisesRegex(ValueError, 'base URL'):
            self.session.source_transfer('source-e0', self.session.urls['gateway']+'/to/fusion/sink', b'x', 1, 'suffix')
        self.assertEqual(self.paths, [])


CHAIN_PROBE = r"""
import hashlib, json, os, sys, tempfile, threading
from pathlib import Path
sys.path.insert(0, sys.argv[1])
os.environ['NODE_NAME'] = 'fixture'
import http_runtime
from edge_client import source_server
assert getattr(http_runtime.request, 'vqa_adapter', False), 'adapter not installed'
root = Path(tempfile.mkdtemp())
(root/'a.pcm').write_bytes(b'pcm')
(root/'pairs.json').write_text(json.dumps({'pairs': [dict(question_id=1, split='calibration', raw_audio_path=str(root/'a.pcm'),
                                                        raw_audio_sha256=hashlib.sha256(b'pcm').hexdigest())]}))
(root/'reference.jsonl').write_text('')
source = source_server(root, host='127.0.0.1', port=0)
threading.Thread(target=source.serve_forever, daemon=True).start()
src = 'http://127.0.0.1:%d' % source.server_port
def raising(error):
    def adapter(_):
        raise error
    return adapter
P = http_runtime.ProtocolError
cases = dict(nested_504=P('Expired during model execution', 504), genuine_500=RuntimeError('model crashed: timed out reading weights'),
             nested_429=P('Serving queue capacity reached', 429), nested_502=P('upstream timed out', 502))
out = {}
body = lambda rid, speech: dict(run_id='r', request_id=rid, question_id=1, representation='raw', speech_url=speech,
                                fusion_url='http://127.0.0.1:9', remaining_s=5)
for name, error in cases.items():
    with http_runtime.serve('speech', raising(error)) as speech:
        try:
            http_runtime.request(src+'/branch', body(name, speech), timeout=10)
            out[name] = 'no error'
        except Exception as caught:
            out[name] = repr(caught)
        if name == 'nested_504':
            try:
                http_runtime.request(speech+'/execute', dict(run_id='r', request_id='direct', data=b'x', fusion_url='http://127.0.0.1:9',
                                                            remaining_s=5), timeout=10)
            except Exception as caught:
                out['direct_504'] = repr(caught)
print(json.dumps(out))
"""


@unittest.skipUnless(HAVE_ASSETS, 'frozen VQA assets not present')
class InjectedRequestAdapter(unittest.TestCase):
    """The owned ConfigMap carries the legacy runtime text plus a pilot request adapter; legacy disk files are unchanged."""

    def setUp(self):
        sys.path.insert(0, str(HERE))
        import run_pilot
        self.run_pilot = run_pilot
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.tmp = tmp
        self.session = run_pilot.VqaSession(tmp/'runs', 'qnv-adapter')
        self.cm = self.session.config_map('http://cache')

    def test_config_map_injects_only_the_adapter_hook_into_the_runtime_text(self):
        live = ROOT/'experiments/real-topology-live'
        data = self.cm['data']
        legacy = (live/'http_runtime.py').read_text()
        head, guard = legacy.split("\n\nif __name__ == '__main__':")
        self.assertTrue(data['http_runtime.py'].startswith(head))
        self.assertTrue(data['http_runtime.py'].endswith("\n\nif __name__ == '__main__':"+guard))
        self.assertEqual(data['http_runtime.py'][len(head):-len(guard)].count('_vqa_wrap'), 2)
        self.assertEqual(data['vqa_http_adapter.py'], (HERE/'vqa_http_adapter.py').read_text())
        for name in ('edge_client.py', 'model_adapter.py', 'bootstrap.py', 'load_trace.py', 'net_control.py', 'wan_gateway.py'):
            self.assertEqual(data[name], (live/name).read_text())
        self.assertEqual(self.cm['metadata']['labels'], {'experiment': 'quality-network-vqa', 'run-id': 'qnv-adapter'})

    def test_actual_handlers_carry_nested_504_to_the_ledger_and_keep_other_codes_errors(self):
        import metrics
        config = self.tmp/'config'
        config.mkdir()
        for name, text in self.cm['data'].items():
            (config/name).write_text(text)
        result = subprocess.run([sys.executable, '-c', CHAIN_PROBE, str(config)], capture_output=True, text=True, timeout=120,
                                cwd=self.tmp, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(result.returncode, 0, result.stderr)
        reprs = json.loads(result.stdout)
        # Terminal rows keep repr(error) exactly as load_trace records it.
        failure = {k: metrics.failure_evidence(dict(status='error', error=v)) for k, v in reprs.items()}
        self.assertEqual({k: f['outcome'] for k, f in failure.items()},
                         dict(nested_504='timeout', direct_504='timeout', genuine_500='error', nested_429='error', nested_502='error'))
        self.assertEqual(failure['nested_504']['http_codes'], [500, 504])
        self.assertEqual(failure['nested_504']['cause'], 'nested_server_deadline_http_504')
        self.assertEqual(failure['direct_504']['http_codes'], [504])
        self.assertEqual(failure['genuine_500']['http_codes'], [500, 500])
        self.assertEqual(failure['nested_429']['http_codes'], [500, 429])
        self.assertEqual(failure['nested_502']['http_codes'], [500, 502])
        self.assertIn('Expired during model execution', failure['nested_504']['message'])
        self.assertTrue(all(len(v) < 4096 for v in reprs.values()))


if __name__ == '__main__':
    unittest.main()
