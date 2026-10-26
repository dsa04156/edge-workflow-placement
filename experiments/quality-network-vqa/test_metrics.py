"""Release/terminal ledger -> goodput and official-quality aggregation seam (module API and CLI)."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import metrics  # noqa: E402


def gt(qid, answer):
    return dict(question_id=qid, split='evaluation',
                human_answers=[dict(answer=answer, answer_confidence='yes', answer_id=i+1) for i in range(10)])


PAIRS = {1: gt(1, 'cat'), 2: gt(2, 'dog'), 3: gt(3, 'red'), 4: gt(4, 'two')}


def config(n=4, rate=1.0):
    return dict(phase='p', question_ids=[1, 2, 3, 4][:n], offsets_s=[i/rate for i in range(n)], rate_rps=rate, deadline_s=10)


def release(i, s):
    return dict(event='release', index=i, question_id=[1, 2, 3, 4][i], scheduled_s=s, actual_release_s=s+.01)


def done(i, s, finished, answer, status='ok'):
    # Shape of load_trace terminal rows: output survives even when the old code relabels status.
    return dict(event='terminal', index=i, question_id=[1, 2, 3, 4][i], scheduled_s=s, actual_release_s=s+.4,
                finished_s=finished, status=status, quality_ok=status == 'ok', output=dict(answers=[answer, 'x']))


def ledger(*terminals, end=True):
    events = [dict(event='phase_start')]
    for t in terminals:
        events += [release(t['index'], t['scheduled_s']), t]
    return events + ([dict(event='phase_end')] if end else [])


class Goodput(unittest.TestCase):
    def summarize(self, events, cfg=None, slo_s=2.0, qmin=.4):
        return metrics.summarize_phase(events, cfg or config(), PAIRS, slo_s=slo_s, qmin=qmin)

    def test_latency_is_from_scheduled_origin_not_actual_release(self):
        out = self.summarize(ledger(done(0, 0, 1.9, 'cat'), done(1, 1, 3.05, 'dog'),
                                    done(2, 2, 3.5, 'red'), done(3, 3, 4.0, 'two')))
        row = out['requests'][1]
        self.assertAlmostEqual(row['latency_ms'], 2050.0)
        self.assertEqual(row['outcome'], 'late')
        self.assertEqual(out['counts']['on_time'], 3)

    def test_deadline_boundary_is_inclusive(self):
        out = self.summarize(ledger(done(0, 0, 2.0, 'cat'), done(1, 1, 3.0000001, 'dog'),
                                    done(2, 2, 3, 'red'), done(3, 3, 4, 'two')))
        self.assertEqual([r['outcome'] for r in out['requests']], ['on_time', 'late', 'on_time', 'on_time'])

    def test_late_success_keeps_quality_and_is_excluded_from_goodput(self):
        # Old trace relabels a >10 s completion as deadline_error; its output is still scored.
        out = self.summarize(ledger(done(0, 0, 12, 'cat', status='deadline_error'), done(1, 1, 2, 'dog'),
                                    done(2, 2, 3, 'red'), done(3, 3, 4, 'two')))
        self.assertEqual(out['requests'][0]['outcome'], 'late')
        self.assertEqual(out['requests'][0]['vqa_accuracy'], 1.0)
        self.assertEqual(out['counts']['on_time'], 3)
        self.assertAlmostEqual(out['goodput_rps'], 3/4)
        self.assertEqual(out['quality']['returned']['count'], 4)

    def test_drops_and_errors_stay_in_the_arrival_denominator(self):
        drop = dict(event='terminal', index=1, question_id=2, scheduled_s=1, actual_release_s=1, finished_s=1,
                    status='driver_capacity', quality_ok=False)
        error = dict(event='terminal', index=2, question_id=3, scheduled_s=2, actual_release_s=2, finished_s=12,
                     status='error', error="TimeoutError('Absolute workflow deadline elapsed')", quality_ok=False)
        out = self.summarize(ledger(done(0, 0, 1, 'cat'), drop, error, done(3, 3, 4, 'two')))
        self.assertEqual(out['counts']['arrivals'], 4)
        self.assertEqual(out['counts']['dropped'], 1)
        self.assertEqual(out['counts']['timeout'], 1)
        self.assertAlmostEqual(out['on_time_rate'], .5)
        self.assertAlmostEqual(out['quality']['returned']['mean'], 1.0)
        self.assertAlmostEqual(out['quality']['all_arrivals_zero_imputed'], .5)
        self.assertAlmostEqual(out['quality']['coverage'], .5)

    def test_server_deadline_http_504_is_a_timeout_and_other_http_errors_are_not(self):
        # load_trace keeps repr(error) only; http_runtime answers queue/execution expiry with 504.
        def failed(i, error):
            return dict(event='terminal', index=i, question_id=i+1, scheduled_s=i, actual_release_s=i, finished_s=i+3,
                        status='error', error=error, quality_ok=False)
        out = self.summarize(ledger(failed(0, "<HTTPError 504: 'Gateway Timeout'>"), failed(1, "<HTTPError 500: 'Internal Server Error'>"),
                                    failed(2, "<HTTPError 429: 'Too Many Requests'>"), failed(3, "<HTTPError 502: 'timed out upstream'>")))
        self.assertEqual([r['outcome'] for r in out['requests']], ['timeout', 'error', 'error', 'error'])
        self.assertEqual([r['failure']['http_code'] for r in out['requests']], [504, 500, 429, 502])
        self.assertEqual(out['requests'][0]['failure']['cause'], 'server_deadline_http_504')
        self.assertEqual(out['counts']['timeout'], 1)
        self.assertEqual(out['counts']['error'], 3)

    def test_nested_chain_504_is_a_timeout_only_when_the_innermost_status_is_504(self):
        def chain(i, codes, message):
            evidence = dict(http_code=codes[0], http_codes=codes, message=message, url='http://x', body_excerpt='')
            return dict(event='terminal', index=i, question_id=i+1, scheduled_s=i, actual_release_s=i, finished_s=i+3, status='error',
                        error='HTTPChainError('+json.dumps(evidence, sort_keys=True)+')', quality_ok=False)
        out = self.summarize(ledger(chain(0, [500, 504], 'Expired during model execution'), chain(1, [500, 500], "RuntimeError('timed out')"),
                                    chain(2, [500, 429], 'Serving queue capacity reached'), chain(3, [502], 'upstream timed out')))
        self.assertEqual([r['outcome'] for r in out['requests']], ['timeout', 'error', 'error', 'error'])
        self.assertEqual(out['requests'][0]['failure']['cause'], 'nested_server_deadline_http_504')
        self.assertEqual([r['failure']['http_codes'] for r in out['requests']], [[500, 504], [500, 500], [500, 429], [502]])
        self.assertEqual(out['requests'][0]['failure']['message'], 'Expired during model execution')

    def test_missing_terminal_is_explicit_not_deleted(self):
        out = self.summarize(ledger(done(0, 0, 1, 'cat'), done(1, 1, 2, 'dog'), done(2, 2, 3, 'red'), end=False))
        self.assertEqual(out['counts']['missing_terminal'], 1)
        self.assertEqual(out['requests'][3]['outcome'], 'missing_terminal')
        self.assertFalse(out['ledger']['phase_end_seen'])
        self.assertAlmostEqual(out['on_time_rate'], .75)

    def test_duplicate_terminal_is_refused(self):
        events = ledger(done(0, 0, 1, 'cat'), done(1, 1, 2, 'dog'), done(2, 2, 3, 'red'), done(3, 3, 4, 'two'))
        events.insert(-1, done(0, 0, 1.5, 'cat'))
        with self.assertRaisesRegex(ValueError, 'Duplicate terminal'):
            self.summarize(events)

    def test_terminal_without_release_is_refused(self):
        events = [dict(event='phase_start'), done(0, 0, 1, 'cat'), dict(event='phase_end')]
        with self.assertRaisesRegex(ValueError, 'without release'):
            self.summarize(events, config(n=1))

    def test_quality_below_threshold_reports_goodput_and_failed_gate_separately(self):
        out = self.summarize(ledger(done(0, 0, 1, 'cat'), done(1, 1, 2, 'no'), done(2, 2, 3, 'no'), done(3, 3, 4, 'no')))
        self.assertAlmostEqual(out['goodput_rps'], 1.0)
        self.assertAlmostEqual(out['quality']['all_arrivals_zero_imputed'], .25)
        self.assertFalse(out['quality_gate']['passed'])
        self.assertEqual(out['quality_gate']['qmin'], .4)
        self.assertIsNone(out['quality_qualified_goodput_rps'])

    def test_cpu_reference_match_is_reported_but_not_the_quality(self):
        out = self.summarize(ledger(done(0, 0, 1, 'cat', status='quality_error'), done(1, 1, 2, 'dog'),
                                    done(2, 2, 3, 'red'), done(3, 3, 4, 'two')))
        self.assertEqual(out['counts']['cpu_reference_mismatch'], 1)
        self.assertEqual(out['requests'][0]['outcome'], 'on_time')
        self.assertEqual(out['requests'][0]['vqa_accuracy'], 1.0)

    def test_window_is_count_over_rate(self):
        out = self.summarize(ledger(*(done(i, i*2, i*2+1, a) for i, a in enumerate(['cat', 'dog', 'red', 'two']))),
                             config(rate=.5))
        self.assertEqual(out['window_s'], 8.0)
        self.assertAlmostEqual(out['goodput_rps'], .5)


class Cli(unittest.TestCase):
    def test_cli_summarizes_saved_phase_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp/'pairs.json').write_text(json.dumps(dict(pairs=list(PAIRS.values()))))
            (tmp/'p.config.json').write_text(json.dumps(config()))
            events = ledger(done(0, 0, 1, 'cat'), done(1, 1, 2, 'dog'), done(2, 2, 4.5, 'red'), done(3, 3, 4, 'two'))
            (tmp/'p.events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
            result = subprocess.run([sys.executable, str(HERE/'metrics.py'), '--pairs', str(tmp/'pairs.json'),
                                     '--config', str(tmp/'p.config.json'), '--events', str(tmp/'p.events.jsonl'),
                                     '--slo-s', '2.0', '--qmin', '0.4', '--out', str(tmp/'summary.json')],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            out = json.loads((tmp/'summary.json').read_text())
            self.assertEqual(out['counts']['on_time'], 3)
            self.assertEqual(out['counts']['late'], 1)


if __name__ == '__main__':
    unittest.main()
