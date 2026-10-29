"""Bounded live diagnostic: the unchanged live.main qualification lifecycle on BOTH arms with the diagnostic stage shipped.

  python3 diag_live.py --frozen <dir> --prepared <dir> --cuda-cache-pvc <json> --io-profile sd-adaptive-v1 --print-release-draft
  python3 diag_live.py ... (same four) --runs <dir> --release <json> --stage qualification [--run-id gmv-diag-...]

Only two things differ from live.py, both applied for the duration of live.main and restored afterwards:
- live.SHIPPED: vlm_diag_stage.py is shipped under the ConfigMap name vlm_stage.py (the name the unchanged vlm_worker.py
  imports), the unchanged vlm_stage.py as vlm_stage_base.py, plus ttft_probe.py. Every other shipped file is live's.
- live.dependency_hashes: the modules a fresh process importing THIS runner loads, plus every shipped/prepared/build file,
  so a release drafted by live.py (or before any change to these files) fails release_problems before any kubectl call.
Refused before anything is loaded: a missing --prepared, --cuda-cache-pvc or --io-profile sd-adaptive-v1 (the existing SD
cache PVC profile), and --arm (this diagnostic is the pair only). Everything else -- preflight, PVC/PV checks, cache
upload, identity/receipt binding, the frozen probe + qualification schedule (one probe + three qualification requests per
arm), first-failure stop, durable ledger, UID-only cleanup -- is live.py's, unchanged.
Interrupt semantics are live.main's: SIGINT raises KeyboardInterrupt inside execute, and the finally block still runs the
UID-only delete, absence verification and protected snapshot, writing cleanup.json. A second signal during that block can
cut cleanup short (cleanup.json then records the error); send one SIGINT and wait for exit, never a blind KILL.
"""
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import live
import protocol as P

HERE = Path(__file__).resolve().parent
SHIPPED = dict(live.SHIPPED, **{'vlm_stage.py': HERE/'vlm_diag_stage.py', 'vlm_stage_base.py': HERE/'vlm_stage.py',
                                'ttft_probe.py': HERE/'ttft_probe.py'})
SD_PROFILE = live.C.SD_PROFILE['name']


def dependency_hashes():
    """live.dependency_hashes with this runner as the fresh process's import root and the diagnostic shipped set."""
    probe = ("import json,sys;sys.path.insert(0,sys.argv[1]);import diag_live;"
             "print(json.dumps(sorted({m.__file__ for m in list(sys.modules.values()) if getattr(m,'__file__',None)})))")
    loaded = json.loads(subprocess.run([sys.executable, '-c', probe, str(HERE)], capture_output=True, text=True, check=True, timeout=120,
                                       env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}).stdout)
    files = {Path(f).resolve() for f in loaded if f.endswith('.py') and Path(f).resolve().is_relative_to(live.ROOT/'experiments')}
    files |= {p.resolve() for p in [*SHIPPED.values(), *live.SHIPPED_PREPARED.values(), *live.BUILD_CODE]}
    return {str(f.relative_to(live.ROOT)): P.file_sha(f) for f in sorted(files)}


def check_args(argv):
    """live.py's option set, exact names only (no abbreviations), with this diagnostic's restrictions."""
    ap = argparse.ArgumentParser(prog='diag_live.py', allow_abbrev=False)
    for flag in ('--frozen', '--runs', '--release', '--stage', '--run-id', '--arm', '--prepared', '--cuda-cache-pvc', '--io-profile'):
        ap.add_argument(flag)
    ap.add_argument('--print-release-draft', action='store_true')
    ap.add_argument('--plan', action='store_true')
    a = ap.parse_args(argv)
    problems = []
    if a.arm is not None:
        problems.append('--arm is refused: this diagnostic runs both arms (mobilint + cuda) only')
    if not a.prepared or not a.cuda_cache_pvc:
        problems.append('--prepared and --cuda-cache-pvc are required (existing prepared CUDA cache only)')
    elif not Path(a.prepared, 'prepared-manifest.json').is_file() or not Path(a.cuda_cache_pvc).is_file():
        problems.append('--prepared must hold prepared-manifest.json and --cuda-cache-pvc must be an existing binding file')
    if a.io_profile != SD_PROFILE:
        problems.append(f'--io-profile {SD_PROFILE} is required (existing SD cache PVC profile)')
    if problems:
        raise SystemExit('diag_live refused: '+'; '.join(problems))
    return a


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    a = check_args(argv)
    if a.run_id is None and not (a.print_release_draft or a.plan):
        argv += ['--run-id', 'gmv-diag-'+datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%S')]
    saved = live.SHIPPED, live.dependency_hashes
    live.SHIPPED, live.dependency_hashes = SHIPPED, dependency_hashes
    try:
        live.main(argv)
    finally:
        live.SHIPPED, live.dependency_hashes = saved


if __name__ == '__main__':
    main()
