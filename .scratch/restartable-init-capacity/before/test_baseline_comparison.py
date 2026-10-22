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
import verify_baseline_comparison as vb
from budget_placement import with_server_budget
from run_budget_placement import BudgetPilot
from run_load import Session
from run_smoke import ASSETS, HERE, NODES, NS

E0, E1, C = NODES['e0'], NODES['e1'], NODES['c']
PAIRS = json.loads((ASSETS/'load-pairs.json').read_text())['pairs']
REFS = {r['question_id']: r for r in map(json.loads, (ASSETS/'reference-v1/results.jsonl').read_text().splitlines())}
MODELS = json.loads((ASSETS/'reference-v1/metadata.json').read_text())['stages']
FROZEN = ASSETS/'wan-20261021t044624'
# Hand-checked fixture costs; see collect test for the derived placements.
COMPUTE = dict(vision={E0: 200, E1: 200, C: 20}, speech={E0: 300, E1: 300, C: 30}, fusion={E0: 100, E1: 100, C: 10})
IMAGE_MS = {10: {E0: 0, E1: 60, C: 50}, 2: {E0: 0, E1: 450, C: 400}}
AUDIO_MS = {10: {E1: 0, E0: 40, C: 30}, 2: {E1: 0, E0: 150, C: 100}}


def hand_profile(rate, capacity, condition):
    join = {a+'|'+b: 0 if a == b else 10 for a in NODES.values() for b in NODES.values()}
    tasks = []
    for task, inputs in [('vision', [dict(source_node=E0, bytes=40000, transfer_ms={E0+'|'+n: v for n, v in IMAGE_MS[rate].items()})]),
                         ('speech', [dict(source_node=E1, bytes=30000, transfer_ms={E1+'|'+n: v for n, v in AUDIO_MS[rate].items()})]),
                         ('fusion', [dict(task='vision', bytes=2048, transfer_ms=dict(join)), dict(task='speech', bytes=80, transfer_ms=dict(join))])]:
        tasks.append(dict(id=task, candidates=list(NODES.values()), compute_ms=COMPUTE[task], image='fixture@sha256:test',
                          cpu_m=1010, memory_mib=2080, inputs=inputs))
    return dict(provenance='measured', evidence_id='hand-checked local fixture', namespace=NS, nodes=capacity, tasks=tasks,
                outputs=[dict(task='fusion', destination_node=E0, transfer_ms={n+'|'+E0: 0 if n == E0 else 20 for n in NODES.values()})],
                representation='compressed', condition=condition, model='local fixture')


class FakePilot(rb.BaselinePilot):
    """Real deploy/remove/capacity code; only kubectl and data-plane phases are faked."""

    def __init__(self, out, scheduler_choice, cpu=None, fail_label=None, timeout_label=None):
        self.run, self.out, self.counter, self.resources, self.actual = Path(out).name, Path(out), 0, [], {}
        self.calibration_run, self.active_profile, self.operators, self.generation, self.guarded = None, None, [], 0, False
        self.pairs = rb.reduced_pairs(PAIRS)
        self.models, self.compute, self.cal_outputs = MODELS, {}, {}
        self.scheduler_choice, self.fail_label, self.timeout_label = scheduler_choice, fail_label, timeout_label
        self.cpu = cpu or {n: '4' for n in NODES.values()}
        self.pods, self.uids, self.calls, self.traces, self.applied = {}, 0, [], [], []
        (self.out/'collector-source').mkdir()
        for path in HERE.glob('*.py'):
            (self.out/'collector-source'/path.name).write_bytes(path.read_bytes())
        planner = Path(rb.prepare.__file__)
        (self.out/'planner-source.py').write_bytes(planner.read_bytes())
        self.save('planner-source.json', dict(path=str(planner), sha256=hashlib.sha256(planner.read_bytes()).hexdigest()))
        self.save('git-source.json', dict(commit='fixture', files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.glob('*.py')}))

    # --- kubectl seam ---
    def call(self, stem, args, stdin=None, timeout=40):
        self.counter += 1
        self.calls.append((stem, args))
        if args[0] == 'apply' and '--dry-run=server' not in args:
            for item in json.loads(stdin)['items']:
                if item['kind'] != 'Pod':
                    continue
                task = item['metadata']['name'].removeprefix(self.run+'-')
                terms = item['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']
                node = terms[0]['matchFields'][0]['values'][0] if len(terms) == 1 else self.scheduler_choice[self.condition][task]
                self.uids += 1
                pod = copy.deepcopy(item)
                pod['metadata'].update(uid=f'uid-{self.uids}', creationTimestamp='2026-10-23T00:00:00Z')
                pod['spec']['nodeName'] = node
                pod['status'] = dict(phase='Running', podIP=f'10.0.0.{self.uids}', conditions=[dict(type='PodScheduled', status='True')])
                self.pods[item['metadata']['name']] = pod
                self.applied.append((stem, task, len(terms), node))
            return ''
        if args[0] == 'delete':
            for item in json.loads(stdin)['items']:
                self.pods.pop(item['metadata']['name'], None)
            return ''
        if args[:2] == ['get', 'pod']:
            return json.dumps(self.pods[args[4]])
        if args[:2] == ['get', 'nodes']:
            return json.dumps(dict(items=[dict(metadata=dict(name=n), status=dict(conditions=[dict(type='Ready', status='True')],
                allocatable=dict(cpu=self.cpu[n], memory='8Gi'))) for n in NODES.values()]))
        if args[:3] == ['get', 'pods', '-A']:
            return json.dumps(dict(items=list(self.pods.values())))
        if 'kube-system' in args:
            raise AssertionError('scheduler Pod JSON must not pass through persisted Session.call')
        if args[:2] == ['get', 'events']:
            return json.dumps(dict(items=[dict(involvedObject=dict(uid=p['metadata']['uid']), reason='Scheduled',
                source=dict(component='default-scheduler')) for p in self.pods.values()]))
        return ''

    # --- data plane fakes (no network) ---
    condition = None

    def refresh_network(self, label):
        pass

    def shape(self, mbps, delay, label):
        self.save('net-'+label+'.json', dict(mbps=mbps, delay_ms=delay))

    def guard(self, enabled):
        self.guarded = enabled

    def deploy(self):
        self.cache_url, self.urls, self.zones = 'http://cache', {}, {}

    def network_preflight(self):
        self.save('network-preflight.json', dict(status='passed'))

    def qualify_all(self):
        rows = []
        for task in rb.TASKS:
            for alias, node in NODES.items():
                label = 'qualify-'+task+'-'+alias
                self.deploy_operators({task: node}, label)
                for index, pair in enumerate([self.pairs[0]]*5+self.pairs):
                    rows.append(dict(task=task, node=node, question_id=pair['question_id'], split=pair['split'], warmup=index < 5,
                        quality_ok=True, receipt=dict(model_sha256=MODELS[task]['model_sha256'], pod_uid=self.actual[task]['metadata']['uid'],
                            node=node, serving_torch_threads=1, compute_ms=COMPUTE[task][node])))
                self.remove_operators(label)
        self.compute = COMPUTE
        self.save('qualification-all.json', rows)

    def transfer_profiles(self, condition, mbps, delay, representation, capacity):
        profile = hand_profile(mbps, capacity, condition)
        self.save('profile-'+condition+'.json', profile)
        self.save('transfer-'+condition+'.json', dict(table={}, payload_bytes={k: [1]*n for k, n in vb.TRANSFER_SIZES.items()}))
        return profile

    def drain(self, label):
        self.save(label+'.drain.json', [dict(attempt=0, idle=True), dict(attempt=1, idle=True)])
        for attempt in (0, 1):
            self.save(label+f'-drain-network-{attempt}.json', dict(qdiscs=[dict(attrs=[['TCA_STATS', dict(backlog=0)]])]))

    def observe_scheduler_condition(self, condition):
        self.condition = condition

    def trace(self, label, placement, mbps, delay, rate, count, relay=True, representation='raw', split='evaluation'):
        self.traces.append((label, dict(placement), mbps, delay, rate, count, representation, split))
        if label == self.fail_label:
            raise TimeoutError('Source trace control deadline exceeded')
        pairs = [p for p in self.pairs if p['split'] == split]
        config = dict(run_id=self.run, phase=label, question_ids=[pairs[i % len(pairs)]['question_id'] for i in range(count)],
            placement=placement, mbps=mbps, oneway_delay_ms=delay, rate_rps=rate, representation=representation, deadline_s=120)
        self.save(label+'.config.json', config)
        rows = []
        for index, qid in enumerate(config['question_ids']):
            if label == self.timeout_label and index == 0:
                # load_trace failure row: phase only, no run_id/request_id/e2e_ms/receipts.
                rows.append(dict(event='terminal', index=index, question_id=qid, status='error', quality_ok=False,
                    error="TimeoutError('Absolute workflow deadline elapsed waiting for branches')",
                    finished_s=120.5, scheduled_to_terminal_ms=120500.0, deadline_exceeded=True, phase=label))
                continue
            row = fake_row(index, qid, {t: self.actual[t] for t in rb.TASKS})
            rows.append(dict(row, run_id=self.run, request_id=label+'-'+str(index), phase=label))
        self.save(label+'.rows.json', rows)
        self.drain(label)
        return rows

    def close(self):
        self.save('protected-after.json', dict(protected_count=0, changes=[]))
        self.save('cleanup.json', dict(remaining_owned=len(self.pods), errors=[]))


def fake_row(index, qid, pods):
    pair, ref = next(p for p in PAIRS if p['question_id'] == qid), REFS[qid]
    output = dict(answer_ids=ref['answer_ids'], scores=ref['scores'])
    out_bytes = json.dumps(output, separators=(',', ':')).encode()
    out_hash = hashlib.sha256(out_bytes).hexdigest()
    def receipt(task, inputs, input_bytes, output_sha, output_bytes):
        pod = pods[task]
        return dict(task=task, pod_uid=pod['metadata']['uid'], node=pod['spec']['nodeName'], device='cpu',
            model_sha256=MODELS[task]['model_sha256'], serving_torch_threads=1, serving_torch_interop_threads=1,
            input_sha256=inputs, input_bytes=input_bytes, output_sha256=output_sha, output_bytes=output_bytes)
    receipts = [receipt('vision', dict(source=pair['image_sha256']), dict(source=pair['image_bytes']), 'v'*64, 2048),
                receipt('speech', dict(source=pair['audio_sha256']), dict(source=pair['audio_bytes']), 's'*64, 80),
                receipt('fusion', dict(vision='v'*64, speech='s'*64), dict(vision=2048, speech=80), out_hash, len(out_bytes))]
    return dict(event='terminal', index=index, question_id=qid, status='ok', e2e_ms=500.0+index, receipts=receipts,
        source_node=E0, speech_source_node=E1, output=output, top5_order_matches=True, max_score_abs_error=0.0, deadline_exceeded=False)


# Fake scheduler picks E1/E1/C at 10 Mbps and C/C/C at 2 Mbps (both inside the candidates).
CHOICE = {'compressed-10': dict(vision=E1, speech=E1, fusion=C), 'compressed-2': dict(vision=C, speech=C, fusion=C)}


# Built at runtime so the copied collector-source of this test never contains the literal value.
CANARY = 'canary-' + hashlib.sha256(b'scheduler env').hexdigest()[:16]
SCHEDULER_POD = dict(metadata=dict(name='kube-scheduler-master', uid='s1', annotations={'kubernetes.io/config.hash': 'abc'}),
    spec=dict(nodeName='master', containers=[dict(name='kube-scheduler', image='registry.k8s.io/kube-scheduler:v1.30.0',
        command=['kube-scheduler', '--kubeconfig=/etc/kubernetes/scheduler.conf'], env=[dict(name='TOKEN', value=CANARY)],
        volumeMounts=[dict(mountPath='/etc/kubernetes/scheduler.conf')])], volumes=[dict(hostPath=dict(path='/etc/kubernetes/scheduler.conf'))]),
    status=dict(containerStatuses=[dict(name='kube-scheduler', imageID='sha256:sched')]))


def fake_kubectl(behaviour='ok'):
    """subprocess.run stand-in for the scheduler snapshot; raw stdout carries the env canary."""
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert command[:3] == ['rtk', 'proxy', 'kubectl'] and 'kube-system' in command, command
        raw = json.dumps(dict(items=[SCHEDULER_POD])).encode()
        if behaviour == 'timeout':
            # Full partial stdout: the whole env canary reaches the timeout path.
            assert CANARY.encode() in raw
            raise subprocess.TimeoutExpired(command, kwargs['timeout'], output=raw)
        if kwargs.get('stderr') not in (None, subprocess.PIPE):
            kwargs['stderr'].write(b'')
        return subprocess.CompletedProcess(command, 1 if behaviour == 'fail' else 0, stdout=raw)
    run.calls = calls
    return run


def persisted_with(root, needle):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob('*') if p.is_file() and needle.encode() in p.read_bytes())


def run_fake(temp, kubectl=None, **kwargs):
    pilot = FakePilot(temp, CHOICE, **kwargs)
    original = rb.observe_scheduler
    def observe(session, condition, profile):
        session.condition = condition
        return original(session, condition, profile)
    pilot.kubectl = kubectl or fake_kubectl()
    with patch.object(rb, 'observe_scheduler', observe), patch('subprocess.run', pilot.kubectl), \
         contextlib.redirect_stdout(io.StringIO()):
        try:
            rb.collect(pilot, rb.build_design(PAIRS))
        except BaseException as error:
            pilot.error = error
    return pilot


def verify_quiet(path):
    return vb.verify(Path(path))


class DesignTests(unittest.TestCase):
    def test_reduced_design_uses_first_pairs_in_original_order(self):
        design = rb.build_design(PAIRS)
        self.assertEqual(design['calibration_ids'], [1713820, 3733440, 4556100, 3355890])
        self.assertEqual(design['evaluation_ids'], [3506790, 5341940, 787710])
        # 9 task-nodes x (5 warmup + 4 calibration + 3 evaluation).
        self.assertEqual(design['qualification']['expected_rows'], 108)
        # 2 conditions x 6 policies x (1 warmup + 3 eval).
        self.assertEqual(design['max_workflow_requests'], 48)
        self.assertEqual([c['mbps'] for c in design['conditions']], [10, 2])
        self.assertEqual(design['policies'], ['kube-default', 'greedy', 'heft', 'greedy-swap', 'exact', 'no-network'])

    def test_dry_run_prints_design_without_session_or_subprocess(self):
        before = sorted(p.name for p in ASSETS.iterdir())
        stdout = io.StringIO()
        with patch('subprocess.run', side_effect=AssertionError('no subprocess')), \
             patch('subprocess.check_output', side_effect=AssertionError('no subprocess')), \
             patch.object(Session, '__init__', side_effect=AssertionError('no session')), contextlib.redirect_stdout(stdout):
            self.assertEqual(rb.main(['--dry-run']), 0)
        printed = json.loads(stdout.getvalue())
        self.assertTrue(printed['dry_run'])
        self.assertEqual(printed['qualification']['expected_rows'], 108)
        self.assertIn('experiments/real-topology-live/run_baseline_comparison.py', printed['sources'])
        self.assertEqual(sorted(p.name for p in ASSETS.iterdir()), before)

    def test_live_mode_must_be_explicit(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            rb.main([])

    def test_frozen_profiles_reproduce_offline_decisions(self):
        # Literal expectations from .scratch/scheduling-baseline-comparison/offline-decisions.json.
        ten = rb.profile_plans(json.loads((FROZEN/'profile-calibration-10.json').read_text()))
        two = rb.profile_plans(json.loads((FROZEN/'profile-calibration-2.json').read_text()))
        self.assertEqual(set(ten), {'greedy', 'heft', 'greedy-swap', 'exact', 'no-network'})
        for policy in ten:
            self.assertEqual(ten[policy]['placement'], dict(vision=C, speech=C, fusion=C), policy)
        for policy in ('greedy', 'heft', 'greedy-swap', 'exact'):
            self.assertEqual(two[policy]['placement'], dict(vision=E0, speech=C, fusion=C), policy)
        self.assertEqual(two['no-network']['placement'], dict(vision=C, speech=C, fusion=C))
        self.assertAlmostEqual(ten['exact']['predicted_e2e_proxy_ms'], 683.6156961871893)

    def test_synthetic_budget_profile_is_rejected(self):
        profile = json.loads((FROZEN/'profile-calibration-10.json').read_text())
        with self.assertRaises(ValueError):
            rb.profile_plans(with_server_budget(profile, C, 2))

    def test_scheduler_provenance_drops_env(self):
        result = rb.scheduler_provenance(dict(items=[SCHEDULER_POD]))
        self.assertNotIn(CANARY, json.dumps(result))
        self.assertEqual(result['pods'][0]['containers'][0]['image'], 'registry.k8s.io/kube-scheduler:v1.30.0')
        self.assertEqual(result['pods'][0]['containers'][0]['command'][0], 'kube-scheduler')


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def pilot(self, **kwargs):
        pilot = FakePilot(self.temp.name, CHOICE, **kwargs)
        pilot.deploy()
        capacity = pilot.capacity()
        pilot.active_profile = hand_profile(10, capacity, 'compressed-10')
        return pilot

    def test_three_operators_on_server_pass_without_slot2_validator(self):
        pilot = self.pilot()
        actual = pilot.deploy_operators(dict(vision=C, speech=C, fusion=C), 'all-server')
        self.assertEqual(actual, dict(vision=C, speech=C, fusion=C))
        # The parent's slot2 admission validator rejects the same physical placement.
        pilot.remove_operators('all-server')
        with self.assertRaises(ValueError):
            BudgetPilot.deploy_operators(pilot, dict(vision=C, speech=C, fusion=C), 'all-server-budget')

    def test_physical_capacity_rejects_pinned_placement_before_apply(self):
        # Profile frozen at 4 CPUs; current physical 2 CPUs cannot hold 3 x 1010m requests.
        pilot = self.pilot()
        pilot.cpu = {E0: '4', E1: '4', C: '2'}
        with self.assertRaises(ValueError):
            pilot.deploy_operators(dict(vision=C, speech=C, fusion=C), 'too-large')
        self.assertFalse(any(args[0] == 'apply' for _, args in pilot.calls))

    def test_scheduler_observation_is_unpinned_and_recorded(self):
        pilot = self.pilot()
        pilot.condition = 'compressed-10'
        actual = rb.observe_scheduler(pilot, 'compressed-10', pilot.active_profile)
        self.assertEqual(actual, CHOICE['compressed-10'])
        self.assertEqual({(task, terms) for stem, task, terms, _ in pilot.applied}, {(t, 3) for t in rb.TASKS})
        record = json.loads((Path(self.temp.name)/'scheduler-compressed-10.json').read_text())
        self.assertEqual(record['validation'], 'ok')
        self.assertEqual(len(record['events']), 3)
        self.assertEqual(pilot.operators, [])


class CollectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.rundir = Path(cls.temp.name)/'run'
        cls.rundir.mkdir()
        cls.pilot = run_fake(cls.rundir)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def tampered(self, edit):
        target = Path(tempfile.mkdtemp(dir=self.temp.name))/'run'
        shutil.copytree(self.rundir, target)
        edit(target)
        return verify_quiet(target)

    def test_collect_completes_with_hand_derived_groups(self):
        self.assertFalse(hasattr(self.pilot, 'error'), getattr(self.pilot, 'error', None))
        schedule = json.loads((self.rundir/'schedule-frozen.json').read_text())['jobs']
        groups = {(j['condition'], tuple(j['placement'].values())): sorted(j['policies']) for j in schedule}
        self.assertEqual(groups, {
            ('compressed-10', (C, C, C)): ['exact', 'greedy', 'greedy-swap', 'heft', 'no-network'],
            ('compressed-10', (E1, E1, C)): ['kube-default'],
            ('compressed-2', (E0, C, C)): ['exact', 'greedy', 'greedy-swap', 'heft'],
            ('compressed-2', (C, C, C)): ['kube-default', 'no-network']})
        # 4 groups x (warmup trace of 1 calibration + 3 evaluation requests).
        self.assertEqual(sorted((count, split) for _, _, _, _, _, count, _, split in self.pilot.traces),
                         [(1, 'calibration')]*4 + [(3, 'evaluation')]*4)
        self.assertTrue(all((rate, delay, rep) == (0.1, 20, 'compressed') for _, _, _, delay, rate, _, rep, _ in self.pilot.traces))
        pinned = [terms for stem, _, terms, _ in self.pilot.applied if stem.startswith('eval-')]
        self.assertEqual(pinned, [1]*12)
        self.assertNotIn('admission_budget', (self.rundir/'profile-compressed-2.json').read_text())

    def test_verifier_passes_complete_fake_run(self):
        result = verify_quiet(self.rundir)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['status'], 'passed')
        self.assertEqual((result['groups'], result['evaluation_attempts'], result['receipts']), (4, 12, 48))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(vb.main([str(self.rundir)]), 0)

    def test_verifier_output_never_overwrites(self):
        existing = Path(self.temp.name)/'audit.json'
        existing.write_text('{}')
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(FileExistsError):
            vb.main([str(self.rundir), '--output', str(existing)])
        self.assertEqual(existing.read_text(), '{}')

    def edit_json(self, name, change):
        def edit(target):
            path = target/name
            value = json.loads(path.read_text())
            change(value)
            path.write_text(json.dumps(value))
        return edit

    def first_eval(self):
        return json.loads((self.rundir/'schedule-frozen.json').read_text())['jobs'][0]['label']

    def test_verifier_rejects_wrong_receipt_identity(self):
        label = self.first_eval()
        result = self.tampered(self.edit_json(label+'.rows.json', lambda rows: rows[1]['receipts'][0].update(pod_uid='other')))
        self.assertEqual(result['status'], 'invalid')
        self.assertTrue(any('runtime identity' in e for e in result['errors']))

    def test_verifier_rejects_missing_request_row(self):
        label = self.first_eval()
        result = self.tampered(self.edit_json(label+'.rows.json', lambda rows: rows.pop()))
        self.assertEqual(result['status'], 'invalid')
        self.assertTrue(any('terminal ledger' in e for e in result['errors']))

    def test_verifier_rejects_policy_map_mismatch(self):
        def change(mapping):
            mapping['compressed-2']['no-network'] = mapping['compressed-2']['greedy']
        result = self.tampered(self.edit_json('policy-observation-map.json', change))
        self.assertEqual(result['status'], 'invalid')
        self.assertIn('policy-observation map matches schedule', result['errors'])

    def test_verifier_rejects_invented_kube_default(self):
        def change(plans):
            plans['compressed-10']['kube-default']['placement'] = dict(vision=C, speech=C, fusion=C)
        result = self.tampered(self.edit_json('plans-frozen.json', change))
        self.assertEqual(result['status'], 'invalid')
        self.assertIn('compressed-10: kube-default equals observed selection', result['errors'])

    def test_verifier_rejects_duplicate_same_placement_observation(self):
        def change(schedule):
            jobs = schedule['jobs']
            first = next(j for j in jobs if j['condition'] == 'compressed-2' and 'no-network' in j['policies'])
            first['policies'].remove('no-network')
            jobs.append(dict(first, policies=['no-network'], label='eval-9-compressed-2'))
        result = self.tampered(self.edit_json('schedule-frozen.json', change))
        self.assertEqual(result['status'], 'invalid')
        self.assertIn('compressed-2: no duplicate observation of the same placement', result['errors'])

    def test_verifier_rejects_synthetic_budget(self):
        result = self.tampered(self.edit_json('profile-compressed-2.json', lambda p: p.update(admission_budget=dict(slots=2))))
        self.assertIn('compressed-2: no synthetic admission budget', result['errors'])

    def errors_for(self, name, change):
        result = self.tampered(self.edit_json(name, change))
        self.assertEqual(result['status'], 'invalid')
        return result['errors']

    def test_verifier_rejects_scheduler_pod_evidence_mismatch(self):
        # Review P1: the captured Pod, not only duplicated placement dicts, must carry the selection.
        cases = {
            'off-candidate node': ('scheduler-compressed-10-deployment.json', lambda d: d['pods']['vision']['spec'].update(nodeName='not-a-candidate')),
            'pod node differs from record': ('scheduler-compressed-10-deployment.json', lambda d: d['pods']['speech']['spec'].update(nodeName=C)),
            'record uid differs from pod': ('scheduler-compressed-10.json', lambda r: r['pods']['vision'].update(uid='uid-999')),
            'not default-scheduler': ('scheduler-compressed-2-deployment.json', lambda d: d['pods']['fusion']['spec'].update(schedulerName='other')),
            'unscheduled state': ('scheduler-compressed-2-deployment.json',
                lambda d: d['pods']['fusion']['status'].update(conditions=[dict(type='PodScheduled', status='False')])),
            'pinned captured pod': ('scheduler-compressed-10-deployment.json',
                lambda d: d['pods']['vision']['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']
                    .update(nodeSelectorTerms=[{'matchFields': [{'key': 'metadata.name', 'operator': 'In', 'values': [E1]}]}])),
            'candidate list changed': ('scheduler-compressed-10.json', lambda r: r.update(candidates=[E1, C])),
            'no scheduled event': ('scheduler-compressed-10.json', lambda r: r.update(events=r['events'][1:])),
        }
        for case, (name, change) in cases.items():
            with self.subTest(case):
                self.assertTrue(any('scheduler Pod' in e for e in self.errors_for(name, change)), case)

    def test_verifier_rejects_wrong_workflow_identity(self):
        # Review P2: run/phase/request identity from config and live terminal rows.
        label = self.first_eval()
        cases = {
            'row run_id': (label+'.rows.json', lambda rows: rows[0].update(run_id='wan-other')),
            'row request_id': (label+'.rows.json', lambda rows: rows[0].update(request_id=label+'-2')),
            'row phase': (label+'.rows.json', lambda rows: rows[1].update(phase=label+'-warm')),
            'row missing request_id': (label+'.rows.json', lambda rows: rows[2].pop('request_id')),
            'config run_id': (label+'.config.json', lambda c: c.update(run_id='wan-other')),
            'config phase': (label+'-warm.config.json', lambda c: c.update(phase=label)),
        }
        for case, (name, change) in cases.items():
            with self.subTest(case):
                self.assertTrue(any('identity' in e for e in self.errors_for(name, change)), case)

    def test_verifier_rejects_wrong_final_output_bytes(self):
        # Review P2: fusion output_bytes must equal the hashed serialized payload length.
        label = self.first_eval()
        errors = self.errors_for(label+'.rows.json', lambda rows: rows[0]['receipts'][2].update(output_bytes=-123))
        self.assertIn(label+': final payload bytes', errors)

    def test_scheduler_snapshot_is_projected_before_persistence(self):
        # Standards P2: no persisted artifact may contain the scheduler env value.
        self.assertEqual(persisted_with(self.rundir, CANARY), [])
        self.assertFalse(any('kube-system' in args for _, args in self.pilot.calls))
        (command, kwargs), = self.pilot.kubectl.calls
        self.assertEqual(kwargs['timeout'], 40)
        event = json.loads(next(self.rundir.glob('*-scheduler-snapshot.event.json')).read_text())
        self.assertEqual((event['status'], event['returncode'], event['timeout_s']), ('completed', 0, 40))
        self.assertTrue(event['started_at'] <= event['finished_at'])
        provenance = json.loads((self.rundir/'scheduler-provenance.json').read_text())
        self.assertEqual(provenance['pods'][0]['containers'][0]['image'], 'registry.k8s.io/kube-scheduler:v1.30.0')

    def test_verifier_rejects_raw_scheduler_snapshot(self):
        def edit(target):
            (target/'999-scheduler-snapshot.stdout').write_text('{}')
        self.assertIn('no raw scheduler snapshot persisted', self.tampered(edit)['errors'])

    def test_verifier_rejects_changed_source(self):
        def edit(target):
            path = target/'collector-source'/'run_baseline_comparison.py'
            path.write_text(path.read_text()+'\n# changed\n')
        result = self.tampered(edit)
        self.assertIn('source provenance experiments/real-topology-live/run_baseline_comparison.py', result['errors'])


class FailureTests(unittest.TestCase):
    def test_timeout_keeps_phase_failure_partial_evidence_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            pilot = run_fake(temp, fail_label='eval-1-compressed-10')
            self.assertIsInstance(pilot.error, TimeoutError)
            ends = [json.loads(p.read_text()) for p in Path(temp).glob('phase-*.end.json')]
            failed = [e for e in ends if e['status'] != 'ok']
            self.assertEqual([(e['name'], e['status']) for e in failed], [('evaluation-eval-1-compressed-10', 'timeout')])
            failure = json.loads((Path(temp)/'failure.json').read_text())
            self.assertEqual(failure['completed_labels'], ['eval-0-compressed-10'])
            self.assertTrue((Path(temp)/'cleanup.json').is_file())
            result = verify_quiet(temp)
            self.assertEqual(result['status'], 'partial')
            self.assertIn('failure.json present: partial run', result['errors'])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(vb.main([temp]), 1)

    def test_scheduler_snapshot_failure_and_timeout_persist_no_env(self):
        for behaviour, error_type, status in [('fail', RuntimeError, 'error'), ('timeout', subprocess.TimeoutExpired, 'timeout')]:
            with self.subTest(behaviour), tempfile.TemporaryDirectory() as temp:
                pilot = run_fake(temp, kubectl=fake_kubectl(behaviour))
                self.assertIsInstance(pilot.error, error_type)
                self.assertEqual(persisted_with(temp, CANARY), [])
                event = json.loads(next(Path(temp).glob('*-scheduler-snapshot.event.json')).read_text())
                self.assertEqual(event['status'], status)
                self.assertIn('finished_at', event)
                end = json.loads(next(Path(temp).glob('phase-*-scheduler-provenance.end.json')).read_text())
                self.assertEqual(end['status'], status)
                self.assertTrue((Path(temp)/'cleanup.json').is_file())
                self.assertEqual(verify_quiet(temp)['status'], 'partial')

    def test_failed_row_keeps_phase_identity_without_success_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            pilot = run_fake(temp, timeout_label='eval-0-compressed-10')
            self.assertFalse(hasattr(pilot, 'error'))
            path = Path(temp)/'eval-0-compressed-10.rows.json'
            rows = json.loads(path.read_text())
            rows[0]['phase'] = 'eval-1-compressed-10'
            path.write_text(json.dumps(rows))
            result = verify_quiet(temp)
            self.assertEqual(result['status'], 'invalid')
            self.assertIn('eval-0-compressed-10: terminal phase identity', result['errors'])

    def test_request_timeout_row_is_counted_not_dropped(self):
        with tempfile.TemporaryDirectory() as temp:
            pilot = run_fake(temp, timeout_label='eval-0-compressed-10')
            self.assertFalse(hasattr(pilot, 'error'))
            result = verify_quiet(temp)
            self.assertEqual(result['errors'], [])
            self.assertEqual((result['evaluation_attempts'], result['evaluation_successes'], result['evaluation_failures']), (12, 11, 1))
            summary = json.loads((Path(temp)/'pilot-summary.json').read_text())['observations']['eval-0-compressed-10']
            self.assertEqual((summary['attempts'], summary['success'], len(summary['failures'])), (3, 2, 1))


# Inherited Pilot.capacity() lists every Pod; raw JSON can carry env, args and annotations.
CAPACITY_PODS = [
    dict(metadata=dict(name='app', uid='p1', namespace='team', annotations={'kubectl.kubernetes.io/last-applied-configuration': CANARY}),
         spec=dict(nodeName=C, overhead=dict(cpu='100m', memory='64Mi'),
             containers=[dict(name='a', args=['--token='+CANARY], env=[dict(name='TOKEN', value=CANARY)],
                              resources=dict(requests=dict(cpu='500m', memory='1Gi'), limits=dict(cpu='2'))),
                         dict(name='b', resources=dict(requests=dict(cpu='250m', memory='512Mi')))],
             initContainers=[dict(name='init', env=[dict(name='TOKEN', value=CANARY)], resources=dict(requests=dict(cpu='1', memory='256Mi')))]),
         status=dict(phase='Running', podIP='10.0.0.9')),
    dict(metadata=dict(name='done', uid='p2', namespace='team'),
         spec=dict(nodeName=C, containers=[dict(name='a', resources=dict(requests=dict(cpu='3', memory='7Gi')))]),
         status=dict(phase='Succeeded')),
    dict(metadata=dict(name='edge', uid='p3', namespace='team'),
         spec=dict(nodeName=E0, containers=[dict(name='a', env=[dict(name='X', value=CANARY)], resources=dict(requests=dict(cpu='2', memory='1Gi')))],
             initContainers=[dict(name='i', resources=dict(requests=dict(cpu='3', memory='3Gi')))]),
         status=dict(phase='Pending')),
    dict(metadata=dict(name='bare', uid='p4', namespace='team'), spec=dict(nodeName=E1, containers=[dict(name='a')]), status=dict(phase='Running'))]


def capacity_kubectl(pods, behaviour='ok'):
    """subprocess.run stand-in at the I/O boundary for the inherited nodes/pods capacity calls."""
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        args = command[4:]
        if args == ['get', 'nodes', '-o', 'json']:
            raw = json.dumps(dict(items=[dict(metadata=dict(name=n), status=dict(conditions=[dict(type='Ready', status='True')],
                allocatable=dict(cpu='4', memory='8Gi'))) for n in NODES.values()])).encode()
        elif args == ['get', 'pods', '-A', '-o', 'json']:
            raw = json.dumps(dict(items=pods)).encode()
            assert CANARY.encode() in raw
            if behaviour == 'timeout':
                raise subprocess.TimeoutExpired(command, kwargs['timeout'], output=raw)
        else:
            raise AssertionError(command)
        if kwargs['stdout'] is subprocess.PIPE:
            return subprocess.CompletedProcess(command, 0, stdout=raw)
        kwargs['stdout'].write(raw)
        return subprocess.CompletedProcess(command, 0)
    run.calls = calls
    return run


def real_pilot(out):
    """Real BaselinePilot methods (Session.call, Pilot.capacity) without the cluster-facing __init__."""
    pilot = rb.BaselinePilot.__new__(rb.BaselinePilot)
    pilot.out, pilot.counter, pilot.calibration_run, pilot.active_profile = Path(out), 0, None, None
    return pilot


class CapacityPersistenceTests(unittest.TestCase):
    def capacity(self, temp, pods=CAPACITY_PODS, behaviour='ok'):
        pilot = real_pilot(temp)
        kubectl = capacity_kubectl(pods, behaviour)
        with patch('subprocess.run', kubectl):
            return pilot, kubectl, pilot.capacity()

    def test_inherited_capacity_persists_no_raw_pod_env(self):
        with tempfile.TemporaryDirectory() as temp:
            _, kubectl, _ = self.capacity(temp)
            self.assertEqual(persisted_with(temp, CANARY), [])
            self.assertEqual(len(kubectl.calls), 2)
            # Node call keeps the unchanged Session.call path; pods call keeps events but not raw stdout.
            self.assertTrue((Path(temp)/'001-nodes.stdout').is_file())
            self.assertFalse((Path(temp)/'002-pods.stdout').exists())
            event = json.loads((Path(temp)/'002-pods.event.json').read_text())
            self.assertEqual((event['status'], event['returncode'], event['timeout_s']), ('completed', 0, 40))
            self.assertEqual(event['command'][4:], ['get', 'pods', '-A', '-o', 'json'])
            projected = json.loads((Path(temp)/'002-pods.projected.json').read_text())
            self.assertEqual(set(projected['items'][0]), {'metadata', 'spec', 'status'})
            self.assertEqual(projected['items'][0]['metadata'], dict(name='app', uid='p1', namespace='team'))

    def test_projected_capacity_matches_raw_accounting_with_init_and_overhead(self):
        with tempfile.TemporaryDirectory() as temp:
            _, _, capacity = self.capacity(temp)
        # C: max(500m+250m, 1000m init)+100m overhead = 1100m; max(1536Mi, 256Mi)+64Mi = 1600Mi; Succeeded Pod skipped.
        # E0: Pending counts; max(2000m, 3000m init) = 3000m and max(1024Mi, 3072Mi) = 3072Mi.
        self.assertEqual(capacity, {C: dict(cpu_free_m=2900, memory_free_mib=6592), E0: dict(cpu_free_m=1000, memory_free_mib=5120),
                                    E1: dict(cpu_free_m=4000, memory_free_mib=8192)})
        raw = Session.__new__(Session)
        with tempfile.TemporaryDirectory() as temp:
            raw.out, raw.counter = Path(temp), 0
            with patch('subprocess.run', capacity_kubectl(CAPACITY_PODS)):
                self.assertEqual(rb.Pilot.capacity(raw), capacity)

    def test_restartable_init_container_is_still_rejected(self):
        pods = copy.deepcopy(CAPACITY_PODS)
        pods[0]['spec']['initContainers'][0]['restartPolicy'] = 'Always'
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RuntimeError, 'Restartable init'):
                self.capacity(temp, pods)
            self.assertEqual(persisted_with(temp, CANARY), [])

    def test_pods_timeout_persists_no_env(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(subprocess.TimeoutExpired):
                self.capacity(temp, behaviour='timeout')
            self.assertEqual(persisted_with(temp, CANARY), [])
            self.assertEqual(json.loads((Path(temp)/'002-pods.event.json').read_text())['status'], 'timeout')

    def test_other_calls_use_unchanged_session_call(self):
        with tempfile.TemporaryDirectory() as temp:
            pilot = real_pilot(temp)
            with patch('subprocess.run', capacity_kubectl(CAPACITY_PODS)):
                raw = pilot.call('nodes', ['get', 'nodes', '-o', 'json'])
            self.assertEqual(raw, (Path(temp)/'001-nodes.stdout').read_text())
            with patch.object(Session, 'call', return_value='{}') as parent, patch.object(rb, 'projected_call') as projected:
                pilot.call('ns-pods', ['get', 'pods', '-n', NS, '-o', 'json'])
                pilot.call('apply', ['apply', '-f', '-'], 'stdin', timeout=5)
            self.assertEqual(parent.call_count, 2)
            projected.assert_not_called()


if __name__ == '__main__':
    unittest.main()
