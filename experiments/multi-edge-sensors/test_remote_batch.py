"""Regression for the r1 integration failure: one controller exec argument over the Linux per-argument
limit (OSError 7). Session.remote must split a large batch into bounded execs and keep every row in order,
and refuse a single over-limit call explicitly. Real controller runtime process behind the fake kubectl."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from test_collect import HERE, fake_env

sys.path.insert(0, str(HERE))


class RemoteBatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        env = fake_env(cls.tmp.name)
        import os
        os.environ.update({k: env[k] for k in ('PATH', 'FAKE_KUBE_STATE', 'FAKE_PYTHON', 'FAKE_NODES', 'FAKE_FAIL', 'FAKE_SCHED', 'MES_POD_PORT')})
        import collect
        cls.collect = collect
        session = collect.Session.__new__(collect.Session)
        session.out, session.run, session.counter, session.resources, session.unresolved = Path(cls.tmp.name)/'run', 'mes-batch', 0, [], []
        session.out.mkdir()
        session.cache_url = 'http://unused'
        code = {name: (HERE/name).read_text() for name in collect.CODE}
        code.update({'scenario.json': (HERE/'scenario-20261022.json').read_text(), 'bundles.json': '{}'})
        session.create('ctl', [dict(apiVersion='v1', kind='ConfigMap', metadata=session.meta('mes-batch-code', 'code'), data=code),
                               session.pod('mes-batch-ctl', 'controller', ['etri-ser0002-cgnmsb'], role='controller')])
        assert session.wait_ready('ctl', ['mes-batch-ctl'], 60) == {'mes-batch-ctl': None}
        session.controller = 'mes-batch-ctl'
        cls.session = session
        cls.url = f"http://{session.pod_state('mes-batch-ctl')['status']['podIP']}:{collect.PORT}"

    @classmethod
    def tearDownClass(cls):
        cls.session.delete_owned('cleanup', [r['name'] for r in cls.session.resources])
        cls.tmp.cleanup()

    def test_batch_far_over_one_argument_is_split_and_complete(self):
        calls = [dict(tag=f'{i:04d}-'+'x'*1500, path='/health') for i in range(300)]
        self.assertGreater(len(json.dumps(calls)), 128*1024)
        rows, error = self.session.remote('big', self.url+'/batch', calls, 120)
        self.assertIsNone(error)
        self.assertEqual([r['tag'] for r in rows], [c['tag'] for c in calls])
        self.assertTrue(all(r['status'] == 'ok' for r in rows))
        parts = sorted(p.name for p in self.session.out.glob('*-big-part*.event.json'))
        self.assertGreater(len(parts), 1)

    def test_single_call_over_the_limit_is_an_explicit_error(self):
        with self.assertRaisesRegex(RuntimeError, 'exceeds'):
            self.session.remote('huge', self.url+'/batch', [dict(tag='y'*(100*1024), path='/payload?bytes=1')], 60)


if __name__ == '__main__':
    unittest.main()
