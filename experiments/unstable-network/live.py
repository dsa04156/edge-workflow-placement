"""Supervised live collection of the unstable-edge-network re-experiment (only after a coordinator release; never in tests).

Adapted copy of experiments/quality-network-baselines/live.py (sha256 in the plan's copied_sources). Unchanged control
boundary: create-only objects with recorded creation UIDs, cleanup by those UIDs only (multi-edge-sensors collect),
no exec/attach/logs/cp/port-forward to edge-node Pods, the cache upload stream only after a fresh ownership GET, and the
only network control is tc inside the owned gateway Pod netns (NET_ADMIN on the netctl sidecar only; no host network
change, no edge sidecar, no tunnel). Requests start at the edge generator Pod; the collector sends control only.

Changes:
- netctl sidecar runs netctl.py (HTTP :8081 in the gateway Pod netns) instead of per-call `kubectl exec net_control.py`:
  it alone holds the trace; the collector arms it per cell, starts it aligned to the generator, restores and tears down.
- one fresh profile + route probes under the good condition; that frozen good profile is every cell's policy input.
- per cell: idle barrier (+ gateway reset) -> netctl arm (phase 0) -> generator /trace (start_delay_s) -> netctl /start
  aligned from the collector's own round trips -> wait (generator terminal, then the netctl window end) -> restore
  (in-place change to good) -> generator stop/settle unless seen terminal -> ledger salvage -> drain barrier (generator included) -> netctl status -> teardown. After each
  cell a fleet check stops the run (no further cells) on a lost/restarted worker, recording the cell as fault-affected.
- coverage gate keeps CPU+GPU+NPU with several multi-choice tasks (no `fixed` policy here).

R1 corrections (independent spec/standards review 2026-10-27):
- an accepted or uncertain start error (a lost /trace reply, a failed netctl start, a /status timeout) never ends the cell
  silently: the collector sends generator /stop, waits boundedly for its terminal state, then salvages the ledger; a
  generator that does not stop is salvaged as a marked partial ledger (a row per scheduled arrival). Worker idleness is
  never taken as a stopped generator. Unresolved generation or a failed salvage fences every later cell.
- a failed or unknown drain barrier (before or after a cell) fences the cell's normal teardown/reset and every later cell;
  the owned qdisc is then removed only by the end-of-run teardown, labelled emergency (never an idle barrier).
- a start alignment over the tolerance marks the cell not comparable (analyze.timing_validity decides offline, including
  application lag).
- one absolute wall budget (WallBudget, design.BUDGETS wall_budget_s from before preparation) bounds every kubectl and
  HTTP call; cleanup_reserve_s at its end is kept for the network teardown and the UID-owned Pod cleanup.

R2 corrections (independent spec/standards review round 2):
- a normal completion (generator terminal) restores only after the netctl reports its whole frozen window (window_end),
  however early the generator finished; the wait is bounded, and a failed or unreachable window marks the timing invalid.
- a server-Pod stream's timeout is recomputed after its fresh UID ownership GET, at the actual kubectl start; the run's
  cleanup (live.cleanup) skips collect.cleanup when its fixed-timeout protected-after snapshot can no longer fit.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
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
import traces  # noqa: E402
sys.path.insert(1, str(design.QNH))
import qualify  # noqa: E402  (puts real-topology-live, quality-network-vqa and multi-edge-sensors on sys.path after it)
sys.path.append(str(design.QNB))  # unchanged reuse: bounded_http (and the shipped dag_worker/gateway files)
import bounded_http  # noqa: E402

ownership, baseline = qualify.ownership, qualify.baseline
NS = qualify.NS
LIVE = qualify.LIVE
SHIPPED_QNH = ('worker.py', 'stages.py', 'bootstrap.py', 'proc.py')        # unchanged
SHIPPED_QNB = ('dag_worker.py', 'gateway.py', 'bounded_http.py')            # unchanged (reused, not copied)
SHIPPED_HERE = ('generator.py', 'policies.py', 'netctl.py', 'traces.py')   # this experiment
PYROUTE2 = qualify.ASSETS/'network-wheels/pyroute2-0.9.6-py3-none-any.whl'
PYROUTE2_SHA256 = '3334091326e560a506635449af03b26920d22d4e5a7996aed354363d106fcef8'
MODEL_KEYS = ('model_sha256', 'derived_artifact_sha256')
CORPUS_TIMEOUT_S = 600
LEDGER_TIMEOUT_S = 120
WAIT_SLACK_S = 60          # wait for the generator's terminal state past start delay + window + cancel
WINDOW_SLACK_S = 10        # wait for the netctl window_end past its latest possible scheduled end (change call, poll)
STOP_SLACK_S = 30          # after /stop every admitted request ends by its absolute cancel_s; this much more to settle
PROTECTED_SNAPSHOT_S = 40  # run_baseline_comparison.projected_call's own timeout (protected-after inside collect.cleanup)
INHERITED_CALL_S = 60      # collect.Session.call default timeout, passed unclamped by inherited pod_states/owned_state


class WallBudgetExceeded(bounded_http.DeadlineExceeded):
    def __init__(self, what):
        TimeoutError.__init__(self, f'wall budget: {what}')


class WallBudget:
    """The one absolute wall budget of an execute run (design.BUDGETS wall_budget_s), started before preparation.
    Preparation, calibration and collection (each cell's restore/stop/salvage/drain included) end cleanup_reserve_s before
    the wall end (work_until). The end-of-run network teardown and the UID-owned Pod cleanup may use the reserve up to
    cleanup_until, which keeps PROTECTED_SNAPSHOT_S for the diagnostic snapshot that collect.cleanup runs with its own
    timeout. A call with no time left before its limit is refused (WallBudgetExceeded), never started."""

    def __init__(self, clock=time.monotonic, budgets=design.BUDGETS):
        self.clock, self.started = clock, clock()
        self.budget_s, self.reserve_s = budgets['wall_budget_s'], budgets['cleanup_reserve_s']
        self.end = self.started + self.budget_s
        self.work_until = self.end - self.reserve_s
        self.cleanup_until = self.end - PROTECTED_SNAPSHOT_S

    def bound(self, timeout, until, what):
        left = until - self.clock()
        if left <= 0:
            raise WallBudgetExceeded(f'{what}: no time left before the limit')
        return min(timeout, left)

    def record(self):
        now = self.clock()
        return dict(budget_s=self.budget_s, cleanup_reserve_s=self.reserve_s, elapsed_s=now - self.started,
                    work_remaining_s=self.work_until - now, wall_remaining_s=self.end - now)


def config_data():
    data = {f: (design.QNH/f).read_text() for f in SHIPPED_QNH}
    data.update({f: (design.QNB/f).read_text() for f in SHIPPED_QNB})
    data.update({f: (HERE/f).read_text() for f in SHIPPED_HERE})
    data['http_runtime.py'] = (LIVE/'http_runtime.py').read_text()  # unchanged frozen copy
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
    pyroute2 fetched sha-verified from the owned cache, then netctl.py serves :8081 and acts only on this Pod's netns.
    RUN_ID binds every netctl command to this run; on exit netctl tears its qdisc down."""
    port = design.BUDGETS['netctl_port']
    setup = ("import urllib.request,io,zipfile,hashlib,runpy,sys;op=urllib.request.build_opener(urllib.request.ProxyHandler({}));"
             f"b=op.open({cache_url+'/netctl/'+PYROUTE2.name!r},timeout=120).read();assert hashlib.sha256(b).hexdigest()=={PYROUTE2_SHA256!r};"
             "zipfile.ZipFile(io.BytesIO(b)).extractall('/work/net');sys.path[:0]=['/work/net','/config'];"
             "runpy.run_path('/config/netctl.py',run_name='__main__')")
    netctl = dict(name='netctl', image=qualify.IMAGE, imagePullPolicy='IfNotPresent', command=['python3', '-u', '-c', setup],
                  env=[dict(name='RESEARCH_LINK_CONTROL', value='owned-pod-netns'), dict(name='RUN_ID', value=run),
                       dict(name='NETCTL_PORT', value=str(port)), dict(name='PYTHONDONTWRITEBYTECODE', value='1')],
                  ports=[dict(containerPort=port)],
                  readinessProbe={'httpGet': {'path': '/health', 'port': port}, 'periodSeconds': 5, 'timeoutSeconds': 3},
                  volumeMounts=[dict(name='work', mountPath='/work'), dict(name='config', mountPath='/config', readOnly=True)],
                  resources={'requests': {'cpu': '10m', 'memory': '32Mi'}, 'limits': {'cpu': '1', 'memory': '128Mi'}},
                  securityContext={'runAsUser': 0, 'runAsNonRoot': False, 'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                   'capabilities': {'drop': ['ALL'], 'add': ['NET_ADMIN']}})
    pod = helper_pod(run, 'gateway', design.SERVER_NODE, ['python3', '-u', '/config/gateway.py'],
                     {'requests': {'cpu': '500m', 'memory': '256Mi', 'ephemeral-storage': '128Mi'},
                      'limits': {'cpu': '2', 'memory': '1Gi', 'ephemeral-storage': '1Gi'}}, extra_containers=[netctl],
                     env=[dict(name='GATEWAY_UPSTREAM_CAP_S', value=str(design.BUDGETS['gateway_upstream_cap_s']))])
    pod['spec']['securityContext'].pop('fsGroup')  # the netctl root sidecar needs no group remap; volumes are emptyDir/configMap
    return pod


class UnstableSession(qualify.HetSession):
    """HetSession kubectl seam (create-only UIDs, fail-closed stream guard) for this experiment's labels/run ids."""

    def __init__(self, out_root, run_id, plan, budget=None):
        if not re.fullmatch(r'un-[a-z0-9][a-z0-9-]{0,36}', run_id):
            raise ValueError('run-id must match un-[a-z0-9-]')
        self.run, self.out, self.wave = run_id, Path(out_root)/run_id, plan
        self.out.mkdir(parents=True, exist_ok=False)
        self.counter, self.resources, self.unresolved, self.actual = 0, [], [], {}
        self.expected = {'cache': (design.SERVER_NODE, False), 'gateway': (design.SERVER_NODE, False), 'generator': (design.ORIGIN_NODE, True),
                         **{w['key']: (w['node'], w['edge']) for w in plan['workers']}}
        self.budget, self.cleaning = budget, False

    def begin_cleanup(self):
        """From here kubectl calls may use the cleanup reserve (budget.cleanup_until) instead of the work budget."""
        self.cleaning = True

    def limit(self):
        return self.budget.cleanup_until if self.cleaning else self.budget.work_until

    def call(self, stem, args, stdin=None, timeout=60):
        """Every kubectl call is bounded by the remaining wall budget. A call with no time left is refused before kubectl
        and recorded (empty stdout, the reason in stderr, a .deadline.json), so inherited callers that read those files
        report an error, never success. A timed-out call keeps its partial stdout/stderr (collect.Session.call).
        A stream passes the unchanged fresh UID guard first (stream_guard); the bound is taken after it, at the start."""
        if args and args[0] in qualify.STREAM_VERBS and self.stream_guard(stem, args):
            return ''
        if self.budget is not None:
            try:
                timeout = self.budget.bound(timeout, self.limit(), stem)
            except WallBudgetExceeded as error:
                self.counter += 1
                tag = f'{self.counter:04d}-{stem}'
                (self.out/(tag+'.stdout')).write_bytes(b'')
                (self.out/(tag+'.stderr')).write_text(f'{error}; kubectl not invoked\n')
                self.save(tag+'.deadline.json', dict(args=args[:8], reason=str(error), cleaning=self.cleaning))
                raise
        return ownership.Session.call(self, stem, args, stdin, timeout)

    def stream_guard(self, stem, args):
        """qualify.HetSession.call's stream guard, same rules and records: a stream only to an owned non-edge Pod of this run
        that passes a fresh UID ownership GET. -> True for a skipped `logs`; raises for any other refused verb. Kept here so
        the wall bound is taken after this GET (up to INHERITED_CALL_S), not before it."""
        name = next((a for a in args[1:] if a.startswith(self.run+'-')), None)
        key = name.removeprefix(self.run+'-') if name else None
        reason = None
        if key not in self.expected:
            reason = f'stream target {name!r} is not an owned Pod of this run'
        elif self.expected[key][1]:
            reason = 'kubelet stream to an edge-node Pod is outside the control boundary (unpatched EdgeCore)'
        else:
            try:
                self.owned_state(key, 'stream-owner-'+stem)
            except Exception as error:
                reason = f'fresh ownership check failed: {error!r}'
        if not reason:
            return False
        self.counter += 1
        skipped = args[0] == 'logs'
        self.save(f'{self.counter:04d}-{stem}.{"skipped" if skipped else "refused"}.json',
                  dict(at=datetime.now(timezone.utc).isoformat(), verb=args[0], args=args[:8], reason=reason+'; kubectl not invoked'))
        if skipped:
            return True
        raise RuntimeError(f'refused: {args[0]} ({reason})')

    def require(self, seconds, what):
        """Inherited pod_states/owned_state pass a fixed INHERITED_CALL_S straight to collect.Session.call (no clamp):
        they start only when that whole timeout fits before the limit."""
        if self.budget is not None and self.limit() - self.budget.clock() < seconds:
            raise WallBudgetExceeded(f'{what}: needs {seconds} s')

    def pod_states(self):
        self.require(INHERITED_CALL_S, 'pod-states')
        return super().pod_states()

    def owned_state(self, key, stem):
        self.require(INHERITED_CALL_S, stem)
        return super().owned_state(key, stem)

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


def cleanup(session):
    """The run's UID-owned cleanup: collect.cleanup, unchanged, while its protected-after snapshot (fixed
    PROTECTED_SNAPSHOT_S timeout, not a session call) still fits before the absolute wall end. Every session call of it
    ends by budget.cleanup_until, so that snapshot ends by the wall end. Past cleanup_until no cleanup call could start,
    so collect.cleanup is not entered: cleanup.json records it unverified with every created object still listed."""
    budget = session.budget
    if budget is None or budget.clock() < budget.cleanup_until:
        return ownership.cleanup(session)
    error = f'wall budget: cleanup not started, {budget.end - budget.clock():.1f} s left before the wall end ' \
            f'(protected-after snapshot needs {PROTECTED_SNAPSHOT_S} s); owned objects not deleted or listed'
    session.save('cleanup.json', dict(remaining_owned_pods_and_configmaps=None, cleanup_verified=False, created=session.resources,
                                      unresolved_creation=session.unresolved, preserved_not_ours=None, foreign_with_run_label=None,
                                      errors=[error], skipped=True, rule='delete only by API UID precondition of objects this run '
                                      'created; nothing was deleted, so every created object may remain'))
    return None, [error]


def alignment(gen_sent, gen_recv, net_sent, net_recv, start_delay_s):
    """Collector-clock alignment of the netctl schedule start with the generator t0 (no cross-host clock subtraction).
    The generator received /trace at some collector time g in [gen_sent, gen_recv]; its t0 is g + start_delay_s on its own
    clock. Estimate g by the midpoint; the netctl start delay is computed at net_sent assuming receipt at net_sent. Error
    bound = half the generator round trip + the whole netctl round trip (clock-rate drift over seconds is neglected)."""
    target = (gen_sent + gen_recv) / 2 + start_delay_s
    return dict(netctl_delay_s=target - net_sent, target_collector_s=target,
                bound_ms=((gen_recv - gen_sent) / 2 + (net_recv - net_sent)) * 1000)


class Live:
    """Phases of one release-bound run. http(url, body=None, timeout) is the control-plane HTTP seam; by default every
    call is bounded_http.control (absolute deadline connect .. last byte, reply cap control_max_body_bytes)."""

    def __init__(self, session, plan, plan_dir, http=None, clock=time.monotonic, sleep=time.sleep, budget=None):
        """budget: the run's WallBudget (None only in seam tests: no wall limit)."""
        self.s, self.plan, self.plan_dir, self.budget = session, plan, Path(plan_dir), budget
        self.http, self.clock, self.sleep, self.b = http or self.bounded_control, clock, sleep, design.BUDGETS
        self.urls, self.ips, self.uids, self.skipped, self.faults, self.timing_invalid = {}, {}, {}, [], [], []
        self.limit = budget.work_until if budget else math.inf
        self.cleanup_limit = budget.cleanup_until if budget else math.inf

    def save(self, name, value):
        self.s.save(name, value)

    def bounded_control(self, url, body=None, timeout=None):
        return bounded_http.control(url, body, timeout=timeout, max_body=self.b['control_max_body_bytes'])

    def control(self, url, body=None, timeout=None, until=None):
        """Every control call ends by `until` (if given) and by the wall limit in force (self.limit)."""
        budget = timeout or self.b['control_timeout_s']
        if until is not None:
            budget = min(budget, until - self.clock())
        wall = self.limit - self.clock()
        if wall < budget:
            if wall <= 0:
                raise WallBudgetExceeded(url)
            budget = wall
        if budget <= 0:
            raise bounded_http.DeadlineExceeded(budget)
        return self.http(url, body, timeout=budget)

    @contextmanager
    def within(self, limit):
        saved, self.limit = self.limit, limit
        try:
            yield
        finally:
            self.limit = saved

    def netctl(self, path, body=None):
        url = self.urls['netctl'] + path
        return self.control(url, None if body is None else dict(body, run_id=self.s.run))

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
            while pending and self.clock() - started < self.b['ready_window_s'] and self.clock() < self.limit:
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
        self.urls['netctl'] = f"http://{self.ips['gateway']}:{self.b['netctl_port']}"
        self.verify_workers(failed)

    def verify_workers(self, failed):
        """Identity and load status over HTTP (/health): node and Pod UID must be the creation UID's; a worker whose
        stage did not load, or whose loaded model / derived-artifact hashes are not the plan-bound ones, is removed from
        the candidate set with its evidence (no substitution)."""
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
        self.save('live-candidates.json', dict(candidates=self.candidates, coverage=cover, removed=removed))
        if any(not ws for ws in self.candidates.values()) or not cover['ok']:
            raise RuntimeError(f'live candidates fail the heterogeneous comparison gate: coverage={cover} removed={sorted(removed)}')

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
        health = self.netctl('/health')
        if health.get('run_id') != self.s.run or health.get('installed') or health.get('traces') != {k: traces.trace_sha256(k) for k in traces.TRACES}:
            raise RuntimeError(f'netctl sidecar is not this run\'s clean frozen controller: {health}')
        self.save('netctl-initial.json', dict(health=health, snapshot=self.netctl('/snapshot', {})))

    def job(self, path, body, name, timeout):
        until = min(self.clock() + timeout, self.limit)
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
        """Unchanged rule (baselines live.profile_problems): every calibration receipt is the created worker's and the
        plan-bound model provenance; every (task, candidate) answered every calibration question; every vision/speech
        split path gave the original path's final answer; every other preprocessing candidate matched the reference
        producer byte for byte or gave that final answer downstream."""
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

    def calibrate_network(self):
        """Route probes under the good condition (netctl arms the stable trace's phase 0 and never starts it) -> the one
        frozen good profile every cell's policies start from. The relay-vs-direct control probe runs unshaped first."""
        self.probes('unshaped-direct-control', force_direct=True)
        self.netctl('/arm', dict(label='calibration-good', trace='stable', trace_sha256=traces.trace_sha256('stable'),
                                 cloud=self.cloud_ips, edge=self.edge_ips))
        try:
            links = self.probes('good')
        finally:
            self.netctl('/restore', dict(label='calibration-good'))
            self.save('netctl-calibration-good.json', dict(status=self.netctl('/status?label=calibration-good'),
                                                           teardown=self.netctl('/teardown', {})))
        bad = [k for k, v in links.items() if not v['complete']]
        if bad:
            raise RuntimeError(f'good: incomplete route probes {bad}')
        profile = dict(compute_ms=self.compute, links={k: v['fit'] for k, v in links.items()}, payload_bytes=self.payload,
                       source=f'measured:{self.s.run}:good')
        self.save('profile-good.json', profile)
        return profile

    def probes(self, name, force_direct=False):
        pairs = design.dag_pairs(self.candidates)
        if force_direct:
            pairs = [(a, b) for a, b in pairs if self.zone(a) != self.zone(b)]
        with baseline.phase(self.s, 'probes-'+name):
            result = self.job('/probes', dict(job='probes-'+name, pairs=pairs, sizes=list(self.b['probe_sizes']), reps=self.b['probe_reps'],
                                              timeout_s=self.b['probe_timeout_s'], force_direct=force_direct), 'probes-'+name,
                              self.b['probe_job_timeout_s'])
        self.save(f'probes-{name}.json', result)
        if result['state'] != 'done':
            raise RuntimeError(f"probes-{name} job {result['state']}")
        return result['result']['links']

    def zone(self, key):
        return 'edge' if key == policies.ORIGIN else next(w['zone'] for w in self.workers if w['key'] == key)

    # -- cells
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
        try:  # idle workers are not a stopped generator: no trace may still be admitting or holding requests
            gen = self.control(self.urls['generator']+'/status', until=until)
            running = sorted(k for k, t in gen['traces'].items() if t['state'] == 'running' or t.get('inflight'))
        except Exception as error:
            gen, running = dict(error=repr(error)[:200]), None
        state = dict(queues=queues, gateway_active=gw.get('active'), generator_running=running,
                     **({'generator_error': gen['error']} if running is None else {}))
        idle = gw.get('active') == 0 and running == [] and \
            all(q.get(k) == 0 for q in queues.values() for k in ('waiting', 'active', 'joins_open', 'inflight'))
        progress = {k: (q.get('completed'), q.get('dag_requests')) for k, q in queues.items()}
        return idle, progress, state

    def drain(self, label):
        """Stable idle barrier (baselines rule): no queued/active model call, open join or admitted request on any worker and
        no relayed request, holding again with no new progress after the settle interval; then the gateway counters reset."""
        started, last = self.clock(), None
        until = started + self.b['drain_timeout_s']
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

    def trace_body(self, cell, profile):
        """The generator's whole input for a cell: no trace name, phase or future network value (generator.TRACE_KEYS)."""
        spec = next(dict(s) for s in design.POLICY_SPECS if s['name'] == cell['policy'])
        return dict(label=cell['label'], policy=spec,
                    arrivals=design.arrivals(cell['rate_rps'], design.DESIGN['window_s'], cell['seed'], self.trace_questions),
                    deadline_s=self.b['deadline_s'], cancel_s=self.b['cancel_s'], max_inflight=self.b['max_inflight'], profile=profile,
                    quality=self.quality, observation_contract=dict(stale_after_s=self.b['obs_stale_s']),
                    start_delay_s=self.b['start_delay_s'])

    def cell_need_s(self):
        """Worst case of one cell inside its bounds: pre-cell barrier, start delay + window + cancel + wait slack (the
        netctl window wait ends earlier, but for one last status call), and the
        finalization (restore, stop/settle window, full + partial ledger reads, gateway health, drain + reset, netctl
        status and teardown). A cell starts only when this fits before the deadline, so its finalization never needs the
        cleanup reserve."""
        b = self.b
        barrier = b['drain_timeout_s'] + b['drain_settle_s'] + b['control_timeout_s']
        run = b['start_delay_s'] + design.DESIGN['window_s'] + b['cancel_s'] + WAIT_SLACK_S + 4 * b['control_timeout_s']
        finalize = 4 * b['control_timeout_s'] + b['cancel_s'] + STOP_SLACK_S + 2 * LEDGER_TIMEOUT_S + barrier
        return barrier + run + finalize

    def trace_cell(self, cell, profile, deadline):
        label, window = cell['label'], design.DESIGN['window_s']
        if deadline - self.clock() < self.cell_need_s():
            self.skipped.append(dict(cell, reason='wall budget' if deadline >= self.limit else 'collection budget'))
            return
        before = self.drain('before-'+label)
        self.save(f'drain-before-{label}.json', before)
        if not before['idle']:
            self.skipped.append(dict(cell, reason='not idle before trace (drain timeout)'))
            self.fence(label, ['drain_not_idle'], barrier='before')
            return
        self.netctl('/arm', dict(label=label, trace=cell['trace'], trace_sha256=traces.trace_sha256(cell['trace']),
                                 cloud=self.cloud_ips, edge=self.edge_ips))
        terminal, stage = None, 'generator_start'
        try:
            body = self.trace_body(cell, profile)
            self.save(f'trace-{label}.request.json', {k: v for k, v in body.items() if k != 'profile'})
            gen_sent = self.clock()
            self.control(self.urls['generator']+'/trace', body)
            gen_recv = net_sent = self.clock()
            stage = 'netctl_start'
            plan = alignment(gen_sent, gen_recv, net_sent, net_sent, self.b['start_delay_s'])
            started = self.netctl('/start', dict(label=label, delay_s=plan['netctl_delay_s']))
            net_recv = self.clock()
            align = dict(alignment(gen_sent, gen_recv, net_sent, net_recv, self.b['start_delay_s']), gen_sent=gen_sent, gen_recv=gen_recv,
                         net_sent=net_sent, net_recv=net_recv, netctl_reply=started, tolerance_ms=self.b['align_tolerance_ms'])
            align['within_tolerance'] = align['bound_ms'] <= self.b['align_tolerance_ms']
            # Start alignment is necessary, not sufficient: analyze.timing_validity adds the actual application lag offline.
            align['comparable_timing'] = None if align['within_tolerance'] else False
            if not align['within_tolerance']:
                self.timing_invalid.append(label)
            self.save(f'align-{label}.json', align)
            stage = 'wait'
            terminal = self.wait_trace(label, window)
            if terminal:  # normal completion: the restore must not cut the frozen window short
                stage = 'window'
                self.wait_window(label, align, window)
        except Exception as error:  # an accepted or uncertain start: the generator may be admitting requests
            self.save(f'trace-{label}.error.json', dict(stage=stage, error=repr(error)[:1000], at_s=self.clock()))
        finally:
            self.finish_cell(label, terminal)

    def finish_cell(self, label, terminal):
        """Every step is attempted and recorded on its own (a failed step never blocks the next one nor overwrites the
        original failure): restore -> /stop + settle unless the generator was seen terminal -> salvage -> gateway ->
        drain barrier -> netctl status -> teardown, the last ONLY after a resolved generation, a salvaged ledger and an
        idle barrier. Otherwise the fault fences every later cell and the owned qdisc stays for the end-of-run teardown,
        so no backlog is flushed between cells and called idle."""
        self.attempt(f'netctl-restore-{label}', lambda: self.netctl('/restore', dict(label=label)))
        generation = dict(outcome='terminal', state=terminal, stop_sent=False) if terminal else self.stop_generation(label)
        self.save(f'generation-{label}.json', generation)
        salvage = self.salvage(label, generation)
        self.attempt(f'gateway-{label}', lambda: self.control(self.urls['gateway']+'/health'))
        drain = self.attempt(f'drain-after-{label}', lambda: self.drain('after-'+label))
        self.attempt(f'netctl-status-{label}', lambda: self.netctl('/status?label='+label))
        kinds = (['generation_unresolved'] if generation['outcome'] == 'unresolved' else []) + \
            (['salvage_failed'] if salvage == 'failed' else []) + ([] if (drain or {}).get('idle') else ['drain_not_idle'])
        if kinds:
            self.fence(label, kinds, barrier='after', salvage=salvage, generation=generation['outcome'])
            self.save(f'netctl-teardown-{label}.skipped.json', dict(reason='fenced: no normal teardown/reset without a resolved '
                                                                    'generation, salvaged ledger and idle barrier', kinds=kinds))
            return
        self.attempt(f'netctl-teardown-{label}', lambda: self.netctl('/teardown', {}))

    def fence(self, label, kinds, **detail):
        """Record a cell-level fault: no later cell runs (run_cells), the evidence of this cell stays as it is."""
        record = dict(label=label, kinds=kinds, at_s=self.clock(), **detail)
        self.faults += [dict(label=label, kind=k, at_s=record['at_s']) for k in kinds]
        self.save(f'fault-{label}-cell.json', record)

    def stop_generation(self, label):
        """Explicit /stop, then a bounded wait for the generator's terminal state (cancel_s + STOP_SLACK_S: every admitted
        request ends by its absolute cancel). /stop is re-sent while the label runs, since a late-processed /trace clears
        the stop flag. -> outcome terminal | not_admitted (label absent at every poll of the whole settle window) |
        unresolved (still running, or the status unknown at the end)."""
        until = min(self.clock() + self.b['cancel_s'] + STOP_SLACK_S, self.limit)
        record, seen = dict(outcome='unresolved', stop_sent=True, stops=[], polls=[]), set()
        while True:
            try:
                record['stops'].append(self.control(self.urls['generator']+'/stop', {}, until=until))
            except Exception as error:
                record['stops'].append(dict(error=repr(error)[:300]))
            try:
                state = self.control(self.urls['generator']+'/status', until=until)['traces'].get(label)
                record['polls'].append(dict(at_s=self.clock(), state=state))
                seen.add('absent' if state is None else 'present')
                if state is not None and state['state'] != 'running' and not state.get('inflight'):
                    return dict(record, outcome='terminal', state=state)
            except Exception as error:
                record['polls'].append(dict(at_s=self.clock(), error=repr(error)[:300]))
                seen.add('unknown')
            if self.clock() + self.b['poll_s'] >= until:
                break
            self.sleep(self.b['poll_s'])
        return dict(record, outcome='not_admitted') if seen == {'absent'} else record

    def salvage(self, label, generation):
        """-> complete | partial | not_admitted | failed. A terminal trace's ledger; else (or if that read fails) the
        generator's marked partial ledger, which keeps a row per scheduled arrival (status None while pending). The
        salvaged ledger is saved as ledger-<label>.json; every failed read is kept in ledger-<label>.error.json."""
        if generation['outcome'] == 'not_admitted':
            return 'not_admitted'
        url = self.urls['generator']+'/ledger?label='+label
        reads = ([('full', url)] if generation['outcome'] == 'terminal' else []) + [('partial', url+'&partial=1')]
        errors, value = [], None
        for kind, target in reads:
            try:
                value = self.control(target, timeout=LEDGER_TIMEOUT_S)
                break
            except Exception as error:
                errors.append(dict(read=kind, error=repr(error)[:1000]))
        if errors:
            self.save(f'ledger-{label}.error.json', dict(attempts=errors))
        if value is None:
            return 'failed'
        self.save(f'ledger-{label}.json', value)
        return 'partial' if value.get('partial') else 'complete'

    def attempt(self, stem, fn):
        try:
            value = fn()
        except Exception as error:
            self.save(stem+'.error.json', dict(error=repr(error)[:1000]))
            return None
        self.save(stem+'.json', value)
        return value

    def wait_trace(self, label, window):
        """-> the generator's terminal trace state, or None if it still runs at the wait bound (a failed /status call
        raises; either way the caller stops and settles the generator before salvage)."""
        until = min(self.clock() + self.b['start_delay_s'] + window + self.b['cancel_s'] + WAIT_SLACK_S, self.limit)
        while self.clock() < until:
            try:
                state = self.control(self.urls['generator']+'/status', until=until)['traces'][label]
            except TimeoutError:
                if self.clock() < until:
                    raise
                break
            if state['state'] != 'running':
                return state
            self.sleep(self.b['poll_s'])
        return None

    def wait_window(self, label, align, window):
        """After a normal completion: poll the netctl until its window is over (completed, or failed: it restored good
        itself), up to its latest possible end (netctl t0 <= net_recv + delay_s on the collector clock) + WINDOW_SLACK_S;
        a generator that finished early never ends the window first. Bounded: polls stop at that limit, each call by the
        control timeout and the wall limit. Any outcome other than completed marks the cell's timing invalid."""
        until = min(align['net_recv'] + align['netctl_delay_s'] + window + WINDOW_SLACK_S, self.limit)
        record = dict(outcome='incomplete', until_s=until, polls=[])
        while True:
            try:
                state = self.netctl('/status?label='+label).get('state')
                record['polls'].append(dict(at_s=self.clock(), state=state))
                if state in ('completed', 'failed'):
                    record['outcome'] = state
                    break
                record['outcome'] = 'incomplete'
            except Exception as error:
                record['polls'].append(dict(at_s=self.clock(), error=repr(error)[:300]))
                record['outcome'] = 'unknown'
            if self.clock() + self.b['poll_s'] >= until:
                break
            self.sleep(self.b['poll_s'])
        self.save(f'window-{label}.json', record)
        if record['outcome'] != 'completed' and label not in self.timing_invalid:
            self.timing_invalid.append(label)
        return record

    def fleet_check(self):
        """-> problems: a worker Pod that is not Running/Ready, restarted, or whose /health identity changed (OOM, device
        loss). Fault cells are recorded and no further cell is run."""
        problems, states = [], self.s.pod_states()
        for w in self.workers:
            obj = states.get(w['key'])
            status = (obj or {}).get('status', {})
            restarts = sum(c.get('restartCount', 0) for c in status.get('containerStatuses', []))
            terminated = [c['state']['terminated'].get('reason') for c in status.get('containerStatuses', []) if 'terminated' in c.get('state', {})]
            if obj is None or obj['metadata']['uid'] != self.uids[w['key']] or status.get('phase') != 'Running' or restarts or terminated:
                problems.append(dict(worker=w['key'], phase=status.get('phase'), restarts=restarts, terminated=terminated))
                continue
            try:
                h = self.control(self.urls[w['key']]+'/health', timeout=10)
                if h.get('pod_uid') != self.uids[w['key']]:
                    problems.append(dict(worker=w['key'], health='identity changed'))
            except Exception as error:
                problems.append(dict(worker=w['key'], health=repr(error)[:200]))
        return problems

    def run_cells(self, cells, profile, deadline):
        for i, cell in enumerate(cells):
            if self.faults:
                self.skipped += [dict(c, reason='stopped after fault-affected cell '+self.faults[0]['label']) for c in cells[i:]]
                return
            with baseline.phase(self.s, 'trace-'+cell['label']):
                self.trace_cell(cell, profile, deadline)
            problems = self.fleet_check()
            if problems:
                fault = dict(label=cell['label'], kind='fleet', problems=problems, at_s=self.clock())
                self.faults.append(fault)
                self.save(f"fault-{cell['label']}.json", fault)

    def close_network(self, aborted=False):
        """End-of-run teardown of the owned qdisc; may use the cleanup reserve. After a fence (or an aborted run) it is
        EMERGENCY cleanup of the owned network control only: never an idle barrier, never evidence that queues drained."""
        fenced = sorted({f['label'] for f in self.faults if f['kind'] != 'fleet'})
        record = dict(emergency=bool(fenced) or aborted, idle_barrier=False, fenced_by=fenced, aborted=aborted)
        with self.within(self.cleanup_limit):
            for key, call in (('teardown', lambda: self.netctl('/teardown', {})), ('health', lambda: self.netctl('/health'))):
                try:
                    record[key] = call()
                except Exception as error:
                    record[key] = dict(error=repr(error)[:1000])
        self.save('netctl-emergency-teardown.json' if record['emergency'] else 'netctl-final-teardown.json', record)

    def run(self):
        started = self.clock()
        self.deploy()
        deadline = min(self.limit, self.clock() + self.b['collection_s'])  # the wall budget includes the preparation above
        self.configure()
        self.quality = self.plan['quality']
        self.trace_questions = [p['question_id'] for p in self.plan['pairs'] if p['split'] == 'evaluation']
        cells = self.plan['schedule']['cells']
        if design.check_schedule(cells):
            raise RuntimeError('plan schedule fails the frozen balance check')
        self.save('cells.json', cells)
        aborted = True
        try:
            self.profile()
            profile = self.calibrate_network()
            self.run_cells(cells, profile, deadline)
            aborted = False
        finally:
            try:
                self.close_network(aborted)
            finally:
                self.save('collection.json', dict(elapsed_s=self.clock()-started, skipped=self.skipped, faults=self.faults,
                                                  timing_invalid=self.timing_invalid, deadline_reached=self.clock() >= deadline,
                                                  wall_budget=self.budget.record() if self.budget else None,
                                                  candidates=getattr(self, 'candidates', None)))


def utc():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
