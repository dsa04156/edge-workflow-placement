"""Source trace seam: bounded open-loop release and complete terminal ledger."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
import unittest


class TraceTests(unittest.TestCase):
    def test_slow_rpc_does_not_move_future_release_times_and_all_attempts_end(self):
        from load_trace import run_trace
        class Slow(BaseHTTPRequestHandler):
            def do_POST(self):
                time.sleep(.25)
                self.send_error(503)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Slow)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            rows = []
            config = dict(run_id='trace', phase='slow', question_ids=[1]*3, offsets_s=[0,.03,.06],
                endpoints={'fusion': f'http://127.0.0.1:{server.server_port}'}, remote_source='',
                representation='raw', deadline_s=1, max_inflight=4)
            summary = run_trace(config, {1: {'question_id': 1}}, {1: {}}, rows.append)
            attempts = [r for r in rows if r['event'] == 'terminal']
            releases = [r for r in rows if r['event'] == 'release']
            self.assertEqual(len(attempts), 3)
            self.assertEqual(summary['attempts'], 3)
            self.assertEqual(summary['failures'], 3)
            self.assertLess(max(r['release_lag_ms'] for r in releases), 80)
            self.assertTrue(all(r['status'] == 'error' for r in attempts))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
