"""Offline replay of the final guard(False) abort in wan-20261021t101203.

Seam: subprocess.run inside Session.call (the same seam the existing
test_budget_placement/test_control_receipt fixtures patch). The fake transport
answers each kubectl exec with the *captured* bytes of events 971-976:
source-e0/e1/c return their captured stdout (rc 0); sink-e0 returns the captured
"error sending request ... unexpected EOF" stderr (rc 1) on every attempt.
uuid4 is pinned to the captured operation IDs so the generated wrapped command
must equal the captured argv byte-for-byte (fidelity check).

Scenarios
  A captured   : sink-e0 EOF on all 3 attempts -> expect exact captured abort.
  B transient  : EOF on attempt 1 only           -> expect retry recovers (control: the
                 retry branch is live, so A is a window-length outcome, not a dead branch).
No cluster, network, or artifact writes: all Session output goes to a temp dir.
Exit 0 only when every assertion holds (red-capable).
"""
import json
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
RUN = 'wan-20261021t101203'
ART = ROOT / 'artifacts/topology-profile-live' / RUN
sys.path.insert(0, str(ROOT / 'experiments/real-topology-live'))
from run_baseline_comparison import BaselinePilot  # noqa: E402

OP = re.compile(r"run_once\('/work/control-receipts','([a-f0-9]{32})','([0-9a-f-]{36})'")


def captured(prefix):
    event = json.loads(next(ART.glob(prefix + '-*.event.json')).read_text())
    stem = next(ART.glob(prefix + '-*.event.json')).name[:-len('.event.json')]
    return dict(event=event, stdout=(ART / (stem + '.stdout')).read_bytes(),
                stderr=(ART / (stem + '.stderr')).read_bytes())


def scenario(eof_attempts):
    caps = {k: captured(p) for k, p in [('source-e0', '971'), ('source-e1', '972'),
                                        ('source-c', '973'), ('sink-e0', '974')]}
    retries = [captured('975'), captured('976')]
    peers = json.loads((ART / 'peers-eval-2-compressed-2.json').read_text())
    ops, uids = [], {}
    for key, cap in caps.items():
        op, uid = OP.search(cap['event']['command'][-1]).groups()
        ops.append(op)
        uids[key] = uid
    with tempfile.TemporaryDirectory() as temp:
        pilot = BaselinePilot.__new__(BaselinePilot)
        pilot.run, pilot.out, pilot.counter = RUN, Path(temp), 970
        pilot.actual = {k: {'metadata': {'uid': uids.get(k, '00000000-0000-0000-0000-000000000000')}} for k in peers}
        pilot.zones = {k: v['zone'] for k, v in peers.items()}
        pilot.urls = {k: v['url'] for k, v in peers.items()}
        ip = lambda u: u.split('//')[1].split(':')[0]
        pilot.cloud = [ip(v['url']) for v in peers.values() if v['zone'] == 'cloud']
        pilot.edge = [ip(v['url']) for v in peers.values() if v['zone'] == 'edge']
        pilot.guarded, pilot.operators, pilot.resources = True, ['vision', 'speech', 'fusion'], []
        calls, sleeps, sink_attempts = [], [], [0]

        def transport(command, input=None, stdout=None, stderr=None, timeout=None):
            pod = command[command.index('exec') + 3] if 'exec' in command else None
            key = pod[len(RUN) + 1:] if pod else None
            calls.append(dict(key=key, verb=command[4], argv=command))
            if key == 'sink-e0':
                expected = (caps['sink-e0'] if sink_attempts[0] == 0 else retries[sink_attempts[0] - 1])['event']['command']
                assert command == expected, 'generated sink-e0 argv differs from captured'
                sink_attempts[0] += 1
                if sink_attempts[0] <= eof_attempts:
                    stderr.write(caps['sink-e0']['stderr'])
                    return subprocess.CompletedProcess(command, 1)
                stdout.write(caps['source-e0']['stdout'])  # any valid net_control JSON
                return subprocess.CompletedProcess(command, 0)
            if key in caps:
                assert command == caps[key]['event']['command'], 'generated argv differs: ' + key
                stdout.write(caps[key]['stdout'])
                return subprocess.CompletedProcess(command, 0)
            # Anything past sink-e0 (later guard keys, restore RPCs, logs, delete) is not
            # part of the captured failure; answer neutrally and record it.
            stdout.write(b'{"status": "ready"}' if key else b'')
            return subprocess.CompletedProcess(command, 0)

        sequence, fresh = iter(ops), uuid.uuid4
        with patch('subprocess.run', side_effect=transport), \
             patch('uuid.uuid4', side_effect=lambda: next((uuid.UUID(hex=o) for o in sequence), None) or fresh()), \
             patch('time.sleep', side_effect=sleeps.append), \
             patch.object(BaselinePilot, 'pod_rpc', lambda self, k, url, body, stem: {'status': 'ready'}), \
             patch.object(BaselinePilot, 'refresh_network', lambda self, label: None):
            try:
                pilot.remove_operators('eval-2-compressed-2')
                error = None
            except RuntimeError as caught:
                error = str(caught)
        tags = sorted(p.name for p in Path(temp).glob('*.stderr'))
        return dict(error=error, sink_attempts=sink_attempts[0], sleeps=sleeps, guarded=pilot.guarded,
                    exec_keys=[c['key'] for c in calls if c['verb'] == 'exec'],
                    logs_or_delete=[c['verb'] for c in calls if c['verb'] in ('logs', 'delete')],
                    stderr_files=tags[:6], counter=pilot.counter)


def main():
    a = scenario(3)
    b = scenario(1)
    print(json.dumps(dict(A_captured=a, B_transient=b), indent=1))
    checks = {
        'A aborts with captured error': a['error'] == '976-guard-False-sink-e0-receipt-2 failed; inspect phase stderr',
        'A three sink-e0 attempts': a['sink_attempts'] == 3,
        'A two 1s sleeps (~2s retry window)': a['sleeps'] == [1, 1],
        'A exec order matches 971-976': a['exec_keys'] == ['source-e0', 'source-e1', 'source-c', 'sink-e0', 'sink-e0', 'sink-e0'],
        'A no logs/delete in remove_operators': a['logs_or_delete'] == [],
        'A guard state left True': a['guarded'] is True,
        'A counter ends at 976': a['counter'] == 976,
        'B transient EOF recovers': b['error'] is None and b['sink_attempts'] == 2 and b['guarded'] is False,
    }
    for name, ok in checks.items():
        print(('PASS ' if ok else 'FAIL ') + name)
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
