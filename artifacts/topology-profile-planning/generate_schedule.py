"""Planning-only allocation; does not contact Kubernetes or run workloads.

Run: rtk proxy uv run --offline --with numpy --with pandas python artifacts/topology-profile-planning/generate_schedule.py
Output paths must not exist. Use a new directory for a revised plan.
"""
import hashlib
import importlib.util
import json
import platform
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
SKILL = Path('/home/jinuk/.codex/skills/experimental-design/scripts/randomization.py')
SEED = 20261019
POLICIES = ['resource', 'topology_only', 'profile_only', 'topology_profile', 'task_fastest', 'data_greedy']
REGIMES = ['native', 'server_cap20', 'server_cap5']


def main():
    output = ROOT / 'pilot-schedule.csv'
    metadata = ROOT / 'schedule-manifest.json'
    if output.exists() or metadata.exists():
        raise FileExistsError('Preserve the previous allocation; use a new output directory')
    spec = importlib.util.spec_from_file_location('allocation', SKILL)
    allocation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(allocation)
    rows = []
    for block in range(1, 4):
        regime_seed = SEED + block * 100
        regimes = allocation.assign_factorial_runs(pd.DataFrame({'regime': REGIMES}), seed=regime_seed)
        for network_index, regime in enumerate(regimes['regime'], 1):
            policy_seed = regime_seed + network_index
            order = allocation.assign_factorial_runs(pd.DataFrame({'policy': POLICIES}), seed=policy_seed)
            for policy_index, policy in enumerate(order['policy'], 1):
                rows.append(dict(slot=len(rows) + 1, block=f'b{block:02d}', sensors='split',
                                 network_order=network_index, regime=regime, policy_order=policy_index,
                                 policy=policy, warmup_requests=5, evaluation_requests=20,
                                 regime_seed=regime_seed, policy_seed=policy_seed,
                                 execution_group='PENDING_PLACEMENT', status='PLANNED_NOT_EXECUTED'))
    frame = pd.DataFrame(rows)
    assert len(frame) == 54
    assert frame.groupby(['block', 'regime'])['policy'].nunique().eq(6).all()
    assert frame.groupby(['regime', 'policy']).size().eq(3).all()
    with output.open('x') as handle:
        frame.to_csv(handle, index=False)
    report = dict(status='PLANNED_NOT_EXECUTED', seed=SEED, block_count=3,
                  policy_slots=len(frame), maximum_evaluation_requests=1080, maximum_warmup_requests=270,
                  scope='Exploratory pilot budget; no sample-size or power guarantee',
                  randomization='Network order within block; policy order within network; shared plans resolved later',
                  python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__,
                  generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  allocation_skill_path=str(SKILL), allocation_skill_sha256=hashlib.sha256(SKILL.read_bytes()).hexdigest(),
                  csv_sha256=hashlib.sha256(output.read_bytes()).hexdigest())
    with metadata.open('x') as handle:
        json.dump(report, handle, indent=2)
        handle.write('\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
