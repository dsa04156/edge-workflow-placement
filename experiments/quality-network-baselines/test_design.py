"""Plan/CLI/manifest/guard contracts (no kubectl ever runs here).

Covers: release binding and rc 2 before any kubectl call, identical seeded arrivals for every policy, balanced policy
order, pilot -> frozen loads (refuses without a sustained rate), candidate coverage from the observed inventory
(CPU+GPU+NPU, several multi-choice tasks, NPU ASR excluded), Pod security/placement, edge stream refusal.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import baselines  # noqa: E402
import design  # noqa: E402
import live  # noqa: E402
import policies  # noqa: E402

ROOT = design.ROOT
INVENTORY = ROOT/'artifacts/quality-network-baselines/preflight-20261026T020419Z/node-inventory.json'
EXPORT = ROOT/'artifacts/quality-network-heterogeneous/export-20261023T075427Z'
DEPS = [ROOT/'artifacts/quality-network-heterogeneous/deps-20261023T075919Z', ROOT/'artifacts/quality-network-heterogeneous/deps-20261023T080028Z']


def workers():
    return design.dag_workers(live.qualify.workers_for(live.qualify.catalog.load_audit(INVENTORY)))


class Candidates(unittest.TestCase):
    def test_current_inventory_offers_cpu_gpu_npu_and_multiple_choices(self):
        ws = workers()
        cands = design.candidates(ws)
        cover = design.coverage(cands)
        self.assertTrue(cover['ok'], cover)
        self.assertGreaterEqual(len(cover['multi_choice_tasks']), 4)
        self.assertEqual(set(cands['image_preprocess']) | set(cands['audio_preprocess']), {'cpu-c', 'cpu-e0'})
        self.assertIn('npu-c', cands['vision'])
        self.assertNotIn('npu-c', cands['speech'])  # known-broken NPU ASR probe is not a candidate
        self.assertTrue(any(k.startswith('jgpu-') for k in cands['speech']))
        self.assertEqual({w['backend'] for w in ws}, {'cpu-torch', 'openvino-npu', 'cuda-torch'})
        self.assertIsNotNone(design.fixed_plan(ws))

    def test_dag_pairs_cover_every_edge_of_every_plan(self):
        cands = design.candidates(workers())
        pairs = set(design.dag_pairs(cands))
        for t, ws in cands.items():
            for w in ws:
                for s, d, _ in policies.EDGES:
                    if d == t:
                        srcs = [policies.ORIGIN] if s == policies.ORIGIN else cands[s]
                        self.assertTrue(all((a, w) in pairs for a in srcs))


class Traces(unittest.TestCase):
    def test_arrivals_are_seeded_identical_and_inside_the_window(self):
        qids = list(range(40))
        a = design.arrivals(2.0, 60.0, 7, qids)
        self.assertEqual(a, design.arrivals(2.0, 60.0, 7, qids))
        self.assertNotEqual(a, design.arrivals(2.0, 60.0, 8, qids))
        self.assertTrue(all(0 < x['offset_s'] < 60 for x in a))
        self.assertEqual([x['index'] for x in a], list(range(len(a))))
        self.assertTrue(80 < len(a) < 160)

    def test_compare_cells_share_one_trace_per_block_condition_load(self):
        frozen = dict(loads_rps=dict(low=0.3, near_saturation=0.8, high=1.3))
        cells = design.compare_cells(frozen)
        names = [s['name'] for s in design.POLICY_SPECS]
        groups = {}
        for c in cells:
            groups.setdefault((c['block'], c['condition'], c['load']), []).append(c)
        for group in groups.values():
            self.assertEqual(sorted(c['policy'] for c in group), sorted(names))
            self.assertEqual(len({(c['seed'], c['rate_rps']) for c in group}), 1)
        first_positions = {}
        for order in design.balanced_orders(names, list(groups)).values():
            first_positions[order[0]] = first_positions.get(order[0], 0) + 1
        self.assertEqual(set(first_positions), set(names))

    def test_freeze_loads_rule_and_refusal(self):
        pilot = [dict(condition='good', policy='fixed', rate_rps=r, requests=n, ok=ok, admission_drops=0)
                 for r, n, ok in ((0.25, 5, 5), (0.5, 10, 10), (1.0, 20, 14), (2.0, 40, 12))]
        pilot.append(dict(condition='good', policy='dag_reopt', rate_rps=1.0, requests=20, ok=19, admission_drops=0))
        pilot.append(dict(condition='limited', policy='dag_reopt', rate_rps=2.0, requests=40, ok=40, admission_drops=0))
        frozen = design.freeze_loads(pilot)
        self.assertEqual(frozen['saturation_rps'], 1.0)
        self.assertEqual(frozen['loads_rps'], dict(low=0.3, near_saturation=0.8, high=1.3))
        with self.assertRaises(ValueError):
            design.freeze_loads([dict(condition='good', policy='fixed', rate_rps=1.0, requests=20, ok=2, admission_drops=0)])


class Release(unittest.TestCase):
    def test_release_must_bind_every_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'release.json'
            bind = dict(stage='pilot', run_id='qnb-a', plan_sha256='p', shipped_sha256='s', budgets_sha256='b')
            self.assertFalse(design.release_ok(path, bind)[0])
            path.write_text(json.dumps(dict(release='RELEASE', **bind)))
            self.assertTrue(design.release_ok(path, bind)[0])
            path.write_text(json.dumps(dict(release='RELEASE', **dict(bind, run_id='qnb-b'))))
            self.assertEqual(design.release_ok(path, bind), (False, "release does not bind: ['run_id']"))
            path.write_text('RELEASE p s b')
            self.assertFalse(design.release_ok(path, bind)[0])


class PlanAndCli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.plan_dir = Path(cls.tmp.name)/'plan'
        cls.plan, cls.plan_sha = baselines.plan(cls.plan_dir, INVENTORY, EXPORT, DEPS, hash_files=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_plan_records_catalog_quality_status_and_not_implemented_priors(self):
        self.assertEqual(self.plan['quality']['mode'], 'diagnostic')
        self.assertIn('0.355', self.plan['quality']['status'])
        self.assertEqual({v['status'] for v in self.plan['prior_adapters'].values()}, {'not_implemented'})
        cat = json.loads((self.plan_dir/'catalog.json').read_text())
        self.assertEqual(cat['nodes'], 12)
        self.assertIn('deployed candidate', {r['dag_role'] for r in cat['rows']})
        statuses = {r['status'] for r in cat['rows']}
        self.assertTrue({'node_unavailable', 'occupied'} & statuses)
        corpus = json.loads((self.plan_dir/'corpus.json').read_text())
        self.assertEqual(len(corpus['files']), 120)
        paths = {e['path'] for e in json.loads((self.plan_dir/'assets-manifest.json').read_text())['files']}
        self.assertIn('onnx/vision-static-448.onnx', paths)
        self.assertNotIn('onnx/speech-dynamic.onnx', paths)
        self.assertIn('netctl/pyroute2-0.9.6-py3-none-any.whl', paths)

    def test_execute_without_release_is_refused_before_kubectl(self):
        with mock.patch.object(subprocess, 'run', side_effect=AssertionError('kubectl must not run')) as run:
            rc = baselines.main(['execute', '--plan', str(self.plan_dir), '--stage', 'pilot', '--run-id', 'qnb-t1',
                                 '--release', str(self.plan_dir/'missing.json'), '--output-root', self.tmp.name])
            self.assertEqual(rc, 2)
            run.assert_not_called()

    def test_execute_with_wrong_binding_is_refused_and_compare_needs_frozen(self):
        release = self.plan_dir/'wrong-release.json'
        bind, drift = baselines.binding(self.plan_dir, 'pilot', 'qnb-t2', verify_files=False)
        self.assertEqual(drift, [])
        release.write_text(json.dumps(dict(release='RELEASE', **dict(bind, budgets_sha256='x'))))
        with mock.patch.object(subprocess, 'run', side_effect=AssertionError('kubectl must not run')):
            self.assertEqual(baselines.main(['execute', '--plan', str(self.plan_dir), '--stage', 'pilot', '--run-id', 'qnb-t2',
                                             '--release', str(release), '--output-root', self.tmp.name]), 2)
            self.assertEqual(baselines.main(['execute', '--plan', str(self.plan_dir), '--stage', 'compare', '--run-id', 'qnb-t2',
                                             '--release', str(release), '--output-root', self.tmp.name]), 2)

    def test_dry_run_prints_binding_without_kubectl(self):
        with mock.patch.object(subprocess, 'run', side_effect=AssertionError('kubectl must not run')):
            self.assertEqual(baselines.main(['dry-run', '--plan', str(self.plan_dir), '--stage', 'pilot', '--run-id', 'qnb-t3']), 0)

    def test_pod_manifests_security_and_placement(self):
        run = 'qnb-t4'
        ws = self.plan['workers']
        pods = [live.worker_pod(run, w, 'http://10.0.0.1:8080', 'x') for w in ws]
        pods += [live.gateway_pod(run, 'http://10.0.0.1:8080'), live.generator_pod(run), live.cache_pod(run)]
        for pod in pods:
            spec = pod['spec']
            self.assertEqual(pod['metadata']['labels']['experiment'], design.EXPERIMENT)
            self.assertFalse(spec.get('hostNetwork') or spec.get('hostPID') or spec.get('hostIPC'))
            self.assertFalse(any('hostPath' in v for v in spec['volumes']))
            self.assertFalse(spec['automountServiceAccountToken'])
            self.assertLessEqual(spec['activeDeadlineSeconds'], design.BUDGETS['cache_pod_lifetime_s'])
            for c in spec['containers']:
                sc = c['securityContext']
                self.assertFalse(sc.get('privileged'))
                self.assertFalse(sc['allowPrivilegeEscalation'])
                added = sc['capabilities'].get('add', [])
                if added:
                    self.assertEqual((pod['metadata']['name'], c['name'], added), (run+'-gateway', 'netctl', ['NET_ADMIN']))
        node = lambda p: p['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms'][0]['matchFields'][0]['values'][0]
        by = {p['metadata']['name']: p for p in pods}
        self.assertEqual(node(by[run+'-generator']), design.ORIGIN_NODE)
        self.assertEqual(node(by[run+'-gateway']), design.SERVER_NODE)
        for w in ws:
            pod = by[run+'-'+w['key']]
            env = {e['name']: e.get('value') for e in pod['spec']['containers'][0]['env']}
            self.assertEqual(env['WORKER_MODULE'], 'dag_worker')
            self.assertEqual(env['STAGES'].split(','), w['stages'])
            self.assertEqual(node(pod), w['node'])

    def test_session_refuses_edge_streams_before_kubectl(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(subprocess, 'run', side_effect=AssertionError('kubectl must not run')):
            s = live.BaselineSession(tmp, 'qnb-t5', self.plan)
            for key in ('generator', next(w['key'] for w in self.plan['workers'] if w['edge'])):
                with self.assertRaises(RuntimeError):
                    s.call('x', ['exec', '-n', live.NS, 'qnb-t5-'+key, '--', 'true'])
                self.assertEqual(s.call('y', ['logs', '-n', live.NS, 'qnb-t5-'+key]), '')
            with self.assertRaises(ValueError):
                live.BaselineSession(tmp, 'qnh-wrong', self.plan)

    def test_config_map_ships_exactly_the_hashed_sources(self):
        cm = live.config_map('qnb-t6')
        self.assertEqual(set(cm['data']), set(self.plan['shipped_sources']))
        self.assertLess(sum(len(v) for v in cm['data'].values()), 900_000)


if __name__ == '__main__':
    unittest.main()
