"""Preserve the frozen E2 calibration and policies at the common HTTP boundary."""

import hashlib
import json
from pathlib import Path

import dynamic_client
import probe
from policy_runner import readonly


def load_e2_contract(directory):
    directory=Path(directory)
    selection_bytes=(directory/'selection.json').read_bytes()
    selection=json.loads(selection_bytes)
    run=json.loads((directory/'run.json').read_text())
    workers=json.loads((directory/'workers.json').read_text())['items']
    cm=next(item for item in workers if item['kind']=='ConfigMap')['data']
    for module in [dynamic_client,probe]:
        name=Path(module.__file__).name
        digest=hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
        if digest!=run['source_sha256'][name] or hashlib.sha256(cm[name].encode()).hexdigest()!=digest:
            raise ValueError(f'E2 source contract changed: {name}')
    for filename,digest in selection['calibration_log_sha256'].items():
        if Path(filename).name!=filename or hashlib.sha256((directory/filename).read_bytes()).hexdigest()!=digest:
            raise ValueError(f'E2 calibration evidence changed: {filename}')
    if run['design']['horizon_requests']!=4 or set(selection['profiles'])!={'arm','intel_npu'}:
        raise ValueError('This bridge preserves the two-worker E2 H4 contract only')
    digest=hashlib.sha256(selection_bytes).hexdigest()
    profiles={}
    for source,costs in selection['profiles'].items():
        profiles[source]={'id':f"e2-{run['run_id']}-{source}-{digest}",
            'provenance':f"Historical E2 dog calibration; selection SHA256 {digest}; service-object preparation H4 policy",
            'input_id':'dog','source':source,'model_sha256':probe.MODEL_SHA256,
            'candidates':[['arm','intel_npu'] for _ in range(3)],
            'cost_ms':{**costs,'service_prepare':{'arm':None,'intel_npu':selection['preparation_ms']}}}
    return {'version':1,'run_id':run['run_id'],'selection_sha256':digest,
            'model_sha256':probe.MODEL_SHA256,'profiles':profiles,
            'initial_plan':selection['initial_plan'],'preparation_ms':selection['preparation_ms'],
            'source_sha256':{name:run['source_sha256'][name] for name in ['dynamic_client.py','probe.py']},
            'calibration_log_sha256':selection['calibration_log_sha256'],
            'historical_targets':{a:{k:t[k] for k in ['node','backend']} for a,t in run['targets'].items()}}


class E2Policy:
    """One stateful instance per trajectory; abort the trajectory after a failed attempt."""

    def __init__(self,name,contract):
        if name not in ['fixed','warm_dag','ready_dag','ready_greedy','credit_hysteresis']:
            raise ValueError('Unknown E2 policy')
        self.name=name
        self.profiles=readonly(contract['profiles'])
        self.selection={'initial_plan':list(contract['initial_plan']),
                        'preparation_ms':contract['preparation_ms']}
        self.state={}
        self.information={}

    def __call__(self,snapshot):
        source=snapshot['request']['source']
        if snapshot['profile']!=self.profiles[source]:
            raise ValueError('E2 policy requires its unchanged calibrated profile and common candidates')
        costs=snapshot['profile']['cost_ms']
        plan,self.state,self.information=dynamic_client.choose(
            self.name,costs,self.selection,snapshot['workers']['intel_npu']['service_object_ready'],self.state,source)
        return list(plan)


def task_fastest(snapshot):
    """Task-only minimum among the supplied common candidates; lexical ties."""
    profile=snapshot['profile']
    if any(profile['cost_ms']['task'][f'{i}|{a}'] is None
           for i,aliases in enumerate(profile['candidates']) for a in aliases):
        raise ValueError('Task-fastest requires measured task costs for every common candidate')
    return [min(aliases,key=lambda a:(profile['cost_ms']['task'][f'{i}|{a}'],a))
            for i,aliases in enumerate(profile['candidates'])]
