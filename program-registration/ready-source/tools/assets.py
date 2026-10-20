"""Verified local runtime asset import and offline CPU environment setup."""
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "artifacts/topology-profile-live"

def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        while block := source.read(1024 * 1024): value.update(block)
    return value.hexdigest()

def import_assets(path):
    path = Path(path).resolve()
    lock = json.loads((ROOT / "asset-lock.json").read_text())
    if path.stat().st_size != lock["bytes"] or sha(path) != lock["sha256"]:
        raise ValueError("Runtime archive does not match asset-lock.json")
    if ASSETS.exists(): raise FileExistsError("Assets already exist; preserve them and use a fresh package")
    ASSETS.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="asset-import-", dir=ASSETS.parent) as temp:
        stage = Path(temp) / "ready"
        stage.mkdir()
        with tarfile.open(path) as archive:
            members = archive.getmembers()
            if len(members) != len(lock["members"]) or {m.name for m in members} != set(lock["members"]) or not all(m.isfile() for m in members):
                raise ValueError("Unexpected runtime archive members")
            archive.extractall(stage, filter="data")
        for name, record in lock["members"].items():
            item = stage / name
            if item.stat().st_size != record["bytes"] or sha(item) != record["sha256"]:
                raise ValueError("Asset hash mismatch: " + name)
        bundles = json.loads((stage / "load-bundles.json").read_text())
        bundle_dir = stage / "offline-bundles"
        bundle_dir.mkdir()
        with tarfile.open(stage / "load-cache-upload.tar") as archive:
            members = archive.getmembers()
            if len(members) != len(bundles) or {m.name for m in members} != set(bundles) or not all(m.isfile() for m in members):
                raise ValueError("Unexpected deployment bundle members")
            archive.extractall(bundle_dir, filter="data")
        for name, record in bundles.items():
            item = bundle_dir / name
            if item.stat().st_size != record["bytes"] or sha(item) != record["sha256"]:
                raise ValueError("Deployment bundle mismatch: " + name)
        with tarfile.open(bundle_dir / "source.tar") as archive:
            archive.extractall(stage / "load-cache", filter="data")
        pairs = json.loads((stage / "load-pairs.json").read_text())["pairs"]
        for pair in pairs:
            for kind in ("image", "audio", "raw_image", "raw_audio"):
                item = stage / "load-cache/corpus" / Path(pair[kind + "_path"]).name
                if sha(item) != pair[kind + "_sha256"]: raise ValueError("Input hash mismatch")
        (stage / "package-assets-ready.json").write_text(json.dumps({"archive_sha256": lock["sha256"], "pairs": len(pairs)}))
        stage.rename(ASSETS)
    print(json.dumps({"assets": str(ASSETS), "pairs": len(pairs), "status": "verified"}))

def require_assets():
    if not (ASSETS / "package-assets-ready.json").is_file():
        raise RuntimeError("Run import-assets with the supplied runtime asset archive first")
    return ASSETS

def setup_local(python):
    assets = require_assets()
    check = subprocess.check_output([python, "-c", "import sys,platform,json;print(json.dumps([list(sys.version_info[:2]),platform.machine()]))"], text=True)
    version, arch = json.loads(check)
    if version != [3, 11] or arch not in ("x86_64", "aarch64"):
        raise ValueError("Bundled wheels require Linux CPython 3.11 on x86_64 or aarch64")
    if platform.system() != "Linux": raise ValueError("Local CPU runtime requires Linux")
    work = ROOT / "local-runtime"
    work.mkdir(exist_ok=False)
    try:
        bundles = json.loads((assets / "load-bundles.json").read_text())
        for name in ("wheels-" + arch + ".tar", "vision.tar", "speech.tar", "fusion.tar"):
            path = assets / "offline-bundles" / name
            if sha(path) != bundles[name]["sha256"]: raise ValueError("Bundle changed: " + name)
            with tarfile.open(path) as archive: archive.extractall(work, filter="data")
        subprocess.run([python, "-m", "venv", str(work / "venv")], check=True)
        executable = work / "venv/bin/python"
        subprocess.run([str(executable), "-m", "pip", "install", "--disable-pip-version-check", "--no-index", "--require-hashes", "--find-links", str(work / "wheels"), "-r", str(work / "requirements.txt")], check=True)
        (work / "ready.json").write_text(json.dumps({"python": str(executable), "architecture": arch, "status": "installed"}))
    except BaseException:
        (work / "setup-failed.txt").write_text("Setup failed; preserve logs and inspect dependencies before retrying.\n")
        raise
    print(json.dumps({"local_runtime": str(work), "status": "installed"}))
