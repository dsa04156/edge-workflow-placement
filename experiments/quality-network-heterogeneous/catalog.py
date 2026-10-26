"""Full heterogeneous qualification catalog: every observed node x device family x VQA stage, never filtered.

Source of the observed inventory: the read-only audit projection .scratch/quality-network-heterogeneous/
node-inventory.json (2026-10-23T07:40Z, 12 nodes). Each row keeps separate, independent fields so that no
step is conflated with another:
  availability : registered | occupied | node_unavailable              (Kubernetes state at observation)
  dependency   : available | unavailable | not_checked (+ evidence)   (runtime/toolchain obtainability)
  execution    : not_attempted | dependency_unavailable | export_failed | compile_failed | compile_incomplete | compiled_not_executed |
                 deployment_failed | runtime_failed | interrupted_pending | partial (some valid receipts, never qualified) |
                 executed (all 60 bound); deployment_blocker (separate field) marks a node held back pending revalidation
  parity       : not_evaluated | failed | passed                      (vs frozen CPU reference, all 60 pairs)
  quality      : not_evaluated | failed | passed                      (official VQA, Qmin 0.40, cal AND eval)
`status` is a single summary label taken from the first blocking field. Only rows with real receipts can be
`executed`; nothing here is inferred from node names or from another model's smoke test.
"""
import csv
import io
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
AUDIT = ROOT/'.scratch/quality-network-heterogeneous/node-inventory.json'
EVIDENCE = '.scratch/quality-network-heterogeneous/raw'
STAGES = ('vision', 'speech', 'fusion')
QMIN = 0.40

FAMILY = {  # resource key -> (family, backend, dependency, dependency evidence)
    'nvidia.com/gpu': ('nvidia-server-gpu', 'cuda-torch', 'available',
                       'torch 2.9.1+cu130 aarch64 wheels (GB10, e0) / x86_64 cu130 wheels from download.pytorch.org; runtimeClass handler nvidia'),
    'nvidia.com/gpu.shared': ('nvidia-jetson-gpu', 'cuda-torch', 'not_checked',
                              'one driver-matched recipe per node from observed driver evidence (in-Pod gate kept): cu130-aarch64-cp311 on '
                              'node-inventory-python (CUDA>=13 driver; torch 2.9.1+cu130 / torchvision 0.24.1 official pair) or jp61-aarch64-cp310 on '
                              'nvcr.io/nvidia/l4t-jetpack@sha256:34ccf0f3 (12.x driver; held after run 2 torchvision op-registration failure)'),
    'npu.intel.com/accel': ('intel-npu', 'openvino-npu', 'available',
                            'image ra-npu-intel@sha256:725d604c (OpenVINO 2026.3.1, present on node) + pillow 11.3.0 cp312'),
    'hailo.ai/h8': ('hailo-8', 'hailort-hef', 'unavailable',
                    f'{EVIDENCE}/toolchain-readme-evidence.json: Hailo-8 needs Dataflow Compiler v3.x, customer access only; '
                    f'HailoRT 4.23.0 runtime images exist ({EVIDENCE}/registry-10.254.192.47.json) but no HEF for these models'),
    'mobilint.com/npu': ('mobilint', 'mobilint-mxq', 'unavailable',
                         f'run qnh-20261023t090355 health-mblt-compile-001.json: the public docker.io/mobilint/qbcompiler:1.4-cpu-ubuntu22.04 '
                         f'image starts but has no qbcompiler installed ({EVIDENCE}/mobilint-qbcompiler-dockerhub-1.4.json); the official '
                         f'tutorial says the wheel needs a Download Center account (not created); qbruntime 1.4.0 runtime images exist; '
                         f'no MXQ for these models; the identical probe is not repeated'),
    'rockchip.com/rk3399pro': ('rockchip-rk3399pro', 'rknn', 'available',
                               f'{EVIDENCE}/toolchain-readme-evidence.json: RKNN-Toolkit 1.7.5 (RK3399Pro) public GitHub release'),
}
# Derived-artifact facts from the local export (export-*/manifest.json); compile on the device is still required.
EXPORT_FACTS = {('openvino-npu', 'fusion'): ('export_failed', 'fusion-dynamic.onnx traced export fails for other token lengths (ORT ScatterND)')}
# Observed in run qnh-20261023t090355 (coordinator: no heavy installs here until storage/readiness are positively revalidated).
RUN1 = 'artifacts/quality-network-heterogeneous/qnh-20261023t090355'
DEPLOYMENT_BLOCKERS = {
    ('etri-dev0007-jetagx', 'cuda-torch'): f'pending storage revalidation: driver 13020 (CUDA13 recipe would match) but both Pods were Evicted for '
                                           f'node ephemeral-storage (available 7829728Ki < threshold 8670909579) during cu130 install / l4t pull; '
                                           f'{RUN1}/health-jgpu-dev0007-cu130-003.json, .scratch/quality-network-heterogeneous/live-extra/snapshot-091807.json',
}
# dev0007 root fs 13.49 GiB free at 09:49Z (live-extra/filesystem-summary-0949.json): below the 13Gi cu130 budget plus image
# and reserve, so it stays blocked. dev0009 was positively revalidated (live-extra/dev0009-revalidation-0954.json) and is a
# wave candidate again; its run-1 node-Unknown evidence stays in that run.
RUN2 = 'artifacts/quality-network-heterogeneous/qnh-20261023t095013'
RECIPE_FAILURES = {  # prior-run recipe failures held from unchanged retry (pending repair; not hardware unsupported)
    ('etri-dev0001-jetorn', 'cuda-torch'): dict(
        execution='runtime_failed', failure_phase='load', evidence_run=RUN2,
        execution_evidence=f'jp61-aarch64-cp310 (nv24.08 torch 2.5.0a0 + PyPI torchvision 0.20.0): sysroot preload and torch import succeeded; '
                           f'vision/fusion: RuntimeError operator torchvision::nms does not exist; speech: ImportError Wav2Vec2ForCTC '
                           f'(cascade suspected, unproven); {RUN2}/health-jgpu-dev0001-jp61-002.json. Recipe dependency/ABI failure pending '
                           f'repair; not retried unchanged.'),
}
SERVING = {'openvino-npu': {'vision'}, 'cuda-torch': set(STAGES), 'cpu-torch': set(STAGES)}


def load_audit(path=AUDIT):
    return json.loads(Path(path).read_text())


def rows_for(node, cpu_family):
    out = []
    base = dict(node=node['node'], arch=node['arch'], role=node['role'], ready=node['ready'])
    unavailable = node['ready'] != 'True' or node.get('unschedulable')
    for stage in STAGES:
        out.append(dict(base, family=cpu_family, resource=None, backend='cpu-torch', stage=stage, units_registered=None,
                        units_in_use=None, availability='node_unavailable' if unavailable else 'registered',
                        dependency='available', dependency_evidence='torch 2.8.0+cpu wheelhouse (frozen reference runtime)'))
    for key, units in node['accelerators_allocatable'].items():
        if key not in FAMILY:
            raise ValueError(f'Unclassified accelerator resource {key} on {node["node"]}; refusing to drop it')
        family, backend, dependency, evidence = FAMILY[key]
        used = node['accelerators_requested_by_running_or_pending_pods'].get(key, 0)
        availability = 'node_unavailable' if unavailable else ('occupied' if used >= int(units) else 'registered')
        for stage in STAGES:
            row = dict(base, family=family, resource=key, backend=backend, stage=stage, units_registered=int(units), units_in_use=used,
                       availability=availability, dependency=dependency, dependency_evidence=evidence)
            if (node['node'], backend) in DEPLOYMENT_BLOCKERS:
                row['deployment_blocker'] = DEPLOYMENT_BLOCKERS[(node['node'], backend)]
            held = RECIPE_FAILURES.get((node['node'], backend))
            if held and stage in SERVING.get(backend, ()):
                row.update(held)
            fact = EXPORT_FACTS.get((backend, stage))
            if fact:
                row.update(execution=fact[0], execution_evidence=fact[1])
            elif backend in SERVING and stage not in SERVING[backend]:
                row.update(execution='not_attempted', execution_evidence='separate compilation probe (exact-length reshape) in the live wave')
            out.append(row)
    return out


def build(audit=None, results=None):
    """results: {(node, backend, stage): {execution, parity, quality, evidence}} from analyze; absent -> not_attempted."""
    audit = audit or load_audit()
    rows = []
    for node in audit['nodes']:
        rows += rows_for(node, 'cpu-'+node['arch'])
    for row in rows:
        row.setdefault('execution', 'not_attempted')
        row.setdefault('parity', 'not_evaluated')
        row.setdefault('quality', 'not_evaluated')
        result = (results or {}).get((row['node'], row['backend'], row['stage']))
        if result:
            if result.get('execution') == 'executed' and not result.get('receipts'):
                raise ValueError(f'{row["node"]}/{row["backend"]}/{row["stage"]}: executed without real receipts')
            row.update({k: v for k, v in result.items() if k in ('execution', 'parity', 'quality', 'execution_evidence', 'failure_phase',
                                                                 'coverage', 'invalid_reasons', 'by_recipe', 'receipts', 'parity_evidence',
                                                                 'quality_evidence')})
        row['status'] = summary(row)
    return dict(observed=audit['source_header'].get('started_utc') if 'source_header' in audit else None, qmin=QMIN,
                nodes=len(audit['nodes']), rows=rows)


def summary(row):
    if row['availability'] in ('node_unavailable', 'occupied'):
        return row['availability']
    if row['dependency'] == 'unavailable':
        return 'dependency_unavailable'
    if row.get('deployment_blocker') and row['execution'] == 'not_attempted':
        return 'deployment_blocked'
    if row['execution'] != 'executed':
        return row['execution']
    if row['parity'] == 'failed':
        return 'parity_failed'
    if row['quality'] == 'failed':
        return 'quality_failed'
    if row['quality'] == 'passed':
        return 'quality_passed'
    return 'executed'


def to_csv(catalog):
    fields = ['node', 'arch', 'role', 'ready', 'family', 'resource', 'backend', 'stage', 'units_registered', 'units_in_use', 'availability',
              'dependency', 'execution', 'parity', 'quality', 'status', 'deployment_blocker', 'dependency_evidence']
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(catalog['rows'])
    return out.getvalue()


def write(catalog, out_dir):
    out_dir = Path(out_dir)
    with (out_dir/'catalog.json').open('x') as handle:
        json.dump(catalog, handle, indent=1)
    with (out_dir/'catalog.csv').open('x') as handle:
        handle.write(to_csv(catalog))
