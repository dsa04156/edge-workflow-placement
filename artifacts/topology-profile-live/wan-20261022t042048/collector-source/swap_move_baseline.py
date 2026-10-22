"""Offline preregistration of greedy-swap-move: greedy, one pair swap, then at most one task relocation.

Planning only. The output is not a live-run plan: the existing collector/validator contract stays at
four policies and is not called with this fifth policy. Legacy greedy enumerates the feasible space
for completion checks, so total timing is not a scalable-greedy cost; the repair step does not.
"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import sys
import time

import budget_placement
from budget_placement import select_budget_policies, validate_placement
import placement_policies
from placement_policies import prepare, sequential

CONTRACT = 'swap-move-baseline-offline-v1'


def _key(profile, placement):
    return prepare.cost(profile, placement, 'topology_profile'), tuple(sorted(placement.items()))


def _best(profile, baseline, proposals):
    """Same tie break as simple_alternatives/analyze_single_move; baseline is the zero-change candidate."""
    tasks = {t['id']: t for t in profile['tasks']}
    counts = dict(proposals=0, rejected_unqualified=0, rejected_capacity=0)
    distinct = [dict(baseline)]
    for proposal in proposals:
        counts['proposals'] += 1
        if any(node not in tasks[task]['candidates'] for task, node in proposal.items()):
            counts['rejected_unqualified'] += 1
            continue
        try:
            validate_placement(profile, proposal)
        except ValueError:
            counts['rejected_capacity'] += 1
            continue
        if proposal not in distinct:
            distinct.append(proposal)
    counts['validated_distinct'] = len(distinct)
    return min(distinct, key=lambda p: _key(profile, p)), counts


def _swaps(placement):
    for a, b in itertools.combinations(placement, 2):
        proposal = dict(placement)
        proposal[a], proposal[b] = proposal[b], proposal[a]
        yield proposal


def _moves(profile, placement):
    for task in profile['tasks']:
        for node in task['candidates']:
            if node != placement[task['id']]:
                yield dict(placement, **{task['id']: node})


def greedy_swap_move(profile):
    """Public selector. Does not mutate profile; raises ValueError when greedy has no feasible placement."""
    prepare.validate(profile)
    start = time.perf_counter()
    greedy = sequential(profile)
    greedy_end = time.perf_counter()
    swapped, swap_counts = _best(profile, greedy, _swaps(greedy))
    swap_end = time.perf_counter()
    moved, move_counts = _best(profile, swapped, _moves(profile, swapped))
    end = time.perf_counter()
    changed = [t for t in swapped if swapped[t] != moved[t]]
    return dict(
        status='ok', placement=moved, predicted_e2e_proxy_ms=prepare.cost(profile, moved, 'topology_profile'),
        profile_sha256=prepare.digest(profile),
        greedy_placement=greedy, greedy_proxy_ms=prepare.cost(profile, greedy, 'topology_profile'),
        swap_placement=swapped, swap_proxy_ms=prepare.cost(profile, swapped, 'topology_profile'),
        move=dict(task=changed[0], source=swapped[changed[0]], destination=moved[changed[0]]) if changed else None,
        candidates=dict(swap=swap_counts, move=move_counts),
        timing_ms=dict(greedy=(greedy_end-start)*1000, swap=(swap_end-greedy_end)*1000,
                       move=(end-swap_end)*1000, repair=(end-greedy_end)*1000, total=(end-start)*1000),
        timing_scope='repair=swap+move only; total includes legacy greedy, whose completion check '
                     'enumerates the feasible space; not a scalable-greedy cost',
        rule='greedy; best single pair swap; then at most one task relocation; no convergence iteration; '
             'key=(topology_profile proxy, sorted placement items)')


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def plan(profile, server):
    """Existing four budget policies plus greedy-swap-move for one profile; offline, never execution-ready."""
    prepare.validate(profile)
    if server not in profile['nodes']:
        raise ValueError('Server is not a profile node: '+str(server))
    budget = profile.get('admission_budget')
    if budget is not None and budget.get('server') != server:
        raise ValueError('Server differs from profile admission_budget server')
    added = greedy_swap_move(profile)
    policies = select_budget_policies(profile, server)
    if added['greedy_placement'] != policies['greedy']['placement']:
        raise ValueError('Greedy differs from existing budget policy')
    if added['swap_placement'] != policies['greedy-swap']['placement']:
        raise ValueError('Pair swap differs from existing greedy-swap policy')
    policies['greedy-swap-move'] = added
    return dict(
        contract=CONTRACT, execution_ready=False,
        scope='offline cost-table plan; not a live run; existing collector/validator keep their four-policy '
              'contract and were not called; no observation or performance claim',
        provenance=dict(server=server, profile_sha256=prepare.digest(profile),
                        implementations_sha256={Path(m.__file__).name: _sha256(m.__file__) for m in
                                                (sys.modules[__name__], budget_placement, placement_policies, prepare)}),
        policies=policies)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('profile', type=Path)
    parser.add_argument('--server', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.out.exists():
            raise ValueError('Refusing to overwrite '+str(args.out))
        if not args.out.parent.is_dir():
            raise ValueError('Output directory does not exist: '+str(args.out.parent))
        raw = args.profile.read_bytes()
        result = plan(json.loads(raw), args.server)
        result['provenance'].update(profile_path=str(args.profile.resolve()),
                                    profile_file_sha256=hashlib.sha256(raw).hexdigest())
        text = json.dumps(result, indent=2)
        with args.out.open('x') as handle:
            handle.write(text)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('swap_move_baseline: '+type(error).__name__+': '+str(error), file=sys.stderr)
        return 2
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
