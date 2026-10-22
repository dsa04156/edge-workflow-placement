"""Post-hoc cost-table diagnostic, not another measured policy experiment."""
import argparse
import hashlib
import json
from pathlib import Path

from budget_placement import validate_placement
from placement_policies import prepare


def analyze(run, output):
    implementations = {}
    for current, frozen in [
        (Path(prepare.__file__), run/'planner-source.py'),
        (Path(__file__).with_name('budget_placement.py'), run/'collector-source/budget_placement.py'),
        (Path(__file__).with_name('placement_policies.py'), run/'collector-source/placement_policies.py'),
    ]:
        if current.read_bytes() != frozen.read_bytes():
            raise ValueError('Planner implementation differs from frozen run: '+str(current))
        implementations[current.name] = hashlib.sha256(current.read_bytes()).hexdigest()
    plans = json.loads((run/'plans-frozen.json').read_text())
    results = {}
    for condition, policies in plans.items():
        path = run/('profile-'+condition+'.json')
        profile = json.loads(path.read_text())
        baseline = policies['greedy-swap']['placement']
        candidates = [dict(baseline)]
        for task in profile['tasks']:
            for node in task['candidates']:
                candidate = dict(baseline, **{task['id']: node})
                try:
                    validate_placement(profile, candidate)
                except ValueError:
                    continue
                if candidate not in candidates:
                    candidates.append(candidate)
        best = min(candidates, key=lambda p: (
            prepare.cost(profile, p, 'topology_profile'), tuple(sorted(p.items()))))
        results[condition] = dict(
            profile_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            baseline=baseline, baseline_proxy_ms=prepare.cost(profile, baseline, 'topology_profile'),
            feasible_candidates=len(candidates), selected=best,
            selected_proxy_ms=prepare.cost(profile, best, 'topology_profile'),
            exact=policies['ours']['placement'],
            same_as_exact=best == policies['ours']['placement'])
    evidence = dict(
        source_run=str(run.resolve()),
        analyzer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        implementations_sha256=implementations,
        source_plans_sha256=hashlib.sha256((run/'plans-frozen.json').read_bytes()).hexdigest(),
        scope='post-hoc one-task relocation after frozen one-pair swap; cost table only; '
              'no new AI execution, no independent observation, not preregistered policy performance',
        conditions=results)
    with output.open('x') as handle:
        json.dump(evidence, handle, indent=2)
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('run', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.run, args.out), indent=2))
