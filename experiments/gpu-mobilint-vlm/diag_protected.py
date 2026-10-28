"""Protected-workload delta of a diag_probe run (read-only): every foreign Pod UID whose projected fields changed,
appeared or disappeared between the run's protected-before and protected-after snapshots."""
import glob
import json
from pathlib import Path
import sys


def load(run_dir, label):
    files = sorted(glob.glob(str(Path(run_dir)/f'*-{label}.projected.json')))
    return json.loads(Path(files[-1]).read_text()) if files else None


def delta(run_dir):
    before, after = load(run_dir, 'protected-before'), load(run_dir, 'protected-after')
    if before is None or after is None:
        return dict(complete=False, before=before is not None, after=after is not None)
    changed = {uid: dict(before=before[uid], after=after[uid]) for uid in before.keys() & after.keys() if before[uid] != after[uid]}
    return dict(complete=True, pods_before=len(before), pods_after=len(after), changed=changed,
                disappeared={uid: before[uid] for uid in before.keys() - after.keys()},
                appeared={uid: after[uid] for uid in after.keys() - before.keys()})


if __name__ == '__main__':
    print(json.dumps(delta(sys.argv[1]), indent=1))
