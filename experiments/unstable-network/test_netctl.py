"""Network control seam: the netctl sidecar (netctl.py) that alone holds a trace and applies it in the owned gateway netns.

- Runner with a fake clock and a recording tc backend (system boundary): phases are applied at their planned offsets by
  in-place `change` only (no root delete/re-add while the trace runs), every application keeps planned/actual times,
  restore cancels pending phases and returns to good without flushing, a failed change restores, teardown removes.
- Real loopback HTTP: arm/start/status/restore/teardown; unapproved traces, wrong run ids and tampered schedules are refused.
- Real kernel (unprivileged user+net namespace via `unshare -rn`, pinned pyroute2 0.9.6 wheel): an in-place change keeps
  the owned root/handles, the queue backlog and the counters, whereas delete/re-add (the old net_control.py behaviour)
  resets them. Skipped only when unprivileged namespaces are unavailable.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import netctl  # noqa: E402
import traces  # noqa: E402

WHEEL = ROOT/'artifacts/topology-profile-live/network-wheels/pyroute2-0.9.6-py3-none-any.whl'
WHEEL_SHA256 = '3334091326e560a506635449af03b26920d22d4e5a7996aed354363d106fcef8'
CLOUD, EDGE = ['10.1.0.5', '10.1.0.6'], ['10.2.0.7']


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def wait(self, event, timeout):
        self.now += max(0.0, timeout)
        return event.is_set()


class RecordingTc:
    """Fake system boundary: records every tc operation and can fail one change."""

    def __init__(self, fail_on_change=None):
        self.ops, self.installed, self.fail_on_change, self.changes = [], False, fail_on_change, 0

    def install(self, cond, cloud, edge):
        if self.installed:
            raise RuntimeError('owned root present')
        self.installed = True
        self.ops.append(('install', dict(cond), list(cloud), list(edge)))

    def change(self, cond):
        self.changes += 1
        if self.fail_on_change == self.changes:
            raise OSError('netlink error')
        self.ops.append(('change', dict(cond)))

    def teardown(self):
        self.ops.append(('teardown',))
        self.installed = False

    def snapshot(self):
        return dict(installed=self.installed, qdisc_summary=[])


def arm_body(label='c1', trace='burst', run_id='un-test'):
    return dict(run_id=run_id, label=label, trace=trace, trace_sha256=traces.trace_sha256(trace), cloud=CLOUD, edge=EDGE)


class RunnerSchedule(unittest.TestCase):
    def runner(self, tc=None):
        clock = FakeClock()
        r = netctl.Runner(tc or RecordingTc(), clock=clock, wait=clock.wait, run_id='un-test')
        return r, clock

    def run_to_end(self, r, label='c1', trace='burst'):
        r.arm(arm_body(label, trace))
        r.start(dict(run_id='un-test', label=label, delay_s=2.0))
        r.join(label)
        return r.status(label)

    def test_burst_trace_is_installed_once_then_changed_in_place_at_each_boundary(self):
        tc = RecordingTc()
        r, _ = self.runner(tc)
        self.run_to_end(r)
        kinds = [op[0] for op in tc.ops]
        self.assertEqual(kinds, ['install'] + ['change'] * 6)
        self.assertEqual(tc.ops[0][1], dict(mbps=100, delay_ms=0, jitter_ms=0, loss_pct=0.0))
        self.assertEqual(tc.ops[3][1], dict(mbps=2, delay_ms=20, jitter_ms=5, loss_pct=100.0))  # the 2 s outage at t=28 (3rd change)

    def test_each_application_records_its_planned_offset_and_lag(self):
        r, _ = self.runner()
        status = self.run_to_end(r)
        changes = [e for e in status['events'] if e['kind'] == 'change']
        self.assertEqual([e['planned_offset_s'] for e in changes], [10, 20, 28, 30, 40, 50])
        self.assertTrue(all(e['ok'] and e['apply_lag_ms'] >= 0 for e in changes))
        self.assertEqual(status['events'][-1]['kind'], 'window_end')

    def test_stable_trace_never_changes_the_qdisc(self):
        tc = RecordingTc()
        r, _ = self.runner(tc)
        self.run_to_end(r, trace='stable')
        self.assertEqual([op[0] for op in tc.ops], ['install'])

    def test_restore_cancels_pending_phases_and_returns_to_good_without_teardown(self):
        tc = RecordingTc()
        clock = FakeClock()
        gate = threading.Event()

        def slow_wait(event, timeout):  # hold the schedule thread inside its first wait until restore is requested
            gate.wait(5)
            return event.is_set()
        r = netctl.Runner(tc, clock=clock, wait=slow_wait, run_id='un-test')
        r.arm(arm_body())
        r.start(dict(run_id='un-test', label='c1', delay_s=2.0))
        threading.Timer(0.05, gate.set).start()
        r.restore(dict(run_id='un-test', label='c1'))
        self.assertEqual([op[0] for op in tc.ops], ['install', 'change'])
        self.assertEqual(tc.ops[1][1], traces.GOOD)
        self.assertTrue(tc.installed)
        self.assertIn('restore', [e['kind'] for e in r.status('c1')['events']])

    def test_failed_change_restores_good_and_stops_the_schedule(self):
        tc = RecordingTc(fail_on_change=2)  # the t=20 change fails
        r, _ = self.runner(tc)
        status = self.run_to_end(r)
        self.assertEqual(status['state'], 'failed')
        failed = [e for e in status['events'] if e['kind'] == 'change' and not e['ok']]
        self.assertEqual(len(failed), 1)
        self.assertEqual(tc.ops[-1], ('change', traces.GOOD))
        self.assertEqual(sum(1 for op in tc.ops if op[0] == 'change'), 2)  # t=10 change + the restore; nothing after the failure

    def test_teardown_removes_the_owned_root(self):
        tc = RecordingTc()
        r, _ = self.runner(tc)
        self.run_to_end(r)
        r.teardown(dict(run_id='un-test'))
        self.assertEqual(tc.ops[-1], ('teardown',))
        self.assertFalse(tc.installed)

    def test_second_arm_without_teardown_is_refused(self):
        r, _ = self.runner()
        self.run_to_end(r)
        with self.assertRaises(ValueError):
            r.arm(arm_body('c2'))

    def test_start_twice_is_refused(self):
        r, _ = self.runner()
        r.arm(arm_body())
        r.start(dict(run_id='un-test', label='c1', delay_s=2.0))
        with self.assertRaises(ValueError):
            r.start(dict(run_id='un-test', label='c1', delay_s=2.0))
        r.join('c1')


class RunnerRefusals(unittest.TestCase):
    def test_unknown_trace_tampered_sha_and_foreign_run_are_refused_before_any_tc_call(self):
        tc = RecordingTc()
        r = netctl.Runner(tc, run_id='un-test')
        for body in (dict(arm_body(), trace='flood'), dict(arm_body(), trace_sha256='0' * 64), arm_body(run_id='un-other')):
            with self.assertRaises(ValueError):
                r.arm(body)
        self.assertEqual(tc.ops, [])

    def test_non_pod_ip_targets_are_refused(self):
        r = netctl.Runner(RecordingTc(), run_id='un-test')
        with self.assertRaises(ValueError):
            r.arm(dict(arm_body(), cloud=['192.168.0.4']))


class HttpControl(unittest.TestCase):
    def setUp(self):
        self.tc = RecordingTc()
        self.runner = netctl.Runner(self.tc, run_id='un-test')
        self.server = netctl.serve(self.runner, host='127.0.0.1', port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as reply:
            return json.loads(reply.read())

    def test_a_short_trace_runs_over_http_and_reports_its_timeline(self):
        self.call('/arm', arm_body('h1', 'stable'))
        self.call('/start', dict(run_id='un-test', label='h1', delay_s=0.05))
        self.call('/restore', dict(run_id='un-test', label='h1'))
        status = self.call('/status?label=h1')
        self.assertEqual([e['kind'] for e in status['events']][:1], ['install'])
        self.call('/teardown', dict(run_id='un-test'))
        self.assertFalse(self.call('/health')['installed'])

    def test_refusal_is_a_400_with_no_tc_call(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.call('/arm', dict(arm_body(), trace='flood'))
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(self.tc.ops, [])


KERNEL_PROBE = r'''
import json, socket, sys, time
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import os
os.environ['RESEARCH_LINK_CONTROL'] = 'owned-pod-netns'
from pyroute2 import IPRoute
import netctl
with IPRoute() as ip:
    lo = ip.link_lookup(ifname='lo')[0]
    ip.link('set', index=lo, state='up')
    for a in ('10.77.0.2', '10.77.0.3'):
        ip.addr('add', index=lo, address=a, mask=32)
tc = netctl.Pyroute2Tc('lo')
narrow = dict(mbps=2, delay_ms=20, jitter_ms=5, loss_pct=1.0)
lossy = dict(mbps=10, delay_ms=20, jitter_ms=5, loss_pct=0.5)
def blast(n=120):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for _ in range(n):
        try:
            s.sendto(b'x' * 1000, ('10.77.0.2', 9))
        except OSError:
            pass
    s.close()
def band1(snap):
    return {q['kind']: q for q in snap['qdisc_summary'] if q['handle'] in (0x100000, 0x110000)}
out = {}
tc.install(narrow, ['10.77.0.2'], ['10.77.0.3'])
blast()
before = tc.snapshot()
tc.change(lossy)
after = tc.snapshot()
out['change'] = dict(before=band1(before), after=band1(after), roots_before=[q['handle'] for q in before['qdisc_summary'] if q['parent'] == 0xffffffff],
                     roots_after=[q['handle'] for q in after['qdisc_summary'] if q['parent'] == 0xffffffff])
time.sleep(1.0)
tc.teardown()
tc.install(narrow, ['10.77.0.2'], ['10.77.0.3'])
blast()
loaded = tc.snapshot()
tc.teardown()
tc.install(lossy, ['10.77.0.2'], ['10.77.0.3'])
fresh = tc.snapshot()
out['recreate'] = dict(before=band1(loaded), after=band1(fresh))
tc.teardown()
out['after_teardown_roots'] = [q['handle'] for q in tc.snapshot()['qdisc_summary'] if q['parent'] == 0xffffffff]
print(json.dumps(out))
'''


@unittest.skipUnless(shutil.which('unshare') and WHEEL.is_file(), 'unshare or pinned pyroute2 wheel unavailable')
class RealKernelChange(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if hashlib.sha256(WHEEL.read_bytes()).hexdigest() != WHEEL_SHA256:
            raise unittest.SkipTest('pyroute2 wheel is not the pinned file')
        cls.tmp = tempfile.mkdtemp()
        zipfile.ZipFile(WHEEL).extractall(Path(cls.tmp)/'wheel')
        script = Path(cls.tmp)/'probe.py'
        script.write_text(KERNEL_PROBE)
        done = subprocess.run(['unshare', '-rn', sys.executable, str(script), str(Path(cls.tmp)/'wheel'), str(HERE)],
                              capture_output=True, text=True, timeout=60)
        if done.returncode and 'unshare' in done.stderr and 'Operation not permitted' in done.stderr:
            raise unittest.SkipTest('unprivileged user namespaces are not permitted here')
        if done.returncode:
            raise AssertionError(done.stderr[-2000:])
        cls.result = json.loads(done.stdout)
        cls.kernel = os.uname().release

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_in_place_change_keeps_root_handles(self):
        c = self.result['change']
        self.assertEqual(c['roots_before'], [0x10000])
        self.assertEqual(c['roots_after'], [0x10000])
        self.assertEqual(set(c['before']), {'tbf', 'netem'})
        self.assertEqual(set(c['after']), {'tbf', 'netem'})

    def test_in_place_change_keeps_the_queued_backlog_and_counters(self):
        before, after = self.result['change']['before']['netem'], self.result['change']['after']['netem']
        self.assertGreater(before['qlen'], 0, 'the probe did not build a queue; the check would be vacuous')
        self.assertGreater(after['qlen'], 0)
        self.assertGreater(before['drops'], 0, 'the 64-packet netem limit should have dropped part of the 120-datagram burst')
        self.assertGreaterEqual(after['drops'], before['drops'])
        self.assertGreaterEqual(after['packets'], before['packets'])

    def test_delete_and_recreate_loses_queue_and_counters(self):
        before, after = self.result['recreate']['before']['netem'], self.result['recreate']['after']['netem']
        self.assertGreater(before['qlen'], 0)
        self.assertEqual((after['qlen'], after['packets'], after['drops']), (0, 0, 0))

    def test_teardown_leaves_no_owned_root(self):
        self.assertNotIn(0x10000, self.result['after_teardown_roots'])


if __name__ == '__main__':
    unittest.main()
