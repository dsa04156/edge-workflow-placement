import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class PackagePlanTests(unittest.TestCase):
    def test_measured_profile_produces_known_placement_outside_repository(self):
        with tempfile.TemporaryDirectory(prefix="edgeflow test ") as temp:
            out = Path(temp) / "plan"
            result = subprocess.run([sys.executable, str(ROOT / "edgeflow.py"), "plan", "--profile", str(ROOT / "examples/profile.json"), "--out", str(out)], cwd=temp, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads((out / "plan.json").read_text())
            self.assertEqual(plan["placement"], {"vision":"etri-dev0003-raspi5", "speech":"etri-ser0002-cgnmsb", "fusion":"etri-ser0002-cgnmsb"})
            self.assertTrue((out / "pods.json").is_file())

if __name__ == "__main__": unittest.main()
