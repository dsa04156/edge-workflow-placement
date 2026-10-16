"""Real child-process timeout must preserve partial stdout and stderr."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from phase_capture import capture_phase


class PhaseCaptureTest(unittest.TestCase):
    def test_timeout_retains_partial_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            command=['rtk','proxy',sys.executable,'-u','-c',
                     "import sys,time;print('partial-row',flush=True);print('partial-error',file=sys.stderr,flush=True);time.sleep(5)"]
            with self.assertRaises(subprocess.TimeoutExpired):
                capture_phase(command, '', base, 'test', timeout=0.5)
            self.assertIn('partial-row', (base/'test.log').read_text())
            self.assertIn('partial-error', (base/'test.stderr').read_text())
            self.assertEqual(json.loads((base/'test.execution.json').read_text())['status'],'timeout')

    def test_success_records_host_events(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            result=capture_phase(['rtk','proxy',sys.executable,'-c','print(42)'],'',base,'test',timeout=2)
            self.assertEqual(result.returncode,0)
            self.assertEqual((base/'test.log').read_text().strip(),'42')
            event=json.loads((base/'test.execution.json').read_text())
            self.assertEqual(event['status'],'completed')
            self.assertLessEqual(event['started_at'],event['completed_at'])


if __name__=='__main__':unittest.main()
