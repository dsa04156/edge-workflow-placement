"""Startup artifact seam: mounted files may vanish after readiness."""
import hashlib
from pathlib import Path
import runpy
import tempfile
import unittest
from freeze_config import freeze_config


class FreezeTests(unittest.TestCase):
    def test_started_pod_code_survives_missing_config_volume(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);mount=root/'mount';mount.mkdir()
            source=mount/'net_control.py';source.write_text('observed = 42\n')
            expected={'net_control.py':hashlib.sha256(source.read_bytes()).hexdigest()}
            frozen=root/'private'
            freeze_config(mount,frozen,expected)
            source.unlink();mount.rmdir()
            with self.assertRaises(FileNotFoundError):runpy.run_path(str(source))
            self.assertEqual(runpy.run_path(str(frozen/'net_control.py'))['observed'],42)


if __name__=='__main__':unittest.main()
