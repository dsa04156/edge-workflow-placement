"""Release-bound collector seam (live.Live): one cell's control sequence, alignment, restore/teardown on failure and
the fault stop, with fakes only at the system boundaries (Pod HTTP control endpoints, kubectl pod states, time).

Worked alignment example (fake clock; every control call takes 20 ms on the collector clock):
  generator /trace sent at T, answered at T+0.02 -> its t0 = (T+0.01) + 2.0 on the collector clock (midpoint estimate);
  netctl /start is sent at T+0.02 -> delay_s = T+2.01 - (T+0.02) = 1.99; bound = 0.01 + 0.02 = 30 ms (<= 250 ms tolerance).
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.append(str(ROOT/'experiments/quality-network-baselines'))
sys.path.append(str(ROOT/'experiments/real-topology-live'))
import generator  # noqa: E402
import live  # noqa: E402
import traces  # noqa: E402

RTT = 0.02
QUEUE_IDLE = dict(waiting=0, active=0, joins_open=0, inflight=0, completed=1, dag_requests=1)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += s


class Pods:
    """Fake control endpoints of the owned Pods (system boundary). fail: {(component, path): exception}.
    The generator keeps per-label trace state like the real one: /trace admits a label in state trace_end, /stop ends a
    running trace only when stop_ends, /ledger refuses a running trace unless partial=1, an unknown label is a 400.
    accept_then_fail: /trace admits the label, then the reply is lost (raises). status_fail: first N /status calls raise.
    The netctl runs its window like the real Runner: /start schedules t0 = receipt + delay_s, /status is running until
    t0 + window_s, then completed (window_end), restored if /restore came first; netctl_state forces a state (failed)."""

    def __init__(self, clock, fail=None, busy=False, trace_end='done', stop_ends=True, accept_then_fail=None, status_fail=0,
                 netctl_state=None):
        self.clock, self.fail, self.busy, self.calls = clock, fail or {}, busy, []
        self.trace_end, self.stop_ends, self.accept_then_fail, self.status_fail = trace_end, stop_ends, accept_then_fail, status_fail
        self.traces, self.net, self.netctl_state = {}, {}, netctl_state

    def __call__(self, url, body=None, timeout=None):
        component, path = url.split('://')[1].split('/', 1)[0].split(':')[0], '/' + url.split('://')[1].split('/', 1)[1]
        component = {'gen': 'generator', 'gw': 'gateway', 'gwnet': 'netctl'}.get(component, component)
        sent = self.clock()
        self.clock.now += RTT
        self.calls.append((component, path.split('?')[0], body, sent))
        error = self.fail.get((component, path.split('?')[0]))
        if error:
            raise error
        if component == 'netctl':
            return self.netctl(path, body, sent)
        if component == 'generator':
            return self.generator(path, body)
        if component == 'gateway':
            return dict(active=0, counters={}) if path == '/health' else dict(status='reset')
        if path == '/health':
            return dict(pod_uid='uid-'+component)
        return dict(QUEUE_IDLE, waiting=1) if self.busy else dict(QUEUE_IDLE)  # worker /run mode=queue

    def netctl(self, path, body, sent):
        if path == '/start':
            self.net['t0'] = sent + body['delay_s']
        if path == '/restore':
            self.net.setdefault('restored', sent)
        if path.startswith('/status'):
            end = self.net.get('t0', float('inf')) + live.design.DESIGN['window_s']
            state = self.netctl_state or ('armed' if 't0' not in self.net else 'restored' if self.net.get('restored', end) < end
                                          else 'completed' if sent >= end else 'running')
            return dict(trace='burst', events=[dict(kind='window_end')] if state == 'completed' else [], state=state)
        return dict(status='ok', label=(body or {}).get('label'))

    def generator(self, path, body):
        if path == '/trace':
            self.traces[body['label']] = dict(state=self.trace_end, inflight=0)
            if self.accept_then_fail:
                raise self.accept_then_fail
            return dict(status='started')
        if path == '/stop':
            for t in self.traces.values():
                if t['state'] == 'running' and self.stop_ends:
                    t['state'] = 'done'
            return dict(status='stopping')
        if path == '/status':
            if self.status_fail and self.traces:  # only after a trace was admitted (not the pre-cell barrier)
                self.status_fail -= 1
                raise TimeoutError('status reply timed out')
            return dict(traces={k: dict(v) for k, v in self.traces.items()})
        if path.startswith('/ledger'):
            label = path.split('label=')[1].split('&')[0]
            if label not in self.traces:
                raise OSError('HTTP 400 unknown label')
            state = self.traces[label]['state']
            if state == 'running' and 'partial=1' not in path:
                raise OSError('HTTP 400 trace still running')
            return dict(label=label, state=state, partial=state == 'running',
                        rows=[dict(index=0, status='ok'), dict(index=1, status=None), dict(index=2, status=None)])
        return dict(status='started')


class Session:
    def __init__(self, states=None):
        self.run, self.saved, self.states = 'un-test', {}, states

    def save(self, name, value):
        self.saved[name] = value

    def pod_states(self):
        return self.states or {k: dict(metadata=dict(uid='uid-'+k), status=dict(phase='Running', containerStatuses=[dict(restartCount=0, state={})]))
                               for k in ('cpu-e', 'cpu-c')}


def collector(fail=None, busy=False, states=None, budget=None, **generator):
    clock = Clock()
    pods = Pods(clock, fail, busy, **generator)
    lv = live.Live(Session(states), plan={'quality': {}}, plan_dir='.', http=pods, clock=clock, sleep=clock.sleep,
                   budget=budget(clock) if budget else None)
    lv.workers = [dict(key='cpu-e', zone='edge'), dict(key='cpu-c', zone='cloud')]
    lv.urls = {'cpu-e': 'http://cpu-e:8080', 'cpu-c': 'http://cpu-c:8080', 'generator': 'http://gen:8080', 'gateway': 'http://gw:8080',
               'netctl': 'http://gwnet:8081'}
    lv.uids = {'cpu-e': 'uid-cpu-e', 'cpu-c': 'uid-cpu-c'}
    lv.cloud_ips, lv.edge_ips, lv.quality, lv.trace_questions = ['10.0.0.2'], ['10.0.0.3'], dict(mode='diagnostic', qmin=0.4), ['1', '2']
    return lv, pods, clock


CELL = dict(block=0, trace='burst', policy='dag_reopt', position=0, rate_rps=1.6, seed=123, label='b0-burst-dag_reopt')
PROFILE = dict(compute_ms={}, links={}, payload_bytes={}, source='synthetic-test')


def key_calls(pods):
    keep = {('netctl', '/arm'), ('generator', '/trace'), ('netctl', '/start'), ('netctl', '/restore'), ('generator', '/ledger'),
            ('netctl', '/status'), ('netctl', '/teardown')}
    return [(c, p) for c, p, _, _ in pods.calls if (c, p) in keep]


class CellSequence(unittest.TestCase):
    def test_arm_trace_start_window_restore_salvage_teardown_in_order(self):
        lv, pods, clock = collector()
        lv.trace_cell(CELL, PROFILE, deadline=clock() + 10000)
        calls = [c for i, c in enumerate(key_calls(pods)) if i == 0 or c != key_calls(pods)[i-1]]  # window polls collapsed
        self.assertEqual(calls, [('netctl', '/arm'), ('generator', '/trace'), ('netctl', '/start'), ('netctl', '/status'),
                                 ('netctl', '/restore'), ('generator', '/ledger'), ('netctl', '/status'), ('netctl', '/teardown')])

    def test_the_netctl_start_is_aligned_from_the_collector_round_trips(self):
        lv, pods, clock = collector()
        lv.trace_cell(CELL, PROFILE, deadline=clock() + 10000)
        start = next(b for c, p, b, _ in pods.calls if (c, p) == ('netctl', '/start'))
        self.assertAlmostEqual(start['delay_s'], 1.99, places=9)
        align = lv.s.saved['align-b0-burst-dag_reopt.json']
        self.assertAlmostEqual(align['bound_ms'], 30.0, places=6)
        self.assertTrue(align['within_tolerance'])

    def test_the_generator_receives_no_trace_phase_or_network_value(self):
        lv, pods, clock = collector()
        lv.trace_cell(CELL, PROFILE, deadline=clock() + 10000)
        body = next(b for c, p, b, _ in pods.calls if (c, p) == ('generator', '/trace'))
        self.assertEqual(set(body), set(generator.TRACE_KEYS))
        self.assertNotIn('burst', repr({k: v for k, v in body.items() if k != 'label'}))

    def test_the_netctl_arm_names_the_frozen_trace_and_its_hash(self):
        lv, pods, clock = collector()
        lv.trace_cell(CELL, PROFILE, deadline=clock() + 10000)
        arm = next(b for c, p, b, _ in pods.calls if (c, p) == ('netctl', '/arm'))
        self.assertEqual((arm['trace'], arm['trace_sha256'], arm['run_id']), ('burst', traces.trace_sha256('burst'), 'un-test'))


class FailureHandling(unittest.TestCase):
    def test_a_failed_generator_start_still_restores_and_tears_down_without_starting_netctl(self):
        lv, pods, clock = collector(fail={('generator', '/trace'): OSError('refused')})
        lv.trace_cell(CELL, PROFILE, deadline=clock() + 10000)
        calls = key_calls(pods)
        self.assertNotIn(('netctl', '/start'), calls)
        self.assertEqual(calls[-1], ('netctl', '/teardown'))
        self.assertIn(('netctl', '/restore'), calls)

    def test_a_failed_restore_does_not_block_ledger_salvage_or_teardown(self):
        lv, pods, clock = collector(fail={('netctl', '/restore'): OSError('netctl gone')})
        lv.trace_cell(CELL, PROFILE, deadline=clock() + 10000)
        calls = key_calls(pods)
        self.assertIn(('generator', '/ledger'), calls)
        self.assertEqual(calls[-1], ('netctl', '/teardown'))
        self.assertIn('netctl-restore-b0-burst-dag_reopt.error.json', lv.s.saved)

    def test_a_fleet_that_is_not_idle_skips_the_cell_before_any_network_change(self):
        lv, pods, clock = collector(busy=True)
        lv.trace_cell(CELL, PROFILE, deadline=clock() + 10000)
        self.assertEqual(key_calls(pods), [])
        self.assertEqual(lv.skipped[0]['reason'], 'not idle before trace (drain timeout)')

    def test_a_lost_worker_marks_the_cell_fault_affected_and_stops_later_cells(self):
        dead = {'cpu-e': dict(metadata=dict(uid='uid-cpu-e'), status=dict(phase='Running', containerStatuses=[dict(restartCount=0, state={})])),
                'cpu-c': dict(metadata=dict(uid='uid-cpu-c'), status=dict(phase='Failed', containerStatuses=[
                    dict(restartCount=0, state=dict(terminated=dict(reason='OOMKilled')))]))}
        lv, pods, clock = collector(states=dead)
        second = dict(CELL, label='b0-burst-dag_static', policy='dag_static', position=1)
        lv.run_cells([CELL, second], PROFILE, deadline=clock() + 100000)
        self.assertIn('fault-b0-burst-dag_reopt.json', lv.s.saved)
        self.assertEqual(lv.s.saved['fault-b0-burst-dag_reopt.json']['problems'][0]['terminated'], ['OOMKilled'])
        self.assertEqual([s['label'] for s in lv.skipped], ['b0-burst-dag_static'])
        self.assertEqual(sum(1 for c, p in key_calls(pods) if p == '/arm'), 1)


SECOND = dict(CELL, label='b0-burst-dag_static', policy='dag_static', position=1)


def gen_calls(pods, *paths):
    return [p for c, p, _, _ in pods.calls if c == 'generator' and p in paths]


def arms(pods):
    return sum(1 for c, p in key_calls(pods) if p == '/arm')


class GeneratorStopAndSalvage(unittest.TestCase):
    """R1 standards F1: any accepted or uncertain start error stops admission, settles, then salvages."""

    def test_a_status_timeout_after_an_accepted_start_stops_admission_before_salvage(self):
        lv, pods, clock = collector(trace_end='running', status_fail=1)
        lv.trace_cell(CELL, PROFILE, deadline=clock() + 10000)
        self.assertEqual(gen_calls(pods, '/stop', '/ledger')[:2], ['/stop', '/ledger'])
        self.assertEqual(lv.s.saved['ledger-b0-burst-dag_reopt.json']['state'], 'done')
        self.assertIn('status reply timed out', lv.s.saved['trace-b0-burst-dag_reopt.error.json']['error'])
        self.assertEqual(lv.s.saved['generation-b0-burst-dag_reopt.json']['outcome'], 'terminal')
        self.assertEqual(lv.faults, [])

    def test_an_uncertain_start_that_the_generator_admitted_is_stopped_and_salvaged(self):
        lv, pods, clock = collector(trace_end='running', accept_then_fail=TimeoutError('reply lost'))
        lv.trace_cell(CELL, PROFILE, deadline=clock() + 10000)
        self.assertNotIn(('netctl', '/start'), key_calls(pods))
        self.assertIn('/stop', gen_calls(pods, '/stop'))
        self.assertEqual(lv.s.saved['ledger-b0-burst-dag_reopt.json']['state'], 'done')
        self.assertEqual(lv.s.saved['trace-b0-burst-dag_reopt.error.json']['stage'], 'generator_start')

    def test_a_start_the_generator_never_admitted_is_recorded_and_not_a_fault(self):
        lv, pods, clock = collector(fail={('generator', '/trace'): OSError('refused')})
        lv.trace_cell(CELL, PROFILE, deadline=clock() + 10000)
        self.assertEqual(lv.s.saved['generation-b0-burst-dag_reopt.json']['outcome'], 'not_admitted')
        self.assertEqual(lv.faults, [])
        self.assertEqual(key_calls(pods)[-1], ('netctl', '/teardown'))

    def test_a_generator_that_does_not_stop_keeps_its_partial_rows_and_fences_later_cells(self):
        lv, pods, clock = collector(trace_end='running', status_fail=1, stop_ends=False)
        lv.run_cells([CELL, SECOND], PROFILE, deadline=clock() + 100000)
        ledger = lv.s.saved['ledger-b0-burst-dag_reopt.json']
        self.assertTrue(ledger['partial'])
        self.assertEqual([r['index'] for r in ledger['rows']], [0, 1, 2])
        self.assertEqual(lv.s.saved['generation-b0-burst-dag_reopt.json']['outcome'], 'unresolved')
        self.assertIn('generation_unresolved', [f['kind'] for f in lv.faults])
        self.assertNotIn(('netctl', '/teardown'), key_calls(pods))
        self.assertEqual([s['label'] for s in lv.skipped], ['b0-burst-dag_static'])
        self.assertEqual(arms(pods), 1)

    def test_a_failed_salvage_of_a_stopped_trace_fences_later_cells(self):
        lv, pods, clock = collector(fail={('generator', '/ledger'): OSError('ledger gone')})
        lv.run_cells([CELL, SECOND], PROFILE, deadline=clock() + 100000)
        self.assertIn('ledger-b0-burst-dag_reopt.error.json', lv.s.saved)
        self.assertIn('salvage_failed', [f['kind'] for f in lv.faults])
        self.assertEqual(arms(pods), 1)

    def test_idle_workers_are_not_a_stopped_generator(self):
        lv, pods, clock = collector()
        pods.traces['earlier'] = dict(state='running', inflight=0)
        idle, _, state = lv.quiescence()
        self.assertFalse(idle)
        self.assertEqual(state['generator_running'], ['earlier'])


def busy_after_ledger(lv, pods):
    base = lv.http

    def http(url, body=None, timeout=None):
        result = base(url, body, timeout)
        if '/ledger?' in url:
            pods.busy = True
        if url.endswith('/teardown'):
            pods.busy = False  # a root delete would flush the backlog: the next cell must not see it as idle
        return result
    lv.http = http


class DrainFence(unittest.TestCase):
    """R1 spec F2 / standards F2: a failed or unknown post-cell drain fences teardown and every later cell."""

    def test_a_failed_post_cell_drain_skips_normal_teardown_and_fences_later_cells(self):
        lv, pods, clock = collector()
        busy_after_ledger(lv, pods)
        lv.run_cells([CELL, SECOND], PROFILE, deadline=clock() + 100000)
        self.assertFalse(lv.s.saved['drain-after-b0-burst-dag_reopt.json']['idle'])
        self.assertNotIn(('netctl', '/teardown'), key_calls(pods))
        self.assertIn('netctl-teardown-b0-burst-dag_reopt.skipped.json', lv.s.saved)
        self.assertIn(('netctl', '/status'), key_calls(pods))
        self.assertEqual(lv.faults[0]['kind'], 'drain_not_idle')
        self.assertEqual(arms(pods), 1)
        self.assertEqual([s['label'] for s in lv.skipped], ['b0-burst-dag_static'])

    def test_an_unreachable_drain_is_unknown_and_fences_too(self):
        lv, pods, clock = collector()
        base = lv.http

        def http(url, body=None, timeout=None):
            if '/run' in url and pods.calls and any(p == '/ledger' for _, p, _, _ in pods.calls):
                raise OSError('worker unreachable')
            return base(url, body, timeout)
        lv.http = http
        lv.run_cells([CELL, SECOND], PROFILE, deadline=clock() + 100000)
        self.assertEqual(lv.faults[0]['kind'], 'drain_not_idle')
        self.assertNotIn(('netctl', '/teardown'), key_calls(pods))
        self.assertEqual(arms(pods), 1)

    def test_the_end_of_run_teardown_after_a_fence_is_labelled_emergency_not_a_barrier(self):
        lv, pods, clock = collector()
        busy_after_ledger(lv, pods)
        lv.run_cells([CELL, SECOND], PROFILE, deadline=clock() + 100000)
        lv.close_network()
        record = lv.s.saved['netctl-emergency-teardown.json']
        self.assertTrue(record['emergency'])
        self.assertFalse(record['idle_barrier'])
        self.assertEqual(record['fenced_by'], ['b0-burst-dag_reopt'])
        self.assertEqual(key_calls(pods)[-1], ('netctl', '/teardown'))

    def test_a_clean_run_end_teardown_is_not_emergency(self):
        lv, pods, clock = collector()
        lv.run_cells([CELL], PROFILE, deadline=clock() + 100000)
        lv.close_network()
        self.assertFalse(lv.s.saved['netctl-final-teardown.json']['emergency'])
        self.assertNotIn('netctl-emergency-teardown.json', lv.s.saved)


class TimingMark(unittest.TestCase):
    def test_an_alignment_over_tolerance_marks_the_cell_not_comparable(self):
        lv, pods, clock = collector()
        base = lv.http

        def slow_start(url, body=None, timeout=None):
            result = base(url, body, timeout)
            if url.endswith('/start'):
                clock.now += 1.0
            return result
        lv.http = slow_start
        lv.trace_cell(CELL, PROFILE, deadline=clock() + 10000)
        align = lv.s.saved['align-b0-burst-dag_reopt.json']
        self.assertFalse(align['within_tolerance'])
        self.assertFalse(align['comparable_timing'])
        self.assertEqual(lv.timing_invalid, ['b0-burst-dag_reopt'])


def sent_at(pods, component, path):
    return next(t for c, p, _, t in pods.calls if (c, p) == (component, path))


class WindowCompletion(unittest.TestCase):
    """R2 spec F1: a normal completion restores only after the netctl ran the whole frozen window, however early the
    generator finished; a failed or unreachable netctl window is bounded and marks the timing invalid."""

    WINDOW = live.design.DESIGN['window_s']

    def test_a_generator_done_before_the_window_end_restores_only_after_the_netctl_window_end(self):
        lv, pods, clock = collector()  # terminal at the first poll: faster than any real policy
        lv.trace_cell(CELL, PROFILE, deadline=clock() + 10000)
        self.assertGreaterEqual(sent_at(pods, 'netctl', '/restore'), pods.net['t0'] + self.WINDOW)
        self.assertEqual(lv.s.saved['window-b0-burst-dag_reopt.json']['outcome'], 'completed')
        self.assertEqual(lv.timing_invalid, [])
        self.assertEqual(lv.s.saved['netctl-status-b0-burst-dag_reopt.json']['state'], 'completed')

    def test_a_fast_and_a_slow_generator_both_restore_after_the_window_end(self):
        restored = []
        for finish_s in (0.1, 30.0, 70.0):
            lv, pods, clock = collector(trace_end='running')
            base, done = lv.http, {}

            def http(url, body=None, timeout=None):
                if url.endswith('/status') and 'gen' in url and 't0' in pods.net and clock() >= pods.net['t0'] + finish_s:
                    pods.traces[CELL['label']]['state'] = 'done'
                return base(url, body, timeout)
            lv.http = http
            lv.trace_cell(CELL, PROFILE, deadline=clock() + 10000)
            restored.append(sent_at(pods, 'netctl', '/restore') - pods.net['t0'])
            self.assertEqual(lv.s.saved['window-b0-burst-dag_reopt.json']['outcome'], 'completed')
            self.assertEqual(lv.timing_invalid, [])
        self.assertTrue(all(r >= self.WINDOW for r in restored), restored)

    def test_an_unreachable_netctl_window_is_bounded_and_marks_the_timing_invalid(self):
        lv, pods, clock = collector(fail={('netctl', '/status'): OSError('netctl unreachable')})
        lv.trace_cell(CELL, PROFILE, deadline=clock() + 10000)
        window = lv.s.saved['window-b0-burst-dag_reopt.json']
        self.assertEqual(window['outcome'], 'unknown')
        self.assertLessEqual(sent_at(pods, 'netctl', '/restore'), window['until_s'] + live.design.BUDGETS['control_timeout_s'])
        self.assertEqual(lv.timing_invalid, ['b0-burst-dag_reopt'])
        self.assertEqual(key_calls(pods)[-1], ('netctl', '/teardown'))

    def test_a_failed_netctl_window_stops_the_wait_and_marks_the_timing_invalid(self):
        lv, pods, clock = collector(netctl_state='failed')
        lv.trace_cell(CELL, PROFILE, deadline=clock() + 10000)
        self.assertEqual(lv.s.saved['window-b0-burst-dag_reopt.json']['outcome'], 'failed')
        self.assertLess(sent_at(pods, 'netctl', '/restore'), pods.net['t0'] + self.WINDOW)
        self.assertEqual(lv.timing_invalid, ['b0-burst-dag_reopt'])

    def test_an_error_after_the_start_still_stops_at_once_without_waiting_for_the_window(self):
        lv, pods, clock = collector(trace_end='running', status_fail=1)
        lv.trace_cell(CELL, PROFILE, deadline=clock() + 10000)
        self.assertLess(sent_at(pods, 'generator', '/stop'), pods.net['t0'])
        self.assertLess(sent_at(pods, 'netctl', '/restore'), sent_at(pods, 'generator', '/stop'))
        self.assertNotIn('window-b0-burst-dag_reopt.json', lv.s.saved)


def balanced_cells():
    """A stdlib cyclic Latin schedule that satisfies design.check_schedule (the real plan uses the frozen helper)."""
    names = [s['name'] for s in live.design.POLICY_SPECS]
    cells, design = [], live.design.DESIGN
    for g, (b, t) in enumerate((b, t) for b in range(design['blocks']) for t in design['traces']):
        seed = live.design.trace_seed(design['seed'], b, t, design['rate_rps'])
        cells += [dict(block=b, trace=t, policy=names[(g + j) % 6], position=j, rate_rps=design['rate_rps'], seed=seed,
                       label=f'b{b}-{t}-{names[(g + j) % 6]}') for j in range(6)]
    return cells


class WallBudget(unittest.TestCase):
    """R1 spec F3: one absolute wall budget from before preparation, with a cleanup reserve."""

    def test_preparation_time_is_taken_from_the_collection_budget(self):
        b = live.design.BUDGETS
        lv, pods, clock = collector(budget=live.WallBudget)
        lv.plan = dict(quality={}, pairs=[dict(question_id='1', split='evaluation')], schedule=dict(cells=balanced_cells()))
        self.assertEqual(live.design.check_schedule(lv.plan['schedule']['cells']), [])
        prepared_until = lv.budget.started + b['wall_budget_s'] - b['cleanup_reserve_s'] - 200  # less than one cell's need left

        def deploy():  # stand-in for kubectl deploy/upload/readiness: only its elapsed time matters here
            clock.now = prepared_until
        lv.deploy, lv.configure, lv.profile = deploy, lambda: None, lambda: None
        lv.calibrate_network = lambda: PROFILE
        lv.run()
        collection = lv.s.saved['collection.json']
        self.assertEqual(len(collection['skipped']), 36)
        self.assertTrue(all(s['reason'] == 'wall budget' for s in collection['skipped']))
        self.assertEqual(arms(pods), 0)
        self.assertEqual(collection['wall_budget']['budget_s'], 10800)

    def test_control_calls_are_bounded_by_the_remaining_work_budget(self):
        lv, pods, clock = collector(budget=live.WallBudget)
        seen = []
        base = lv.http
        lv.http = lambda url, body=None, timeout=None: (seen.append(timeout), base(url, body, timeout))[1]
        clock.now = lv.budget.work_until - 5
        lv.control('http://gw:8080/health')
        self.assertLessEqual(seen[-1], 5)
        clock.now = lv.budget.work_until + 1
        with self.assertRaises(live.bounded_http.DeadlineExceeded):
            lv.control('http://gw:8080/health')

    def test_the_end_of_run_network_cleanup_may_use_the_reserve(self):
        lv, pods, clock = collector(budget=live.WallBudget)
        clock.now = lv.budget.work_until + 10
        lv.close_network()
        self.assertEqual(key_calls(pods)[-1], ('netctl', '/teardown'))
        self.assertIn('netctl-final-teardown.json', lv.s.saved)


class Kubectl:
    """Fake `rtk proxy kubectl` at the subprocess boundary: every call takes 1 s of the fake clock (duration(args) if
    given); owned objects exist until a UID-precondition delete. Records the timeout and args of each call; a call longer
    than its timeout is killed at the timeout like subprocess.run, keeping the partial stdout it wrote."""

    def __init__(self, clock, run, duration=None):
        self.clock, self.run, self.timeouts, self.calls, self.duration = clock, run, [], [], duration or (lambda args: 1.0)
        self.present = {run: dict(kind='ConfigMap', uid='uid-cm'), run+'-generator': dict(kind='Pod', uid='uid-gen')}

    def __call__(self, command, stdin=None, input=None, stdout=None, stderr=None, timeout=None):
        self.timeouts.append((self.clock(), timeout))
        args = [a for a in command[3:] if not a.startswith('--request-timeout')]
        self.calls.append(dict(at=self.clock(), timeout=timeout, args=args))
        took = self.duration(args)
        if timeout < took:
            if args[0] == 'logs':
                stdout.write(b'partial log')
            self.clock.now += timeout
            raise subprocess.TimeoutExpired(command, timeout)
        self.clock.now += took
        out, err, rc = '{}', '', 0
        if args[:2] == ['get', 'pods,configmaps']:
            out = json.dumps(dict(items=[dict(kind=v['kind'], metadata=dict(name=k, uid=v['uid'], labels={'run-id': self.run}))
                                         for k, v in self.present.items()]))
        elif args[:3] == ['get', 'pods', '-A']:
            out = json.dumps(dict(items=[]))
        elif args[0] == 'get' and args[1] in ('pod', 'configmap'):
            name = args[4]
            if name in self.present:
                out = json.dumps(dict(metadata=dict(name=name, uid=self.present[name]['uid']), spec=dict(nodeName=live.design.SERVER_NODE)))
            else:
                out, err, rc = '', f'Error from server (NotFound): {name}', 1
        elif args[:2] == ['delete', '--raw']:
            self.present.pop(args[2].rsplit('/', 1)[1], None)
        if stdout is subprocess.PIPE:
            return types.SimpleNamespace(returncode=rc, stdout=out.encode())
        stdout.write(out.encode())
        stderr.write(err.encode())
        return types.SimpleNamespace(returncode=rc)


class CleanupReserve(unittest.TestCase):
    """The real collect.cleanup through UnstableSession: kubectl calls are bounded by the absolute wall end, the cleanup
    reserve stays usable after the work budget is spent, and an unverifiable cleanup is reported, never success."""

    def session(self, at):
        clock = Clock()
        budget = live.WallBudget(clock)
        root = Path(tempfile.mkdtemp(prefix='un-cleanup-'))
        self.addCleanup(shutil.rmtree, root)
        s = live.UnstableSession(root, 'un-test', dict(workers=[]), budget=budget)
        s.resources += [dict(kind='ConfigMap', name='un-test', uid='uid-cm'), dict(kind='Pod', name='un-test-generator', uid='uid-gen')]
        s.save('protected-before.json', {})
        clock.now = at(budget)
        return s, budget, clock, Kubectl(clock, 'un-test')

    def test_cleanup_runs_inside_the_reserve_after_the_work_budget_is_spent(self):
        s, budget, clock, kubectl = self.session(lambda b: b.work_until + 10)
        with mock.patch('subprocess.run', kubectl):
            with self.assertRaises(live.bounded_http.DeadlineExceeded):
                s.call('late-work', ['get', 'pods', '-n', live.NS])
            s.begin_cleanup()
            remaining, errors = live.ownership.cleanup(s)
        self.assertEqual(remaining, 0)
        self.assertTrue(json.loads((s.out/'cleanup.json').read_text())['cleanup_verified'])
        self.assertTrue(all(timeout <= budget.end - at + 1e-9 for at, timeout in kubectl.timeouts))
        self.assertLessEqual(clock(), budget.end)

    def test_cleanup_that_cannot_finish_before_the_wall_end_is_reported_unverified(self):
        s, budget, clock, kubectl = self.session(lambda b: b.cleanup_until - 2.5)
        with mock.patch('subprocess.run', kubectl):
            s.begin_cleanup()
            remaining, errors = live.ownership.cleanup(s)
        record = json.loads((s.out/'cleanup.json').read_text())
        self.assertIsNone(remaining)
        self.assertFalse(record['cleanup_verified'])
        self.assertTrue(any('wall budget' in e for e in errors))
        self.assertTrue(list(s.out.glob('*.deadline.json')))
        self.assertTrue(kubectl.present)  # the owned objects really were left; the record says so


def slow_server_ownership_get(run, guard_s=59.0, lookups_before=1):
    """The R2 probes' timing: the fresh stream-owner GET of the server cache Pod takes guard_s (its own limit is 60 s);
    the existence lookup(s) of the same Pod before it (collect.cleanup: one) and every other call 1 s; logs would stream
    for 30 s; the protected snapshot (`get pods -A`) takes its whole 40 s."""
    gets = []

    def duration(args):
        if args[:3] == ['get', 'pods', '-A']:
            return live.PROTECTED_SNAPSHOT_S
        if args[0] == 'logs':
            return 30.0
        if args[:2] == ['get', 'pod'] and args[4] == run+'-cache':
            gets.append(args)
            return 1.0 if len(gets) <= lookups_before else guard_s
        return 1.0
    return duration


class StreamBudget(unittest.TestCase):
    """R2 spec F2 / standards P2: the fresh ownership GET before a server-Pod stream consumes wall time, so the stream's
    timeout is recomputed after it; the protected-after snapshot of collect.cleanup is bounded by the absolute wall end
    or skipped."""

    def session(self, before_cleanup_until, slow=True, **timing):
        clock = Clock()
        budget = live.WallBudget(clock)
        root = Path(tempfile.mkdtemp(prefix='un-stream-'))
        self.addCleanup(shutil.rmtree, root)
        s = live.UnstableSession(root, 'un-test', dict(workers=[]), budget=budget)
        s.resources += [dict(kind='Pod', name='un-test-cache', uid='uid-cache')]
        s.save('protected-before.json', {})
        kubectl = Kubectl(clock, 'un-test', slow_server_ownership_get('un-test', **timing) if slow else None)
        kubectl.present['un-test-cache'] = dict(kind='Pod', uid='uid-cache')
        s.begin_cleanup()
        clock.now = budget.cleanup_until - before_cleanup_until
        return s, budget, clock, kubectl

    def test_the_stream_timeout_is_recomputed_after_the_fresh_ownership_get(self):
        s, budget, clock, kubectl = self.session(62, lookups_before=0)
        with mock.patch('subprocess.run', kubectl):
            with self.assertRaises(subprocess.TimeoutExpired):
                s.call('logs-final-cache', ['logs', '-n', live.NS, 'un-test-cache'], timeout=30)
        guard, stream = kubectl.calls
        self.assertEqual(guard['args'][:2], ['get', 'pod'])  # the fresh UID guard still runs first
        self.assertEqual(stream['args'][0], 'logs')
        self.assertAlmostEqual(stream['timeout'], budget.cleanup_until - stream['at'])
        self.assertAlmostEqual(stream['timeout'], 3.0)  # not the 30 s nor the 62 s left before the GET
        self.assertLessEqual(clock(), budget.cleanup_until)
        self.assertEqual(next(s.out.glob('[0-9]*[0-9]-logs-final-cache.stdout')).read_bytes(), b'partial log')

    def test_a_stream_with_no_time_left_after_the_ownership_get_is_refused_before_kubectl(self):
        s, budget, clock, kubectl = self.session(60, guard_s=60.0, lookups_before=0)  # the GET uses its whole 60 s
        with mock.patch('subprocess.run', kubectl):
            with self.assertRaises(live.WallBudgetExceeded):
                s.call('logs-final-cache', ['logs', '-n', live.NS, 'un-test-cache'], timeout=30)
        self.assertEqual([c['args'][0] for c in kubectl.calls], ['get'])
        self.assertTrue(list(s.out.glob('*-logs-final-cache.deadline.json')))

    def test_both_review_cleanup_probes_end_by_the_wall_end_and_report_unverified(self):
        for before in (62, 65):  # spec R2 (cleanup_until - 62) and standards R2 (- 65) starting points
            with self.subTest(before=before):
                s, budget, clock, kubectl = self.session(before)
                with mock.patch('subprocess.run', kubectl):
                    remaining, errors = live.cleanup(s)
                record = json.loads((s.out/'cleanup.json').read_text())
                snapshot = next(c for c in kubectl.calls if c['args'][:3] == ['get', 'pods', '-A'])
                self.assertLessEqual(snapshot['at'] + snapshot['timeout'], budget.end)
                self.assertLessEqual(clock(), budget.end)
                self.assertTrue(all(c['at'] + c['timeout'] <= budget.cleanup_until + 1e-9 for c in kubectl.calls if c is not snapshot))
                self.assertIsNone(remaining)
                self.assertFalse(record['cleanup_verified'])
                self.assertTrue(kubectl.present)

    def test_a_cleanup_with_no_time_left_for_the_protected_snapshot_skips_it_and_reports_unverified(self):
        s, budget, clock, kubectl = self.session(-15)  # 25 s before the wall end: the 40 s snapshot cannot fit
        with mock.patch('subprocess.run', kubectl):
            remaining, errors = live.cleanup(s)
        record = json.loads((s.out/'cleanup.json').read_text())
        self.assertEqual(kubectl.calls, [])
        self.assertIsNone(remaining)
        self.assertFalse(record['cleanup_verified'])
        self.assertEqual(record['remaining_owned_pods_and_configmaps'], None)
        self.assertEqual({r['uid'] for r in record['created']}, {'uid-cache'})
        self.assertTrue(any('wall budget' in e for e in errors))
        self.assertLessEqual(clock(), budget.end)

    def test_a_cleanup_with_time_left_still_runs_the_frozen_cleanup_to_verification(self):
        s, budget, clock, kubectl = self.session(200, slow=False)
        with mock.patch('subprocess.run', kubectl):
            remaining, errors = live.cleanup(s)
        self.assertEqual(remaining, 0)
        self.assertTrue(json.loads((s.out/'cleanup.json').read_text())['cleanup_verified'])
        self.assertIn('logs', [c['args'][0] for c in kubectl.calls])


class Alignment(unittest.TestCase):
    def test_bound_is_half_the_generator_round_trip_plus_the_netctl_round_trip(self):
        a = live.alignment(10.0, 10.1, 10.1, 10.15, 2.0)
        self.assertAlmostEqual(a['netctl_delay_s'], 1.95)
        self.assertAlmostEqual(a['bound_ms'], 100.0)


if __name__ == '__main__':
    unittest.main()
