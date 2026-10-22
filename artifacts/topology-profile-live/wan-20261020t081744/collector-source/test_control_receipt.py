"""Process/control boundary regressions; never executes cluster commands."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid
from unittest.mock import patch

from run_budget_placement import BudgetPilot
from control_receipt import command


class ControlReceiptTests(unittest.TestCase):
    def launch(self, script):
        return subprocess.run(['python3', '-c', script], capture_output=True, text=True,
                              env=dict(os.environ, POD_UID='test-pod'), timeout=10)

    def test_lost_response_returns_receipt_without_duplicate_side_effect(self):
        with tempfile.TemporaryDirectory() as temp:
            output = str(Path(temp)/'effect')
            code = "from pathlib import Path;p=Path("+repr(output)+");p.open('x').write('once');print('done')"
            script = command(uuid.uuid4().hex, 'test-pod', code, None, temp)
            first = self.launch(script)  # Imagine the transport drops this response.
            self.assertEqual(first.returncode, 0, first.stderr)
            second = self.launch(script)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(second.stdout, 'done\n')

    def test_partial_operation_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as temp:
            code = "print('partial');raise RuntimeError('kernel failure')"
            script = command(uuid.uuid4().hex, 'test-pod', code, None, temp)
            self.assertNotEqual(self.launch(script).returncode, 0)
            second = self.launch(script)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn('Indeterminate control; refusing replay', second.stderr)

    def test_process_death_after_start_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as temp:
            script = command(uuid.uuid4().hex, 'test-pod', 'import os;os._exit(9)', None, temp)
            self.assertEqual(self.launch(script).returncode, 9)
            second = self.launch(script)
            self.assertIn('Indeterminate control; refusing replay', second.stderr)

    def test_changed_command_or_pod_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            operation = uuid.uuid4().hex
            self.assertEqual(self.launch(command(operation, 'test-pod', "print('ok')", None, temp)).returncode, 0)
            changed = self.launch(command(operation, 'test-pod', "print('different')", None, temp))
            self.assertIn('Operation identity changed', changed.stderr)
            changed = self.launch(command(operation, 'other-pod', "print('ok')", None, temp))
            self.assertIn('Pod UID changed', changed.stderr)

    def test_simultaneous_recovery_serializes_one_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            code = "import time;from pathlib import Path;Path("+repr(str(Path(temp)/'effect'))+").open('x').write('once');time.sleep(.1);print('done')"
            script = command(uuid.uuid4().hex, 'test-pod', code, None, temp)
            processes = [subprocess.Popen(['python3', '-c', script], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, env=dict(os.environ, POD_UID='test-pod')) for _ in range(2)]
            for process in processes:
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertEqual(stdout, 'done\n')

    def test_pre_dispatch_eof_does_not_abort_a_recoverable_control(self):
        with tempfile.TemporaryDirectory() as temp:
            pilot = BudgetPilot.__new__(BudgetPilot)
            pilot.run, pilot.out, pilot.counter = 'fixture', Path(temp), 0
            pilot.actual = {'source-e1': {'metadata': {'uid': 'fixture-uid'}}}
            attempts = []

            def transport(command, **kwargs):
                attempts.append(command)
                if len(attempts) == 1:
                    kwargs['stderr'].write(b'error sending request: unexpected EOF\n')
                    return subprocess.CompletedProcess(command, 1)
                kwargs['stdout'].write(b'{"backlog_bytes":0}\n')
                return subprocess.CompletedProcess(command, 0)

            with patch('subprocess.run', side_effect=transport):
                result = pilot.net('source-e1', {'action': 'unblock', 'destinations': []}, 'restore-control')
            self.assertEqual(result['backlog_bytes'], 0)
            self.assertEqual(len(list(pilot.out.glob('*.event.json'))), 2)
            self.assertIn('unexpected EOF', (pilot.out/'001-restore-control.stderr').read_text())


if __name__ == '__main__':
    unittest.main()
