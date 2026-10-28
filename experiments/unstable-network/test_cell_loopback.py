"""One whole cell over real loopback HTTP (slow, ~75 s): live.Live.trace_cell drives the real generator, the unchanged
DagWorkers and gateway, and the real netctl HTTP server whose tc backend is the recording fake (no kernel change).
Fake model stages; plumbing evidence only (never performance). Also checks the gateway Pod manifest security contract.
"""
from pathlib import Path
import sys
import threading
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_executor  # noqa: E402  (sets up the reused-module paths)
from test_netctl import RecordingTc  # noqa: E402
import design  # noqa: E402
import live  # noqa: E402
import netctl  # noqa: E402


class Session:
    def __init__(self):
        self.run, self.saved = 'un-test', {}

    def save(self, name, value):
        self.saved[name] = value

    def pod_states(self):
        return {}


class WholeCell(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = test_executor.Cluster(poll_s=0.2)
        cls.tc = RecordingTc()
        cls.runner = netctl.Runner(cls.tc, run_id='un-test')
        cls.nsrv = netctl.serve(cls.runner, host='127.0.0.1', port=0)
        threading.Thread(target=cls.nsrv.serve_forever, daemon=True).start()
        lv = live.Live(Session(), plan={'quality': {}}, plan_dir='.')
        lv.workers = [dict(key=k, zone=z) for k, z in cls.c.zones.items()]
        lv.urls = dict(cls.c.urls, generator=cls.c.origin_url, gateway=cls.c.gateway_url,
                       netctl=f'http://127.0.0.1:{cls.nsrv.server_address[1]}')
        lv.cloud_ips, lv.edge_ips = ['10.0.0.2'], ['10.0.0.3']  # filter targets only; the recording backend applies nothing
        lv.quality, lv.trace_questions = dict(mode='diagnostic', qmin=0.4), cls.c.questions
        cls.cell = dict(block=0, trace='burst', policy='dag_reopt', position=0, rate_rps=1.6, seed=design.trace_seed(20261027, 0, 'burst', 1.6),
                        label='b0-burst-dag_reopt')
        lv.trace_cell(cls.cell, cls.c.profile(), deadline=lv.clock() + 10000)
        cls.saved = lv.s.saved

    @classmethod
    def tearDownClass(cls):
        cls.nsrv.shutdown()
        cls.nsrv.server_close()
        cls.c.close()

    def test_every_scheduled_arrival_has_one_terminal_row(self):
        request = self.saved['trace-b0-burst-dag_reopt.request.json']
        ledger = self.saved['ledger-b0-burst-dag_reopt.json']
        self.assertEqual([r['index'] for r in ledger['rows']], [a['index'] for a in request['arrivals']])
        self.assertTrue(all(r['status'] in ('ok', 'late_ok', 'failed', 'worker_rejected', 'timeout', 'admission_drop') for r in ledger['rows']))

    def test_the_trace_was_applied_by_in_place_changes_then_restored_and_torn_down(self):
        kinds = [op[0] for op in self.tc.ops]
        self.assertEqual(kinds[0], 'install')
        self.assertEqual(kinds[1:7], ['change'] * 6)
        self.assertEqual(kinds[-1], 'teardown')
        self.assertNotIn('install', kinds[1:])

    def test_alignment_is_recorded_within_tolerance_on_loopback(self):
        align = self.saved['align-b0-burst-dag_reopt.json']
        self.assertTrue(align['within_tolerance'])
        self.assertLess(align['bound_ms'], design.BUDGETS['align_tolerance_ms'])

    def test_the_fleet_drained_after_the_cell(self):
        self.assertTrue(self.saved['drain-after-b0-burst-dag_reopt.json']['idle'])


class StatusTimeoutAfterStart(unittest.TestCase):
    """R1 standards F1 reproduction, adapted to the fix: real generator/workers/gateway/netctl HTTP, one injected /status
    timeout after the generator admitted the trace. The collector must stop admission, wait for the generator's terminal
    state, salvage a ledger row per scheduled arrival, and only then drain (generator included) and tear down."""

    @classmethod
    def setUpClass(cls):
        cls.c = test_executor.Cluster(poll_s=0.2)
        cls.runner = netctl.Runner(RecordingTc(), run_id='un-test')
        cls.nsrv = netctl.serve(cls.runner, host='127.0.0.1', port=0)
        threading.Thread(target=cls.nsrv.serve_forever, daemon=True).start()
        lv = live.Live(Session(), plan={'quality': {}}, plan_dir='.')
        lv.workers = [dict(key=k, zone=z) for k, z in cls.c.zones.items()]
        lv.urls = dict(cls.c.urls, generator=cls.c.origin_url, gateway=cls.c.gateway_url,
                       netctl=f'http://127.0.0.1:{cls.nsrv.server_address[1]}')
        lv.cloud_ips, lv.edge_ips = ['10.0.0.2'], ['10.0.0.3']
        lv.quality, lv.trace_questions = dict(mode='diagnostic', qmin=0.4), cls.c.questions
        cls.calls, injected = [], []

        def http(url, body=None, timeout=None):
            cls.calls.append(url.split('?')[0])
            if url == cls.c.origin_url + '/status' and cls.c.origin_url + '/trace' in cls.calls and not injected:
                injected.append(url)
                raise TimeoutError('injected: one /status reply lost after the generator admitted the trace')
            return lv.bounded_control(url, body, timeout)
        lv.http = http
        cls.cell = dict(block=0, trace='burst', policy='dag_reopt', position=0, rate_rps=1.6, seed=design.trace_seed(20261027, 0, 'burst', 1.6),
                        label='b0-burst-dag_reopt')
        lv.trace_cell(cls.cell, cls.c.profile(), deadline=lv.clock() + 10000)
        cls.lv, cls.saved = lv, lv.s.saved

    @classmethod
    def tearDownClass(cls):
        cls.c.gen.stop_flag.set()
        cls.runner.teardown_all()
        cls.nsrv.shutdown()
        cls.nsrv.server_close()
        cls.c.close()

    def test_admission_was_stopped_before_the_ledger_was_read(self):
        stop, ledger = self.c.origin_url + '/stop', self.c.origin_url + '/ledger'
        self.assertIn(stop, self.calls)
        self.assertLess(self.calls.index(stop), self.calls.index(ledger))
        self.assertNotEqual(self.c.gen.status()['traces'][self.cell['label']]['state'], 'running')

    def test_every_scheduled_arrival_was_salvaged(self):
        request = self.saved['trace-b0-burst-dag_reopt.request.json']
        ledger = self.saved['ledger-b0-burst-dag_reopt.json']
        self.assertFalse(ledger['partial'])
        self.assertEqual([r['index'] for r in ledger['rows']], [a['index'] for a in request['arrivals']])
        self.assertIn('not_sent', {r['status'] for r in ledger['rows']})

    def test_the_failure_and_the_recovery_are_recorded_separately_and_the_cell_is_not_a_fault(self):
        self.assertIn('injected', self.saved['trace-b0-burst-dag_reopt.error.json']['error'])
        self.assertEqual(self.saved['generation-b0-burst-dag_reopt.json']['outcome'], 'terminal')
        self.assertTrue(self.saved['drain-after-b0-burst-dag_reopt.json']['idle'])
        self.assertEqual(self.lv.faults, [])


class GatewayPodSecurity(unittest.TestCase):
    def test_only_the_netctl_sidecar_gets_net_admin_and_it_is_run_bound(self):
        pod = live.gateway_pod('un-test', 'http://10.0.0.9:8080')
        relay, side = pod['spec']['containers']
        self.assertEqual(relay['securityContext']['capabilities'], {'drop': ['ALL']})
        self.assertTrue(relay['securityContext']['runAsNonRoot'])
        sc = side['securityContext']
        self.assertEqual(sc['capabilities'], {'drop': ['ALL'], 'add': ['NET_ADMIN']})
        self.assertFalse(sc['allowPrivilegeEscalation'])
        self.assertNotIn('privileged', sc)
        self.assertFalse(pod['spec'].get('hostNetwork', False))
        env = {e['name']: e.get('value') for e in side['env']}
        self.assertEqual((env['RUN_ID'], env['RESEARCH_LINK_CONTROL']), ('un-test', 'owned-pod-netns'))


if __name__ == '__main__':
    unittest.main()
