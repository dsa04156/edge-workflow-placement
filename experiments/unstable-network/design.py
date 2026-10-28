"""Frozen design of the unstable-edge-network re-experiment (pure functions; no kubectl).

Same heterogeneous VQA DAG, workers, models, precision, preprocessing, 60 reused pairs, 2 s deadline and diagnostic
quality contract as quality-network-baselines; what changes is the network: each cell runs one pre-registered
time-varying trace (traces.py) in the owned gateway netns while open-loop requests arrive. Six policies; no `fixed`.

Copied verbatim from experiments/quality-network-baselines/design.py (sha256 in the plan's copied_sources; this
directory never imports that module because both directories define `policies`/`design`): canonical, digest,
dag_workers, MODEL_FILES, model_expectations, candidates, coverage, dag_pairs, arrivals, trace_seed, release_ok.
New here: POLICY_SPECS, DESIGN, BUDGETS, RECOVERY, schedule (experimental-design assign_factorial_runs), check_schedule,
cells_estimate.
"""
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
QNH = ROOT/'experiments/quality-network-heterogeneous'
QNB = ROOT/'experiments/quality-network-baselines'
HELPER = ROOT/'.agents/skills/experimental-design/scripts/randomization.py'
sys.path.insert(0, str(HERE))
import policies  # noqa: E402
import traces  # noqa: E402

EXPERIMENT = 'unstable-network'
ORIGIN_NODE = 'etri-dev0003-raspi5'
SERVER_NODE = 'etri-ser0002-cgnmsb'
QMIN, SLO_S, CANCEL_S = 0.40, 2.0, 10.0  # diagnostic contract kept from the 2026-10-23 pilot; not tuned to results
QUALITY_MODE = 'diagnostic'              # eval 0.355 < Qmin on the reused 60 pairs; a quality miss never blocks the network run
HOSTED = {'cpu-torch': list(policies.TASKS), 'openvino-npu': ['vision'], 'cuda-torch': ['vision', 'speech', 'fusion']}
STAGE_OF_TASK = {t: t for t in policies.TASKS}
POLICY_SPECS = (dict(name='task_fastest'), dict(name='greedy_comm'), dict(name='dag_static'), dict(name='dag_reopt'),
                dict(name='dag_reopt_hysteresis', rel=0.15, abs_ms=50.0, dwell_s=5.0), dict(name='dag_reopt_no_network'))
DESIGN = dict(traces=('stable', 'slow', 'burst'), blocks=2, rate_rps=1.6, window_s=traces.WINDOW_S, seed=20261027,
              unit='block/run (requests inside a cell are not independent replicates)',
              note='exploratory 2-block repetition; no significance or capacity claim; sample/conditions fixed before collection')
# Recovery metric (pre-registered): for every degraded -> good boundary B of a trace (slow: 40; burst: 30 and 50), the
# first recovery R is the scheduled offset of the first arrival at or after B whose valid answer is in deadline; the
# recovery window is [R, R + 10 s) and its in-deadline completion fraction (valid ok / scheduled arrivals in the window,
# window cut at the trace end) is reported with R - B. No ok arrival after B -> R None, fraction None, 'not_recovered'.
RECOVERY = dict(window_s=10.0, boundaries={'slow': (40,), 'burst': (30, 50), 'stable': ()},
                definition='first in-deadline valid answer of an arrival scheduled at or after the degraded->good boundary; '
                           'in-deadline fraction of arrivals scheduled in the following fixed 10 s window')
# max_inflight 8 (baselines: 32) limits OOM risk and admission blow-up; every admission drop is recorded. dag_max_active,
# gateway slots and worker memory limits are unchanged (a memory-limit change needs preflight + coordinator approval).
# wall_budget_s is one absolute limit from before preparation (live.WallBudget); everything except the end-of-run network
# teardown and the UID-owned Pod cleanup ends cleanup_reserve_s (300 s since R1) before it.
BUDGETS = dict(ready_window_s=2100, cache_ready_s=180, upload_timeout_s=1800, probe_timeout_s=60, profile_timeout_s=1800,
               control_timeout_s=30, poll_s=5, drain_timeout_s=60, drain_settle_s=2, max_inflight=8, dag_max_active=96,
               deadline_s=SLO_S, cancel_s=CANCEL_S, obs_poll_s=1.0, obs_poll_timeout_s=0.5, obs_stale_s=3.0, profile_pairs=8,
               profile_warmup=1, probe_sizes=(4096, 1048576), probe_reps=3, probe_job_timeout_s=1800, collection_s=6000,
               cleanup_reserve_s=300, worker_pod_lifetime_s=12600, cache_pod_lifetime_s=14700, gateway_upstream_cap_s=60,
               control_max_body_bytes=32 * 1024 * 1024, start_delay_s=2.0, align_tolerance_ms=250, netctl_port=8081,
               wall_budget_s=10800)
assert BUDGETS['worker_pod_lifetime_s'] > BUDGETS['collection_s'] + BUDGETS['ready_window_s'] + BUDGETS['cleanup_reserve_s']
assert BUDGETS['worker_pod_lifetime_s'] > BUDGETS['wall_budget_s']
assert BUDGETS['cache_pod_lifetime_s'] > BUDGETS['worker_pod_lifetime_s'] + BUDGETS['upload_timeout_s']
assert BUDGETS['cancel_s'] < BUDGETS['gateway_upstream_cap_s'] and BUDGETS['deadline_s'] < BUDGETS['cancel_s']
assert BUDGETS['obs_stale_s'] >= 3 * BUDGETS['obs_poll_s']


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


def _helper():
    spec = importlib.util.spec_from_file_location('experimental_design_randomization', HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # needs numpy + pandas (plan time only)
    return module


def schedule(design=DESIGN, specs=POLICY_SPECS):
    """Blocked, balanced, randomized run order (seed design['seed']) with the experimental-design helper:
    - blocks run in order; inside a block the trace order is assign_factorial_runs over the traces (seed + 2 + block);
    - the six policies' order inside each (block, trace) group is one row of a 6x6 Latin square whose rows are assigned
      to the six groups by assign_factorial_runs (seed + 1) and whose symbols are the policies in assign_factorial_runs
      order (seed): every policy takes every position exactly once over the six groups;
    - all policies of one (block, trace) share one arrival seed (trace_seed), i.e. the same request cohort."""
    import numpy
    import pandas as pd
    helper = _helper()
    names = [s['name'] for s in specs]
    groups = [(b, t) for b in range(design['blocks']) for t in design['traces']]
    if len(groups) != len(names):
        raise ValueError('the Latin-square balance needs as many (block, trace) groups as policies')
    seed = design['seed']
    symbols = helper.assign_factorial_runs(pd.DataFrame(dict(policy=names)), seed=seed)['policy'].tolist()
    rows = helper.assign_factorial_runs(pd.DataFrame(dict(block=[g[0] for g in groups], trace=[g[1] for g in groups])), seed=seed + 1)
    latin_row = {(int(r.block), r.trace): int(r.run_order) - 1 for r in rows.itertuples()}
    cells = []
    for b in range(design['blocks']):
        order = helper.assign_factorial_runs(pd.DataFrame(dict(trace=list(design['traces']))), seed=seed + 2 + b)['trace'].tolist()
        for t in order:
            arrival_seed = trace_seed(seed, b, t, design['rate_rps'])
            for j in range(len(names)):
                policy = symbols[(latin_row[(b, t)] + j) % len(names)]
                cells.append(dict(block=b, trace=t, policy=policy, position=j, rate_rps=design['rate_rps'], seed=arrival_seed,
                                  label=f'b{b}-{t}-{policy}'))
    problems = check_schedule(cells, design, specs)
    if problems:
        raise ValueError(f'generated schedule fails its own balance check: {problems}')
    return dict(cells=cells, seed=seed, latin_rows={f'b{b}-{t}': r for (b, t), r in sorted(latin_row.items())}, symbols=symbols,
                helper=dict(path=str(HELPER.relative_to(ROOT)), sha256=hashlib.sha256(HELPER.read_bytes()).hexdigest(),
                            function='assign_factorial_runs', numpy=numpy.__version__, pandas=pd.__version__),
                method=schedule.__doc__)


def check_schedule(cells, design=DESIGN, specs=POLICY_SPECS):
    """Stdlib re-check of a frozen schedule -> list of problems (empty = valid)."""
    names, problems = [s['name'] for s in specs], []
    want = sorted((t, p) for t in design['traces'] for p in names)
    for b in range(design['blocks']):
        if sorted((c['trace'], c['policy']) for c in cells if c['block'] == b) != want:
            problems.append(f'block {b} is not every (trace, policy) exactly once')
    if [c['block'] for c in cells] != sorted(c['block'] for c in cells):
        problems.append('blocks are not in order')
    n = len(names)
    for i in range(0, len(cells), n):
        group = cells[i:i + n]
        if len({(c['block'], c['trace']) for c in group}) != 1 or [c['position'] for c in group] != list(range(n)):
            problems.append(f'cells {i}..{i + n - 1} are not one contiguous (block, trace) group in position order')
        elif len({c['seed'] for c in group}) != 1 or group[0]['seed'] != trace_seed(design['seed'], group[0]['block'], group[0]['trace'],
                                                                                      design['rate_rps']):
            problems.append(f'cells {i}..{i + n - 1} do not share the frozen arrival seed')
    for p in names:
        if sorted(c['position'] for c in cells if c['policy'] == p) != list(range(n)):
            problems.append(f'{p} does not take every position exactly once')
    for c in cells:
        if c['label'] != f"b{c['block']}-{c['trace']}-{c['policy']}" or c['rate_rps'] != design['rate_rps']:
            problems.append(f"{c['label']}: label or rate differs from the design")
    return problems


def cells_estimate(cells, budgets=BUDGETS, window_s=DESIGN['window_s']):
    """Planning estimate (not measured): window + cancel + start delay + drain/teardown overhead per cell."""
    per_cell = window_s + budgets['cancel_s'] + budgets['start_delay_s'] + budgets['drain_settle_s'] + 15
    return dict(cells=len(cells), per_cell_s=per_cell, traces_s=len(cells) * per_cell, request_minutes=len(cells) * window_s / 60,
                expected_arrivals=round(len(cells) * window_s * DESIGN['rate_rps']),
                note='planning estimate; bounded in execution by collection_s; deploy/upload/profile/probe extra (see report)')
