"""Heterogeneous VQA qualification collector: plan (no kubectl) | execute (supervised, review-gated) | analyze (offline).

Inputs are the frozen 60-pair manifest (20 calibration / 40 evaluation, no filtering) and the read-only audit
inventory. Workers are HTTP-only (worker.py + bootstrap.py); ONE multi-stage worker per allocated accelerator unit.
The collector never execs/logs/attaches into an edge-node Pod. Server-node kubelet streams (cache upload only)
need a fresh API GET showing this run's creation UID on the expected node. Cleanup deletes creation-UID matches only
(multi-edge-sensors collect.cleanup). Output separates, for every catalog row:
  execution (real receipts on the requested device) / parity (vs frozen CPU reference) / quality (official VQA, Qmin).
Cost tables use successful real receipts only; main-plan candidates require execution + parity + quality passed.
"""
# Bind the stdlib profilers first: reused real-topology-live/placement_policies.py prepends real-topology-profile/
# (which has its own profile.py) to sys.path, and torch/transformers import the stdlib `profile` lazily.
import cProfile  # noqa: F401
import profile  # noqa: F401
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import statistics
import sys
import tarfile
import time
import urllib.error
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LIVE = ROOT/'experiments/real-topology-live'
MES = ROOT/'experiments/multi-edge-sensors'
VQA = ROOT/'experiments/quality-network-vqa'
sys.path.insert(0, str(HERE))
import catalog  # noqa: E402
sys.path.insert(1, str(VQA))
import vqa_eval  # noqa: E402
sys.path.insert(2, str(LIVE))
from http_runtime import request  # noqa: E402
import run_baseline_comparison as baseline  # noqa: E402
sys.path.insert(3, str(MES))
import collect as ownership  # noqa: E402

EXPERIMENT = 'quality-network-heterogeneous'
NS = 'topology-profile-lab'
QMIN = 0.40
ASSETS = ROOT/'artifacts/topology-profile-live'
GT_PAIRS = ROOT/'artifacts/topology-profile-assets/20261019T091725Z/selected-data/pairs.json'
LOAD_PAIRS = ASSETS/'load-pairs.json'
REFERENCE = ASSETS/'reference-v1'
CORPUS = ASSETS/'load-cache/corpus'
OUT_ROOT = ROOT/'artifacts/quality-network-heterogeneous'
JELLYBEAN_TAR_MEMBER_PREFIX = 'jellybean/'
IMAGE = '10.254.192.47:5000/node-inventory-python@sha256:370a8339507905053693d178e1cda8fbf77937f045ead58a3c32d8cead2ca67f'
INTEL_NPU_IMAGE = '192.168.0.56:5000/ra-npu-intel@sha256:725d604c11e02c7ddce47c7ab4e362d98245860644ce40679bf9c5e2dec1ea1a'
# Public official compiler image (Docker Hub), pinned by digest; see raw/mobilint-qbcompiler-dockerhub-1.4.json.
MOBILINT_COMPILER_IMAGE = 'docker.io/mobilint/qbcompiler@sha256:62b5bc714571dffd94fb561275767ca80d15337a433630a3ae7936305d531a7b'
# Official NVIDIA NGC JetPack 6.1 line (L4T r36.4, cuDNN 9) for the JP6.1 redist torch; arm64 index digest verified in
# .scratch/quality-network-heterogeneous/dependency-followup/jetson-images.json. Python/pip presence is checked in-Pod.
JP61_IMAGE = 'nvcr.io/nvidia/l4t-jetpack@sha256:34ccf0f3b63c6da9eee45f2e79de9bf7fdf3beda9abfd72bbf285ae9d40bb673'
# Explicit live budgets (seconds). Workers start together; one shared readiness window; the collection deadline is
# counted from worker creation; Pod lifetimes exceed collection + cleanup so terminal evidence is read before deletion.
BUDGETS = dict(ready_window_s=2100, collection_s=5400, request_cap_s=600, min_request_s=5, poll_s=10, cleanup_reserve_s=900,
               upload_timeout_s=1800, cache_ready_s=180, install_timeout_s=1200, fetch_deadline_s=600, compile_timeout_s=4200,
               worker_pod_lifetime_s=7200, cache_pod_lifetime_s=9300)
assert BUDGETS['worker_pod_lifetime_s'] > BUDGETS['collection_s'] + BUDGETS['cleanup_reserve_s']
assert BUDGETS['cache_pod_lifetime_s'] > BUDGETS['upload_timeout_s'] + BUDGETS['cache_ready_s'] + BUDGETS['worker_pod_lifetime_s']
assert BUDGETS['compile_timeout_s'] < BUDGETS['collection_s'] and BUDGETS['install_timeout_s'] < BUDGETS['ready_window_s']
TERMINAL_WAITING = ('ImagePullBackOff', 'InvalidImageName', 'ErrImageNeverPull', 'CreateContainerError', 'CreateContainerConfigError',
                    'CrashLoopBackOff', 'RunContainerError')
STREAM_VERBS = ('exec', 'attach', 'logs', 'cp', 'port-forward', 'top', 'debug')
OBSERVED_RUNTIME_CLASSES = ('ai-scientist-nvidia', 'hairp-lab-nvidia', 'nvidia-spark', 'ra-nvidia-rtx5060')
RULE = dict(feature_rtol=1e-4, feature_atol=1e-5, transcript='exact', answer_ids='exact', score_atol=1e-4)
REQUIREMENTS = {
    'cpu-x86_64-cp311': ['torch==2.8.0+cpu', 'torchvision==0.23.0+cpu', 'transformers==4.57.1', 'numpy==1.26.4', 'pillow==11.3.0',
                         'soundfile==0.13.1', 'pyyaml==6.0.3'],
    'cpu-aarch64-cp311': ['torch==2.8.0+cpu', 'torchvision==0.23.0', 'transformers==4.57.1', 'numpy==1.26.4', 'pillow==11.3.0',
                          'soundfile==0.13.1', 'pyyaml==6.0.3'],
    'cu130-aarch64-cp311': ['torch==2.9.1+cu130', 'torchvision==0.24.1', 'transformers==4.57.1', 'numpy==1.26.4', 'pillow==11.3.0',
                            'soundfile==0.13.1', 'pyyaml==6.0.3'],
    'npu-x86_64-cp312': ['pillow==11.3.0'],
    # nv24.08 torch is 2.5.0a0 while PyPI torchvision 0.20.0 pins torch==2.5.0: installed --no-deps from the complete set.
    'jp61-aarch64-cp310': ['torch==2.5.0a0+872d972e41.nv24.08.17622132', 'torchvision==0.20.0', 'transformers==4.57.1', 'tokenizers==0.22.2',
                           'safetensors==0.8.0', 'huggingface_hub==0.36.2', 'numpy==1.26.4', 'pillow==11.3.0', 'soundfile==0.13.1',
                           'pyyaml==6.0.3', 'regex==2026.9.29', 'filelock', 'typing-extensions', 'sympy', 'networkx', 'jinja2', 'fsspec',
                           'markupsafe', 'mpmath', 'packaging', 'requests', 'certifi', 'idna', 'urllib3', 'charset-normalizer', 'tqdm', 'cffi',
                           'pycparser', 'hf-xet'],
}
NO_DEPS_RECIPES = ('jp61-aarch64-cp310',)
# One driver-matched recipe per Jetson, chosen from observed driver evidence (never pull both base images on a node).
# Nodes absent here get no Jetson worker: dev0007 is a catalog deployment blocker (catalog.DEPLOYMENT_BLOCKERS); dev0001's
# unchanged JP6.1 recipe is held (catalog.RECIPE_FAILURES) instead of being retried.
RUN1 = 'artifacts/quality-network-heterogeneous/qnh-20261023t090355'
RUN2 = 'artifacts/quality-network-heterogeneous/qnh-20261023t095013'
REVALIDATION = '.scratch/quality-network-heterogeneous/live-extra'
# JP6.1 runtime closure (prepare_jp61_sysroot.py: unresolved [] against the pinned image inventory, see sysroot-manifest.json).
# Shipped as real files under their sonames and preloaded by bootstrap with RTLD_GLOBAL in this order before importing torch.
JP61_SYSROOT = OUT_ROOT/'toolchains-jp61-sysroot-20261023T093145Z'
SYSROOT_PRELOAD = {'jp61-aarch64-cp310': (('libgfortran.so.5', 'libgfortran.so.5.0.0'), ('libopenblas.so.0', 'libopenblasp-r0.3.20.so'),
                                          ('libcusparseLt.so.0', 'libcusparseLt.so.0.6.2.3'))}
JETSON_RECIPES = {
    'etri-dev0009-jetorn': dict(recipe='cu130-aarch64-cp311', driver_api=13020, l4t='R39.2.1',
                                evidence=f'{RUN1}/health-jgpu-dev0009-cu130-002.json (sha256 8ae211b2...) driver_version 13020, cu130 gate passed; '
                                         f'revalidated {REVALIDATION}/dev0009-revalidation-0954.json (sha256 c2c0a565...: Ready, no taints/pressure, '
                                         f'gpu.shared unallocated, root fs 68.69 GiB free) and dev0009-storage-budget-0955.json (fed121ed..., 13Gi); '
                                         f'pair metadata .scratch/quality-network-heterogeneous/cu130-pair-metadata.json'),
}
# Held, not retried unchanged: torch imports after the sysroot fix but PyPI torchvision 0.20.0 native ops do not register
# against nv24.08 torch (run 2); pending repair, kept in the catalog as runtime_failed.
JETSON_HELD = {
    'etri-dev0001-jetorn': dict(recipe='jp61-aarch64-cp310', driver_api=12060, l4t='R36.4.7',
                                evidence=f'{RUN2}/health-jgpu-dev0001-jp61-002.json (sha256 df74948f...): operator torchvision::nms does not exist'),
}
WHEELHOUSES = {'cpu-x86_64-cp311': ASSETS/'wheelhouse/x86_64', 'cpu-aarch64-cp311': ASSETS/'wheelhouse/aarch64'}
ROOT_ACCESS = 'runAsUser 0 like the proven e0 GPU/NPU Jobs; not privileged, no escalation, all capabilities dropped'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def utc():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


# ------------------------------------------------------------------ wave definition
def workers_for(audit):
    """Candidate workers from the observed inventory. Occupied / unavailable units get no Pod (recorded in the catalog)."""
    nodes = {n['node']: n for n in audit['nodes']}
    ready = lambda name: name in nodes and nodes[name]['ready'] == 'True' and not nodes[name].get('unschedulable')
    free = lambda name, key: int(nodes[name]['accelerators_allocatable'].get(key, 0)) - nodes[name]['accelerators_requested_by_running_or_pending_pods'].get(key, 0)
    out = []
    if ready('etri-ser0002-cgnmsb'):
        out.append(dict(key='cpu-c', node='etri-ser0002-cgnmsb', edge=False, backend='cpu-torch', stages=['vision', 'speech', 'fusion'],
                        recipe='cpu-x86_64-cp311', image=IMAGE, resources={}, root=False))
        if free('etri-ser0002-cgnmsb', 'npu.intel.com/accel') >= 1:
            out.append(dict(key='npu-c', node='etri-ser0002-cgnmsb', edge=False, backend='openvino-npu', stages=['vision', 'speech'],
                            recipe='npu-x86_64-cp312', image=INTEL_NPU_IMAGE, resources={'npu.intel.com/accel': 1}, root=True,
                            stage_roles=dict(vision='serving candidate', speech='exact-length compilation probe')))
    if ready('etri-dev0003-raspi5'):
        out.append(dict(key='cpu-e0', node='etri-dev0003-raspi5', edge=True, backend='cpu-torch', stages=['vision', 'speech', 'fusion'],
                        recipe='cpu-aarch64-cp311', image=IMAGE, resources={}, root=False))
    for name, n in sorted(nodes.items()):
        if 'nvidia.com/gpu.shared' in n['accelerators_allocatable'] and ready(name):
            choice = JETSON_RECIPES.get(name)
            if choice and free(name, 'nvidia.com/gpu.shared') >= 1:
                recipe = choice['recipe']
                out.append(dict(key='jgpu-'+name.split('-')[1]+'-'+recipe.split('-')[0], node=name, edge=True, backend='cuda-torch',
                                stages=['vision', 'speech', 'fusion'], recipe=recipe, image=JP61_IMAGE if recipe.startswith('jp61') else IMAGE,
                                resources={'nvidia.com/gpu.shared': 1}, root=True, recipe_selection=choice,
                                image_assumption=None if not recipe.startswith('jp61') else
                                'l4t-jetpack r36.4 pulled and started with python3 on dev0001 in run 1; torch needs the pinned sysroot preload'))
        # Mobilint compile probe not repeated: qbcompiler:1.4-cpu lacks qbcompiler (run 1, health-mblt-compile-001.json); the catalog
        # keeps that dependency_unavailable evidence. The allocation probe below is unchanged.
        for key, short in (('hailo.ai/h8', 'hailo'), ('mobilint.com/npu', 'mblt')):
            if key in n['accelerators_allocatable'] and ready(name) and free(name, key) >= 1:
                out.append(dict(key=f'{short}-'+name.split('-')[1], node=name, edge=n['role'] == 'edge', backend='probe-allocation', stages=[],
                                recipe='probe-allocation', image=IMAGE, resources={key: 1}, root=True,
                                scope='device-plugin allocation + device-node discovery only; no compiler/artifact, no model execution'))
    return out


def pod_manifest(run, worker, cache_url, manifest_sha):
    env = [dict(name='NODE_NAME', valueFrom={'fieldRef': {'fieldPath': 'spec.nodeName'}}),
           dict(name='POD_UID', valueFrom={'fieldRef': {'fieldPath': 'metadata.uid'}}),
           dict(name='RECIPE', value=worker['recipe']), dict(name='BACKEND', value=worker['backend']),
           dict(name='STAGES', value=','.join(worker['stages'])), dict(name='CACHE_URL', value=cache_url or ''),
           dict(name='ASSET_MANIFEST_SHA256', value=manifest_sha or ''), dict(name='HOME', value='/work'),
           dict(name='TMPDIR', value='/work/tmp'), dict(name='PYTHONDONTWRITEBYTECODE', value='1'), dict(name='HF_HUB_OFFLINE', value='1'),
           dict(name='INSTALL_TIMEOUT_S', value=str(BUDGETS['install_timeout_s'])), dict(name='FETCH_DEADLINE_S', value=str(BUDGETS['fetch_deadline_s'])),
           dict(name='COMPILE_TIMEOUT_S', value=str(BUDGETS['compile_timeout_s'])),
           dict(name='TRANSFORMERS_OFFLINE', value='1')]
    units = {k: str(v) for k, v in worker['resources'].items()}
    heavy = worker['backend'] in ('cuda-torch', 'cpu-torch', 'mobilint-compile')
    storage = (worker.get('storage') or {}).get('quantity')  # per-recipe accounting from plan(); request == limit == work emptyDir
    resources = {'requests': dict(cpu='1' if heavy else '250m', memory='3Gi' if heavy else '512Mi', **{'ephemeral-storage': storage or '1Gi'}, **units),
                 'limits': dict(cpu='4' if heavy else '1', memory='8Gi' if heavy else '3Gi', **{'ephemeral-storage': storage or '14Gi'}, **units)}
    user = {'runAsUser': 0} if worker['root'] else {'runAsUser': 65532, 'runAsGroup': 65532, 'runAsNonRoot': True}
    container = dict(name='worker', image=worker['image'], imagePullPolicy='IfNotPresent', command=worker.get('command', ['python3', '-u', '/config/bootstrap.py']),
                     env=env, resources=resources, ports=[dict(containerPort=8080)],
                     readinessProbe={'httpGet': {'path': '/ready', 'port': 8080}, 'periodSeconds': 5, 'timeoutSeconds': 3},
                     volumeMounts=[dict(name='work', mountPath='/work'), dict(name='tmp', mountPath='/tmp'),
                                   dict(name='config', mountPath='/config', readOnly=True)],
                     securityContext={'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}})
    spec = dict(restartPolicy='Never', activeDeadlineSeconds=BUDGETS['worker_pod_lifetime_s'], terminationGracePeriodSeconds=3, automountServiceAccountToken=False,
                securityContext=dict(user, seccompProfile={'type': 'RuntimeDefault'}),
                affinity={'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [
                    {'matchFields': [{'key': 'metadata.name', 'operator': 'In', 'values': [worker['node']]}]}]}}},
                containers=[container], volumes=[{'name': 'work', 'emptyDir': {'sizeLimit': storage or '14Gi'}}, {'name': 'tmp', 'emptyDir': {'sizeLimit': '2Gi'}},
                                                 {'name': 'config', 'configMap': {'name': run}}])
    return dict(apiVersion='v1', kind='Pod', metadata=meta(run, run+'-'+worker['key']), spec=spec)


def cache_manifest(run):
    container = dict(name='worker', image=IMAGE, imagePullPolicy='IfNotPresent', command=['python3', '-u', '-m', 'http.server', '8080', '--directory', '/work'],
                     resources={'requests': {'cpu': '100m', 'memory': '256Mi', 'ephemeral-storage': '1Gi'},
                                'limits': {'cpu': '1', 'memory': '1Gi', 'ephemeral-storage': '14Gi'}},
                     readinessProbe={'httpGet': {'path': '/', 'port': 8080}, 'periodSeconds': 5, 'timeoutSeconds': 3},
                     volumeMounts=[dict(name='work', mountPath='/work')],
                     securityContext={'runAsUser': 65532, 'runAsGroup': 65532, 'runAsNonRoot': True, 'allowPrivilegeEscalation': False,
                                      'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}})
    spec = dict(restartPolicy='Never', activeDeadlineSeconds=BUDGETS['cache_pod_lifetime_s'], terminationGracePeriodSeconds=3, automountServiceAccountToken=False,
                securityContext={'fsGroup': 65532, 'seccompProfile': {'type': 'RuntimeDefault'}},
                affinity={'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [
                    {'matchFields': [{'key': 'metadata.name', 'operator': 'In', 'values': ['etri-ser0002-cgnmsb']}]}]}}},
                containers=[container], volumes=[{'name': 'work', 'emptyDir': {'sizeLimit': '14Gi'}}])
    return dict(apiVersion='v1', kind='Pod', metadata=meta(run, run+'-cache'), spec=spec)


def meta(run, name):
    return dict(name=name, namespace=NS, labels={'experiment': EXPERIMENT, 'run-id': run})


SHIPPED = ('worker.py', 'stages.py', 'bootstrap.py', 'mobilint_compile.py', 'proc.py')


def shipped_hashes(run):
    return {name: hashlib.sha256(text.encode()).hexdigest() for name, text in config_map(run)['data'].items()}


def verify_shipped(wave, run):
    """The ConfigMap built at execute time must carry exactly the sources hashed into the plan."""
    current = shipped_hashes(run)
    if current != wave.get('shipped_sources'):
        drift = sorted(k for k in set(current) | set(wave.get('shipped_sources') or {}) if current.get(k) != (wave.get('shipped_sources') or {}).get(k))
        raise RuntimeError(f'shipped source drift since the plan: {drift}')
    return current


def config_map(run):
    data = {f: (HERE/f).read_text() for f in SHIPPED}
    data['http_runtime.py'] = (LIVE/'http_runtime.py').read_text()  # unchanged frozen copy (framing helpers)
    return dict(apiVersion='v1', kind='ConfigMap', metadata=meta(run, run), data=data)


def asset_files(export_dir, deps_dirs, recipes=None, calibration_inputs=True):
    """(group, published path, local source) for every file the cache serves; hashes come from the files themselves.
    recipes limits the wheel/sysroot groups to the recipes a wave actually uses (default: all)."""
    models = ASSETS/'model-assets'
    files = [('common', 'models/resnet18-f37072fd.pth', models/'resnet18-f37072fd.pth'), ('common', 'models/mutan-resnet18.pth.tar', models/'mutan-resnet18.pth.tar'),
             ('common', 'models/vocab.json', models/'vocab.json')]
    files += [('common', 'models/asr/'+p.name, p) for p in sorted((models/'asr').iterdir())]
    files += [('onnx', 'onnx/'+n, Path(export_dir)/n) for n in ('vision-static-448.onnx', 'speech-dynamic.onnx')]
    calibration = [p for p in json.loads(LOAD_PAIRS.read_text())['pairs'] if p['split'] == 'calibration']
    if calibration_inputs:
        files += [('calib', 'calib/'+Path(p['raw_image_path']).name, CORPUS/Path(p['raw_image_path']).name) for p in calibration]  # quantizer input
    houses = dict(WHEELHOUSES)
    for d in deps_dirs:
        for sub in sorted(Path(d).iterdir()):
            if sub.is_dir() and (sub/'manifest.json').exists() and json.loads((sub/'manifest.json').read_text())['status'] == 'complete':
                houses[sub.name] = sub
    for recipe in REQUIREMENTS if recipes is None else [r for r in REQUIREMENTS if r in recipes]:
        if recipe not in houses:
            raise FileNotFoundError(f'wheelhouse for {recipe} not prepared')
        files += [(recipe, f'wheels/{recipe}/{p.name}', p) for p in sorted(houses[recipe].glob('*.whl'))]
        files += [(recipe, f'sysroot/{recipe}/{soname}', JP61_SYSROOT/'sysroot/lib'/real) for soname, real in SYSROOT_PRELOAD.get(recipe, ())]
    return files


def unpacked_bytes(path):
    with zipfile.ZipFile(path) as archive:
        return sum(info.file_size for info in archive.infolist())


GIB = 1 << 30


def storage_budget(worker, entries):
    """Pod-level /work accounting (bytes) for one worker: every file bootstrap fetches for it + jellybean extraction + the
    unpacked install (/work/site) + pip staging under TMPDIR=/work/tmp (pip --target stages the whole install before
    moving it; counted once more in full, which also covers the largest uncompressed wheel) + 1 GiB logs/margin.
    The container image is node-level storage and is reported separately (not part of this request)."""
    if worker['recipe'] not in REQUIREMENTS:
        return None
    fetched = [e for e in entries if e['group'] in ('common', worker['recipe']) or (e['group'] == 'onnx' and worker['backend'] == 'openvino-npu')]
    wheels = [e for e in fetched if e['path'].endswith('.whl')]
    unpacked = [e.get('unpacked_bytes') or 0 for e in wheels]
    jellybean = sum(e.get('bytes') or 0 for e in fetched if e['path'] == 'jellybean.tar')
    total = sum(e.get('bytes') or 0 for e in fetched) + jellybean + 2 * sum(unpacked) + GIB
    quantity = -(-total // GIB)
    return dict(fetched_bytes=sum(e.get('bytes') or 0 for e in fetched), jellybean_extract_bytes=jellybean, install_unpacked_bytes=sum(unpacked),
                pip_staging_bytes=sum(unpacked), largest_unpacked_wheel_bytes=max(unpacked, default=0), margin_bytes=GIB,
                total_bytes=total, quantity=f'{quantity}Gi', image_note='container image layers are node-level storage, checked separately')


def node_image_storage(workers):
    """Node-level image storage (compressed registry layers) per worker image where it is known; not a Pod request."""
    inventory = load_json(JP61_SYSROOT/'image-inventory.json', {})
    jp61 = sum(layer['size'] for layer in inventory.get('manifest_layers', [])) or None
    return {w['key']: dict(node=w['node'], image=w['image'], compressed_layer_bytes=jp61 if w['image'] == JP61_IMAGE else None,
                           note='kubelet image filesystem; check node free space separately before release') for w in workers}


def jellybean_tar(out):
    """Deterministic jellybean.tar re-packed from the frozen fusion.tar (sha recorded) members under jellybean/."""
    source = ASSETS/'cache-bundles/fusion.tar'
    target = Path(out)/'jellybean.tar'
    with tarfile.open(source) as src, tarfile.open(target, 'x') as dst:
        for member in src.getmembers():
            if member.name.startswith(JELLYBEAN_TAR_MEMBER_PREFIX) or member.name == 'jellybean':
                dst.addfile(member, src.extractfile(member) if member.isfile() else None)
    return target, sha(source)


def plan(out_dir, export_dir, deps_dirs, audit=None, hash_files=True):
    """Writes wave.json + catalog (no kubectl). hash_files=False only in unit tests (no multi-GB hashing)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=False)
    audit = audit or catalog.load_audit()
    workers = workers_for(audit)
    used = {w['recipe'] for w in workers}
    files = asset_files(export_dir, deps_dirs, recipes=used, calibration_inputs=any(w['backend'] == 'mobilint-compile' for w in workers))
    jb, fusion_sha = jellybean_tar(out)
    entries = [dict(group='common', path='jellybean.tar', source=str(jb), bytes=jb.stat().st_size, sha256=sha(jb) if hash_files else None)]
    entries += [dict(group=g, path=p, source=str(s), bytes=s.stat().st_size, sha256=sha(s) if hash_files else None,
                     **({'unpacked_bytes': unpacked_bytes(s)} if p.endswith('.whl') else {})) for g, p, s in files]
    for w in workers:
        budget = storage_budget(w, entries)
        if budget:
            w['storage'] = budget
    export = json.loads((Path(export_dir)/'manifest.json').read_text())
    recipes = {k: dict(requirements=v, no_deps=k in NO_DEPS_RECIPES,
                       **({'preload': [f'sysroot/{k}/{soname}' for soname, _ in SYSROOT_PRELOAD[k]],
                           'sysroot_manifest': str(JP61_SYSROOT/'sysroot-manifest.json'),
                           'sysroot_manifest_sha256': sha(JP61_SYSROOT/'sysroot-manifest.json')} if k in SYSROOT_PRELOAD else {}))
               for k, v in REQUIREMENTS.items() if k in used}
    assets = dict(files=entries, recipes=recipes,
                  onnx={k: dict(path=f'onnx/{n}', sha256=export['stages'][k]['export']['sha256'])
                        for k, n in (('vision', 'vision-static-448.onnx'), ('speech', 'speech-dynamic.onnx'))},
                  fusion_tar_sha256=fusion_sha, export_dir=str(export_dir), deps_dirs=[str(d) for d in deps_dirs])
    with (out/'assets-manifest.json').open('x') as handle:
        json.dump(assets, handle, indent=1)
    manifest_sha = sha(out/'assets-manifest.json')
    run = 'qnh-PLACEHOLDER'
    pods = [pod_manifest(run, w, 'http://CACHE_IP:8080', manifest_sha) for w in workers]
    wave = dict(created_utc=utc(), experiment=EXPERIMENT, namespace=NS, qmin=QMIN, parity_rule=RULE,
                inputs=dict(pairs=str(LOAD_PAIRS.relative_to(ROOT)), pairs_sha256=sha(LOAD_PAIRS), gt=str(GT_PAIRS.relative_to(ROOT)),
                            gt_sha256=sha(GT_PAIRS), split='20 calibration / 40 evaluation, no filtering'),
                workers=workers, assets_manifest_sha256=manifest_sha, shipped_sources=shipped_hashes(run),
                planner_sources={f: sha(HERE/f) for f in ('qualify.py', 'catalog.py', 'export_onnx.py', 'prepare_deps.py', 'prepare_jp61_sysroot.py')},
                asset_bytes=sum(e.get('bytes', 0) for e in entries), node_image_storage=node_image_storage(workers),
                manifests_template=dict(configmap_files=sorted(config_map(run)['data']), cache=cache_manifest(run), pods=pods),
                not_created=[r for r in catalog.build(audit)['rows'] if r['stage'] == 'vision' and r['status'] in ('occupied', 'node_unavailable')],
                review_gate='execute refuses to run without --review-release pointing to a coordinator release naming this wave sha256')
    with (out/'wave.json').open('x') as handle:
        json.dump(wave, handle, indent=1)
    catalog.write(catalog.build(audit), out)
    return wave


# ------------------------------------------------------------------ live session (supervised only)
class HetSession(ownership.Session):
    """collect.Session kubectl seam with create-only UID records and a fail-closed stream guard."""

    def __init__(self, out_root, run_id, wave):
        if not re.fullmatch(r'qnh-[a-z0-9][a-z0-9-]{0,36}', run_id):
            raise ValueError('run-id must match qnh-[a-z0-9-]')
        self.run, self.out, self.wave = run_id, Path(out_root)/run_id, wave
        self.out.mkdir(parents=True, exist_ok=False)
        self.counter, self.resources, self.unresolved, self.actual = 0, [], [], {}
        self.expected = {'cache': ('etri-ser0002-cgnmsb', False), **{w['key']: (w['node'], w['edge']) for w in wave['workers']}}

    delete_record = ownership.Session.delete_record
    owned_present = ownership.Session.owned_present
    delete_owned = ownership.Session.delete_owned

    def record_for(self, name):
        return next((r for r in reversed(self.resources) if r['name'] == name and r['kind'] == 'Pod'), None)

    def owned_state(self, key, stem):
        name = self.run+'-'+key
        obj = json.loads(ownership.Session.call(self, stem, ['get', 'pod', '-n', NS, name, '-o', 'json']))
        record = self.record_for(name)
        if record is None or obj['metadata']['uid'] != record['uid']:
            raise RuntimeError(f'{key}: Pod UID is not the UID this run created')
        node = obj.get('spec', {}).get('nodeName')
        if node != self.expected[key][0]:
            raise RuntimeError(f'{key} runs on {node}, expected {self.expected[key][0]}')
        return obj

    def call(self, stem, args, stdin=None, timeout=60):
        if args and args[0] in STREAM_VERBS:
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
            if reason:
                self.counter += 1
                skipped = args[0] == 'logs'
                self.save(f'{self.counter:04d}-{stem}.{"skipped" if skipped else "refused"}.json',
                          dict(at=datetime.now(timezone.utc).isoformat(), verb=args[0], args=args[:8], reason=reason+'; kubectl not invoked'))
                if skipped:
                    return ''
                raise RuntimeError(f'refused: {args[0]} ({reason})')
        return ownership.Session.call(self, stem, args, stdin, timeout)

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
                          owned_labels=labels.get('experiment') == EXPERIMENT and labels.get('run-id') == self.run)
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

    def pod_states(self):
        """One API GET (no stream) of this run's Pods -> {key: object}; a Pod whose UID is not this run's creation UID
        is reported as a failed foreign object, never adopted."""
        listing = json.loads(ownership.Session.call(self, 'pod-states', ['get', 'pods', '-n', NS, '-l', 'run-id='+self.run, '-o', 'json']))
        out = {}
        for obj in listing.get('items', []):
            key = obj['metadata']['name'].removeprefix(self.run+'-')
            record = self.record_for(obj['metadata']['name'])
            if key in self.expected and record and record['uid'] == obj['metadata'].get('uid'):
                out[key] = obj
            elif key in self.expected:
                out[key] = dict(obj, status=dict(phase='Failed', reason='ForeignUID', message='Pod UID differs from creation UID'))
        return out

    def wait_ready(self, key, timeout):
        """API wait (not a stream); a failed wait is recorded, never retried silently."""
        try:
            self.call('ready-'+key, ['wait', 'pod', '-n', NS, self.run+'-'+key, '--for=condition=Ready', f'--timeout={timeout}s'], timeout=timeout+10)
            return True
        except Exception:
            return False


def http(url, body=None, timeout=120):
    import urllib.request
    if body is None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as response:
            return json.load(response)
    return request(url, body, timeout=timeout)


def corpus():
    pairs = json.loads(LOAD_PAIRS.read_text())['pairs']
    refs = {r['question_id']: r for r in map(json.loads, (REFERENCE/'results.jsonl').read_text().splitlines())}
    return pairs, refs


def stage_inputs(stage, backend, pair, refs, processor=None):
    q = pair['question_id']
    if stage == 'vision':
        return dict(data=(CORPUS/Path(pair['raw_image_path']).name).read_bytes())
    if stage == 'speech':
        raw = (CORPUS/Path(pair['raw_audio_path']).name).read_bytes()
        if backend == 'openvino-npu':
            import numpy as np
            audio = np.load(io.BytesIO(raw), allow_pickle=False)
            values = processor(audio, sampling_rate=16000, return_tensors='np', padding='longest').input_values[0].astype(np.float32)
            out = io.BytesIO()
            np.save(out, values, allow_pickle=False)
            return dict(data=out.getvalue(), collector_preprocessing='Wav2Vec2Processor (exact, original length) on the collector')
        return dict(data=raw)
    return dict(inputs=dict(vision=base64.b64encode((REFERENCE/f'{q}.npy').read_bytes()).decode(),
                            speech=base64.b64encode(refs[q]['transcript'].encode()).decode()))


def error_phase(text):
    """Structured worker error phase (prepare/compile/inference) from an HTTP error body, else None."""
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value.get('phase') if isinstance(value, dict) else None


def read_jsonl(path, limit_fragment=300):
    """Rows of an append-only JSONL file; malformed/truncated lines (e.g. an interrupted last write) are returned as
    evidence (line number, bounded raw fragment, reason) instead of aborting. The file itself is never modified."""
    rows, malformed = [], []
    with Path(path).open('rb') as handle:
        for number, raw in enumerate(handle, 1):
            text = raw.decode(errors='replace').rstrip('\n')
            if not text.strip():
                continue
            try:
                value = json.loads(text)
                if not isinstance(value, dict):
                    raise ValueError('not an object')
                rows.append(value)
            except ValueError as error:
                malformed.append(dict(line=number, fragment=text[:limit_fragment], bytes=len(raw), reason=f'malformed_row: {error}'[:200]))
    return rows, malformed


def expected_input_sha(stage, backend, pair, refs, processor=None):
    """sha256 of the exact payload the collector sends for (stage, pair); fusion -> {vision, speech}."""
    payload = stage_inputs(stage, backend, pair, refs, processor)
    if stage == 'fusion':
        return {k: hashlib.sha256(base64.b64decode(v)).hexdigest() for k, v in payload['inputs'].items()}
    return hashlib.sha256(payload['data']).hexdigest()


def request_timeout(deadline, clock):
    """Per-request timeout: min(REQUEST_CAP_S, remaining run budget); None when the budget is exhausted."""
    if deadline is None:
        return BUDGETS['request_cap_s']
    remaining = deadline - clock()
    return min(BUDGETS['request_cap_s'], remaining) if remaining > BUDGETS['min_request_s'] else None


def qualify_stage(session, worker, url, stage, pairs, refs, processor, deadline=None, clock=time.monotonic, http_fn=None):
    http_fn = http_fn or http
    rows = []
    path = session.out/f'receipts-{worker["key"]}-{stage}.jsonl'
    with path.open('x') as handle:
        for index, pair in enumerate(pairs):
            body = dict(run_id=session.run, request_id=f'{worker["key"]}-{stage}-{index}', stage=stage,
                        **{k: v for k, v in stage_inputs(stage, worker['backend'], pair, refs, processor).items() if k != 'collector_preprocessing'})
            started = time.perf_counter()
            row = dict(index=index, question_id=pair['question_id'], split=pair['split'], stage=stage, worker=worker['key'])
            timeout = request_timeout(deadline, clock)
            if timeout is None:
                row.update(status='not_sent_deadline')
                handle.write(json.dumps(row)+'\n')
                rows.append(row)
                continue
            try:
                reply = http_fn(url+'/run', body, timeout=timeout)
                row.update(status='ok', data=reply['data'], receipt=reply['receipt'])
            except urllib.error.HTTPError as error:
                text = error.read(4000).decode(errors='replace')
                row.update(status='http_error', code=error.code, body=text, error_phase=error_phase(text))
            except Exception as error:
                row.update(status='transport_error', error=repr(error)[:500])
            row['collector_ms'] = (time.perf_counter()-started)*1000
            handle.write(json.dumps(row)+'\n')
            rows.append(row)
    return rows


def release_ok(wave, release):
    path = Path(release)
    return path.exists() and 'RELEASE' in path.read_text() and wave['assets_manifest_sha256'] in path.read_text()


COMPOSITIONS = (  # (name, vision worker, speech worker, fusion worker); only workers actually in the wave are used
    ('cpu-c/cpu-c/cpu-c', 'cpu-c', 'cpu-c', 'cpu-c'),
    ('cpu-e0/cpu-e0/cpu-e0', 'cpu-e0', 'cpu-e0', 'cpu-e0'),
    ('npu-c/cpu-c/cpu-c', 'npu-c', 'cpu-c', 'cpu-c'),
)


def compositions_for(wave):
    """Controls (all-CPU, NPU+CPU, each all-GPU) plus, for each Jetson CUDA worker, the mixed CPU+GPU+NPU DAG
    npu-c vision || jgpu speech -> cpu-c fusion. Only workers present in the wave are used."""
    keys = {w['key'] for w in wave['workers']}
    out = [c for c in COMPOSITIONS if {c[1], c[2], c[3]} <= keys]
    jetsons = sorted(k for k in keys if k.startswith('jgpu-'))
    out += [(f'{k}/{k}/{k}', k, k, k) for k in jetsons]
    if {'npu-c', 'cpu-c'} <= keys:
        out += [(f'npu-c/{k}/cpu-c', 'npu-c', k, 'cpu-c') for k in jetsons]
    return out


def call_worker(url, body, timeout=None, http_fn=None):
    if timeout is None:
        return 'not_sent_deadline', dict(error='run budget exhausted before this request')
    try:
        return 'ok', (http_fn or http)(url+'/run', body, timeout=timeout)
    except urllib.error.HTTPError as error:
        text = error.read(4000).decode(errors='replace')
        return 'http_error', dict(code=error.code, body=text, error_phase=error_phase(text))
    except Exception as error:
        return 'transport_error', dict(error=repr(error)[:500])


def qualify_composition(session, name, workers, urls, pairs, refs, processor, decode, deadline=None, clock=time.monotonic, http_fn=None):
    """End-to-end per pair with FRESH worker calls: vision and speech branches in parallel on their workers, then
    fusion on the fusion worker with the returned feature and transcript. Every branch reply (data + receipt) is kept;
    a failed branch makes that row incomplete (missing answer), never substituted."""
    from concurrent.futures import ThreadPoolExecutor
    slug = name.replace('/', '_')
    rows = []
    with (session.out/f'composition-{slug}.jsonl').open('x') as handle, ThreadPoolExecutor(max_workers=2) as pool:
        for index, pair in enumerate(pairs):
            row = dict(index=index, question_id=pair['question_id'], split=pair['split'], composition=name, branches={})
            jobs = {}
            for stage in ('vision', 'speech'):
                w = workers[stage]
                body = dict(run_id=session.run, request_id=f'e2e-{slug}-{stage}-{index}', stage=stage,
                            **{k: v for k, v in stage_inputs(stage, w['backend'], pair, refs, processor).items() if k != 'collector_preprocessing'})
                jobs[stage] = pool.submit(call_worker, urls[stage], body, request_timeout(deadline, clock), http_fn)
            for stage, job in jobs.items():
                status, reply = job.result()
                row['branches'][stage] = dict(status=status, **({'data': reply['data'], 'receipt': reply['receipt']} if status == 'ok' else reply))
            v, sp = row['branches']['vision'], row['branches']['speech']
            if v['status'] != 'ok' or sp['status'] != 'ok':
                row['status'] = 'upstream_missing'
            else:
                out = base64.b64decode(sp['data'])
                transcript = decode(out) if workers['speech']['backend'] == 'openvino-npu' else out.decode()
                body = dict(run_id=session.run, request_id=f'e2e-{slug}-fusion-{index}', stage='fusion',
                            inputs=dict(vision=v['data'], speech=base64.b64encode(transcript.encode()).decode()))
                status, reply = call_worker(urls['fusion'], body, request_timeout(deadline, clock), http_fn)
                row['branches']['fusion'] = dict(status=status, **({'data': reply['data'], 'receipt': reply['receipt']} if status == 'ok' else reply))
                row.update(status=status, transcript=transcript)
                if status == 'ok':
                    row['answer'] = json.loads(base64.b64decode(reply['data']))['answers'][0]
            handle.write(json.dumps(row)+'\n')
            rows.append(row)
    return rows


def composition_result(name, rows, workers, created_uids, refs, processor=None, decode=None, malformed=()):
    """Bind each composition row to the pinned manifest and its three worker receipts (stage/backend/device/node/UID,
    request ID, input and output hashes). Only valid rows contribute answers; reasons are counted, rows kept."""
    pairs = expected_pairs()
    by_id = {p['question_id']: (i, p) for i, p in enumerate(pairs)}
    slug = name.replace('/', '_')
    reasons, answers, seen = {}, {p['question_id']: None for p in pairs}, set()

    invalid = []

    def bad(reason, row=None):
        reasons[reason] = reasons.get(reason, 0) + 1
        invalid.append(dict(index=(row or {}).get('index'), question_id=(row or {}).get('question_id'), reason=reason))
    for item in malformed:
        reasons['malformed_row'] = reasons.get('malformed_row', 0) + 1
        invalid.append(dict(item, reason='malformed_row'))
    for row in rows:
        q = row.get('question_id')
        if q not in by_id:
            bad('unexpected_question', row)
            continue
        index, pair = by_id[q]
        if q in seen:
            bad('duplicate_question', row)
            continue
        if row.get('split') != pair['split']:
            bad('wrong_split', row)
            continue
        branches = row.get('branches') or {}
        if any((branches.get(st) or {}).get('status') != 'ok' for st in ('vision', 'speech', 'fusion')):
            bad('branch_error', row)
            continue
        ok = True
        for stage in ('vision', 'speech', 'fusion'):
            w, b = workers[stage], branches[stage]
            r = b['receipt']
            data = base64.b64decode(b['data'])
            if stage == 'fusion':
                expected_input = dict(vision=hashlib.sha256(base64.b64decode(branches['vision']['data'])).hexdigest(),
                                      speech=hashlib.sha256((row.get('transcript') or '').encode()).hexdigest())
            else:
                expected_input = expected_input_sha(stage, w['backend'], pair, refs, processor)
            checks = [(r.get('stage') == stage, 'stage_mismatch'),
                      (device_ok(w['backend'], r) and r.get('backend') == w['backend'] and r.get('node') == w['node']
                       and bool(created_uids.get(w['key'])) and r.get('pod_uid') == created_uids.get(w['key']),
                       'identity_mismatch' if created_uids.get(w['key']) else 'missing_creation_uid'),
                      (r.get('request_id') == f'e2e-{slug}-{stage}-{index}', 'request_id_mismatch'),
                      (r.get('input_sha256') == expected_input, 'input_mismatch'),
                      (hashlib.sha256(data).hexdigest() == r.get('output_sha256'), 'output_hash_mismatch')]
            failed = next((reason for passed, reason in checks if not passed), None)
            if failed:
                bad(failed, row)
                ok = False
                break
        if not ok:
            continue
        if workers['speech']['backend'] != 'openvino-npu' and base64.b64decode(branches['speech']['data']).decode() != row.get('transcript'):
            bad('transcript_mismatch', row)
            continue
        seen.add(q)
        answers[q] = json.loads(base64.b64decode(branches['fusion']['data']))['answers'][0]
    devices = {st: dict(worker=workers[st]['key'], node=workers[st]['node'], backend=workers[st]['backend']) for st in workers}
    return dict(status='executed', rows=len(rows), valid_rows=len(seen), invalid_reasons=reasons, invalid_rows=invalid, devices=devices,
                complete=len(seen) == 60 and not invalid, quality=composition_quality(answers))


def execute(session, wave, export_dir, release):
    if not release_ok(wave, release):
        raise RuntimeError('No coordinator review release for this wave; live execution refused')
    shipped = verify_shipped(wave, session.run)  # before any kubectl call
    session.save('wave.json', wave)
    session.save('source-hashes.json', dict(shipped=shipped, collector={f: sha(HERE/f) for f in ('qualify.py', 'catalog.py')},
                                            http_runtime_frozen=sha(LIVE/'http_runtime.py'), review_release=str(release)))
    session.save('protected-before.json', ownership.protected_snapshot(session, 'protected-before'))
    with baseline.phase(session, 'preflight'):
        ownership.refuse_reused_run(session)
    plan_dir = Path(export_dir)
    with baseline.phase(session, 'cache'):
        session.create('cache', [cache_manifest(session.run)])
        if not session.wait_ready('cache', BUDGETS['cache_ready_s']):
            raise RuntimeError('cache Pod not Ready')
        cache = session.owned_state('cache', 'cache-state')
        tar_path = session.out/'assets.tar'
        manifest = json.loads((plan_dir/'assets-manifest.json').read_text())
        with tarfile.open(tar_path, 'x') as tar:
            tar.add(plan_dir/'assets-manifest.json', 'assets-manifest.json')
            for entry in manifest['files']:
                tar.add(entry['source'], entry['path'])
        with tar_path.open('rb') as source:
            session.call('cache-upload', ['exec', '-i', '-n', NS, session.run+'-cache', '-c', 'worker', '--', 'python3', '-c',
                                          "import tarfile,sys;tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall('/work',filter='data')"],
                         stdin=source, timeout=BUDGETS['upload_timeout_s'])
        cache_url = 'http://'+cache['status']['podIP']+':8080'
    with baseline.phase(session, 'workers'):
        session.create('workers', [config_map(session.run)] + [pod_manifest(session.run, w, cache_url, wave['assets_manifest_sha256'])
                                                               for w in wave['workers']])
    from transformers import Wav2Vec2Processor
    processor = Wav2Vec2Processor.from_pretrained(ASSETS/'model-assets/asr', local_files_only=True)
    pairs, refs = corpus()
    return collect_live(session, wave, pairs, refs, processor)


def pod_verdict(obj, pull_errors, key):
    """('ready'|'failed'|'pending', reason) from an API Pod object; definitive failures exit early.
    ErrImagePull is retried by the kubelet, so it is definitive only after 3 consecutive observations."""
    st = obj.get('status', {})
    if st.get('phase') in ('Failed', 'Succeeded'):
        return 'failed', f"pod phase {st.get('phase')}: {st.get('reason') or ''} {st.get('message') or ''}"[:500]
    pulling = False
    for cs in st.get('initContainerStatuses', []) + st.get('containerStatuses', []):
        state = cs.get('state') or {}
        waiting, terminated = state.get('waiting') or {}, state.get('terminated')
        if waiting.get('reason') in TERMINAL_WAITING:
            return 'failed', f"{waiting['reason']}: {waiting.get('message') or ''}"[:500]
        if waiting.get('reason') == 'ErrImagePull':
            pulling = True
        if terminated:
            return 'failed', f"terminated {terminated.get('reason')} exit {terminated.get('exitCode')}"[:500]
    pull_errors[key] = pull_errors.get(key, 0) + 1 if pulling else 0
    if pull_errors[key] >= 3:
        return 'failed', 'ErrImagePull observed in 3 consecutive polls'
    if any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in st.get('conditions', [])):
        return 'ready', None
    return 'pending', None


TERMINAL_COMPILE = ('compiled', 'compile_failed', 'compile_timeout', 'dependency_unavailable')


def collect_live(session, wave, pairs, refs, processor, started=None, clock=time.monotonic, sleep=time.sleep, http_fn=None, budgets=None):
    """Bounded live collection after all worker Pods were created together.
    1. ONE shared readiness window (poll Pod status via API GET; definitive Pod/container failures exit early; /health
       snapshots saved whenever the bootstrap/compile phase changes).
    2. Collection order: accelerator serving stages (Jetson CUDA, Intel NPU vision) -> CPU stages -> compositions (mixed
       CPU+GPU+NPU first) -> long exact-length NPU speech probe. Ready workers are collected DURING the readiness window
       (a never-ready worker no longer idles the ready ones); a ledger makes each stage/composition attempted at most once. Mobilint compilation is only polled in between and never
       blocks; its terminal result is captured, or 'running_at_deadline' is recorded with the last snapshot.
    3. Every request timeout is min(request cap, remaining budget); the whole collection stops at started+collection_s,
       before Pod lifetime minus cleanup reserve, and partial results are always saved."""
    b = dict(BUDGETS, **(budgets or {}))
    http_fn = http_fn or http
    started = clock() if started is None else started
    deadline = started + b['collection_s']
    results = {w['key']: dict(worker=w) for w in wave['workers']}
    compilers = [w for w in wave['workers'] if w['backend'] == 'mobilint-compile']
    last_phase = {}
    accel = [w for w in wave['workers'] if w['backend'] in ('cuda-torch', 'openvino-npu')]
    cpu = [w for w in wave['workers'] if w['backend'] == 'cpu-torch']
    by_key = {w['key']: w for w in wave['workers']}
    done = set()  # completed-stage/composition ledger
    interrupted = None

    def snapshot(key):
        entry = results[key]
        if not entry.get('url'):
            return None
        try:
            health = http_fn(entry['url']+'/health', timeout=max(1, min(20, deadline-clock())))
        except Exception as error:
            entry['health_error'] = repr(error)[:300]
            return None
        phase = (health.get('bootstrap') or health.get('probe') or {}).get('phase') or health.get('status')
        entry['health'] = health
        if last_phase.get(key) != phase:
            last_phase[key] = phase
            entry['snapshots'] = entry.get('snapshots', 0) + 1
            session.save(f"health-{key}-{entry['snapshots']:03d}.json", dict(at_s=clock()-started, phase=phase, health=health))
        return phase

    def poll_compilers(final=False):
        for w in compilers:
            entry = results[w['key']]
            if entry.get('compile_state') in TERMINAL_COMPILE or not entry.get('url'):
                continue
            phase = snapshot(w['key'])
            if phase in TERMINAL_COMPILE:
                entry['compile_state'] = phase
                if phase == 'compiled':
                    try:
                        reply = http_fn(entry['url']+'/run', dict(stage='artifact'), timeout=max(1, min(600, deadline-clock())))
                        data = base64.b64decode(reply['data'])
                        if hashlib.sha256(data).hexdigest() != (entry['health'].get('probe') or {}).get('mxq_sha256'):
                            raise RuntimeError('MXQ artifact hash mismatch')
                        with (session.out/'vision-static-448.mxq').open('xb') as handle:
                            handle.write(data)
                        entry['artifact_saved'] = True
                    except Exception as error:
                        entry['artifact_error'] = repr(error)[:500]
            elif final:
                entry['compile_state'] = 'running_at_deadline'

    def loaded(key, stage):
        e = results.get(key, {})
        return e.get('deployment') == 'ready' and stage in ((e.get('health') or {}).get('stages') or {})

    def run_stage(w, stage):
        entry = results[w['key']]
        if clock() >= deadline:
            entry.setdefault('skipped', {})[stage] = 'collection deadline'
            return
        if not loaded(w['key'], stage):
            return
        try:
            with baseline.phase(session, f"qualify-{w['key']}-{stage}"):
                entry.setdefault('stages', {})[stage] = qualify_stage(session, w, entry['url'], stage, pairs, refs, processor,
                                                                      deadline=deadline, clock=clock, http_fn=http_fn)
        except Exception as error:  # one stage's collector failure is recorded and does not stop the others
            entry.setdefault('stage_errors', {})[stage] = repr(error)[:1500]

    def serving_stages(group):
        for w in group:
            for stage in w['stages']:
                if (w['backend'] == 'openvino-npu' and stage == 'speech') or ('stage', w['key'], stage) in done or not loaded(w['key'], stage):
                    continue
                done.add(('stage', w['key'], stage))
                poll_compilers()
                run_stage(w, stage)

    decode = lambda ids: processor.decode(__import__('torch').from_numpy(__import__('numpy').load(io.BytesIO(ids))), skip_special_tokens=True)

    def compositions():
        for name, vk, sk, fk in sorted(compositions_for(wave), key=lambda c: (0 if c[0].startswith('npu-c/jgpu-') else 1, c[0])):
            if ('composition', name) in done:
                continue
            poll_compilers()
            if clock() >= deadline:
                done.add(('composition', name))
                results.setdefault('compositions', {})[name] = dict(skipped='collection deadline')
                continue
            if not (loaded(vk, 'vision') and loaded(sk, 'speech') and loaded(fk, 'fusion')):
                continue  # incomplete composition: analysis reports it not_attempted with the component evidence
            done.add(('composition', name))
            with baseline.phase(session, 'composition-'+name.replace('/', '_')):
                try:
                    qualify_composition(session, name, dict(vision=by_key[vk], speech=by_key[sk], fusion=by_key[fk]),
                                        {st: results[k]['url'] for st, k in (('vision', vk), ('speech', sk), ('fusion', fk))},
                                        pairs, refs, processor, decode, deadline=deadline, clock=clock, http_fn=http_fn)
                except Exception as error:
                    results.setdefault('compositions', {})[name] = dict(collector_error=repr(error)[:1500])

    def collect_ready():
        serving_stages(accel + cpu)
        compositions()
    try:
        pending, pull_errors = {w['key'] for w in wave['workers']}, {}
        window_end = min(started + b['ready_window_s'], deadline)
        with baseline.phase(session, 'readiness', window_s=b['ready_window_s']):
            while pending:
                states = session.pod_states()
                for key in sorted(pending):
                    obj, entry = states.get(key), results[key]
                    if obj is None:
                        continue
                    entry.update(pod_uid=obj['metadata'].get('uid'), node=obj.get('spec', {}).get('nodeName'))
                    if obj.get('status', {}).get('podIP'):
                        entry['url'] = f"http://{obj['status']['podIP']}:8080"
                    verdict, reason = pod_verdict(obj, pull_errors, key)
                    snapshot(key)
                    if verdict == 'ready':
                        entry.update(deployment='ready', ready_at_s=clock()-started)
                        pending.discard(key)
                    elif verdict == 'failed':
                        entry.update(deployment='deployment_failed', failure_reason=reason, pod_status=obj.get('status'), failed_at_s=clock()-started)
                        pending.discard(key)
                if not pending or clock() >= window_end:
                    break
                collect_ready()  # ready workers are served while others are still pending
                if clock() >= window_end:
                    break
                sleep(b['poll_s'])
            states = session.pod_states() if pending else {}
            for key in pending:  # window expired: keep the last observed state and phase, never pretend completion
                snapshot(key)
                results[key].update(deployment='not_ready_at_window', pod_status=(states.get(key) or {}).get('status'),
                                    last_phase=last_phase.get(key), window_s=b['ready_window_s'])
        serving_stages(accel + cpu)
        late = [k for k in results if isinstance(results[k], dict) and results[k].get('deployment') == 'not_ready_at_window']
        if late and clock() < deadline:  # one bounded late-readiness re-check; still-running workers keep their running snapshot
            states = session.pod_states()
            for key in late:
                obj = states.get(key)
                if obj and obj.get('status', {}).get('podIP'):
                    results[key]['url'] = f"http://{obj['status']['podIP']}:8080"
                if obj and pod_verdict(obj, pull_errors, key)[0] == 'ready' and snapshot(key):
                    results[key].update(deployment='ready', late_ready_at_s=clock()-started)
            serving_stages(accel + cpu)
        compositions()
        for w in accel:
            if w['backend'] == 'openvino-npu' and 'speech' in w['stages']:
                poll_compilers()
                run_stage(w, 'speech')
        with baseline.phase(session, 'compile-wait'):
            while compilers and clock() < deadline and any(results[w['key']].get('compile_state') not in TERMINAL_COMPILE
                                                           and results[w['key']].get('url') for w in compilers):
                poll_compilers()
                sleep(b['poll_s'])
            poll_compilers(final=True)
    except BaseException as error:
        interrupted = repr(error)[:300]
        raise
    finally:
        if interrupted:  # explicit states for what the interrupt left open, with snapshot references
            for key, w in by_key.items():
                entry = results[key]
                refs_ = [f'health-{key}-{i:03d}.json' for i in range(1, (entry.get('snapshots') or 0) + 1)]
                if 'deployment' not in entry:
                    entry.update(deployment='interrupted_pending', interrupted=interrupted, last_phase=last_phase.get(key), snapshot_files=refs_)
                elif entry['deployment'] == 'ready':
                    left = [st for st in w['stages'] if ('stage', key, st) not in done]
                    if left:
                        entry.update(interrupted_before_collection=left, interrupted=interrupted, snapshot_files=refs_)
        results['budget'] = dict(b, elapsed_s=clock()-started, deadline_reached=clock() >= deadline)
        session.save('collector-results.json', {k: ({kk: vv for kk, vv in v.items() if kk != 'stages'} if isinstance(v, dict) else v)
                                                for k, v in results.items()})
    return results


def load_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else default


def worker_failure(worker, entry, run_status):
    """Category for a worker that produced no stage receipts, from collector/health/run-status evidence (or None)."""
    if entry is None:
        reason = 'run stopped before this worker was reached' + (f": {run_status.get('error')}" if run_status else '')
        return dict(execution='not_attempted', execution_evidence=reason)
    if entry.get('collector_error'):
        return dict(execution='runtime_failed', failure_phase='collector', execution_evidence=entry['collector_error'])
    health = entry.get('health') or {}
    phase = (health.get('bootstrap') or {}).get('phase') or (health.get('probe') or {}).get('phase') or health.get('status')
    evidence = dict(deployment=entry.get('deployment'), pod_status=entry.get('pod_status'), health_phase=phase,
                    failure_reason=entry.get('failure_reason'), snapshots=entry.get('snapshots'))
    if entry.get('compile_state') == 'running_at_deadline':
        return dict(execution='compile_incomplete', failure_phase='compile_running_at_collection_deadline',
                    execution_evidence=dict(evidence, probe=health.get('probe')))
    if entry.get('deployment') == 'not_ready_at_window':
        return dict(execution='deployment_failed', failure_phase=f"not_ready_at_window:{entry.get('last_phase') or phase}",
                    execution_evidence=evidence)
    if phase == 'dependency_unavailable':
        blocker = (health.get('bootstrap') or health.get('probe') or {}).get('blocker')
        return dict(execution='dependency_unavailable', execution_evidence=dict(evidence, blocker=blocker))
    if phase in ('install_failed', 'install_timeout', 'bootstrap_failed', 'preload_failed'):
        return dict(execution='deployment_failed', failure_phase='install' if phase.startswith('install') else phase.split('_')[0],
                    execution_evidence=dict(evidence, events=(health.get('bootstrap') or {}).get('events')))
    if phase in ('compile_failed', 'compile_timeout'):
        return dict(execution='compile_failed', failure_phase=phase, execution_evidence=dict(evidence, probe=health.get('probe')))
    if phase == 'compiled':
        return dict(execution='compiled_not_executed', execution_evidence=dict(evidence, mxq_sha256=(health.get('probe') or {}).get('mxq_sha256')))
    if entry.get('deployment') == 'deployment_failed':
        return dict(execution='deployment_failed', failure_phase='ready', execution_evidence=evidence)
    interrupted = (run_status or {}).get('status') == 'error'
    if interrupted and entry.get('deployment') in (None, 'interrupted_pending'):
        # Pending when the run stopped: not a deployment failure and not "no evidence"; point at the saved snapshots.
        return dict(execution='interrupted_pending', failure_phase='pending_at_interrupt',
                    execution_evidence=dict(evidence, run_error=run_status.get('error'), last_phase=entry.get('last_phase') or phase,
                                            health_error=entry.get('health_error'),
                                            snapshot_files=[f"health-{worker['key']}-{i:03d}.json" for i in range(1, (entry.get('snapshots') or 0) + 1)]))
    return None


def stage_load_failure(worker, stage, entry):
    error = ((entry or {}).get('health') or {}).get('errors', {}).get(stage)
    if not error:
        return None
    if error.get('status') == 'dependency_unavailable':
        return dict(execution='dependency_unavailable', execution_evidence=error)
    if error.get('type') == 'DeviceMismatch':  # the compiled model existed; the runtime/device check (adapter) rejected it
        return dict(execution='runtime_failed', failure_phase='device_verification', execution_evidence=error)
    category = 'compile_failed' if worker['backend'] == 'openvino-npu' else 'runtime_failed'
    return dict(execution=category, failure_phase='load', execution_evidence=error)


def catalog_key(worker, stage):
    backend = 'mobilint-mxq' if worker['backend'] == 'mobilint-compile' else worker['backend']
    return (worker['node'], backend, stage)


def analyze(run_dir, audit=None, out_dir=None):
    """Offline and failure-tolerant: always writes analysis.json + catalog for every catalog row, whatever stopped the run.
    out_dir (a new directory) writes a separate re-analysis and leaves the run directory untouched."""
    run_dir = Path(run_dir)
    target = run_dir if out_dir is None else Path(out_dir)
    if out_dir is not None:
        target.mkdir(parents=True, exist_ok=False)
    wave = json.loads((run_dir/'wave.json').read_text())
    created = {r['name']: r['uid'] for f in run_dir.glob('*.created.json') for r in load_json(f, {}).get('confirmed', [])}
    collector = load_json(run_dir/'collector-results.json')
    run_status = load_json(run_dir/'run-status.json')
    pairs, refs = corpus()
    from transformers import Wav2Vec2Processor
    processor = Wav2Vec2Processor.from_pretrained(ASSETS/'model-assets/asr', local_files_only=True)
    decode = lambda ids: processor.decode(__import__('torch').from_numpy(__import__('numpy').load(io.BytesIO(ids))), skip_special_tokens=True)
    by_worker = {}
    for worker in wave['workers']:
        entry = (collector or {}).get(worker['key'])
        if entry is not None and not entry.get('health'):
            entry = dict(entry, health=load_json(run_dir/f"health-{worker['key']}.json"))
        stage_names = worker['stages'] or (['vision'] if worker['backend'] == 'mobilint-compile' else [])
        for stage in stage_names:
            path = run_dir/f"receipts-{worker['key']}-{stage}.jsonl"
            rows, malformed = read_jsonl(path) if path.exists() else ([], [])
            if rows or malformed:
                result = stage_result(worker, stage, rows, created.get(run_dir.name+'-'+worker['key']), refs, decode, processor, malformed)
            else:
                result = (worker_failure(worker, entry, run_status) or stage_load_failure(worker, stage, entry)
                          or (dict(execution='not_attempted', execution_evidence=dict(note='ready; interrupted before collection',
                                                                                       run_error=run_status.get('error')))
                              if (entry or {}).get('deployment') == 'ready' and (run_status or {}).get('status') == 'error' else None)
                          or dict(execution='not_attempted', execution_evidence='no receipts and no failure evidence'))
            result.update(worker=worker['key'], recipe=worker['recipe'])
            by_worker[(worker['key'], stage)] = (worker, result)
    results = {}
    for (key, stage), (worker, result) in by_worker.items():
        ck = catalog_key(worker, stage)
        current = results.get(ck)
        rank = lambda r: EXECUTION_ORDER.index(r['execution']) if r['execution'] in EXECUTION_ORDER else len(EXECUTION_ORDER)
        if current is None or rank(result) < rank(current):
            results[ck] = dict(result, by_recipe=dict((current or {}).get('by_recipe', {})))
        results[ck].setdefault('by_recipe', {})[worker['recipe']] = {k: v for k, v in result.items()
                                                                      if k not in ('receipts', 'partial_receipts', 'by_recipe')}
    compositions = {}
    by_key = {w['key']: w for w in wave['workers']}
    created_uids = {w['key']: created.get(run_dir.name+'-'+w['key']) for w in wave['workers']}
    for name, vk, sk, fk in compositions_for(wave):
        path = run_dir/f"composition-{name.replace('/', '_')}.jsonl"
        workers_of = dict(vision=by_key[vk], speech=by_key[sk], fusion=by_key[fk])
        stages_keys = [catalog_key(by_key[vk], 'vision'), catalog_key(by_key[sk], 'speech'), catalog_key(by_key[fk], 'fusion')]
        if not path.exists():
            compositions[name] = dict(status='not_attempted', stages=stages_keys,
                                      component_execution={st: by_worker.get((workers_of[st]['key'], st), (None, {}))[1].get('execution')
                                                           for st in workers_of},
                                      reason='a component stage was not ready/loaded or the run stopped' + (f": {run_status.get('error')}" if run_status else ''))
            continue
        rows, malformed = read_jsonl(path)
        compositions[name] = dict(composition_result(name, rows, workers_of, created_uids, refs, processor, decode, malformed), stages=stages_keys)
    for comp in compositions.values():
        for key in comp.get('stages', []) if comp.get('quality') else []:
            if key in results and results[key]['execution'] == 'executed':
                results[key].setdefault('quality_by_composition', []).append(comp['quality']['passed'])
    for result in results.values():
        quality = result.get('quality_by_composition')
        result['quality'] = 'not_evaluated' if not quality else ('passed' if any(quality) else 'failed')
    cat = catalog.build(audit, {k: dict(v) for k, v in results.items()})
    out = dict(run=run_dir.name, qmin=QMIN, parity_rule=RULE, run_status=run_status, collector_results_present=collector is not None,
               stage_results={'|'.join(k): {kk: vv for kk, vv in v.items() if kk not in ('receipts', 'partial_receipts')} for k, v in results.items()},
               compositions=compositions, cost_table=cost_table(results), main_plan_candidates=main_plan_candidates(compositions, results),
               note='device execution, CPU-reference parity and the Qmin service gate are separate; Qmin failure keeps a '
                    'working accelerator out of every main experiment profile; partial coverage never qualifies')
    if out_dir is not None:
        out['reanalysis_of'] = str(run_dir)
        out['analyzer_sources'] = {f: sha(HERE/f) for f in ('qualify.py', 'catalog.py')}
    with (target/'analysis.json').open('x') as handle:
        json.dump(out, handle, indent=1, default=str)
    catalog.write(cat, target)
    return out


def wave_run(run_dir):
    return Path(run_dir).name


# ------------------------------------------------------------------ analysis (offline, pure)
def device_ok(backend, receipt):
    if backend == 'cpu-torch':
        return receipt.get('device') == 'cpu'
    if backend == 'cuda-torch':
        return str(receipt.get('device', '')).startswith('cuda') and str(receipt['timing'].get('output_device', '')).startswith('cuda')
    if backend == 'openvino-npu':
        return receipt.get('device') == 'NPU' and receipt['timing'].get('execution_devices') == ['NPU']
    return False


EXECUTION_ORDER = ('executed', 'partial', 'runtime_failed', 'compile_failed', 'compile_incomplete', 'compiled_not_executed', 'deployment_failed',
                   'dependency_unavailable', 'interrupted_pending', 'not_attempted')


def expected_pairs():
    """Pinned manifest: exactly 60 unique question IDs, 20 calibration / 40 evaluation."""
    pairs = json.loads(LOAD_PAIRS.read_text())['pairs']
    ids = [p['question_id'] for p in pairs]
    splits = [p['split'] for p in pairs]
    if len(ids) != 60 or len(set(ids)) != 60 or splits.count('calibration') != 20 or splits.count('evaluation') != 40:
        raise ValueError('pinned pair manifest is not 60 unique / 20 calibration / 40 evaluation')
    return pairs


def stage_result(worker, stage, rows, created_uid, refs, decode=None, processor=None, malformed=()):
    """Bind every row to the pinned manifest and to this worker's stage/node/UID/backend/device/request/input/output.
    Only full unique coverage of all 60 valid rows is `executed`; otherwise `partial` (some valid) or `runtime_failed`.
    Invalid and duplicate rows are kept with per-reason counts; nothing is silently discarded."""
    import numpy as np
    pairs = expected_pairs()
    by_id = {p['question_id']: (i, p) for i, p in enumerate(pairs)}
    reasons, invalid, valid, seen = {}, [], {}, set()

    def bad(row, reason):
        reasons[reason] = reasons.get(reason, 0) + 1
        invalid.append(dict(index=row.get('index'), question_id=row.get('question_id'), reason=reason, status=row.get('status')))
    for item in malformed:
        reasons['malformed_row'] = reasons.get('malformed_row', 0) + 1
        invalid.append(dict(item, reason='malformed_row'))
    phases = {}
    for row in rows:
        if row.get('status') == 'not_sent_deadline':
            bad(row, 'not_sent_deadline')
            continue
        if row.get('status') != 'ok':
            phase = row.get('error_phase') or 'unknown'
            phases[phase] = phases.get(phase, 0) + 1
            bad(row, 'request_error' if phase in ('inference', 'unknown') else f'request_error_{phase}')
            continue
        q, receipt = row.get('question_id'), row.get('receipt') or {}
        if q not in by_id:
            bad(row, 'unexpected_question')
            continue
        index, pair = by_id[q]
        if q in seen:
            bad(row, 'duplicate_question')
            continue
        if row.get('split') != pair['split']:
            bad(row, 'wrong_split')
            continue
        if receipt.get('stage') != stage:
            bad(row, 'stage_mismatch')
            continue
        if not (device_ok(worker['backend'], receipt) and receipt.get('backend') == worker['backend'] and receipt.get('node') == worker['node']
                and created_uid and receipt.get('pod_uid') == created_uid):  # None == None is never identity proof
            bad(row, 'identity_mismatch' if created_uid else 'missing_creation_uid')
            continue
        if receipt.get('request_id') != f"{worker['key']}-{stage}-{index}":
            bad(row, 'request_id_mismatch')
            continue
        if receipt.get('input_sha256') != expected_input_sha(stage, worker['backend'], pair, refs, processor):
            bad(row, 'input_mismatch')
            continue
        try:
            out = base64.b64decode(row['data'], validate=True)
        except Exception:
            bad(row, 'output_hash_mismatch')
            continue
        if hashlib.sha256(out).hexdigest() != receipt.get('output_sha256'):
            bad(row, 'output_hash_mismatch')
            continue
        seen.add(q)
        valid[q] = (row, out)
    coverage = dict(expected=60, valid_unique=len(valid), missing=60 - len(valid), rows=len(rows), invalid=len(invalid),
                    by_split={s: sum(1 for q in valid if by_id[q][1]['split'] == s) for s in ('calibration', 'evaluation')})
    matches = []
    for q, (row, out) in valid.items():
        ref = refs[q]
        if stage == 'vision':
            got = np.load(io.BytesIO(out), allow_pickle=False)
            matches.append(bool(np.allclose(got, np.load(REFERENCE/f'{q}.npy'), rtol=RULE['feature_rtol'], atol=RULE['feature_atol'])))
        elif stage == 'speech':
            text = decode(out) if worker['backend'] == 'openvino-npu' else out.decode()
            matches.append(text == ref['transcript'])
        else:
            got = json.loads(out)
            matches.append(got['answer_ids'] == ref['answer_ids'] and max(abs(a-b) for a, b in zip(got['scores'], ref['scores'])) <= RULE['score_atol'])
    full = len(valid) == 60 and not invalid  # exact: the full valid set and NO invalid/duplicate/unexpected extras
    prep = sum(v for k, v in phases.items() if k in ('prepare', 'compile'))
    if full:
        execution = 'executed'
    elif valid:
        execution = 'partial'
    elif prep:
        execution = 'compile_failed'
    else:
        execution = 'runtime_failed' if (rows or malformed) else 'not_attempted'
    parity = ('passed' if all(matches) else 'failed') if full else ('failed' if valid and not all(matches) else 'not_evaluated')
    return dict(execution=execution, parity=parity, coverage=coverage, invalid_reasons=reasons, invalid_rows=invalid,
                error_phases=phases, failure_phase='compile' if execution == 'compile_failed' else
                ('device_verification' if not valid and phases.get('device_verification') else None),
                request_errors=sum(phases.values()), parity_matches=sum(matches), parity_checked=len(matches),
                receipts=[valid[q][0]['receipt'] for q in valid] if full else [],
                partial_receipts=[] if full else [valid[q][0]['receipt'] for q in valid])


def composition_quality(answers):
    """answers = {question_id: answer or None}. Per split: expected/answered/missing/coverage, answered-only and
    zero-imputed full-cohort means, plus the exact evaluator output. Gate: zero-imputed >= Qmin on cal AND eval."""
    gt = json.loads(GT_PAIRS.read_text())['pairs']
    expected = {p['question_id'] for p in gt}
    rows = [dict(question_id=q, answer=a) for q, a in answers.items() if a is not None and q in expected]
    scored = vqa_eval.score(gt, rows)
    splits = {}
    for name in ('calibration', 'evaluation'):
        sp = scored['splits'][name]
        splits[name] = dict(expected=sp['questions'], answered=sp['answered'], missing=sp['missing'],
                            coverage=sp['answered']/sp['questions'] if sp['questions'] else None,
                            answered_mean=sp['answered_mean'], zero_imputed_mean=sp['all_questions_zero_imputed_mean'])
    values = {k: v['zero_imputed_mean'] for k, v in splits.items()}
    return dict(qmin=QMIN, values=values, splits=splits, passed=all(v >= QMIN for v in values.values()),
                unexpected_answers=sorted(q for q in answers if q not in expected),
                statistic='official VQA; gate on zero-imputed full cohort, cal AND eval; answered-only reported alongside',
                evaluator=vqa_eval.provenance(), evaluator_output=scored)


def cost_table(results):
    """Only successful real receipts; never synthetic, never relabelled across backends."""
    table = {}
    for (node, backend, stage), result in results.items():
        if result.get('execution') != 'executed':
            continue
        for receipt in result['receipts']:
            if receipt['backend'] != backend or receipt['node'] != node:
                raise ValueError('receipt identity does not match its cost-table key')
        walls = [r['worker_wall_ms'] for r in result['receipts']]
        compute = [r['timing'].get('device_compute_ms', r['timing'].get('device_infer_ms')) for r in result['receipts']]
        q = lambda v: statistics.quantiles(v, n=4)[2] if len(v) > 1 else v[0]
        table[f'{node}|{backend}|{stage}'] = dict(n=len(walls), worker_wall_ms_p50=statistics.median(walls), worker_wall_ms_p75=q(walls),
                                                  device_ms_p50=statistics.median(compute), device_ms_p75=q(compute),
                                                  boundary='worker-internal wall per request; HTTP/transport excluded; diagnostic fixture, not load')
    return table


def main_plan_candidates(compositions, results):
    """Only compositions that were executed with every stage executed + parity passed and quality passed."""
    out = []
    for name, comp in compositions.items():
        if comp.get('status') != 'executed' or not comp.get('complete') or not comp.get('stages') or not comp.get('quality'):
            continue
        stages_ok = all(results.get(k, {}).get('execution') == 'executed' and results[k].get('parity') == 'passed' for k in comp['stages'])
        if stages_ok and comp['quality']['passed']:
            out.append(name)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description='Heterogeneous VQA qualification (plan / execute / analyze).')
    sub = ap.add_subparsers(dest='mode', required=True)
    p = sub.add_parser('plan', help='write wave.json, assets manifest and the full catalog; no kubectl')
    p.add_argument('--export', required=True)
    p.add_argument('--deps', action='append', required=True)
    p.add_argument('--out', default=None)
    e = sub.add_parser('execute', help='supervised live wave; refused without a coordinator review release')
    e.add_argument('--plan', required=True)
    e.add_argument('--run-id', required=True)
    e.add_argument('--review-release', required=True)
    e.add_argument('--output-root', default=str(OUT_ROOT))
    a = sub.add_parser('analyze', help='offline analysis of a collected run directory')
    a.add_argument('--run', required=True)
    a.add_argument('--out-dir', help='new directory for a separate re-analysis (the run directory is left untouched)')
    args = ap.parse_args(argv)
    if args.mode == 'plan':
        wave = plan(args.out or OUT_ROOT/f'plan-{utc()}', args.export, args.deps)
        print(json.dumps(dict(workers=[w['key'] for w in wave['workers']], asset_bytes=wave['asset_bytes'],
                              assets_manifest_sha256=wave['assets_manifest_sha256'], not_created=[(r['node'], r['family'], r['status']) for r in wave['not_created']]), indent=1))
        return 0
    if args.mode == 'analyze':
        out = analyze(args.run, out_dir=args.out_dir)
        print(json.dumps({k: out[k] for k in ('main_plan_candidates', 'compositions')}, indent=1, default=str)[:4000])
        return 0
    wave = json.loads((Path(args.plan)/'wave.json').read_text())
    if not release_ok(wave, args.review_release):
        print('refused: no coordinator review release naming assets_manifest_sha256 '+wave['assets_manifest_sha256'], file=sys.stderr)
        return 2
    try:
        verify_shipped(wave, args.run_id)
    except RuntimeError as error:
        print(f'refused: {error}', file=sys.stderr)
        return 2
    session = HetSession(args.output_root, args.run_id, wave)
    status, error = 'error', None
    try:
        execute(session, wave, args.plan, args.review_release)
        status = 'collected'
    except BaseException as exc:
        error = repr(exc)
        raise
    finally:
        remaining, errors = ownership.cleanup(session)
        diff = session.out/'protected-diff.json'
        changes = json.loads(diff.read_text())['changes'] if diff.exists() else None
        session.save('run-status.json', dict(status=status, error=error, cleanup_remaining=remaining, cleanup_errors=errors,
                                             protected_changes=changes, audit='complete' if changes == [] and remaining == 0 else 'partial'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
