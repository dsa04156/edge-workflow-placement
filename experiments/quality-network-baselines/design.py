"""Frozen comparison design (pure functions, no kubectl): workers/candidates from the observed inventory, network
conditions, seeded open-loop arrival traces, balanced policy order, pilot -> frozen offered loads, numeric budgets and
the coordinator release binding.

Workers reuse quality-network-heterogeneous qualify.workers_for (same images, recipes, device-plugin resources, root
policy) and change only the hosted stages:
  cpu-c   ser0002 CPU      image_preprocess, audio_preprocess, vision, speech, fusion
  npu-c   ser0002 Intel NPU vision (direct NPU; tensor input). NPU ASR is NOT a candidate: the exact-length speech probe
          ended with exit 137 after 27/60 (qnh-20261023t100710) and the identical recipe is not rerun.
  cpu-e0  dev0003 CPU      all five tasks (edge CPU; the generator shares this node = edge sensor origin)
  jgpu-*  Jetson CUDA      vision, speech, fusion (only driver-matched, revalidated recipes; dev0009 cu130 today)
Preprocessing candidates are CPU workers only. Allocation-probe workers (Hailo/Mobilint) run no model and are not
deployed here; the full catalog keeps every node/family row with its blocked reason.
"""
import hashlib
import json
import math
from pathlib import Path
import random
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
QNH = ROOT/'experiments/quality-network-heterogeneous'
sys.path.insert(0, str(HERE))
import policies  # noqa: E402

EXPERIMENT = 'quality-network-baselines'
ORIGIN_NODE = 'etri-dev0003-raspi5'
SERVER_NODE = 'etri-ser0002-cgnmsb'
QMIN, SLO_S, CANCEL_S = 0.40, 2.0, 10.0  # diagnostic contract kept from the 2026-10-23 pilot; not tuned to results
HOSTED = {'cpu-torch': list(policies.TASKS), 'openvino-npu': ['vision'], 'cuda-torch': ['vision', 'speech', 'fusion']}
STAGE_OF_TASK = {t: t for t in policies.TASKS}
# Closest analogue of the 10/23 fixed mixed plan (NPU vision + Jetson GPU speech + server CPU fusion) with preprocessing
# split out: image preprocessing next to the NPU, audio preprocessing at the edge next to the Jetson.
FIXED_TEMPLATE = dict(image_preprocess='cpu-c', vision='npu-c', audio_preprocess='cpu-e0', speech='jgpu', fusion='cpu-c')
NETWORKS = {  # gateway egress constraint (owned netns tc via net_control.py; approved values only), not physical links
    'relay-unshaped': dict(mbps=None, delay_ms=0, role='control: relay overhead without shaping'),
    'good': dict(mbps=100, delay_ms=0, role='comparison'),
    'limited': dict(mbps=10, delay_ms=20, role='comparison'),
}
POLICY_SPECS = (dict(name='fixed'), dict(name='task_fastest'), dict(name='greedy_comm'), dict(name='dag_static'),
                dict(name='dag_reopt'), dict(name='dag_reopt_hysteresis', rel=0.15, abs_ms=50.0, dwell_s=5.0))
PILOT = dict(conditions=('relay-unshaped', 'good', 'limited'), rates_rps=(0.25, 0.5, 1.0, 2.0), window_s=20.0,
             policies=('fixed', 'greedy_comm', 'dag_reopt'), seed=20261026)
COMPARE = dict(conditions=('good', 'limited'), window_s=60.0, blocks=2, seed=20261027)
LOAD_FRACTIONS = dict(low=0.3, near_saturation=0.8, high=1.3)
SATURATION_RULE = ('max pilot rate r (good network, any pilot policy) whose frozen scheduled cohort has >= 0.9 valid in-deadline '
                   'answers and no admission drop; bracketed only if a larger tested rate failed, else censored at the largest tested rate')
# control_timeout_s and every other collector control-HTTP timeout is an ABSOLUTE deadline (bounded_http.control:
# connect .. last byte), and a call inside a bounded phase (job poll, drain, trace poll) gets only that phase's remaining
# budget. control_max_body_bytes caps each control reply: the largest are the trace ledger (local smoke 2026-10-26: max
# 9,112 B/row; a compare trace at <= 1.3 x the 2 rps top pilot rate for 60 s is ~156 rows, ~1.4 MB) and the profile job
# result; 32 MiB leaves > 10x margin and is still finite.
BUDGETS = dict(ready_window_s=2100, cache_ready_s=180, upload_timeout_s=1800, probe_timeout_s=60, profile_timeout_s=1800,
               control_timeout_s=30, poll_s=5, drain_timeout_s=60, drain_settle_s=2, max_inflight=32, dag_max_active=96,
               deadline_s=SLO_S, cancel_s=CANCEL_S, obs_poll_s=1.0, obs_poll_timeout_s=0.5, profile_pairs=8, profile_warmup=1,
               probe_sizes=(4096, 1048576), probe_reps=3, probe_job_timeout_s=1800, pilot_collection_s=6000, compare_collection_s=9000,
               cleanup_reserve_s=900, worker_pod_lifetime_s=12600, cache_pod_lifetime_s=14700, gateway_upstream_cap_s=60,
               control_max_body_bytes=32 * 1024 * 1024)
assert BUDGETS['worker_pod_lifetime_s'] > max(BUDGETS['pilot_collection_s'], BUDGETS['compare_collection_s']) + BUDGETS['ready_window_s'] + BUDGETS['cleanup_reserve_s']
assert BUDGETS['cache_pod_lifetime_s'] > BUDGETS['worker_pod_lifetime_s'] + BUDGETS['upload_timeout_s']
assert BUDGETS['cancel_s'] < BUDGETS['gateway_upstream_cap_s'] and BUDGETS['deadline_s'] < BUDGETS['cancel_s']


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=list).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def dag_workers(qnh_workers):
    """Map the qualification wave workers to DAG workers (same image/recipe/resources; DAG stages only)."""
    out = []
    for w in qnh_workers:
        if w['backend'] not in HOSTED:
            continue  # allocation probes: catalog only
        d = {k: v for k, v in w.items() if k not in ('stage_roles',)}
        d['stages'] = list(HOSTED[w['backend']])
        d['zone'] = 'edge' if w['edge'] else 'cloud'
        if w['backend'] == 'openvino-npu':
            d['excluded'] = dict(speech='NPU exact-length ASR probe ended exit 137 at 27/60 (qnh-20261023t100710); recipe not rerun')
        out.append(d)
    return out


# Frozen artifact each model stage loads: qnh bootstrap fetches every manifest file into WORK/<path> and refuses a sha256
# that differs from the manifest (itself checked against the plan's ASSET_MANIFEST_SHA256); stages.py then loads the fixed
# path MODEL_ROOT/<path minus models/> (MODEL_ROOT = WORK/models) and reports sha256 of the file it read.
MODEL_FILES = {'audio_preprocess': 'models/asr/preprocessor_config.json', 'vision': 'models/resnet18-f37072fd.pth',
               'speech': 'models/asr/model.safetensors', 'fusion': 'models/mutan-resnet18.pth.tar'}


def model_expectations(workers, manifest, export):
    """-> {worker key: {stage: {model_sha256, derived_artifact_sha256, chain}}}, the hashes every /health stage report and
    receipt must carry, derived from the frozen assets manifest (and, for the Intel NPU vision ONNX, the frozen offline
    export record that names the shipped weights it was exported from). This is artifact identity only: numerical
    parity with CPU and final answer quality are separate gates. ValueError when a stage has no frozen identity."""
    files = {e['path']: e.get('sha256') for e in manifest['files']}
    out = {}
    for w in workers:
        out[w['key']] = {}
        for stage in w['stages']:
            if stage == 'image_preprocess':
                e = dict(model_sha256=None, derived_artifact_sha256=None,
                         chain='no model file: torchvision transform in shipped stages.py (bound by shipped_sha256)')
            elif w['backend'] in ('cpu-torch', 'cuda-torch') and stage in MODEL_FILES:
                path = MODEL_FILES[stage]
                if path not in files:
                    raise ValueError(f"{w['key']} {stage}: {path} is not in the assets manifest")
                e = dict(model_sha256=files[path], derived_artifact_sha256=None,
                         chain=f'assets-manifest {path} -> bootstrap fetch sha256 check -> stages.py loads MODEL_ROOT/{path[7:]} and hashes it')
            elif (w['backend'], stage) == ('openvino-npu', 'vision'):
                onnx, weights = manifest['onnx']['vision'], MODEL_FILES['vision']
                record = export['stages']['vision']
                if onnx['path'] not in files or files[onnx['path']] not in (None, onnx['sha256']):  # None: unhashed test plan
                    raise ValueError('vision ONNX is not the shipped manifest file')
                if record['export']['sha256'] != onnx['sha256']:
                    raise ValueError('vision ONNX is not the frozen export record artifact')
                if files.get(weights) is not None and record.get('original_model_sha256') != files[weights]:
                    raise ValueError('frozen export record does not trace the vision ONNX to the shipped resnet18 weights')
                e = dict(model_sha256=files.get(weights), derived_artifact_sha256=onnx['sha256'],
                         chain=f"export record original_model_sha256 = assets-manifest {weights}; ONNX = export sha256 = assets-manifest "
                               f"{onnx['path']} -> bootstrap fetch check -> OpenVinoVision expected_onnx_sha256 check and hash of the read file")
            else:
                raise ValueError(f"{w['key']} {stage}@{w['backend']}: no frozen artifact identity")
            out[w['key']][stage] = e
    return out


def candidates(workers):
    return {t: [w['key'] for w in workers if t in w['stages']] for t in policies.TASKS}


def fixed_plan(workers):
    keys = {w['key'] for w in workers}
    jgpu = sorted(k for k in keys if k.startswith('jgpu-'))
    plan = {t: (jgpu[0] if v == 'jgpu' and jgpu else v) for t, v in FIXED_TEMPLATE.items()}
    missing = [t for t, k in plan.items() if k not in keys]
    return None if missing else plan


def coverage(cands):
    """First supported comparison must offer real CPU + GPU + NPU and a choice (>=2 candidates) for several tasks."""
    backends = {k.split('-')[0] for ws in cands.values() for k in ws}
    multi = [t for t, ws in cands.items() if len(ws) >= 2]
    return dict(cpu='cpu' in backends, gpu='jgpu' in backends, npu='npu' in backends, multi_choice_tasks=multi,
                ok={'cpu', 'jgpu', 'npu'} <= backends and len(multi) >= 2,
                plans=math.prod(len(ws) for ws in cands.values()))


def dag_pairs(cands):
    """Every ordered (src, dst) location pair a DAG edge can use under any candidate plan (route probes)."""
    out = set()
    for s, d, _ in policies.EDGES:
        srcs = [policies.ORIGIN] if s == policies.ORIGIN else cands[s]
        dsts = [policies.ORIGIN] if d == policies.ORIGIN else cands[d]
        out |= {(a, b) for a in srcs for b in dsts}
    return sorted(out)


def arrivals(rate_rps, window_s, seed, question_ids):
    """Seeded Poisson arrivals in [0, window_s) and a seeded question order (cycled); identical for every policy."""
    rng = random.Random(seed)
    order = list(question_ids)
    rng.shuffle(order)
    out, t = [], rng.expovariate(rate_rps)
    while t < window_s:
        out.append(dict(index=len(out), offset_s=round(t, 6), question_id=order[len(out) % len(order)]))
        t += rng.expovariate(rate_rps)
    return out


def trace_seed(base, block, condition, load):
    return int(hashlib.sha256(f'{base}|{block}|{condition}|{load}'.encode()).hexdigest()[:8], 16)


def balanced_orders(names, groups):
    """Policy order per comparison group (block, condition, load): cyclic Latin rotation over the consecutive groups, so
    every policy takes every position equally often whenever the group count is a multiple of len(names)."""
    names = list(names)
    return {g: names[i % len(names):] + names[:i % len(names)] for i, g in enumerate(groups)}


def freeze_loads(pilot, fractions=LOAD_FRACTIONS, condition='good'):
    """pilot: [{condition, policy, rate_rps, goodput_rps, admission_drops}] -> frozen absolute offered loads (rps), equal
    for every policy and network condition. Refuses when no pilot rate is sustained (no silent default)."""
    rows = [r for r in pilot if r['condition'] == condition]
    sustained = [r['rate_rps'] for r in rows if r['requests'] and r['ok'] >= 0.9 * r['requests'] and not r.get('admission_drops')]
    if not sustained:
        raise ValueError(f'no sustained pilot rate under {condition}; widen the pilot ladder before freezing')
    saturation = max(sustained)
    above = sorted({r['rate_rps'] for r in rows if r['rate_rps'] > saturation})  # every policy failed these (else sustained)
    loads = {k: max(0.05, round(round(f * saturation / 0.05) * 0.05, 2)) for k, f in fractions.items()}
    return dict(saturation_rps=saturation, rule=SATURATION_RULE, fractions=dict(fractions), loads_rps=loads,
                capacity='bracketed' if above else 'censored_at_largest_tested_rate', upper_failure_rps=above[0] if above else None,
                pilot_cells=len(pilot), condition=condition)


def compare_cells(frozen, compare=COMPARE, specs=POLICY_SPECS):
    groups = [(b, c, load) for b in range(compare['blocks']) for c in compare['conditions'] for load in frozen['loads_rps']]
    orders = balanced_orders([s['name'] for s in specs], groups)
    cells = []
    for b in range(compare['blocks']):
        for c in compare['conditions']:
            for load, rate in frozen['loads_rps'].items():
                seed = trace_seed(compare['seed'], b, c, load)
                for name in orders[(b, c, load)]:
                    cells.append(dict(block=b, condition=c, load=load, rate_rps=rate, seed=seed, policy=name,
                                      label=f'b{b}-{c}-{load}-{name}'))
    return cells


def pilot_cells(pilot=PILOT):
    cells = []
    for c in pilot['conditions']:
        for rate in pilot['rates_rps']:
            seed = trace_seed(pilot['seed'], 0, c, rate)
            for name in pilot['policies']:
                cells.append(dict(block=0, condition=c, load=f'r{rate:g}', rate_rps=rate, seed=seed, policy=name,
                                  label=f'pilot-{c}-r{rate:g}-{name}'))
    return cells


def estimate_seconds(cells, window_s, budgets=BUDGETS, conditions=()):
    per_cell = window_s + budgets['cancel_s'] + budgets['drain_timeout_s'] / 4 + 5
    per_condition = 2 * len(set(conditions)) * 90  # shaping + route probes (planning estimate, not measured)
    return dict(cells=len(cells), per_cell_s=per_cell, traces_s=len(cells) * per_cell, conditions_s=per_condition,
                total_s=len(cells) * per_cell + per_condition, note='planning estimate; bounded in execution by the collection budget')


def release_ok(release_path, binding):
    """A coordinator release is a JSON file {"release": "RELEASE", **binding}; every binding value must match exactly."""
    path = Path(release_path)
    if not path.exists():
        return False, 'release file missing'
    try:
        value = json.loads(path.read_text())
    except ValueError:
        return False, 'release file is not JSON'
    if value.get('release') != 'RELEASE':
        return False, 'release marker missing'
    wrong = sorted(k for k, v in binding.items() if value.get(k) != v)
    return (not wrong), ('ok' if not wrong else f'release does not bind: {wrong}')
