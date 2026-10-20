"""Single entry point for the portable EdgeFlow research application."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
LIVE = ROOT / "experiments/real-topology-live"

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--profile", type=Path, default=ROOT / "examples/profile.json")
    plan.add_argument("--out", type=Path, required=True)
    assets = commands.add_parser("import-assets")
    assets.add_argument("archive", type=Path)
    setup = commands.add_parser("setup-local")
    setup.add_argument("--python", default="python3.11")
    smoke = commands.add_parser("local-smoke")
    smoke.add_argument("--out", type=Path, required=True)
    smoke.add_argument("--count", type=int, default=2)
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--config", type=Path, default=ROOT / "cluster.json")
    run = commands.add_parser("run")
    run.add_argument("--config", type=Path, default=ROOT / "cluster.json")
    run.add_argument("--budget", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("--run", type=Path, required=True)
    verify.add_argument("--config", type=Path, default=ROOT / "cluster.json")
    report = commands.add_parser("report")
    report.add_argument("--run", type=Path, required=True)
    report.add_argument("--config", type=Path, default=ROOT / "cluster.json")
    report.add_argument("--out", type=Path, required=True)
    report.add_argument("--budget", action="store_true")
    commands.add_parser("test")
    args = parser.parse_args()
    if args.command in ("doctor", "run"):
        from tools.cluster import preflight
        preflight(args.config.resolve())
        if args.command == "run":
            env = dict(os.environ, EDGEFLOW_CONFIG=str(args.config.resolve()))
            script = "run_budget_placement.py" if args.budget else "run_placement.py"
            subprocess.run([sys.executable, str(LIVE / script)], check=True, cwd=ROOT, env=env)
    elif args.command == "verify":
        subprocess.run([sys.executable, str(LIVE / "verify_placement.py"), str(args.run.resolve())], check=True, cwd=ROOT, env=dict(os.environ, EDGEFLOW_CONFIG=str(args.config.resolve())))
    elif args.command == "report":
        script = "report_budget.py" if args.budget else "report_placement.py"
        subprocess.run([sys.executable, str(LIVE / script), str(args.run.resolve()), "--out", str(args.out.resolve())], check=True, cwd=ROOT, env=dict(os.environ, EDGEFLOW_CONFIG=str(args.config.resolve())))
    elif args.command == "test":
        for suite in (ROOT / "tests", ROOT / "experiments/real-topology-profile", LIVE):
            subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(suite), "-p", "test_*.py"], check=True, cwd=ROOT)
    elif args.command == "import-assets":
        from tools.assets import import_assets
        import_assets(args.archive)
    elif args.command == "setup-local":
        from tools.assets import setup_local
        setup_local(args.python)
    elif args.command == "local-smoke":
        from tools.assets import require_assets
        require_assets()
        runtime = ROOT / "local-runtime"
        if not (runtime / "ready.json").is_file(): raise RuntimeError("Run setup-local first")
        subprocess.run([str(runtime / "venv/bin/python"), str(ROOT / "tools/local_smoke.py"), "--out", str(args.out.resolve()), "--count", str(args.count)], check=True, cwd=ROOT)
    elif args.command == "plan":
        subprocess.run([sys.executable, str(ROOT / "experiments/real-topology-profile/prepare.py"), "--config", str(args.profile.resolve()), "--policy", "topology_profile", "--run-id", "edgeflow", "--out", str(args.out.resolve())], check=True, cwd=ROOT)

if __name__ == "__main__": main()
