"""Supervised live collection for the baseline-first comparison (only after a coordinator release; never in tests).

Control boundary (inherited from quality-network-heterogeneous qualify.HetSession, edge EdgeCore unpatched):
- create-only objects with recorded creation UIDs; cleanup deletes only those UIDs (multi-edge-sensors collect.cleanup);
- no exec/attach/logs/cp/port-forward to any edge-node Pod (refused before kubectl; edge logs skipped and recorded);
- server-Pod streams (cache upload, gateway netctl) only after a fresh GET shows this run's creation UID on the node;
- the only network control is tc inside the owned gateway Pod netns (net_control.py, approved values; NET_ADMIN on the
  netctl sidecar only, the pattern of real-topology-live/run_load.py). No host network change, no edge sidecar.
Data never flows from the collector: requests start at the edge generator Pod; the collector sends control only.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import tarfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import analyze  # noqa: E402
import design  # noqa: E402
import policies  # noqa: E402
sys.path.insert(0, str(design.QNH))
import qualify  # noqa: E402  (also puts real-topology-live/http_runtime.py on sys.path)
import bounded_http  # noqa: E402

ownership, baseline = qualify.ownership, qualify.baseline
NS = qualify.NS
LIVE = qualify.LIVE
SHIPPED_QNH = ('worker.py', 'stages.py', 'bootstrap.py', 'proc.py')
SHIPPED_HERE = ('dag_worker.py', 'generator.py', 'policies.py', 'gateway.py', 'bounded_http.py')
PYROUTE2 = qualify.ASSETS/'network-wheels/pyroute2-0.9.6-py3-none-any.whl'
MODEL_KEYS = ('model_sha256', 'derived_artifact_sha256')
PYROUTE2_SHA256 = '3334091326e560a506635449af03b26920d22d4e5a7996aed354363d106fcef8'
CORPUS_TIMEOUT_S = 600  # collector /corpus call; the generator's whole fetch budget is 10 s inside it


def config_data():
    data = {f: (design.QNH/f).read_text() for f in SHIPPED_QNH}
    data.update({f: (HERE/f).read_text() for f in SHIPPED_HERE})
    data['http_runtime.py'] = (LIVE/'http_runtime.py').read_text()  # unchanged frozen copy
    data['net_control.py'] = (LIVE/'net_control.py').read_text()    # unchanged frozen copy
    return data


def shipped_hashes():
    import hashlib
    return {k: hashlib.sha256(v.encode()).hexdigest() for k, v in config_data().items()}


def meta(run, name, component):
    return dict(name=name, namespace=NS, labels={'experiment': design.EXPERIMENT, 'run-id': run, 'component': component})


def config_map(run):
    return dict(apiVersion='v1', kind='ConfigMap', metadata=meta(run, run, 'config'), data=config_data())


def cache_pod(run):
    pod = qualify.cache_manifest(run)
    pod['metadata'] = meta(run, run+'-cache', 'cache')
    pod['spec']['activeDeadlineSeconds'] = design.BUDGETS['cache_pod_lifetime_s']
    return pod


def worker_pod(run, worker, cache_url, manifest_sha):
    pod = qualify.pod_manifest(run, worker, cache_url, manifest_sha)
    pod['metadata'] = meta(run, run+'-'+worker['key'], 'worker')
    pod['spec']['activeDeadlineSeconds'] = design.BUDGETS['worker_pod_lifetime_s']
    pod['spec']['containers'][0]['env'] += [dict(name='WORKER_MODULE', value='dag_worker'),
                                            dict(name='DAG_MAX_ACTIVE', value=str(design.BUDGETS['dag_max_active']))]
    return pod


def helper_pod(run, key, node, command, resources, extra_containers=(), env=()):
    container = dict(name='worker', image=qualify.IMAGE, imagePullPolicy='IfNotPresent', command=command,
                     env=[dict(name='NODE_NAME', valueFrom={'fieldRef': {'fieldPath': 'spec.nodeName'}}),
                          dict(name='POD_UID', valueFrom={'fieldRef': {'fieldPath': 'metadata.uid'}}),
                          dict(name='PYTHONDONTWRITEBYTECODE', value='1'), *env],
                     resources=resources, ports=[dict(containerPort=8080)],
                     readinessProbe={'httpGet': {'path': '/ready' if key == 'generator' else '/health', 'port': 8080}, 'periodSeconds': 5,
                                     'timeoutSeconds': 3},
                     volumeMounts=[dict(name='work', mountPath='/work'), dict(name='config', mountPath='/config', readOnly=True)],
                     securityContext={'runAsUser': 65532, 'runAsGroup': 65532, 'runAsNonRoot': True, 'allowPrivilegeEscalation': False,
                                      'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}})
    spec = dict(restartPolicy='Never', activeDeadlineSeconds=design.BUDGETS['worker_pod_lifetime_s'], terminationGracePeriodSeconds=3,
                automountServiceAccountToken=False, securityContext={'fsGroup': 65532, 'seccompProfile': {'type': 'RuntimeDefault'}},
                affinity={'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [
                    {'matchFields': [{'key': 'metadata.name', 'operator': 'In', 'values': [node]}]}]}}},
                containers=[container, *extra_containers],
                volumes=[{'name': 'work', 'emptyDir': {'sizeLimit': '1Gi'}}, {'name': 'config', 'configMap': {'name': run}}])
    return dict(apiVersion='v1', kind='Pod', metadata=meta(run, run+'-'+key, key), spec=spec)


def generator_pod(run):
    return helper_pod(run, 'generator', design.ORIGIN_NODE, ['python3', '-u', '/config/generator.py'],
                      {'requests': {'cpu': '250m', 'memory': '384Mi', 'ephemeral-storage': '256Mi'},
                       'limits': {'cpu': '1', 'memory': '1Gi', 'ephemeral-storage': '1Gi'}})


def gateway_pod(run, cache_url):
    """Gateway relay (non-root) + netctl sidecar: root with ONLY NET_ADMIN, no privilege escalation, read-only root fs,
    pyroute2 fetched sha-verified from the owned cache; acts only on this Pod's own netns (run_load.py pattern)."""
    setup = ("import urllib.request,io,zipfile,hashlib,time;op=urllib.request.build_opener(urllib.request.ProxyHandler({}));"
             f"b=op.open({cache_url+'/netctl/'+PYROUTE2.name!r},timeout=120).read();assert hashlib.sha256(b).hexdigest()=={PYROUTE2_SHA256!r};"
             "zipfile.ZipFile(io.BytesIO(b)).extractall('/work/net');print('netctl-ready',flush=True);"
             f"time.sleep({design.BUDGETS['worker_pod_lifetime_s']})")
    netctl = dict(name='netctl', image=qualify.IMAGE, imagePullPolicy='IfNotPresent', command=['python3', '-u', '-c', setup],
                  env=[dict(name='RESEARCH_LINK_CONTROL', value='owned-pod-netns'), dict(name='PYTHONPATH', value='/work/net:/config')],
                  volumeMounts=[dict(name='work', mountPath='/work'), dict(name='config', mountPath='/config', readOnly=True)],
                  resources={'requests': {'cpu': '10m', 'memory': '32Mi'}, 'limits': {'cpu': '1', 'memory': '96Mi'}},
                  securityContext={'runAsUser': 0, 'runAsNonRoot': False, 'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                   'capabilities': {'drop': ['ALL'], 'add': ['NET_ADMIN']}})
    pod = helper_pod(run, 'gateway', design.SERVER_NODE, ['python3', '-u', '/config/gateway.py'],
                     {'requests': {'cpu': '500m', 'memory': '256Mi', 'ephemeral-storage': '128Mi'},
                      'limits': {'cpu': '2', 'memory': '1Gi', 'ephemeral-storage': '1Gi'}}, extra_containers=[netctl],
                     env=[dict(name='GATEWAY_UPSTREAM_CAP_S', value=str(design.BUDGETS['gateway_upstream_cap_s']))])
    pod['spec']['securityContext'].pop('fsGroup')  # the netctl root sidecar needs no group remap; volumes are emptyDir/configMap
    return pod


class BaselineSession(qualify.HetSession):
    """HetSession kubectl seam (create-only UIDs, fail-closed stream guard) for this experiment's labels/run ids."""

    def __init__(self, out_root, run_id, plan):
        if not re.fullmatch(r'qnb-[a-z0-9][a-z0-9-]{0,36}', run_id):
            raise ValueError('run-id must match qnb-[a-z0-9-]')
        self.run, self.out, self.wave = run_id, Path(out_root)/run_id, plan
        self.out.mkdir(parents=True, exist_ok=False)
        self.counter, self.resources, self.unresolved, self.actual = 0, [], [], {}
        self.expected = {'cache': (design.SERVER_NODE, False), 'gateway': (design.SERVER_NODE, False), 'generator': (design.ORIGIN_NODE, True),
                         **{w['key']: (w['node'], w['edge']) for w in plan['workers']}}

    def create(self, stem, items):
        value = dict(apiVersion='v1', kind='List', items=items)
        self.save(stem+'.manifest.json', value)
        self.call(stem+'-dry-run', ['create', '--dry-run=server', '-f', '-', '-o', 'json'], json.dumps(value))
        error = None
        try:
            raw = self.call(stem, ['create', '-f', '-', '-o', 'json'], json.dumps(value))
        except Exception as exc:
            error = exc
            raw = (self.out/f'{self.counter:04d}-{stem}.stdout').read_text(errors='replace')
        objects, complete = ownership.created_objects(raw)
        records = []
        for obj in objects:
            m = obj['metadata']
            labels = m.get('labels', {})
            record = dict(kind=obj['kind'], name=m['name'], uid=m['uid'], created_at=m.get('creationTimestamp'),
                          owned_labels=labels.get('experiment') == design.EXPERIMENT and labels.get('run-id') == self.run)
            records.append(record)
            self.resources.append(record)
        confirmed = {(r['kind'], r['name']) for r in records}
        if error is not None or not complete:
            self.unresolved += [dict(kind=i['kind'], name=i['metadata']['name'], stem=stem) for i in items
                                if (i['kind'], i['metadata']['name']) not in confirmed]
        self.save(stem+'.created.json', dict(confirmed=records, stdout_complete=complete))
        if error is not None:
            raise RuntimeError(f'{stem}: create failed after {len(records)}/{len(items)} confirmed') from error
        if not complete or len(records) != len(items) or not all(r['owned_labels'] for r in records):
            raise RuntimeError(f'{stem}: create confirmed {len(records)}/{len(items)} owned objects')

    def net(self, spec, stem):
        """tc inside the owned gateway Pod netns (server Pod; stream allowed only after the fresh ownership GET)."""
        raw = self.call(stem, ['exec', '-i', '-n', NS, self.run+'-gateway', '-c', 'netctl', '--', 'python3', '-u', '-c',
                               "import runpy;runpy.run_path('/config/net_control.py',run_name='__main__')"],
                        json.dumps(spec), timeout=60)
        return json.loads(raw)


class Live:
    """Phases of one release-bound run. http(url, body=None, timeout) is the control-plane HTTP seam; by default every
    call is bounded_http.control: one absolute monotonic deadline for connect .. last byte (a trickling peer cannot hold
    it) and a reply cap of BUDGETS['control_max_body_bytes']."""

    def __init__(self, session, plan, plan_dir, stage, frozen=None, http=None, clock=time.monotonic, sleep=time.sleep):
        self.s, self.plan, self.plan_dir, self.stage, self.frozen = session, plan, Path(plan_dir), stage, frozen
        self.http, self.clock, self.sleep, self.b = http or self.bounded_control, clock, sleep, design.BUDGETS
        self.urls, self.ips, self.uids, self.skipped = {}, {}, {}, []

    def save(self, name, value):
        self.s.save(name, value)

    def bounded_control(self, url, body=None, timeout=None):
        return bounded_http.control(url, body, timeout=timeout, max_body=self.b['control_max_body_bytes'])

    def control(self, url, body=None, timeout=None, until=None):
        """One control call, absolute budget min(timeout or control_timeout_s, until - now) when it runs inside a bounded
        phase (until = that phase's clock deadline); an exhausted phase raises DeadlineExceeded without a call."""
        budget = timeout or self.b['control_timeout_s']
        if until is not None:
            budget = min(budget, until - self.clock())
            if budget <= 0:
                raise bounded_http.DeadlineExceeded(budget)
        return self.http(url, body, timeout=budget)

    # -- deployment
    def deploy(self):
        s = self.s
        with baseline.phase(s, 'preflight'):
            ownership.refuse_reused_run(s)
        with baseline.phase(s, 'cache'):
            s.create('cache', [cache_pod(s.run)])
            if not s.wait_ready('cache', self.b['cache_ready_s']):
                raise RuntimeError('cache Pod not Ready')
            cache = s.owned_state('cache', 'cache-state')
            manifest = json.loads((self.plan_dir/'assets-manifest.json').read_text())
            tar_path = s.out/'assets.tar'
            with tarfile.open(tar_path, 'x') as tar:
                tar.add(self.plan_dir/'assets-manifest.json', 'assets-manifest.json')
                for entry in manifest['files']:
                    tar.add(entry['source'], entry['path'])
            with tar_path.open('rb') as source:
                s.call('cache-upload', ['exec', '-i', '-n', NS, s.run+'-cache', '-c', 'worker', '--', 'python3', '-c',
                                        "import tarfile,sys;tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall('/work',filter='data')"],
                       stdin=source, timeout=self.b['upload_timeout_s'])
            tar_path.unlink()  # the tar is a transport copy of plan files already hashed in the plan
            self.cache_url = 'http://'+cache['status']['podIP']+':8080'
        with baseline.phase(s, 'pods'):
            s.create('pods', [config_map(s.run)] + [worker_pod(s.run, w, self.cache_url, self.plan['assets_manifest_sha256'])
                                                    for w in self.plan['workers']] + [gateway_pod(s.run, self.cache_url), generator_pod(s.run)])
        self.wait_all()

    def wait_all(self):
        s, keys = self.s, [w['key'] for w in self.plan['workers']] + ['gateway', 'generator']
        pending, pull, started = set(keys), {}, self.clock()
        verdicts = {}
        with baseline.phase(s, 'readiness', window_s=self.b['ready_window_s']):
            while pending and self.clock() - started < self.b['ready_window_s']:
                states = s.pod_states()
                for key in sorted(pending):
                    obj = states.get(key)
                    if obj is None:
                        continue
                    verdict, reason = qualify.pod_verdict(obj, pull, key)
                    if verdict != 'pending':
                        verdicts[key] = dict(verdict=verdict, reason=reason, at_s=self.clock()-started, node=obj['spec'].get('nodeName'),
                                             uid=obj['metadata']['uid'], ip=obj.get('status', {}).get('podIP'))
                        pending.discard(key)
                if pending:
                    self.sleep(self.b['poll_s'])
        for key in pending:
            verdicts[key] = dict(verdict='not_ready_at_window')
        self.save('readiness.json', verdicts)
        failed = [k for k, v in verdicts.items() if v['verdict'] != 'ready']
        for key, v in verdicts.items():
            if v['verdict'] == 'ready':
                self.urls[key], self.ips[key], self.uids[key] = f"http://{v['ip']}:8080", v['ip'], v['uid']
        if any(k in failed for k in ('gateway', 'generator')):
            raise RuntimeError(f'helper Pods not ready: {failed}')
        self.verify_workers(failed)

    def verify_workers(self, failed):
        """Identity and load status over HTTP (/health): node and Pod UID must be the creation UID's; a worker whose
        stage did not load, or whose loaded model / derived-artifact hashes are not the plan-bound ones
        (plan model_provenance, from the frozen assets), is removed from the candidate set with its evidence (no
        substitution)."""
        health, removed = {}, {}
        expected = self.plan['model_provenance']['expectations']
        for w in self.plan['workers']:
            key = w['key']
            if key in failed:
                removed[key] = 'not ready'
                continue
            try:
                h = self.control(self.urls[key]+'/health', timeout=20)
            except Exception as error:
                removed[key] = f'/health error {error!r}'[:300]
                continue
            health[key] = h
            record = self.s.record_for(self.s.run+'-'+key)
            if h.get('pod_uid') != (record or {}).get('uid') or h.get('node') != w['node']:
                removed[key] = f"identity mismatch: health {h.get('node')}/{h.get('pod_uid')} vs created {w['node']}/{(record or {}).get('uid')}"
            elif h.get('errors'):
                removed[key] = dict(load_errors=h['errors'])
            elif set(w['stages']) - set(h.get('stages') or {}):
                removed[key] = 'stages not loaded'
            else:
                wrong = {t: {k: h['stages'][t].get(k) for k in MODEL_KEYS} for t in w['stages']
                         if any(h['stages'][t].get(k) != expected[key][t][k] for k in MODEL_KEYS)}
                if wrong:
                    removed[key] = dict(model_provenance_mismatch=wrong)
        self.health = health
        self.save('worker-health.json', dict(health=health, removed=removed))
        self.workers = [w for w in self.plan['workers'] if w['key'] not in removed]
        self.candidates = design.candidates(self.workers)
        cover = design.coverage(self.candidates)
        self.fixed = design.fixed_plan(self.workers)
        self.save('live-candidates.json', dict(candidates=self.candidates, coverage=cover, removed=removed, fixed_plan=getattr(self, 'fixed', None)))
        # The comparison gate: live CPU+GPU+NPU with several multi-choice tasks and the fixed heterogeneous baseline.
        # A reduced fleet is recorded above and refused here; it never becomes a silently narrower comparison.
        if any(not ws for ws in self.candidates.values()) or not cover['ok'] or self.fixed is None:
            raise RuntimeError(f'live candidates fail the heterogeneous comparison gate: coverage={cover} fixed_plan={self.fixed} '
                               f'removed={sorted(removed)}')

    # -- configuration and calibration
    def configure(self):
        peers = {w['key']: dict(url=self.urls[w['key']], zone=w['zone']) for w in self.workers}
        peers['origin'] = dict(url=self.urls['generator'], zone='edge')
        self.save('gateway-peers.json', self.control(self.urls['gateway']+'/peers', dict(peers=peers)))
        body = dict(run_id=self.s.run, workers={w['key']: dict(url=self.urls[w['key']], zone=w['zone'], node=w['node'], pod_uid=self.uids[w['key']])
                                                for w in self.workers},
                    gateway_url=self.urls['gateway'], origin_url=self.urls['generator'], origin_zone='edge', candidates=self.candidates,
                    poll_s=self.b['obs_poll_s'], poll_timeout_s=self.b['obs_poll_timeout_s'])
        self.save('generator-configure.json', self.control(self.urls['generator']+'/configure', body))
        corpus = json.loads((self.plan_dir/'corpus.json').read_text())
        self.save('generator-corpus.json', self.control(self.urls['generator']+'/corpus', dict(cache_url=self.cache_url, files=corpus['files'],
                                                                                              budget_s=CORPUS_TIMEOUT_S - 10),
                                                        timeout=CORPUS_TIMEOUT_S))
        self.edge_ips = [self.ips[k] for k in [w['key'] for w in self.workers if w['zone'] == 'edge'] + ['generator']]
        self.cloud_ips = [self.ips[w['key']] for w in self.workers if w['zone'] == 'cloud']
        self.save('net-initial.json', self.s.net(dict(action='snapshot'), 'net-initial'))

    def shape(self, name, restore=False):
        """Each application of a condition is its own phase receipt (a condition recurs: control, pilot, restore). A
        condition change first needs a quiescent fleet (no work of the previous condition crosses it); the final
        restore records its drain but always applies."""
        n = design.NETWORKS[name]
        self.shapes = getattr(self, 'shapes', 0) + 1
        tag = f'{self.shapes:02d}-{name}'
        before = self.drain('before-shape-'+tag)
        self.save(f'drain-before-shape-{tag}.json', before)
        if not before['idle'] and not restore:
            raise RuntimeError(f'not quiescent before network condition {name}')
        spec = dict(action='shape', mbps=n['mbps'], delay_ms=n['delay_ms'], cloud=self.cloud_ips, edge=self.edge_ips)
        self.save(f'net-{tag}.json', self.s.net(spec, 'shape-'+tag))

    def job(self, path, body, name, timeout):
        until = self.clock() + timeout
        self.control(self.urls['generator']+path, body, until=until)
        while self.clock() < until:
            try:
                state = self.control(self.urls['generator']+'/job?name='+name, until=until)
            except TimeoutError:
                if self.clock() < until:
                    raise
                break
            if state['state'] != 'running':
                return state
            self.sleep(self.b['poll_s'])
        return dict(state='timeout', timeout_s=timeout)

    def profile(self):
        cal = [p['question_id'] for p in self.plan['pairs'] if p['split'] == 'calibration'][:self.b['profile_pairs']]
        producers = {t: next(k for k in self.candidates[t] if k.startswith('cpu-c')) if any(k.startswith('cpu-c') for k in self.candidates[t])
                     else self.candidates[t][0] for t in policies.TASKS}
        with baseline.phase(self.s, 'profile'):
            result = self.job('/profile', dict(question_ids=cal, warmup=self.b['profile_warmup'], producers=producers), 'profile',
                              self.b['profile_timeout_s'])
        self.save('profile.json', dict(result, producers=producers))
        if result['state'] != 'done':
            raise RuntimeError(f"profile job {result['state']}")
        problems = self.profile_problems(result['result'], [str(q) for q in cal], producers)
        self.save('profile-qualification.json', dict(qualified=not problems, problems=problems, question_ids=cal))
        if problems:
            raise RuntimeError(f'calibration does not qualify the live candidates: {problems[:5]}')
        self.compute = result['result']['compute_ms']
        self.payload = result['result']['payload_bytes']

    def profile_problems(self, result, qids, producers):
        """Every candidate's calibration receipts are the created worker's (UID, node, backend, device), this run and the
        plan-bound model provenance (never the worker's own /health report); every (task, candidate) answered every
        calibration question; every vision/speech candidate's split path gave the original path's final answer
        (generator.equivalence); every other preprocessing candidate matched the reference producer byte for byte or gave
        that final answer downstream (generator.preprocessing)."""
        expected = self.plan['model_provenance']['expectations']
        workers = {w['key']: dict(node=w['node'], backend=w['backend'],
                                  models={t: {k: d[k] for k in MODEL_KEYS} for t, d in expected[w['key']].items()})
                   for w in self.workers}
        problems = []

        def check(request_id, task, worker, receipt):
            reason = analyze.receipt_reason(receipt, task, worker, dict(request_id=request_id), workers, self.uids, self.s.run)
            if reason:
                problems.append(f'{request_id}: {task}@{worker} {reason}')
        for task in policies.TASKS:
            for worker in self.candidates[task]:
                rows = [r for r in result['rows'] if r['task'] == task and r['worker'] == worker]
                answered = sorted(str(r['question_id']) for r in rows if not r['warmup'] and r.get('status') == 'ok' and r.get('receipt'))
                if answered != sorted(qids):
                    problems.append(f'{task}@{worker}: incomplete calibration ({len(answered)}/{len(qids)} answered)')
                for r in rows:
                    if r.get('status') == 'ok':
                        check(r['request_id'], task, worker, r.get('receipt'))
        for r in result.get('original_rows') or []:
            if r.get('status') != 'ok':
                problems.append(f"{r['request_id']}: original path failed")
            else:
                check(r['request_id'], r['task'], r['worker'], r.get('receipt'))
        expected = {(t, w, q) for t in ('vision', 'speech') for w in self.candidates[t] for q in qids}
        equal = set()
        for r in result.get('equivalence') or []:
            if r.get('answer_equal') is True:
                for path in ('split', 'original'):
                    check(r[path+'_request_id'], 'fusion', r['fusion_worker'], r.get(path+'_receipt'))
                if r['fusion_worker'] == producers['fusion']:
                    equal.add((r['task'], r['worker'], str(r['question_id'])))
        for t, w, q in sorted(expected - equal):
            problems.append(f'{t}@{w} q{q}: split-path final answer not shown equal to the original path')
        # Preprocessing: every non-reference candidate is byte-identical to the reference producer or gives the original
        # path's final answer through the reference model + fusion (generator.preprocessing).
        expected = {(t, w, q) for t in policies.PREPROCESS for w in self.candidates[t] if w != producers[t] for q in qids}
        equal = set()
        for r in result.get('preprocess_equivalence') or []:
            if r.get('answer_equal') is True and r.get('reference') == producers[r['task']]:
                if r.get('proof') == 'downstream_answer':
                    model = 'vision' if r['task'] == 'image_preprocess' else 'speech'
                    if r.get('model_worker') != producers[model] or r.get('fusion_worker') != producers['fusion']:
                        continue
                    check(r['model_request_id'], model, r['model_worker'], r.get('model_receipt'))
                    check(r['fusion_request_id'], 'fusion', r['fusion_worker'], r.get('fusion_receipt'))
                elif r.get('proof') != 'bytes_identical':
                    continue
                equal.add((r['task'], r['worker'], str(r['question_id'])))
        for t, w, q in sorted(expected - equal):
            problems.append(f'{t}@{w} q{q}: preprocessing output neither identical to the reference producer {producers[t]} '
                            f'nor shown to give the original-path final answer')
        return problems

    def probes(self, condition, force_direct=False):
        pairs = design.dag_pairs(self.candidates)
        if force_direct:
            pairs = [(a, b) for a, b in pairs if self.zone(a) != self.zone(b)]
        name = f'probes-{condition}' + ('-direct-control' if force_direct else '')
        with baseline.phase(self.s, name):
            result = self.job('/probes', dict(job=name, pairs=pairs, sizes=list(self.b['probe_sizes']), reps=self.b['probe_reps'],
                                              timeout_s=self.b['probe_timeout_s'], force_direct=force_direct), name,
                              self.b['probe_job_timeout_s'])
        self.save(name+'.json', result)
        if result['state'] != 'done':
            raise RuntimeError(f"{name} job {result['state']}")
        return result['result']['links']

    def zone(self, key):
        return 'edge' if key == policies.ORIGIN else next(w['zone'] for w in self.workers if w['key'] == key)

    def condition_profile(self, condition, links):
        bad = [k for k, v in links.items() if not v['complete']]
        if bad:
            raise RuntimeError(f'{condition}: incomplete route probes {bad}')
        return dict(compute_ms=self.compute, links={k: v['fit'] for k, v in links.items()}, payload_bytes=self.payload,
                    source=f'measured:{self.s.run}:{condition}')

    # -- traces
    def quiescence(self, until=None):
        queues = {}
        for w in self.workers:
            try:
                queues[w['key']] = self.control(self.urls[w['key']]+'/run', dict(mode='queue', run_id=self.s.run, request_id='drain'),
                                                until=until)
            except Exception as error:
                queues[w['key']] = dict(error=repr(error)[:200])
        try:
            gw = self.control(self.urls['gateway']+'/health', until=until)
        except Exception as error:
            gw = dict(error=repr(error)[:200])
        state = dict(queues=queues, gateway_active=gw.get('active'))
        idle = gw.get('active') == 0 and all(q.get(k) == 0 for q in queues.values() for k in ('waiting', 'active', 'joins_open', 'inflight'))
        progress = {k: (q.get('completed'), q.get('dag_requests')) for k, q in queues.items()}
        return idle, progress, state

    def drain(self, label):
        """Idle = every worker has no queued/active model call, open join or admitted request (forwarding included) and the
        gateway relays nothing; it must hold again, with no new progress, after the settle interval (stable barrier)."""
        started, last = self.clock(), None
        until = started + self.b['drain_timeout_s']  # every queue/health call inside gets only the remaining drain budget
        while self.clock() < until:
            idle, progress, last = self.quiescence(until)
            if idle:
                self.sleep(self.b['drain_settle_s'])
                again, progress_after, last = self.quiescence(until)
                if again and progress_after == progress:
                    reset = self.control(self.urls['gateway']+'/reset', dict(label=label))
                    return dict(idle=True, waited_s=self.clock()-started, state=last, gateway_reset=reset)
            self.sleep(1)
        return dict(idle=False, waited_s=self.clock()-started, state=last)

    def trace(self, cell, profile, deadline):
        label = cell['label']
        window = self.frozen_window
        need = window + self.b['cancel_s'] + self.b['drain_timeout_s'] + 30
        if deadline - self.clock() < need:
            self.skipped.append(dict(cell, reason='collection budget'))
            return
        before = self.drain('before-'+label)
        self.save(f'drain-before-{label}.json', before)
        if not before['idle']:
            self.skipped.append(dict(cell, reason='not idle before trace (drain timeout)'))
            return
        spec = next(dict(s) for s in design.POLICY_SPECS if s['name'] == cell['policy'])
        if spec['name'] == 'fixed':
            spec['plan'] = self.fixed
        body = dict(label=label, policy=spec, arrivals=design.arrivals(cell['rate_rps'], window, cell['seed'], self.trace_questions),
                    deadline_s=self.b['deadline_s'], cancel_s=self.b['cancel_s'], max_inflight=self.b['max_inflight'], profile=profile,
                    quality=self.quality)
        self.save(f'trace-{label}.request.json', {k: v for k, v in body.items() if k != 'profile'})
        try:
            self.control(self.urls['generator']+'/trace', body)
        except Exception as error:
            self.save(f'trace-{label}.start-error.json', dict(error=repr(error)[:1000]))
            return
        until = self.clock() + window + self.b['cancel_s'] + 60
        state = None
        while self.clock() < until:
            try:
                state = self.control(self.urls['generator']+'/status', until=until)['traces'][label]
            except TimeoutError:
                if self.clock() < until:
                    raise
                break
            if state['state'] != 'running':
                break
            self.sleep(self.b['poll_s'])
        if state is None or state['state'] == 'running':
            self.control(self.urls['generator']+'/stop', {})
            self.sleep(self.b['cancel_s'])
        self.save(f'ledger-{label}.json', self.control(self.urls['generator']+'/ledger?label='+label, timeout=120))
        self.save(f'gateway-{label}.json', self.control(self.urls['gateway']+'/health'))

    def run(self):
        s, started = self.s, self.clock()
        collection = self.b['pilot_collection_s'] if self.stage == 'pilot' else self.b['compare_collection_s']
        self.deploy()
        deadline = self.clock() + collection
        self.configure()
        self.quality = self.plan['quality']
        self.trace_questions = [p['question_id'] for p in self.plan['pairs'] if p['split'] == 'evaluation']
        self.profile()
        if self.stage == 'pilot':
            pilot = self.plan['pilot']
            cells, conditions, self.frozen_window = design.pilot_cells(pilot), pilot['conditions'], pilot['window_s']
        else:
            compare = self.plan['compare']
            cells, conditions, self.frozen_window = design.compare_cells(self.frozen, compare), compare['conditions'], compare['window_s']
        self.save('cells.json', cells)  # the frozen cell list analysis recomputes every cohort from
        try:
            self.shape('relay-unshaped')
            self.probes('relay-unshaped', force_direct=True)  # mandatory relay-overhead control (direct vs relayed)
            for condition in conditions:
                self.shape(condition)
                profile = self.condition_profile(condition, self.probes(condition))
                self.save(f'profile-{condition}.json', profile)
                for cell in [c for c in cells if c['condition'] == condition]:
                    with baseline.phase(s, 'trace-'+cell['label']):
                        self.trace(cell, profile, deadline)
        finally:
            try:
                self.shape('relay-unshaped', restore=True)
            finally:
                self.save('collection.json', dict(stage=self.stage, elapsed_s=self.clock()-started, skipped=self.skipped,
                                                  deadline_reached=self.clock() >= deadline, candidates=getattr(self, 'candidates', None),
                                                  fixed_plan=getattr(self, 'fixed', None)))


def utc():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
