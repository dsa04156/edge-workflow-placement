"""Explicit 9-Ready-node topology for the baseline collector/verifier.

The collect tests drive the real Pilot.deploy, qualify_all, transfer_profiles,
capacity and deploy_operators code; only kubectl, cache upload, data-plane RPC
and network control are faked (FakePilot seams from test_baseline_comparison).
"""
import base64
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import run_baseline_comparison as rb
import test_baseline_comparison as tb
import topology
import verify_baseline_comparison as vb
from run_placement import Pilot
from run_smoke import ASSETS, HERE, NODES

CONFIG = HERE/'topologies/ready-nodes-20261022.json'
INVENTORY = HERE.parents[1]/'.scratch/ready-node-expansion/inventory.json'
READY = ['etri-dev0003-raspi5', 'etri-dev0002-raspi5', 'etri-ser0002-cgnmsb', 'etri-dev0006-raspi5', 'etri-dev0008-raspi5',
    'etri-dev0009-jetorn', 'etri-ser0001-cg0msb', 'etri-ser0003-cg0ms0', 'etri-ser0004-cgnms0']
NOT_READY = ['etri-dev0001-jetorn', 'etri-dev0004-tedger', 'etri-dev0007-jetagx']
E0, E1, C = NODES['e0'], NODES['e1'], NODES['c']
C4, C1, C3, J9 = 'etri-ser0004-cgnms0', 'etri-ser0001-cg0msb', 'etri-ser0003-cg0ms0', 'etri-dev0009-jetorn'
ARCH = {n: 'amd64' if n in (C, C1, C4) else 'arm64' for n in READY}
# Hand fixture: ser0004 is the fastest server. Transfers: same node 1 ms; same zone 8 ms for raw image/audio,
# 5 ms otherwise; cross zone 50 (10 Mbps) / 400 (2 Mbps). Edge vision 304 makes both 2 Mbps branches tight (no ties).
EDGE_MS, SERVER_MS = dict(vision=304, speech=300, fusion=100), {C: (20, 30, 10), C1: (25, 35, 12), C3: (30, 40, 15), C4: (10, 15, 5)}
COMPUTE = {t: {n: (SERVER_MS[n][i] if n in SERVER_MS else EDGE_MS[t]) for n in READY} for i, t in enumerate(rb.TASKS)}
CROSS = {10: 50, 2: 400}


def node_items(state=None):
    """`kubectl get nodes -o json` shape for all 12 inventory nodes, with per-node overrides."""
    inventory = {n['name']: n for n in json.loads(INVENTORY.read_text())['nodes']}
    items = []
    for name, node in inventory.items():
        node = dict(node, **(state or {}).get(name, {}))
        items.append(dict(metadata=dict(name=name, labels={'kubernetes.io/arch': node['arch']}),
            spec=dict(unschedulable=True, taints=node['taints']) if node['unschedulable'] else dict(taints=node['taints']),
            status=dict(conditions=[dict(type='Ready', status='True' if node['ready'] else 'Unknown')],
                allocatable=dict(cpu='4', memory='8Gi'))))
    return items


class ExpandedPilot(tb.FakePilot):
    """FakePilot kubectl seam, but the real deploy/qualification/transfer/drain code of Pilot."""

    deploy = Pilot.deploy
    qualify_all = Pilot.qualify_all
    transfer_profiles = Pilot.transfer_profiles

    def __init__(self, out, choice, state=None, config=CONFIG):
        super().__init__(out, choice, cpu={n: '4' for n in READY})
        self.use_topology(config)
        # Pilot.__init__ state that FakePilot skips but the real qualify_all fills.
        self.compute, self.refs = {task: {} for task in rb.TASKS}, tb.REFS
        self.state, self.rpcs = state, []

    def call(self, stem, args, stdin=None, timeout=40):
        if args[:2] == ['get', 'nodes']:
            self.counter += 1
            self.calls.append((stem, args))
            # Session.call persists raw stdout; the verifier re-projects it.
            raw = json.dumps(dict(items=node_items(self.state)))
            (self.out/f'{self.counter:03d}-{stem}.stdout').write_text(raw)
            return raw
        return super().call(stem, args, stdin, timeout)

    def key(self, url):
        """Helper key of a source/sink URL, direct or through the gateway relay."""
        if '/to/' in url:
            return url.split('/to/')[1].split('/')[0]
        return next(k for k, v in self.urls.items() if url.startswith(v+'/') or url == v if k.startswith(('source-', 'sink-')))

    def rpc(self, url, body, stem='rpc', timeout=150, retain=True):
        self.rpcs.append((stem, url))
        if url.endswith('/health'):
            return dict(serving_torch_threads=1, serving_queue=0, serving_active=False, http_active=0, pending_joins=0, active_requests=0)
        q = body.get('question_id')
        if url.endswith('/transfer_profile'):
            origin, dest = self.key(url).removeprefix('source-'), self.key(body['target_url']).removeprefix('sink-')
            a, b = self.nodes[origin], self.nodes[dest]
            kind = body['request_id'].split('-')[2]
            same_zone = 8 if kind in ('image', 'audio') else 5
            ms = 1 if a == b else same_zone if self.roles[origin] == self.roles[dest] else CROSS[self.rate]
            data = base64.b64decode(body['data'])
            return dict(source_node=a, sha256=hashlib.sha256(data).hexdigest(), samples_ms=[ms])
        if url.endswith('/infer'):
            # Qualification order is [pairs[0]]*5 + pairs; request ids end with that index.
            task, index = 'fusion', int(body['request_id'].rsplit('-', 1)[1])
            ref = tb.REFS[([self.pairs[0]]*5+self.pairs)[index]['question_id']]
            data = json.dumps(dict(answer_ids=ref['answer_ids'], scores=ref['scores'])).encode()
        else:
            task = 'vision' if body['kind'] == 'image' else 'speech'
            data = (ASSETS/'reference-v1'/f'{q}.npy').read_bytes() if task == 'vision' else tb.REFS[q]['transcript'].encode()
        pod = self.actual[task]
        return dict(data=base64.b64encode(data).decode(), receipt=dict(model_sha256=tb.MODELS[task]['model_sha256'],
            pod_uid=pod['metadata']['uid'], node=pod['spec']['nodeName'], serving_torch_threads=1,
            compute_ms=COMPUTE[task][pod['spec']['nodeName']]))

    def shape(self, mbps, delay, label):
        self.rate = mbps
        super().shape(mbps, delay, label)

    def net(self, key, spec, stem):
        return dict(backlog_bytes=0)

    def close(self):
        # Session.close deletes every owned resource, including the real helper Pods created by deploy().
        self.pods.clear()
        super().close()


SUBPROCESS = []


def upload_and_scheduler(command, **kwargs):
    """subprocess.run stand-in: cache upload succeeds, scheduler snapshot is the projected kube-system call."""
    SUBPROCESS.append(command)
    if 'kube-system' in command:
        return tb.fake_kubectl()(command, **kwargs)
    assert command[:3] == ['rtk', 'proxy', 'kubectl'] and 'exec' in command, command
    return subprocess.CompletedProcess(command, 0)


# Fake default-scheduler choice uses added nodes only (Jetson, arm64 server, control-plane server).
CHOICE = {c: dict(vision=J9, speech=C3, fusion=C1) for c in ('compressed-10', 'compressed-2')}


def run_expanded(out, choice=CHOICE, **kwargs):
    pilot = ExpandedPilot(out, choice, **kwargs)
    SUBPROCESS.clear()
    original = rb.observe_scheduler

    def observe(session, condition, profile):
        session.condition = condition
        return original(session, condition, profile)
    with patch.object(rb, 'observe_scheduler', observe), patch('subprocess.run', upload_and_scheduler), \
         contextlib.redirect_stdout(io.StringIO()):
        try:
            rb.collect(pilot, rb.build_design(tb.PAIRS, pilot.topology))
        except BaseException as error:
            pilot.error = error
    pilot.subprocess_calls = list(SUBPROCESS)
    return pilot


class TopologyConfigTests(unittest.TestCase):
    def test_config_lists_nine_ready_candidates_with_explicit_roles(self):
        frozen = topology.load(CONFIG)
        self.assertEqual(topology.candidates(frozen), READY)
        self.assertEqual(sorted(frozen['excluded']), sorted(NOT_READY))
        names = topology.aliases(frozen)
        self.assertEqual({a: names[a] for a in ('e0', 'e1', 'c')}, dict(NODES))
        roles = {names[a]: r for a, r in topology.roles(frozen).items()}
        self.assertEqual(sorted(n for n, r in roles.items() if r == 'cloud'), sorted([C, C1, C3, C4]))
        self.assertEqual(sorted(n for n, r in roles.items() if r == 'edge'), sorted([E0, E1, 'etri-dev0006-raspi5', 'etri-dev0008-raspi5', J9]))
        self.assertFalse(topology.is_legacy(frozen))
        self.assertTrue(topology.is_legacy(topology.LEGACY))
        self.assertEqual(topology.aliases(topology.LEGACY), NODES)

    def test_invalid_topologies_fail_closed(self):
        base = json.loads(CONFIG.read_text())
        cases = {
            'server without role': lambda t: t['nodes']['c4'].pop('role'),
            'role never defaults to edge': lambda t: t['nodes']['c1'].update(role=None),
            'unknown role': lambda t: t['nodes']['c3'].update(role='server'),
            'moved image source': lambda t: t['nodes']['e0'].update(name='etri-dev0006-raspi5'),
            'cloud gateway as edge': lambda t: t['nodes']['c'].update(role='edge'),
            'excluded node as candidate': lambda t: t['nodes'].update(x7=dict(name='etri-dev0007-jetagx', role='edge')),
            'duplicate node': lambda t: t['nodes'].update(e7=dict(name='etri-dev0006-raspi5', role='edge')),
            'fixed aliases reordered': lambda t: t.update(nodes={k: t['nodes'][k] for k in ['e1', 'e0', 'c', 'c4']}),
            'unsafe alias': lambda t: t['nodes'].update({'C-9': dict(name='other', role='cloud')}),
        }
        for case, change in cases.items():
            raw = copy.deepcopy(base)
            change(raw)
            with self.subTest(case), self.assertRaises(ValueError):
                topology.validate(raw)


class PreflightTests(unittest.TestCase):
    def test_inventory_passes_with_nine_candidates_and_three_excluded(self):
        report = topology.preflight(topology.load(CONFIG), json.loads(INVENTORY.read_text())['nodes'])
        self.assertEqual(report['errors'], [])
        self.assertEqual([c['name'] for c in report['candidates']], READY)
        self.assertEqual({n: e['observed_ready'] for n, e in report['excluded'].items()}, {n: False for n in NOT_READY})
        self.assertEqual(report['unlisted_not_candidates'], [])

    def test_kubectl_projection_matches_inventory_shape(self):
        inventory = {n['name']: n for n in json.loads(INVENTORY.read_text())['nodes']}
        for node in topology.project_nodes(dict(items=node_items())):
            expected = inventory[node['name']]
            self.assertEqual((node['ready'], node['unschedulable'], node['arch']), (expected['ready'], expected['unschedulable'], expected['arch']))
            self.assertEqual(node['taints'], [{k: t[k] for k in ('key', 'effect')} for t in expected['taints']])

    def test_not_ready_cordoned_tainted_missing_or_unknown_arch_fail(self):
        frozen = topology.load(CONFIG)
        cases = {
            'NotReady': ({C4: dict(ready=False)}, 'c4=etri-ser0004-cgnms0: NotReady'),
            'cordoned': ({C1: dict(unschedulable=True)}, 'c1=etri-ser0001-cg0msb: cordoned'),
            'NoSchedule taint': ({J9: dict(taints=[dict(key='node-role.kubernetes.io/control-plane', effect='NoSchedule')])}, 'e9=etri-dev0009-jetorn: unsupported scheduling taints'),
            'unknown arch': ({C3: dict(arch='riscv64')}, "c3=etri-ser0003-cg0ms0: unsupported arch 'riscv64'"),
        }
        for case, (state, message) in cases.items():
            with self.subTest(case):
                report = topology.preflight(frozen, topology.project_nodes(dict(items=node_items(state))))
                self.assertEqual(report['status'], 'failed')
                self.assertTrue(any(e.startswith(message) for e in report['errors']), report['errors'])
        observed = [n for n in topology.project_nodes(dict(items=node_items())) if n['name'] != C3]
        self.assertIn('c3=etri-ser0003-cg0ms0: missing from cluster snapshot', topology.preflight(frozen, observed)['errors'])
        soft = topology.project_nodes(dict(items=node_items({C4: dict(taints=[dict(key='x', effect='PreferNoSchedule')])})))
        self.assertEqual(topology.preflight(frozen, soft)['errors'], [])


class DesignTests(unittest.TestCase):
    def test_legacy_design_has_no_topology_and_unchanged_counts(self):
        design = rb.build_design(tb.PAIRS)
        self.assertNotIn('topology', design)
        self.assertEqual(design, rb.build_design(tb.PAIRS, topology.LEGACY))
        self.assertEqual((design['qualification']['task_nodes'], design['qualification']['expected_rows']), (9, 108))
        self.assertEqual(vb.transfer_sizes(topology.LEGACY), vb.TRANSFER_SIZES)

    def test_expanded_design_freezes_topology_and_workload(self):
        design = rb.build_design(tb.PAIRS, topology.load(CONFIG))
        self.assertEqual(design['topology']['candidates'], READY)
        self.assertEqual(design['topology']['excluded'], sorted(NOT_READY))
        self.assertEqual(design['topology']['fixed'], dict(image_source=E0, audio_source=E1, result_destination=E0, helper_gateway_cache=C))
        # 3 tasks x 9 nodes x (5 warmup + 4 calibration + 3 evaluation).
        self.assertEqual((design['qualification']['task_nodes'], design['qualification']['expected_rows']), (27, 324))
        # Workflow budget and per-placement inputs unchanged: 2 conditions x 6 policies x (1 + 3).
        self.assertEqual((design['max_workflow_requests'], design['evaluation_count'], design['policies']), (48, 3, list(rb.POLICIES)))
        workload = design['workload_estimate']
        # Per condition: image 9 + audio 9 + vision 81 + speech 81 + return 9 + control 9 = 198 pairs x 4 calibration inputs.
        self.assertEqual(workload['transfer_rpcs'], dict(per_condition=792, total=1584, legacy_total=240))
        self.assertEqual(workload['qualification'], dict(deployments=27, requests=324, legacy_deployments=9, legacy_requests=108))
        self.assertEqual(workload['helper_pods'], dict(total=20, legacy_total=8))

    def test_offline_dry_run_with_inventory_shows_candidates_without_kubectl(self):
        stdout = io.StringIO()
        with patch('subprocess.run', side_effect=AssertionError('no subprocess')), \
             patch('subprocess.check_output', side_effect=AssertionError('no subprocess')), \
             patch.object(tb.Session, '__init__', side_effect=AssertionError('no session')), contextlib.redirect_stdout(stdout):
            self.assertEqual(rb.main(['--dry-run', '--topology', str(CONFIG), '--inventory', str(INVENTORY)]), 0)
        printed = json.loads(stdout.getvalue())
        self.assertEqual(printed['topology']['candidates'], READY)
        self.assertEqual(printed['preflight']['status'], 'passed')
        self.assertEqual(sorted(printed['preflight']['excluded']), sorted(NOT_READY))
        self.assertEqual(printed['workload_estimate']['transfer_rpcs']['total'], 1584)

    def test_dry_run_reports_not_ready_inventory_and_execute_rejects_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            inventory = json.loads(INVENTORY.read_text())
            next(n for n in inventory['nodes'] if n['name'] == C4)['ready'] = False
            path = Path(temp)/'inventory.json'
            path.write_text(json.dumps(inventory))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(rb.main(['--dry-run', '--topology', str(CONFIG), '--inventory', str(path)]), 1)
            self.assertIn('c4=etri-ser0004-cgnms0: NotReady', json.loads(stdout.getvalue())['preflight']['errors'])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            rb.main(['--execute', '--topology', str(CONFIG), '--inventory', str(INVENTORY)])


class HelperTests(unittest.TestCase):
    def test_real_deploy_enumerates_helpers_with_explicit_zones(self):
        with tempfile.TemporaryDirectory() as temp:
            pilot = ExpandedPilot(temp, CHOICE)
            with patch('subprocess.run', upload_and_scheduler):
                pilot.deploy()
            helpers = [f'{kind}-{a}' for kind in ('source', 'sink') for a in ('e0', 'e1', 'c', 'e6', 'e8', 'e9', 'c1', 'c3', 'c4')]
            self.assertEqual(list(pilot.helpers()), helpers)
            self.assertEqual({k for k, z in pilot.zones.items() if z == 'cloud'}, {f'{k}-{a}' for k in ('source', 'sink') for a in ('c', 'c1', 'c3', 'c4')})
            placed = {pod['metadata']['name'].removeprefix(pilot.run+'-'): pod['spec']['nodeName'] for pod in pilot.pods.values()}
            self.assertEqual(placed['source-c4'], C4)
            self.assertEqual(placed['sink-e9'], J9)
            self.assertEqual((placed['gateway'], placed['cache']), (C, C))

    def test_real_drain_polls_every_helper(self):
        with tempfile.TemporaryDirectory() as temp:
            pilot = ExpandedPilot(temp, CHOICE)
            with patch('subprocess.run', upload_and_scheduler):
                pilot.deploy()
            pilot.rpcs = []
            Pilot.drain(pilot, 'probe')
            polled = {stem.removeprefix('drain-probe-') for stem, _ in pilot.rpcs}
            self.assertEqual(polled, {*pilot.helpers(), 'gateway'})

    def test_capacity_fails_closed_on_missing_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            pilot = ExpandedPilot(temp, CHOICE)
            original = pilot.call
            def call(stem, args, stdin=None, timeout=40):
                if args[:2] == ['get', 'nodes']:
                    return json.dumps(dict(items=[n for n in node_items() if n['metadata']['name'] != C3]))
                return original(stem, args, stdin, timeout)
            pilot.call = call
            with self.assertRaisesRegex(RuntimeError, 'etri-ser0003-cg0ms0'):
                pilot.capacity()


class ExpandedCollectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.rundir = Path(cls.temp.name)/'run'
        cls.rundir.mkdir()
        cls.pilot = run_expanded(cls.rundir)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def read(self, name):
        return json.loads((self.rundir/name).read_text())

    def tampered(self, edit):
        target = Path(tempfile.mkdtemp(dir=self.temp.name))/'run'
        shutil.copytree(self.rundir, target)
        edit(target)
        return vb.verify(target)

    def test_collect_completes_and_verifier_passes(self):
        self.assertFalse(hasattr(self.pilot, 'error'), getattr(self.pilot, 'error', None))
        result = vb.verify(self.rundir)
        self.assertEqual(result['errors'], [])
        # 10 Mbps: {profile policies on ser0004}, {kube-default}; 2 Mbps: {greedy, heft, swap, exact}, {no-network}, {kube-default}.
        # 5 groups x 3 evaluation requests; receipts 5 x (1 warmup + 3) x 3 tasks.
        self.assertEqual((result['status'], result['groups'], result['evaluation_attempts'], result['receipts']), ('passed', 5, 15, 60))

    def test_same_candidates_and_resources_for_scheduler_and_profiles(self):
        capacity = self.read('capacity-frozen.json')
        self.assertEqual(sorted(capacity), sorted(READY))
        for condition in ('compressed-10', 'compressed-2'):
            profile = self.read(f'profile-{condition}.json')
            self.assertEqual(profile['nodes'], capacity)
            self.assertTrue(all(t['candidates'] == READY for t in profile['tasks']))
            self.assertEqual(self.read(f'scheduler-{condition}.json')['candidates'], READY)
            for pod in self.read(f'scheduler-{condition}.manifest.json')['items']:
                self.assertEqual(vb.whitelist(pod), sorted(READY))

    def test_qualification_and_transfer_rows_scale_with_topology(self):
        rows = self.read('qualification-all.json')
        self.assertEqual(len(rows), 324)
        self.assertEqual({(r['task'], r['node']) for r in rows}, {(t, n) for t in rb.TASKS for n in READY})
        for condition in ('compressed-10', 'compressed-2'):
            sizes = {k: len(v) for k, v in self.read(f'transfer-{condition}.json')['payload_bytes'].items()}
            self.assertEqual(sizes, dict(image=36, audio=36, vision=324, speech=324, control=36, **{'return': 36}))
        self.assertEqual(sum(stem.startswith('compressed-') for stem, _ in self.pilot.rpcs), 1584)

    def test_hand_derived_plans_use_added_nodes(self):
        plans = self.read('plans-frozen.json')
        # 10 Mbps: max(50+10+1, 50+15+1) + 5 + return/control 50+50 = 171 on ser0004; vision on ser0002 would be 50+20+5 = 75 > 66.
        for policy in ('greedy', 'heft', 'greedy-swap', 'exact', 'no-network'):
            self.assertEqual(plans['compressed-10'][policy]['placement'], dict(vision=C4, speech=C4, fusion=C4), policy)
        self.assertAlmostEqual(plans['compressed-10']['exact']['predicted_e2e_proxy_ms'], 171)
        # 2 Mbps: vision e0 1+304+1 = speech e1 1+300+5 = 306, +100 fusion on e0, +2 return/control = 408; speech on e0 = 8+300+1 = 309.
        for policy in ('greedy', 'heft', 'greedy-swap', 'exact'):
            self.assertEqual(plans['compressed-2'][policy]['placement'], dict(vision=E0, speech=E1, fusion=E0), policy)
        self.assertAlmostEqual(plans['compressed-2']['exact']['predicted_e2e_proxy_ms'], 408)
        self.assertEqual(plans['compressed-2']['no-network']['placement'], dict(vision=C4, speech=C4, fusion=C4))
        self.assertEqual(plans['compressed-10']['kube-default']['placement'], CHOICE['compressed-10'])

    def test_fixed_sources_and_frozen_topology_evidence(self):
        frozen = self.read('topology-frozen.json')
        self.assertEqual(frozen['topology'], topology.load(CONFIG))
        self.assertEqual(frozen['sha256'], topology.digest(frozen['topology']))
        self.assertEqual(frozen['config_sha256'], hashlib.sha256(CONFIG.read_bytes()).hexdigest())
        self.assertEqual(self.read('topology-preflight.json')['status'], 'passed')
        for rows in self.rundir.glob('eval-*.rows.json'):
            for row in json.loads(rows.read_text()):
                self.assertEqual((row['source_node'], row['speech_source_node']), (E0, E1))
        self.assertEqual(self.read('profile-compressed-10.json')['outputs'][0]['destination_node'], E0)

    def edit_json(self, name, change):
        def edit(target):
            path = target/name
            value = json.loads(path.read_text())
            change(value)
            path.write_text(json.dumps(value))
        return edit

    def test_verifier_rejects_inconsistent_topology_evidence(self):
        cases = {
            'role changed': ('topology-frozen.json', lambda f: f['topology']['nodes']['c4'].update(role='edge'), 'design differs'),
            'candidate dropped': ('topology-frozen.json', lambda f: f['topology']['nodes'].pop('c4'), 'design differs'),
            'digest edited': ('topology-frozen.json', lambda f: f.update(sha256='0'*64), 'topology digest'),
            'preflight edited': ('topology-preflight.json', lambda p: p.update(status='failed'), 'topology preflight'),
            'profile candidate dropped': ('profile-compressed-2.json', lambda p: p['tasks'][0]['candidates'].remove(C4), 'topology candidates'),
        }
        for case, (name, change, message) in cases.items():
            with self.subTest(case):
                result = self.tampered(self.edit_json(name, change))
                self.assertNotEqual(result['status'], 'passed')
                self.assertTrue(any(message in e for e in result['errors']), result['errors'])
        result = self.tampered(lambda target: (target/'topology-frozen.json').unlink())
        self.assertIn('design differs from the declared reduced design', result['errors'])

    def test_verifier_rejects_raw_preflight_snapshot_mismatch(self):
        def edit(target):
            raw = next(target.glob('*-topology-nodes.stdout'))
            value = json.loads(raw.read_text())
            next(n for n in value['items'] if n['metadata']['name'] == C4)['status']['conditions'][0]['status'] = 'False'
            raw.write_text(json.dumps(value))
        self.assertIn('topology preflight matches raw node snapshot', self.tampered(edit)['errors'])


class ExpandedFailureTests(unittest.TestCase):
    def test_unready_configured_node_stops_before_any_mutation(self):
        cases = {'NotReady': {C4: dict(ready=False)}, 'cordoned': {C1: dict(unschedulable=True)},
                 'tainted': {J9: dict(taints=[dict(key='dedicated', effect='NoExecute')])}}
        for case, state in cases.items():
            with self.subTest(case), tempfile.TemporaryDirectory() as temp:
                pilot = run_expanded(temp, state=state)
                self.assertIsInstance(pilot.error, RuntimeError)
                self.assertIn('Topology preflight failed', str(pilot.error))
                self.assertFalse(any(args[0] in ('apply', 'exec', 'delete') for _, args in pilot.calls))
                self.assertEqual(pilot.subprocess_calls, [])
                self.assertEqual(json.loads((Path(temp)/'topology-preflight.json').read_text())['status'], 'failed')
                self.assertTrue((Path(temp)/'failure.json').is_file())


class ExplicitLegacySizedTests(unittest.TestCase):
    """--topology with exactly the legacy e0/e1/c nodes keeps the explicit contract; only no-flag is legacy."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Path(self.temp.name)/'explicit-three.json'
        self.config.write_text(json.dumps(topology.LEGACY))
        self.assertTrue(topology.is_legacy(topology.load(self.config)))

    def tearDown(self):
        self.temp.cleanup()

    def run_dir(self, name):
        out = Path(self.temp.name)/name
        out.mkdir()
        return out

    def test_unavailable_node_fails_before_any_mutation(self):
        cases = {'NotReady': {E0: dict(ready=False)}, 'cordoned': {C: dict(unschedulable=True)},
                 'tainted': {E1: dict(taints=[dict(key='dedicated', effect='NoSchedule')])}}
        for case, state in cases.items():
            with self.subTest(case):
                out = self.run_dir(case)
                pilot = run_expanded(out, choice=tb.CHOICE, state=state, config=self.config)
                self.assertIsInstance(pilot.error, RuntimeError)
                self.assertIn('Topology preflight failed', str(pilot.error))
                self.assertFalse(any(args[0] in ('apply', 'exec', 'delete') for _, args in pilot.calls))
                self.assertEqual(pilot.subprocess_calls, [])
                self.assertFalse((out/'scheduler-provenance.json').exists())
                self.assertEqual(json.loads((out/'topology-preflight.json').read_text())['status'], 'failed')
                self.assertTrue((out/'topology-frozen.json').is_file())

    def test_valid_run_freezes_verifiable_evidence(self):
        out = self.run_dir('run')
        pilot = run_expanded(out, choice=tb.CHOICE, config=self.config)
        self.assertFalse(hasattr(pilot, 'error'), getattr(pilot, 'error', None))
        frozen = json.loads((out/'topology-frozen.json').read_text())
        self.assertEqual((frozen['topology'], frozen['sha256']), (topology.LEGACY, topology.digest(topology.LEGACY)))
        self.assertEqual(frozen['config_sha256'], hashlib.sha256(self.config.read_bytes()).hexdigest())
        self.assertEqual(json.loads((out/'topology-preflight.json').read_text())['status'], 'passed')
        # Same 3 candidates, so the design itself stays the legacy one the verifier rebuilds.
        self.assertNotIn('topology', json.loads((out/'design.json').read_text()))
        result = vb.verify(out)
        self.assertEqual((result['errors'], result['status']), ([], 'passed'))
        (out/'topology-preflight.json').unlink()
        self.assertIn('missing topology-preflight.json', vb.verify(out)['errors'])

    def test_implicit_legacy_skips_topology_preflight(self):
        out = self.run_dir('implicit')
        pilot = run_expanded(out, choice=tb.CHOICE, config=None)
        self.assertFalse(hasattr(pilot, 'error'), getattr(pilot, 'error', None))
        self.assertIsNone(pilot.topology_config)
        self.assertFalse(any(stem == 'topology-nodes' for stem, _ in pilot.calls))
        self.assertFalse((out/'topology-frozen.json').exists() or (out/'topology-preflight.json').exists())
        self.assertEqual(vb.verify(out)['errors'], [])


if __name__ == '__main__':
    unittest.main()
