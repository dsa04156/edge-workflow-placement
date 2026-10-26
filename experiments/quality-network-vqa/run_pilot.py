"""Quality/network VQA pilot collector: image || speech question -> ResNet18 / wav2vec2-base -> MUTAN.

Scope: first real pilot that builds the measurement base (official VQA quality next to 2.0 s deadline
goodput) and diagnoses the shared-WAN effect on four fixed plans. Not an online/load-aware optimizer,
not a goodput-optimal policy, not a reproduction of any near prior system; one deployment, one block.

--dry-run prints the frozen design, phase/unique-plan bounds and a planning runtime estimate with no
kubectl call. --execute (live, supervised dispatch only) runs in order: protected snapshot -> create-only
deployment of run_load.Session's Pods (sources e0/e1, v-edge e0, s-edge e1, v-cloud/s-cloud/fusion c,
gateway, cache) -> network preflight (Session) -> raw CPU-reference qualification of all 60 inputs on the
four branch candidates (Session.qualify) -> fusion calibration -> calibration official-quality gate
(Qmin) -> guard -> per network condition: transfer profile + calibration traces of all four plans per
rate -> freeze profiles/plans/oracle -> seeded evaluation of unique plans -> restore -> UID-precondition
cleanup (multi-edge-sensors collect.cleanup). Evaluation labels are read only after the plan freeze.

Edge control boundary (e0/e1 run unpatched KubeEdge v1.23.0, docs/experiments/edgecore-crash-fix.md):
no kubelet stream request (exec/attach/logs/cp/port-forward/top/debug) ever targets an edge-node Pod;
VqaSession.call refuses them before kubectl (edge logs are skipped and recorded). Edge Pods carry no
netctl sidecar. The guard blackholes every edge Pod IP inside the server-zone operator Pods only (TCP
needs both directions) and is proven from the server side (exec on c) and, over HTTP only, by the edge
sources' /transfer_profile (direct to fusion must fail, gateway relay must succeed). Network preflight
capacity/latency use the same source HTTP endpoint. The collector issues no node/EdgeCore restart or host
operation; node and Pod status before/after are observed via the API without causal claims.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import random
import re
import sys
import time
import urllib.error

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LIVE = ROOT/'experiments/real-topology-live'
MES = ROOT/'experiments/multi-edge-sensors'
sys.path.insert(0, str(HERE))
import metrics  # noqa: E402
import vqa_eval  # noqa: E402
import vqa_plan  # noqa: E402
sys.path.insert(1, str(LIVE))
import run_load  # noqa: E402
import run_baseline_comparison as baseline  # noqa: E402
from run_smoke import ASSETS as LIVE_ASSETS, NODES, NS  # noqa: E402
sys.path.insert(2, str(MES))
import collect as ownership  # noqa: E402

EXPERIMENT = 'quality-network-vqa'
SEED = 20261023
SLO_S, CANCEL_S, QMIN = 2.0, 10, 0.40
RATES = (0.5, 1.0)
NETWORKS = (100, 2)
DELAY_MS = 20
MAX_INFLIGHT = 32
REPRESENTATION = 'raw'
EDGE_KEYS = ('source-e0', 'source-e1', 'v-edge', 's-edge')
ZONES = {'source-e0': 'edge', 'source-e1': 'edge', 'v-edge': 'edge', 's-edge': 'edge', 'v-cloud': 'cloud', 's-cloud': 'cloud', 'fusion': 'cloud'}
# Intended node of every owned Pod; checked with its creation UID before any write/stream into it.
EXPECTED_NODE = {**{k: NODES['e0'] for k in ('source-e0', 'v-edge')}, **{k: NODES['e1'] for k in ('source-e1', 's-edge')},
                 **{k: NODES['c'] for k in ('v-cloud', 's-cloud', 'fusion', 'gateway', 'cache')}}
SERVER_GUARDED = ('v-cloud', 's-cloud', 'fusion')
STREAM_VERBS = ('exec', 'attach', 'logs', 'cp', 'port-forward', 'top', 'debug')
ASSETS = ROOT/'artifacts/topology-profile-assets/20261019T091725Z/selected-data'
GT_PAIRS = ASSETS/'pairs.json'
LOAD_PAIRS = LIVE_ASSETS/'load-pairs.json'
REFERENCE = LIVE_ASSETS/'reference-v1'
OUTPUT_ROOT = ROOT/'artifacts/quality-network-vqa'
SPEC = ROOT/'.scratch/quality-network-pilot/spec.md'
# (kind, origin Pod, target Pod). Edge-origin hops are timed by the source Pod's /transfer_profile
# (v-edge shares node e0 with source-e0, s-edge shares e1 with source-e1); server-origin hops are
# timed inside the server Pod. Every hop uses the workflow's own route (gateway when cross-zone).
HOPS = (('image', 'source-e0', 'v-edge'), ('image', 'source-e0', 'v-cloud'), ('audio', 'source-e1', 's-edge'),
        ('audio', 'source-e1', 's-cloud'), ('vision', 'source-e0', 'fusion'), ('vision', 'v-cloud', 'fusion'),
        ('speech', 'source-e1', 'fusion'), ('speech', 's-cloud', 'fusion'), ('return', 'fusion', 'source-e0'),
        ('control', 'source-e0', 'fusion'))
# Inserted into the owned ConfigMap copy of http_runtime.py only (see vqa_http_adapter.py).
ADAPTER_HOOK = ('# quality-network-vqa pilot adapter (owned ConfigMap copy only)\n'
                'from vqa_http_adapter import wrap as _vqa_wrap\nrequest = _vqa_wrap(request)')
REUSED = [LIVE/n for n in ('run_load.py', 'run_smoke.py', 'run_placement.py', 'run_baseline_comparison.py', 'placement_policies.py',
                           'edge_client.py', 'load_trace.py', 'http_runtime.py', 'model_adapter.py', 'net_control.py',
                           'wan_gateway.py', 'bootstrap.py')] + [MES/'collect.py', ROOT/'experiments/real-topology-profile/prepare.py']
# Planning estimate constants (seconds); stated, not measured.
ESTIMATE = dict(deploy=(300, 700), network_preflight=(120, 300), qualify_call=(0.5, 3.5), fusion_call=(0.1, 0.5),
                transfer_exec_overhead=(5, 20), phase_overhead=(15, 60), cleanup=(60, 180))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def condition_name(mbps, rate):
    return f'net{mbps}-r{rate:g}'


def conditions():
    return [dict(condition=condition_name(m, r), mbps=m, rate_rps=r, oneway_delay_ms=DELAY_MS) for m in NETWORKS for r in RATES]


def load_inputs():
    """GT pairs (collector only) and the frozen Pod corpus pairs must describe the same 60 questions."""
    gt = json.loads(GT_PAIRS.read_text())['pairs']
    corpus = json.loads(LOAD_PAIRS.read_text())['pairs']
    if [(p['question_id'], p['split']) for p in gt] != [(p['question_id'], p['split']) for p in corpus]:
        raise ValueError('GT pairs and Pod corpus pairs disagree on question/split order')
    if any(p['human_answers'] != q['human_answers'] for p, q in zip(gt, corpus)):
        raise ValueError('GT human answers differ between pairs.json and load-pairs.json')
    for p in gt:
        vqa_eval.human_answers(p)
    if any('raw_image_path' not in p or 'raw_audio_path' not in p for p in corpus):
        raise ValueError('Raw fixture paths missing in the Pod corpus')
    return gt, corpus


def reference_quality(gt):
    rows = [json.loads(line) for line in (REFERENCE/'results.jsonl').read_text().splitlines()]
    return vqa_eval.score(gt, rows)['splits']


def build_design(gt):
    split = lambda s: [p['question_id'] for p in gt if p['split'] == s]
    cal, ev = split('calibration'), split('evaluation')
    order = conditions()
    random.Random(SEED).shuffle(order)
    plans = len(vqa_plan.PLACEMENTS)
    cal_phases = plans*len(order)
    # Filtered selectors + oracle and their +no-quality-filter aliases: at most 2*(4+1) policies, deduplicated to <= 4 plans.
    eval_bounds = (len(order), min(plans, 2*(len(vqa_plan.SELECTORS)+1))*len(order))
    ref = reference_quality(gt)
    windows = lambda count: sum(count/c['rate_rps'] for c in order)
    payload = {kind: [] for kind in ('image', 'audio')}
    corpus = {p['question_id']: p for p in json.loads(LOAD_PAIRS.read_text())['pairs']}
    for q in cal:
        payload['image'].append(corpus[q]['raw_image_bytes'])
        payload['audio'].append(corpus[q].get('raw_audio_bytes', 0))
    transfer_s = [sum(len(cal)*(sum(payload[k])/len(payload[k]))*8/1e6/m for k in payload)*2 + len(HOPS)*len(cal)*4*DELAY_MS/1000
                  for m in NETWORKS]
    est = lambda i: (ESTIMATE['deploy'][i] + ESTIMATE['network_preflight'][i] + 4*len(gt)*ESTIMATE['qualify_call'][i]
                     + len(cal)*ESTIMATE['fusion_call'][i] + sum(transfer_s) + len(NETWORKS)*len(HOPS)*ESTIMATE['transfer_exec_overhead'][i]
                     + plans*windows(len(cal)) + cal_phases*(CANCEL_S + ESTIMATE['phase_overhead'][i])
                     + (windows(len(ev)) if i == 0 else plans*windows(len(ev)))
                     + eval_bounds[i]*(CANCEL_S + ESTIMATE['phase_overhead'][i]) + ESTIMATE['cleanup'][i])
    return dict(experiment=EXPERIMENT, seed=SEED, namespace=NS,
        scope='first pilot: official VQA quality + 2.0 s deadline goodput measurement base and shared-WAN network-effect '
              'diagnosis on four fixed plans; one deployment, one block; no statistical superiority, long-run stability, '
              'online/load-aware optimization, goodput-optimal policy or near-system reproduction claim',
        service='VQA1 validation image + JellyBean spoken question replay from edge Pods; not a video stream or live sensor',
        inputs=dict(gt_pairs=str(GT_PAIRS.relative_to(ROOT)), gt_pairs_sha256=sha(GT_PAIRS), pod_corpus_pairs=str(LOAD_PAIRS.relative_to(ROOT)),
                    pod_corpus_sha256=sha(LOAD_PAIRS), selection='seed20261019, image-disjoint calibration20/evaluation40; '
                    'already evaluated in past pilots, not an unseen holdout; no re-selection', calibration_ids=cal, evaluation_ids=ev,
                    label_rule='GT labels are read by the collector only for scoring; evaluation labels only after plans-frozen.json; '
                               'the frozen Pod corpus file carries human_answers but no Pod decision path reads them'),
        models=dict(adapter=str((LIVE/'model_adapter.py').relative_to(ROOT)), adapter_sha256=sha(LIVE/'model_adapter.py'),
                    reference_metadata_sha256=sha(REFERENCE/'metadata.json'), device='CPU FP32, torch threads 1',
                    candidates='ARM Pi e0/e1 + x86 c CPU only; accelerators unverified, not candidates'),
        quality=dict(evaluator=vqa_eval.provenance(), qmin=QMIN, qmin_scope='exploratory threshold, not a product requirement or paper acceptance',
                     gate='calibration official accuracy of the deployed fusion Pod outputs (stage outputs CPU-reference-identical) >= Qmin '
                          'before any load phase; failure stops load phases and is reported as quality infeasible',
                     phase_gate_statistic=metrics.GATE_STATISTIC, cpu_reference='separate integrity check, never service quality',
                     reference_quality=ref,
                     reference_note='frozen reference-v1 answers: calibration %.3f, evaluation %.3f vs Qmin %.2f' % (
                         ref['calibration']['answered_mean'], ref['evaluation']['answered_mean'], QMIN)),
        slo=dict(slo_s=SLO_S, origin='scheduled release time', end='final result return + fusion ack (load_trace finished_s)',
                 boundary='on time iff latency <= SLO', cancel_timeout_s=CANCEL_S,
                 cancel_semantics='load_trace deadline_s = 10 s system cancellation; the SLO is applied offline; late outputs keep quality'),
        load=dict(open_loop=True, rates_rps=list(RATES), calibration_count=len(cal), evaluation_count=len(ev),
                  duration='count/rate', offsets='i/rate', max_inflight=MAX_INFLIGHT, sources=dict(image=NODES['e0'], audio=NODES['e1']),
                  representation=REPRESENTATION, decode='raw RGB/PCM decoded offline before deployment, outside the clock'),
        network=dict(gateway='owned gateway Pod on c, shared egress cap per zone/direction', mbps=list(NETWORKS), oneway_delay_ms=DELAY_MS,
                     host_shaping=False, relay='in-experiment Pod TCP gateway only; no cloud relay/tunnel',
                     reused='run_load.Session network_preflight/guard/shape/drain'),
        placements={k: dict(placement=p, pods=[vqa_plan.POD[(t, p[t])] for t in vqa_plan.TASKS]) for k, p in vqa_plan.PLACEMENTS.items()},
        dag='vision || speech -> fusion(c fixed): two free Task locations, constrained DAG, four plans',
        selectors=dict(**{s: s for s in vqa_plan.SELECTORS}, oracle='empirical calibration oracle per (network, rate)'),
        quality_ablation='each selector and the oracle also run their no-quality-filter choice as a separate evaluation alias '
                         '(<policy>+no-quality-filter), kept even when the filtered policy is quality-infeasible; one fixed model -> '
                         'expected equivalent, reported as observed',
        cost_table='calibration20 only, fresh per network condition, frozen before evaluation (p75 compute, p75 hop POST)',
        observation_sharing='policies with the same plan in the same condition share one evaluation phase; never across conditions',
        condition_order=order,
        phases=dict(deploy=1, network_preflight=1, qualification_requests=4*len(gt), fusion_calibration_requests=len(cal),
                    transfer_profile_posts=len(NETWORKS)*len(HOPS)*len(cal), calibration_trace_phases=cal_phases,
                    calibration_requests=cal_phases*len(cal), evaluation_unique_plan_phases=dict(min=eval_bounds[0], max=eval_bounds[1]),
                    evaluation_requests=dict(min=eval_bounds[0]*len(ev), max=eval_bounds[1]*len(ev))),
        expected_runtime_s=dict(min=round(est(0)), max=round(est(1)),
                                basis='planning estimate from stated constants and trace windows, not a measurement', constants=ESTIMATE),
        not_implemented=['real kube-scheduler baseline (all plans pre-pinned in one deployment)', 'online/load-aware re-optimization',
                         'repeated blocks / independent environments', 'accelerators', 'compressed representation'])


class VqaSession(run_load.Session):
    """run_load.Session data path with create-only, UID-recorded ownership and an owned output root."""
    delete_record = ownership.Session.delete_record
    owned_present = ownership.Session.owned_present
    delete_owned = ownership.Session.delete_owned

    def __init__(self, output_root, run_id):
        if not re.fullmatch(r'qnv-[a-z0-9][a-z0-9-]{0,36}', run_id):
            raise ValueError('run-id must match qnv-[a-z0-9-] (Pod name prefix)')
        self.run = run_id
        self.out = Path(output_root)/run_id
        self.out.mkdir(parents=True, exist_ok=False)
        (self.out/'collector-source').mkdir()
        for path in [*HERE.glob('*.py'), *REUSED]:
            (self.out/'collector-source'/path.name).write_bytes(path.read_bytes())
        self.counter, self.resources, self.unresolved, self.actual = 0, [], [], {}
        self.gt, self.pairs = load_inputs()

    def meta(self, name):
        return dict(name=name, namespace=NS, labels={'experiment': EXPERIMENT, 'run-id': self.run})

    def edge_target(self, args):
        names = [f'{self.run}-{k}' for k in EDGE_KEYS] + [NODES['e0'], NODES['e1']]
        names += [p['metadata']['name'] for p in getattr(self, 'actual', {}).values()
                  if p.get('spec', {}).get('nodeName') in (NODES['e0'], NODES['e1'])]
        return any(n in a for a in args for n in names)

    def call(self, stem, args, stdin=None, timeout=60):
        """collect.Session.call; a kubelet stream request reaches kubectl only after a fresh API GET shows this run's
        creation UID on the Pod's intended server node. Edge, unknown, missing or mismatched targets fail closed:
        logs are skipped, other verbs refused, both recorded (covers collect_pod_logs and ownership.cleanup)."""
        if args and args[0] in STREAM_VERBS:
            reason = None
            if self.edge_target(args):
                reason = 'kubelet stream to an unpatched-EdgeCore node Pod is outside the control boundary'
            else:
                name = next((a for a in args[1:] if a.startswith(self.run+'-')), None)
                key = name.removeprefix(self.run+'-') if name else None
                if EXPECTED_NODE.get(key) != NODES['c']:
                    reason = f'stream target {name!r} is not an owned server-node Pod of this run'
                else:
                    try:
                        self.owned_state(key, 'stream-owner-'+stem)
                    except Exception as error:
                        reason = f'fresh ownership check failed: {error!r}'
            if reason:
                self.counter += 1
                skipped = args[0] == 'logs'
                self.save(f'{self.counter:04d}-{stem}.{"skipped" if skipped else "refused"}.json', dict(
                    at=datetime.now(timezone.utc).isoformat(), verb=args[0], args=args[:8], reason=reason+'; kubectl not invoked'))
                if skipped:
                    return ''
                raise RuntimeError(f'refused: {args[0]} ({reason})')
        return ownership.Session.call(self, stem, args, stdin, timeout)

    def pod(self, key, zone, task, cache_url=None):
        pod = super().pod(key, zone, task, cache_url)
        if zone in ('e0', 'e1'):
            # No NET_ADMIN sidecar on edge nodes: the guard lives in server Pods only.
            pod['spec']['containers'] = [c for c in pod['spec']['containers'] if c['name'] != 'netctl']
        return pod

    def rpc(self, url, body, stem='rpc', timeout=150, retain=True):
        """Session.rpc semantics; binary payloads are logged as bytes+sha256, not inlined in event files."""
        from http_runtime import request
        control = {k: (dict(bytes=len(v), sha256=hashlib.sha256(v).hexdigest()) if isinstance(v, bytes) else v) for k, v in body.items()}
        for attempt in range(3):
            self.counter += 1
            tag = f'{self.counter:03d}-{stem}-http-{attempt}'
            event = dict(started_at=datetime.now(timezone.utc).isoformat(), method='POST', url=url, timeout_s=timeout, control=control)
            try:
                result = request(url, body, timeout=timeout)
                if retain:
                    self.save(tag+'.response.json', result)
                event.update(status='completed', response_json_sha256=hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest())
                return result
            except urllib.error.HTTPError as error:
                event.update(status='http_error', code=error.code, error=str(error), body=error.read().decode(errors='replace'))
                raise
            except (OSError, TimeoutError) as error:
                event.update(status='transport_error', error=repr(error))
                if attempt == 2:
                    raise
                time.sleep(1)
            finally:
                event['finished_at'] = datetime.now(timezone.utc).isoformat()
                self.save(tag+'.event.json', event)

    def source_transfer(self, source, target_url, data, count, label, timeout=150):
        """edge_client /transfer_profile POSTs to target_url+'/sink': target_url is an endpoint base URL."""
        if target_url.rstrip('/').endswith('/sink'):
            raise ValueError(f'{label}: target_url must be an endpoint base URL; the source appends /sink')
        reply = self.rpc(self.urls[source]+'/transfer_profile', dict(run_id=self.run, request_id=label, target_url=target_url,
                                                                    data=data, count=count), label, timeout=timeout)
        if reply['source_node'] != self.node_of(source) or reply['sha256'] != hashlib.sha256(data).hexdigest() or reply['payload_bytes'] != len(data):
            raise RuntimeError(f'{label}: source transfer identity mismatch')
        return reply

    def source_direct_probe(self, source, label):
        """HTTP-only proof from an edge source: True if a payload reaches fusion /sink directly (no gateway)."""
        try:
            self.source_transfer(source, self.urls['fusion'], b'guard-probe', 1, label, timeout=150)
            return True
        except urllib.error.HTTPError as error:
            if error.code != 500:
                raise
            return False

    def guard(self, enabled):
        action = 'block' if enabled else 'unblock'
        if enabled:
            for source in ('source-e0', 'source-e1'):
                if not self.source_direct_probe(source, f'guard-pre-direct-{source}'):
                    raise RuntimeError('Direct edge->server path fails before the guard; proof would be vacuous')
        for key in SERVER_GUARDED:
            self.save(f'{action}-{key}.json', self.net(key, dict(action=action, destinations=self.edge), f'{action}-{key}'))
        proofs = {}
        for key in SERVER_GUARDED:
            code = ("import urllib.request,json;op=urllib.request.build_opener(urllib.request.ProxyHandler({}));\ntry:\n op.open("
                    + repr(self.urls['source-e0']+'/health') + ",timeout=2);print(json.dumps({'direct_ok':True}))\n"
                    "except Exception as e:print(json.dumps({'direct_ok':False,'error':repr(e)}))")
            direct = json.loads(self.execute(key, code, f'{action}-server-direct-{key}', timeout=10))
            relay = self.pod_rpc(key, self.urls['gateway']+'/to/source-e0/health', {}, f'{action}-server-relay-{key}') if enabled else None
            proofs[key] = dict(server_direct_to_edge=direct, server_relay_to_edge=relay)
            if direct['direct_ok'] == enabled:
                raise RuntimeError(f'{key}: server-side direct path state does not match guard={enabled}')
        for source in ('source-e0', 'source-e1'):
            direct = self.source_direct_probe(source, f'guard-{action}-direct-{source}')
            relay = self.source_transfer(source, self.urls['gateway']+'/to/fusion', b'guard-probe', 1, f'guard-{action}-relay-{source}')
            proofs[source] = dict(edge_direct_to_fusion_ok=direct, edge_relay_samples_ms=relay['samples_ms'])
            if direct == enabled:
                raise RuntimeError(f'{source}: edge direct path state does not match guard={enabled}')
        self.save(f'guard-{action}-proof.json', dict(proofs=proofs, blocked_in=list(SERVER_GUARDED), edge_ips=self.edge,
            scope='server-side /32 blackhole of every edge Pod IP; v-edge/s-edge covered by the same server-side rule, not probed directly'))

    def network_preflight(self):
        """Session.network_preflight thresholds; edge-side probes via source HTTP, whole collector window as denominator."""
        pair = self.pairs[0]
        data = (LIVE_ASSETS/'load-cache/corpus'/Path(pair['raw_image_path']).name).read_bytes()
        if hashlib.sha256(data).hexdigest() != pair['raw_image_sha256']:
            raise RuntimeError('Raw fixture hash changed')
        records = []
        for rate in (100, 10, 2):
            self.shape(rate, 0, 'capacity-'+str(rate))
            url = self.urls['gateway']+'/to/fusion'
            started = time.monotonic()
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs = {k: pool.submit(self.source_transfer, k, url, data, 4, f'capacity-{rate}-{k}') for k in ('source-e0', 'source-e1')}
                results = {k: j.result() for k, j in jobs.items()}
            elapsed = time.monotonic() - started
            throughput = 2*4*len(data)*8/1e6/elapsed
            record = dict(mbps=rate, aggregate_payload_mbps=throughput, collector_elapsed_s=elapsed, payload_bytes=len(data),
                          payload_sha256=pair['raw_image_sha256'], posts_per_source=4,
                          sources={k: dict(samples_ms=r['samples_ms'], source_node=r['source_node']) for k, r in results.items()},
                          boundary='collector monotonic window around both concurrent source HTTP requests (includes the '
                                   'collector->source payload copy and control RPC); per-source samples are diagnostics only')
            records.append(record)
            self.save('capacity-'+str(rate)+'.json', record)
            if throughput > rate*1.12 or (rate in (2, 10) and throughput < rate*.75):
                raise RuntimeError('Aggregate cap validation failed: '+repr(record))
            self.save('capacity-'+str(rate)+'-qdisc.json', self.net('gateway', dict(action='snapshot'), 'capacity-qdisc-'+str(rate)))
        # Reverse direction: unchanged Session probe from the server-side cache Pod.
        self.shape(2, 0, 'reverse-capacity')
        path = pair['raw_image_path']
        extract = "import tarfile;tarfile.open('/work/source.tar').extract("+repr('corpus/'+Path(path).name)+",'/work',filter='data')"
        self.execute('cache', extract, 'reverse-extract')
        code = ("import urllib.request,json,time,hashlib;from concurrent.futures import ThreadPoolExecutor;from pathlib import Path;b=Path("+repr(path)+").read_bytes();"
                "op=urllib.request.build_opener(urllib.request.ProxyHandler({}));start=time.monotonic();\ndef upload(url):\n for _ in range(4):\n"
                "  r=json.loads(op.open(urllib.request.Request(url,json.dumps({'data':__import__('base64').b64encode(b).decode()}).encode(),{'Content-Type':'application/json'}),timeout=60).read());assert r['sha256']==hashlib.sha256(b).hexdigest()\n"
                "with ThreadPoolExecutor(2) as pool:list(pool.map(upload,"+repr([self.urls['gateway']+'/to/'+k+'/sink' for k in ('source-e0', 'source-e1')])+"))\n"
                "print(json.dumps({'payload_bytes':len(b)*8,'wire_base64_bytes':((len(b)+2)//3*4)*8,'elapsed_s':time.monotonic()-start,'hashes_ok':True}))")
        reverse = json.loads(self.execute('cache', code, 'capacity-reverse', timeout=150))
        reverse['wire_base64_mbps'] = reverse['wire_base64_bytes']*8/1e6/reverse['elapsed_s']
        self.save('reverse-capacity.json', reverse)
        if not 1.5 < reverse['wire_base64_mbps'] < 2.24:
            raise RuntimeError('Reverse aggregate cap validation failed')
        latency = []
        for delay in (0, 20):
            self.shape(100, delay, 'latency-'+str(delay))
            reply = self.source_transfer('source-e0', self.urls['gateway']+'/to/fusion', b'probe', 10, f'latency-{delay}')
            latency.append(dict(oneway_delay_ms=delay, application_roundtrip_ms=reply['samples_ms']))
        self.save('latency-probes.json', latency)
        means = [sum(r['application_roundtrip_ms'])/10 for r in latency]
        if means[1]-means[0] < 30:
            raise RuntimeError('Two-direction delay not observed')
        self.shape(None, 0, 'post-preflight')
        self.save('network-preflight.json', dict(status='passed', capacity=records, reverse=reverse, latency=latency,
            semantics='split TCP relay; edge-side probes via source /transfer_profile HTTP; application RTT, not ICMP RTT'))

    def observe_cluster(self, label):
        """API-only node/Pod status observation; no causal claim."""
        def project(snapshot):
            return {n['metadata']['name']: dict(ready=next((c['status'] for c in n['status']['conditions'] if c['type'] == 'Ready'), None),
                    kubelet=n['status']['nodeInfo']['kubeletVersion']) for n in snapshot['items'] if n['metadata']['name'] in NODES.values()}
        nodes = baseline.projected_call(self, 'nodes-'+label, ['get', 'nodes', '-o', 'json'], project)
        pods = {}
        for record in self.resources:
            if record['kind'] == 'Pod':
                try:
                    obj = json.loads(self.call('podstate-'+label+'-'+record['name'].removeprefix(self.run+'-'),
                                               ['get', 'pod', '-n', NS, record['name'], '-o', 'json']))
                    pods[record['name']] = dict(uid=obj['metadata']['uid'], node=obj['spec'].get('nodeName'), phase=obj['status'].get('phase'),
                        restarts={c['name']: c.get('restartCount') for c in obj['status'].get('containerStatuses', [])})
                except Exception as error:
                    pods[record['name']] = dict(error=repr(error))
        self.save(f'cluster-{label}.json', dict(nodes=nodes, pods=pods, note='observation only; collector issues no restart operation'))

    def create(self, stem, items):
        """collect.Session.create semantics with this experiment's label: create-only, API-returned UIDs kept."""
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
            meta = obj['metadata']
            labels = meta.get('labels', {})
            record = dict(kind=obj['kind'], name=meta['name'], uid=meta['uid'], created_at=meta.get('creationTimestamp'),
                          owned_labels=labels.get('experiment') == EXPERIMENT and labels.get('run-id') == self.run)
            records.append(record)
            self.resources.append(record)
        confirmed = {(r['kind'], r['name']) for r in records}
        unresolved = [dict(kind=i['kind'], name=i['metadata']['name'], stem=stem) for i in items
                      if (i['kind'], i['metadata']['name']) not in confirmed]
        if error is not None or not complete:
            self.unresolved += unresolved
        self.save(stem+'.created.json', dict(confirmed=records, unresolved=unresolved if (error is not None or not complete) else [],
                                             stdout_complete=complete))
        if error is not None:
            raise RuntimeError(f'{stem}: create failed after {len(records)}/{len(items)} confirmed objects') from error
        if not complete or len(records) != len(items) or not all(r['owned_labels'] for r in records):
            raise RuntimeError(f'{stem}: create output confirmed {len(records)}/{len(items)} owned objects')

    def apply(self, stem, items):
        self.create(stem, items)

    def owned_state(self, key, stem):
        """API GET of Pod <run>-<key>; refuse unless it carries this run's creation UID and runs on its intended node."""
        name = self.run+'-'+key
        obj = json.loads(self.call(stem, ['get', 'pod', '-n', NS, name, '-o', 'json']))
        record = self.record_for(name)
        if record is None or obj['metadata']['uid'] != record['uid']:
            raise RuntimeError(f'{key}: running Pod UID is not the UID this run created; no write issued')
        node = obj.get('spec', {}).get('nodeName')
        if node != EXPECTED_NODE[key]:
            raise RuntimeError(f'{key} runs on {node}, expected {EXPECTED_NODE[key]}; no write issued')
        return obj

    def deploy(self):
        """run_load.Session.deploy with the same objects and order, except that each Pod's creation UID and node are
        checked before its first write, the cache upload goes through the guarded/logged call seam and the cache is
        part of the recorded identity."""
        self.apply('cache', [self.pod('cache', 'c', 'cache')])
        self.call('cache-ready', ['wait', 'pod', '-n', NS, self.run+'-cache', '--for=condition=Ready', '--timeout=90s'], timeout=100)
        self.actual['cache'] = self.owned_state('cache', 'cache-state')
        cache_url = 'http://'+self.actual['cache']['status']['podIP']+':8080'
        with (LIVE_ASSETS/'load-cache-upload.tar').open('rb') as source:
            self.execute('cache', "import tarfile,sys;tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall('/work',filter='data')",
                         'cache-upload', timeout=300, stdin=source)
        cm = self.config_map(cache_url)
        self.save('configmap-injection.json', {k: hashlib.sha256(v.encode()).hexdigest() for k, v in cm['data'].items()}
                  | dict(legacy_http_runtime_sha256=sha(LIVE/'http_runtime.py'), hook=ADAPTER_HOOK))
        self.zones = dict(ZONES)
        items = [cm, self.pod('gateway', 'c', 'gateway', cache_url)]
        for key, zone, task in [('source-e0', 'e0', 'source'), ('source-e1', 'e1', 'source'), ('v-edge', 'e0', 'vision'),
                                ('s-edge', 'e1', 'speech'), ('v-cloud', 'c', 'vision'), ('s-cloud', 'c', 'speech'), ('fusion', 'c', 'fusion')]:
            items.append(self.pod(key, zone, task, cache_url))
        self.apply('workers', items)
        self.call('workers-ready', ['wait', 'pods', '-n', NS, '-l', 'run-id='+self.run, '--for=condition=Ready', '--timeout=600s'], timeout=610)
        for key in [*self.zones, 'gateway']:
            self.actual[key] = self.owned_state(key, 'state-'+key)
        self.save('actual-pods.json', self.actual)
        self.urls = {k: 'http://'+v['status']['podIP']+':8080' for k, v in self.actual.items()}
        peers = {k: dict(url=self.urls[k], zone=z) for k, z in self.zones.items()}
        self.execute('gateway', "from pathlib import Path;Path('/work/peers.json').write_text("+repr(json.dumps(peers))+")", 'freeze-peers')
        self.cloud = [self.actual[k]['status']['podIP'] for k, z in self.zones.items() if z == 'cloud']
        self.edge = [self.actual[k]['status']['podIP'] for k, z in self.zones.items() if z == 'edge']
        self.save('peers.json', peers)
        self.shape(None, 0, 'initial')  # Probe kernel API and preserve actual qdiscs.

    def config_map(self, cache_url):
        """run_load's ConfigMap files unchanged, except http_runtime.py gets ADAPTER_HOOK before its __main__ guard
        and vqa_http_adapter.py is added (owned ConfigMap only; disk sources untouched)."""
        files = ['http_runtime.py', 'edge_client.py', 'model_adapter.py', 'bootstrap.py', 'load_trace.py', 'net_control.py', 'wan_gateway.py']
        data = {f: (LIVE/f).read_text() for f in files}
        guard = "\n\nif __name__ == '__main__':"
        if data['http_runtime.py'].count(guard) != 1:
            raise RuntimeError('http_runtime.py __main__ guard not found exactly once; adapter not injected')
        data['http_runtime.py'] = data['http_runtime.py'].replace(guard, '\n\n'+ADAPTER_HOOK+guard)
        data['vqa_http_adapter.py'] = (HERE/'vqa_http_adapter.py').read_text()
        data['bundles.json'] = (LIVE_ASSETS/'load-bundles.json').read_text()
        return dict(apiVersion='v1', kind='ConfigMap', metadata=self.meta(self.run), data=data)

    def execute(self, key, code, stem='operation', timeout=60, container='worker', stdin=None):
        # call() re-checks creation UID + intended server node before every exec: a same-name replacement never gets one.
        # '-i' only when stdin is really sent; edge Pods never get stdin from this collector.
        args = ['exec', *(['-i'] if stdin is not None else []), '-n', NS, self.run+'-'+key, '-c', container, '--', 'python3', '-u', '-c', code]
        return self.call(stem, args, stdin, timeout)

    def net(self, key, spec, stem):
        code = "import json,sys;sys.path.insert(0,'/config');from net_control import configure;print(json.dumps(configure("+repr(spec)+"),default=str))"
        return json.loads(self.execute(key, code, stem, container='netctl'))

    def verify_identity(self):
        if set(self.actual) != set(EXPECTED_NODE):
            raise RuntimeError(f'recorded identity covers {sorted(self.actual)}, expected {sorted(EXPECTED_NODE)}')
        for key, pod in self.actual.items():
            record = self.record_for(self.run+'-'+key)
            if record is None or pod['metadata']['uid'] != record['uid']:
                raise RuntimeError(f'{key}: running Pod UID is not the UID this run created')
            if pod['spec']['nodeName'] != EXPECTED_NODE[key]:
                raise RuntimeError(f'{key} runs on {pod["spec"]["nodeName"]}, expected {EXPECTED_NODE[key]}')

    def record_for(self, name):
        return next((r for r in reversed(self.resources) if r['name'] == name and r['kind'] == 'Pod'), None)

    def node_of(self, key):
        return self.actual[key]['spec']['nodeName']

    def route(self, origin, target):
        if self.zones[origin] != self.zones[target]:
            return self.urls['gateway']+'/to/'+target
        return self.urls[target]

    # ---- quality ----
    def qualify_fusion(self):
        """Deployed fusion Pod on reference-identical calibration branch outputs; returns calibration answers."""
        refs = {r['question_id']: r for r in map(json.loads, (REFERENCE/'results.jsonl').read_text().splitlines())}
        models = json.loads((REFERENCE/'metadata.json').read_text())['stages']
        rows = []
        for index, pair in enumerate(p for p in self.pairs if p['split'] == 'calibration'):
            q = pair['question_id']
            reply = self.rpc(self.urls['fusion']+'/infer', dict(run_id=self.run, request_id=f'fusion-cal-{index}', remaining_s=60,
                inputs=dict(vision=base64.b64encode((REFERENCE/f'{q}.npy').read_bytes()).decode(),
                            speech=base64.b64encode(refs[q]['transcript'].encode()).decode())), f'fusion-cal-{index}', timeout=65)
            out = json.loads(base64.b64decode(reply['data']))
            receipt = reply['receipt']
            if receipt['model_sha256'] != models['fusion']['model_sha256'] or receipt['pod_uid'] != self.actual['fusion']['metadata']['uid']:
                raise RuntimeError('Fusion qualification identity mismatch')
            cpu_ok = out['answer_ids'] == refs[q]['answer_ids'] and max(abs(a-b) for a, b in zip(out['scores'], refs[q]['scores'])) <= 1e-4
            rows.append(dict(question_id=q, split='calibration', answers=out['answers'], cpu_reference_match=cpu_ok, receipt=receipt))
            if not cpu_ok:
                self.save('fusion-qualification-failed.json', rows)
                raise RuntimeError('Fusion CPU reference mismatch; no input excluded')
        self.save('fusion-qualification.json', rows)
        return rows

    def quality_gate(self, fusion_rows):
        calibration_gt = [p for p in self.gt if p['split'] == 'calibration']
        scored = vqa_eval.score(calibration_gt, [dict(question_id=r['question_id'], answer=r['answers'][0]) for r in fusion_rows])
        value = scored['splits']['calibration']['all_questions_zero_imputed_mean']
        gate = dict(qmin=QMIN, statistic='calibration official accuracy, deployed fusion outputs, zero-imputed', value=value,
                    passed=value >= QMIN, per_question=scored['per_question'], evaluator=vqa_eval.provenance())
        self.save('quality-gate.json', gate)
        return gate

    # ---- calibration costs ----
    def compute_rows(self, fusion_rows):
        rows = []
        for r in json.loads((self.out/'qualification.json').read_text()):
            if r['split'] != 'calibration':
                continue
            task = 'vision' if r['candidate'].startswith('v-') else 'speech'
            rows.append(dict(task=task, node=self.node_of(r['candidate']), question_id=r['question_id'], split='calibration',
                             compute_ms=r['receipt']['compute_ms']))
        rows += [dict(task='fusion', node=self.node_of('fusion'), question_id=r['question_id'], split='calibration',
                      compute_ms=r['receipt']['compute_ms']) for r in fusion_rows]
        return rows

    def hop_payloads(self, kind, fusion_rows):
        refs = {r['question_id']: r for r in map(json.loads, (REFERENCE/'results.jsonl').read_text().splitlines())}
        outputs = {r['question_id']: r for r in fusion_rows}
        for pair in (p for p in self.pairs if p['split'] == 'calibration'):
            q = pair['question_id']
            if kind in ('image', 'audio'):
                data = (LIVE_ASSETS/'load-cache/corpus'/Path(pair['raw_'+kind+'_path']).name).read_bytes()
                if hashlib.sha256(data).hexdigest() != pair['raw_'+kind+'_sha256']:
                    raise RuntimeError('Raw fixture hash changed')
            elif kind == 'vision':
                data = (REFERENCE/f'{q}.npy').read_bytes()
            elif kind == 'speech':
                data = refs[q]['transcript'].encode()
            elif kind == 'return':
                data = json.dumps(dict(answers=outputs[q]['answers']), separators=(',', ':')).encode()
            else:
                data = b'{}'
            yield q, data

    def transfer_profile(self, mbps, fusion_rows):
        label = f'net{mbps}'
        rows = []
        for kind, origin, target in HOPS:
            base = self.route(origin, target)
            items = list(self.hop_payloads(kind, fusion_rows))
            if origin.startswith('source-'):
                for index, (q, data) in enumerate(items):
                    reply = self.source_transfer(origin, base, data, 1, f'xfer-{label}-{kind}-{target}-{index}', timeout=125)
                    rows.append(dict(kind=kind, origin=self.node_of(origin), destination=self.node_of(target), question_id=q,
                                     split='calibration', ms=reply['samples_ms'][0], bytes=len(data), origin_pod=origin, target_pod=target))
            else:
                encoded = [(q, base64.b64encode(d).decode()) for q, d in items]
                code = ("import sys,json,time,base64;sys.path.insert(0,'/config');from http_runtime import request,digest\nrows=[]\n"
                        "for q,b in "+repr(encoded)+":\n d=base64.b64decode(b);t=time.monotonic();r=request("+repr(base+'/sink')+",{'data':d},timeout=60)\n"
                        " ms=(time.monotonic()-t)*1000\n assert r['sha256']==digest(d) and r['bytes']==len(d)\n rows.append([q,ms,len(d)])\n"
                        "print(json.dumps(rows))")
                for q, ms, size in json.loads(self.execute(origin, code, f'xfer-{label}-{kind}-{origin}', timeout=300)):
                    rows.append(dict(kind=kind, origin=self.node_of(origin), destination=self.node_of(target), question_id=q,
                                     split='calibration', ms=ms, bytes=size, origin_pod=origin, target_pod=target))
        self.save(f'transfer-{label}.json', dict(rows=rows, scope='in-Pod payload POST to /sink on the workflow route; '
                  'application RPC proxy, not pure one-way network time'))
        return rows

    # ---- traces ----
    def trace(self, label, placement, mbps, rate, split):
        pairs = [p for p in self.pairs if p['split'] == split]
        keys = (vqa_plan.POD[('vision', placement['vision'])], vqa_plan.POD[('speech', placement['speech'])])
        config = dict(run_id=self.run, phase=label, question_ids=[p['question_id'] for p in pairs],
            offsets_s=[i/rate for i in range(len(pairs))], endpoints=self.endpoints(keys, True), remote_source=self.urls['source-e1'],
            representation=REPRESENTATION, deadline_s=CANCEL_S, max_inflight=MAX_INFLIGHT, placement=placement,
            plan_key=vqa_plan.key(placement), mbps=mbps, oneway_delay_ms=DELAY_MS, rate_rps=rate, split=split, slo_s=SLO_S,
            cancel_timeout_s=CANCEL_S, deadline_semantics='deadline_s is the 10 s system cancellation; the 2.0 s SLO is applied offline')
        self.save(label+'.config.json', config)
        self.rpc(self.urls['source-e0']+'/start_trace', config, 'start-'+label, timeout=15)
        stop = time.monotonic() + (len(pairs)-1)/rate + CANCEL_S + 270
        events = []
        with (self.out/(label+'.events.jsonl')).open('x') as handle:
            while True:
                status = self.rpc(self.urls['source-e0']+'/trace_status', {'phase': label}, 'poll-'+label, timeout=15, retain=False)
                for row in status['events'][len(events):]:
                    handle.write(json.dumps(row)+'\n')
                handle.flush()
                events = status['events']
                if status['status'] == 'complete':
                    break
                if status['status'] == 'failed':
                    raise RuntimeError('Source trace failed: '+str(status.get('error')))
                if time.monotonic() > stop:
                    raise TimeoutError('Source trace control deadline exceeded')
                time.sleep(2)
        rows = [e for e in events if e['event'] == 'terminal']
        self.save(label+'.rows.json', rows)
        self.verify_rows(rows, keys)
        self.drain(label)
        self.save(label+'.qdisc.json', self.net('gateway', dict(action='snapshot'), 'snapshot-'+label))
        self.save(label+'.relay-counters.json', self.rpc(self.urls['gateway']+'/health', {}, 'relay-counters-'+label))
        return config, events

    def verify_rows(self, rows, keys):
        byid = {p['question_id']: p for p in self.pairs}
        models = json.loads((REFERENCE/'metadata.json').read_text())['stages']
        for row in rows:
            if 'receipts' not in row:
                continue
            receipt = {r['task']: r for r in row['receipts']}
            for task, key in (('vision', keys[0]), ('speech', keys[1]), ('fusion', 'fusion')):
                r, pod = receipt[task], self.actual[key]
                if r['pod_uid'] != pod['metadata']['uid'] or r['node'] != pod['spec']['nodeName'] or r['model_sha256'] != models[task]['model_sha256']:
                    raise RuntimeError('Runtime identity/model hash mismatch')
                if r.get('serving_torch_threads') != 1:
                    raise RuntimeError('Worker thread contract unverified')
            pair = byid[row['question_id']]
            if receipt['vision']['input_sha256']['source'] != pair['raw_image_sha256'] or receipt['speech']['input_sha256']['source'] != pair['raw_audio_sha256']:
                raise RuntimeError('Input transfer hash mismatch')
            if row['source_node'] != NODES['e0'] or row['speech_source_node'] != NODES['e1']:
                raise RuntimeError('Input source moved')


def collect_pod_logs(session):
    edge = [r['name'] for r in session.resources if r['kind'] == 'Pod' and session.edge_target([r['name']])]
    session.save('edge-logs-skipped.json', dict(pods=edge, reason='no kubectl logs to unpatched-EdgeCore node Pods; '
                 'edge evidence = HTTP health, trace_status events and receipts'))
    for record in session.resources:
        if record['kind'] != 'Pod' or record['name'] in edge:
            continue
        for container in ('worker', 'netctl'):
            try:
                session.call('logs-'+record['name'].removeprefix(session.run+'-')+'-'+container,
                             ['logs', '-n', NS, record['name'], '-c', container], timeout=30)
            except Exception:
                pass  # diagnostics only; ownership cleanup still runs


def execute(session):
    gt_cal = {p['question_id']: p for p in session.gt if p['split'] == 'calibration'}
    design = build_design(session.gt)
    session.save('design.json', design)
    session.save('source-hashes.json', {str(p.relative_to(ROOT)): sha(p) for p in [*HERE.glob('*.py'), *REUSED, SPEC]})
    (session.out/'spec-frozen.md').write_bytes(SPEC.read_bytes())
    session.save('protected-before.json', ownership.protected_snapshot(session, 'protected-before'))
    with baseline.phase(session, 'preflight'):
        ownership.refuse_reused_run(session)
    with baseline.phase(session, 'deploy'):
        session.deploy()
        session.verify_identity()
        session.observe_cluster('after-deploy')
    with baseline.phase(session, 'network-preflight'):
        session.network_preflight()
    with baseline.phase(session, 'qualify-branches'):
        session.qualify()
    with baseline.phase(session, 'qualify-fusion'):
        fusion_rows = session.qualify_fusion()
    with baseline.phase(session, 'quality-gate'):
        gate = session.quality_gate(fusion_rows)
    if not gate['passed']:
        session.save('quality-infeasible.json', dict(gate=gate, action='no load phase executed; threshold not lowered'))
        return 'quality_infeasible'
    compute = session.compute_rows(fusion_rows)
    session.guard(True)
    profiles, quality, measured, calibration = {}, {}, {}, {}
    order = conditions()
    random.Random(SEED).shuffle(order)
    rng = random.Random(SEED+1)
    for mbps in dict.fromkeys(c['mbps'] for c in order):
        with baseline.phase(session, f'calibrate-net{mbps}'):
            session.shape(mbps, DELAY_MS, f'cal-net{mbps}')
            transfers = session.transfer_profile(mbps, fusion_rows)
            session.drain(f'xfer-net{mbps}')
            profiles[mbps] = vqa_plan.build_profile(f'net{mbps}', compute, transfers, vqa_plan.deployed_capacity(), str(session.out))
            returned = {k: [] for k in vqa_plan.PLACEMENTS}
            for c in (c for c in order if c['mbps'] == mbps):
                keys = list(vqa_plan.PLACEMENTS)
                rng.shuffle(keys)
                for plan_key in keys:
                    label = f"cal-{c['condition']}-{plan_key}"
                    config, events = session.trace(label, vqa_plan.PLACEMENTS[plan_key], mbps, c['rate_rps'], 'calibration')
                    summary = metrics.summarize_phase(events, config, gt_cal, SLO_S, QMIN)
                    calibration[label] = summary
                    returned[plan_key] += [r['vqa_accuracy'] for r in summary['requests'] if 'vqa_accuracy' in r]
                    measured.setdefault(c['condition'], {})[plan_key] = dict(on_time=summary['counts']['on_time'],
                        arrivals=summary['counts']['arrivals'], p50_ms=summary['returned_latency_ms']['p50'], split='calibration')
            quality[mbps] = {k: (sum(v)/len(v) if v else 0.0) for k, v in returned.items()}
            for c in (c for c in order if c['mbps'] == mbps):
                for k in vqa_plan.PLACEMENTS:
                    measured[c['condition']][k]['quality'] = quality[mbps][k]
    plans = {}
    for c in order:
        plans[c['condition']] = dict(vqa_plan.select(profiles[c['mbps']], quality[c['mbps']], QMIN),
                                     **{'empirical-oracle': vqa_plan.empirical_oracle(measured[c['condition']], QMIN)})
    session.save('calibration-summaries.json', calibration)
    session.save('profiles-frozen.json', profiles)
    session.save('plan-quality-calibration.json', dict(quality={str(k): v for k, v in quality.items()},
        statistic='official accuracy of returned calibration outputs per plan, pooled over both rates of the network condition'))
    session.save('plans-frozen.json', dict(plans=plans, measured=measured, qmin=QMIN))
    jobs = vqa_plan.schedule(plans, SEED)
    if {j['condition'] for j in jobs} != set(plans):
        raise RuntimeError('A network/rate condition has no evaluation phase; refusing a zero-evaluation completion')
    for index, job in enumerate(jobs):
        job['label'] = f"eval-{index}-{job['condition']}-{vqa_plan.key(job['placement'])}"
    session.save('execution-order.json', jobs)
    # Evaluation labels are first read here, after plans-frozen.json exists.
    gt_eval = {p['question_id']: p for p in session.gt if p['split'] == 'evaluation'}
    spec = {c['condition']: c for c in order}
    evaluation = {}
    for job in jobs:
        c = spec[job['condition']]
        with baseline.phase(session, job['label'], policies=job['policies']):
            session.shape(c['mbps'], DELAY_MS, job['label'])
            config, events = session.trace(job['label'], job['placement'], c['mbps'], c['rate_rps'], 'evaluation')
            evaluation[job['label']] = dict(job, summary=metrics.summarize_phase(events, config, gt_eval, SLO_S, QMIN))
    session.save('evaluation-summaries.json', evaluation)
    session.shape(None, 0, 'restored')
    session.guard(False)
    session.save('completed.json', dict(status='all planned phases completed', jobs=len(jobs),
                                        evaluation_requests=sum(s['summary']['counts']['arrivals'] for s in evaluation.values())))
    return 'completed'


def main(argv=None):
    ap = argparse.ArgumentParser(description='Quality/network VQA pilot collector (official VQA quality + 2.0 s goodput).')
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true', help='print the frozen design and estimates; no kubectl call')
    mode.add_argument('--execute', action='store_true', help='live cluster collection (supervised dispatch only)')
    ap.add_argument('--output-root', default=str(OUTPUT_ROOT))
    ap.add_argument('--run-id', default=datetime.now(timezone.utc).strftime('qnv-%Y%m%dt%H%M%S'))
    args = ap.parse_args(argv)
    if args.dry_run:
        gt, _ = load_inputs()
        print(json.dumps(build_design(gt), indent=1))
        return 0
    session = VqaSession(args.output_root, args.run_id)
    status, error = 'error', None
    try:
        status = execute(session)
    except BaseException as exc:
        error = repr(exc)
        raise
    finally:
        try:
            session.observe_cluster('before-cleanup')
        except Exception:
            pass  # observation only
        collect_pod_logs(session)
        remaining, errors = ownership.cleanup(session)
        diff = session.out/'protected-diff.json'
        changes = json.loads(diff.read_text())['changes'] if diff.exists() else None
        # Any protected workload change (or an unverifiable snapshot/cleanup) makes the whole audit partial.
        audit = 'complete' if changes == [] and remaining == 0 else 'partial'
        session.save('run-status.json', dict(status=status, error=error, audit=audit, protected_changes=changes,
                                             cleanup_remaining=remaining, cleanup_errors=errors))
        print('evidence', session.out, flush=True)
    return 0 if status == 'completed' and audit == 'complete' else 1


if __name__ == '__main__':
    sys.exit(main())
