"""Prepared CUDA runtime + persistent cache: offline provenance, bounded streaming install, cache hit/corruption/lock,
storage gate, no pip on the node, collector release/PVC binding and old-mode regression.

  python3 -m unittest test_prepared_runtime      (stdlib only; from experiments/gpu-mobilint-vlm)
"""
import base64
import contextlib
import fcntl
import hashlib
import http.server
import io
import json
import math
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT/'experiments'/'quality-network-heterogeneous')]
import vlm_runtime_cache as C  # noqa: E402
import prepare_runtime as R  # noqa: E402

MiB = 1 << 20
SNAPSHOT = ROOT/'.scratch'/'vlm-persistent-ready-20261028'/'source-before'


def tmpdir(test):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    return Path(tmp.name)


def build(test, contents, runtime=None):
    """(manifest, raw manifest bytes, tar bytes, expect) for {path: bytes} (mode 0o755 when the path ends with .so)."""
    src = tmpdir(test)
    files = []
    for path in sorted(contents):
        (src/path).parent.mkdir(parents=True, exist_ok=True)
        (src/path).write_bytes(contents[path])
        files.append(dict(path=path, size=len(contents[path]), sha256=hashlib.sha256(contents[path]).hexdigest(),
                          mode=0o755 if path.endswith('.so') else 0o644))
    C.write_tar(src/'runtime.tar', files, lambda f: src/f['path'])
    tar = (src/'runtime.tar').read_bytes()
    manifest = dict(format=C.FORMAT, recipe='cu130-aarch64-cp311', runtime=runtime or dict(machine='aarch64', python='3.11'),
                    frozen={'assets-manifest.json': 'a'*64, 'contract.json': hashlib.sha256(contents['contract.json']).hexdigest()},
                    model=dict(key='m'), files=files, file_count=len(files), total_bytes=sum(f['size'] for f in files),
                    content_sha256=C.content_sha256(files), tar=dict(name='runtime.tar', bytes=len(tar), sha256=hashlib.sha256(tar).hexdigest()))
    raw = json.dumps(manifest).encode()
    expect = dict(recipe='cu130-aarch64-cp311', machine='aarch64', python='3.11', asset_manifest_sha256='a'*64,
                  content_sha256=manifest['content_sha256'])
    return manifest, raw, tar, expect


TREE = {'contract.json': b'{"c":1}', 'models/m/model.safetensors': os.urandom(3*MiB+17), 'site/pkg/__init__.py': b'x = 1\n',
        'site/pkg/_ext.so': os.urandom(70000), 'site/share/man/man1/tool.1': b'.TH\n'}


class Calls:
    """fetch_manifest/open_tar doubles that count every transfer."""

    def __init__(self, raw, tar):
        self.raw, self.tar, self.manifest_calls, self.tar_calls = raw, tar, 0, 0

    def fetch_manifest(self):
        self.manifest_calls += 1
        return self.raw

    def open_tar(self):
        self.tar_calls += 1
        return contextlib.closing(io.BytesIO(self.tar))


def ensure(cache, raw, expect, calls, **kw):
    args = dict(approved_device='nvme0n1p1', rate_bps=1 << 30, sync_every=16*MiB, fetch_deadline_s=60, verify_deadline_s=60, pod_uid='uid-1',
                storage=lambda *a: None)
    args.update(kw)
    return C.ensure(cache, hashlib.sha256(raw).hexdigest(), expect, args.pop('approved_device'), calls.fetch_manifest, calls.open_tar,
                    args.pop('rate_bps'), args.pop('sync_every'), args.pop('fetch_deadline_s'), args.pop('verify_deadline_s'),
                    args.pop('pod_uid'), **args)


class PreparationProvenanceTest(unittest.TestCase):
    def site(self, files, records):
        site = tmpdir(self)/'site'
        for path, body in files.items():
            (site/path).parent.mkdir(parents=True, exist_ok=True)
            (site/path).write_bytes(body)
        for dist, rows in records.items():
            (site/dist).mkdir(parents=True, exist_ok=True)
            (site/dist/'RECORD').write_text(''.join(f'{p},{h},\n' for p, h in rows))
        return site

    @staticmethod
    def h(body):
        return 'sha256='+base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode().rstrip('=')

    def test_pip_runs_offline_for_the_foreign_cp311_aarch64_platform_without_compiling(self):
        cmd = R.pip_command('py311', 'W', 'S', 'rep.json', ['torch==2.9.1+cu130'])
        for flags in (['--no-index'], ['--only-binary=:all:'], ['--no-compile'], ['--python-version', '3.11'], ['--implementation', 'cp'],
                      ['--abi', 'cp311'], ['--find-links', 'W'], ['--target', 'S'], ['--report', 'rep.json'], ['--platform', 'manylinux2014_aarch64'],
                      ['--platform', 'manylinux_2_28_aarch64']):
            self.assertIn(flags, [cmd[i:i+len(flags)] for i in range(len(cmd))])
        self.assertEqual(cmd[-1], 'torch==2.9.1+cu130')
        self.assertNotIn('x86_64', ' '.join(cmd))

    def test_every_file_is_bound_to_its_record_and_data_is_relocated_while_bin_is_not_shipped(self):
        body, data, script = b'print(1)\n', b'.TH\n', b'#!/host/python\n'
        site = self.site({'pkg/a.py': body, 'share/man/man1/t.1': data, 'bin/tool': script, 'pkg-1.dist-info/INSTALLER': b'pip\n'},
                         {'pkg-1.dist-info': [('pkg/a.py', self.h(body)), ('../../share/man/man1/t.1', self.h(data)), ('../../bin/tool', ''),
                                              ('pkg-1.dist-info/INSTALLER', ''), ('pkg-1.dist-info/RECORD', '')]})
        files, excluded = R.scan_site(site)
        by_path = {f['path']: f for f in files}
        self.assertEqual(sorted(by_path), ['site/pkg-1.dist-info/INSTALLER', 'site/pkg-1.dist-info/RECORD', 'site/pkg/a.py', 'site/share/man/man1/t.1'])
        self.assertEqual(by_path['site/pkg/a.py']['origin'], 'pkg-1.dist-info')
        self.assertEqual(by_path['site/pkg-1.dist-info/RECORD']['origin'], 'pip-generated:pkg-1.dist-info')
        self.assertTrue(any(e.startswith('bin/') for e in excluded))

    def test_a_file_outside_every_record_or_differing_from_its_record_hash_is_refused(self):
        body = b'print(1)\n'
        site = self.site({'pkg/a.py': body, 'pkg/stray.py': b''}, {'pkg-1.dist-info': [('pkg/a.py', self.h(body)), ('pkg-1.dist-info/RECORD', '')]})
        with self.assertRaisesRegex(ValueError, 'stray.py: not listed'):
            R.scan_site(site)
        site = self.site({'pkg/a.py': b'changed\n'}, {'pkg-1.dist-info': [('pkg/a.py', self.h(body)), ('pkg-1.dist-info/RECORD', '')]})
        with self.assertRaisesRegex(ValueError, 'differs from its RECORD hash'):
            R.scan_site(site)

    def test_a_symlink_in_the_prepared_tree_is_refused(self):
        site = self.site({'pkg/a.py': b''}, {'pkg-1.dist-info': [('pkg/a.py', ''), ('pkg-1.dist-info/RECORD', '')]})
        (site/'pkg'/'link').symlink_to('/etc/passwd')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            R.scan_site(site)

    def test_installed_distributions_must_be_the_frozen_wheels_and_every_pin_is_installed(self):
        entries = [dict(path='wheels/cu130-aarch64-cp311/torch-2.9.1+cu130-cp311-cp311-manylinux_2_28_aarch64.whl', sha256='b'*64)]

        def item(sha, version='2.9.1+cu130'):
            return dict(metadata=dict(name='torch', version=version), requested=True, download_info=dict(
                url='file:///x/torch-2.9.1%2Bcu130-cp311-cp311-manylinux_2_28_aarch64.whl', archive_info=dict(hashes=dict(sha256=sha))))
        dists = R.bind_distributions(dict(install=[item('b'*64)]), entries, ['torch==2.9.1+cu130'])
        self.assertEqual(dists[0]['frozen_path'], entries[0]['path'])
        with self.assertRaisesRegex(ValueError, 'not a frozen wheel'):
            R.bind_distributions(dict(install=[item('c'*64)]), entries, ['torch==2.9.1+cu130'])
        with self.assertRaisesRegex(ValueError, 'not installed'):
            R.bind_distributions(dict(install=[item('b'*64)]), entries, ['torch==2.9.1+cu130', 'numpy==1.26.4'])

    def test_the_tar_is_deterministic_and_a_source_changed_after_the_manifest_is_refused(self):
        m1, _, tar1, _ = build(self, TREE)
        m2, _, tar2, _ = build(self, TREE)
        self.assertEqual((tar1, m1['content_sha256']), (tar2, m2['content_sha256']))
        src = tmpdir(self)
        (src/'contract.json').write_bytes(b'{"c":2}')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            C.write_tar(src/'t.tar', [dict(path='contract.json', size=7, sha256=hashlib.sha256(b'{"c":1}').hexdigest(), mode=0o644)],
                        lambda f: src/f['path'])


class ManifestValidationTest(unittest.TestCase):
    def test_the_built_manifest_is_accepted(self):
        manifest, _, _, expect = build(self, TREE)
        self.assertEqual(C.manifest_problems(manifest, expect), [])

    def test_wrong_runtime_abi_recipe_frozen_binding_or_content_is_rejected(self):
        manifest, _, _, expect = build(self, TREE)
        for key, value in (('machine', 'x86_64'), ('python', '3.10'), ('recipe', 'jp61-aarch64-cp310'), ('asset_manifest_sha256', 'b'*64),
                           ('content_sha256', 'c'*64)):
            self.assertTrue(C.manifest_problems(manifest, dict(expect, **{key: value})), key)
        tampered = json.loads(json.dumps(manifest))
        tampered['files'][0]['size'] += 1
        self.assertTrue(C.manifest_problems(tampered, expect))

    def test_escaping_absolute_foreign_duplicate_and_file_directory_paths_are_rejected(self):
        for path in ('../etc/passwd', '/etc/passwd', 'site/../../x', 'site/./a', 'other/x', 'site/a\\b', 'site//a', 'site'):
            self.assertTrue(C.path_problem(path), path)
        self.assertIsNone(C.path_problem('site/torch/lib/libtorch.so'))
        manifest, _, _, expect = build(self, TREE)
        for files in ([*manifest['files'], manifest['files'][-1]], [dict(manifest['files'][2], path='site/pkg'), *manifest['files'][2:]]):
            m = dict(manifest, files=sorted(files, key=lambda f: f['path']))
            m.update(file_count=len(files), total_bytes=sum(f['size'] for f in files), content_sha256=C.content_sha256(m['files']))
            self.assertTrue(C.manifest_problems(m, dict(expect, content_sha256=m['content_sha256'])))


class StreamingFillTest(unittest.TestCase):
    def test_fill_publishes_the_exact_tree_and_a_hit_then_transfers_nothing(self):
        manifest, raw, tar, expect = build(self, TREE)
        cache, calls = tmpdir(self), Calls(raw, tar)
        event, _, final = ensure(cache, raw, expect, calls)
        self.assertEqual((event['kind'], calls.manifest_calls, calls.tar_calls), ('cache_fill', 1, 1))
        self.assertEqual(final, cache/'gmv-prepared'/'v1'/expect['content_sha256'])
        for path, body in TREE.items():
            self.assertEqual((final/'tree'/path).read_bytes(), body)
        self.assertEqual(os.stat(final/'tree'/'site/pkg/_ext.so').st_mode & 0o777, 0o755)
        self.assertEqual(sorted(os.listdir(final.parent)), ['.lock', expect['content_sha256']])
        event, _, again = ensure(cache, raw, expect, calls)
        self.assertEqual((event['kind'], event['transfers'], calls.manifest_calls, calls.tar_calls, again), ('cache_hit', 0, 1, 1, final))

    def test_written_bytes_are_paced_and_never_more_than_sync_every_are_unsynced(self):
        contents = dict(TREE, **{f'site/many/f{i}.py': os.urandom(100000) for i in range(40)})
        manifest, raw, tar, expect = build(self, contents)
        state = dict(unsynced=0, worst=0, syncs=0)
        real_write, real_fsync = os.write, os.fsync

        def write(fd, data):
            n = real_write(fd, data)
            state['unsynced'] += n
            state['worst'] = max(state['worst'], state['unsynced'])
            return n

        def fsync(fd):
            real_fsync(fd)
            state['syncs'] += 1
        with mock.patch.object(C.os, 'write', write), mock.patch.object(C.os, 'fsync', fsync), \
                mock.patch.object(C.Writer, 'sync', autospec=True, side_effect=lambda self, keep=None: (
                    C_SYNC(self, keep), state.update(unsynced=0))):
            started = time.monotonic()
            ensure(tmpdir(self), raw, expect, Calls(raw, tar), rate_bps=8*MiB, sync_every=1*MiB)
            elapsed = time.monotonic() - started
        self.assertLessEqual(state['worst'], 1*MiB)
        self.assertGreater(state['syncs'], manifest['total_bytes'] // MiB)
        self.assertGreaterEqual(elapsed, manifest['total_bytes'] / (8*MiB) - 0.3)

    def assert_refused(self, tar_bytes, manifest=None, raw=None, expect=None, phase='cache_fill_failed'):
        if manifest is None:
            manifest, raw, _, expect = build(self, TREE)
        cache = tmpdir(self)
        with self.assertRaises(C.CacheError) as caught:
            ensure(cache, raw, expect, Calls(raw, tar_bytes))
        self.assertEqual(caught.exception.phase, phase)
        root = cache/'gmv-prepared'/'v1'
        self.assertFalse((root/expect['content_sha256']).exists())
        self.assertFalse(any(p.name not in ('.lock',) and not p.name.startswith('.staging-') for p in root.iterdir()))
        return cache, raw, expect

    def test_hash_mismatch_truncation_and_trailing_bytes_are_refused_and_nothing_is_published(self):
        manifest, raw, tar, expect = build(self, TREE)
        bad = bytearray(tar)
        offset = tar.index(TREE['models/m/model.safetensors'][:64])
        bad[offset] ^= 1
        for body in (bytes(bad), tar[:len(tar)//2], tar + b'\0'*512):
            self.assert_refused(body, manifest, raw, expect)

    def tar_of(self, members):
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode='w', format=tarfile.PAX_FORMAT) as tar:
            for info, body in members:
                tar.addfile(info, io.BytesIO(body) if body is not None else None)
        return out.getvalue()

    def test_escaping_symlink_hardlink_extra_and_reordered_members_are_refused_without_writing_outside(self):
        manifest, raw, tar, expect = build(self, TREE)
        good = [(tarfile.TarInfo(f['path']), TREE[f['path']]) for f in manifest['files']]
        for info, f in zip((g[0] for g in good), manifest['files']):
            info.size, info.mode = f['size'], f['mode']
        link = tarfile.TarInfo('contract.json')
        link.type, link.linkname = tarfile.SYMTYPE, '/etc/passwd'
        hard = tarfile.TarInfo('contract.json')
        hard.type, hard.linkname = tarfile.LNKTYPE, '../../../../etc/passwd'
        escape = tarfile.TarInfo('../../escape.txt')
        escape.size = 3
        for members in ([(link, None), *good[1:]], [(hard, None), *good[1:]], [(escape, b'bad'), *good], [*good[1:], good[0]],
                        [*good, (escape, b'bad')], good[:-1]):
            cache, _, _ = self.assert_refused(self.tar_of(members), manifest, raw, expect)
            self.assertFalse(any(cache.parent.glob('escape.txt')) or (cache/'escape.txt').exists())

    def test_a_deadline_interrupted_fill_is_never_published_and_the_next_writer_clears_the_staging(self):
        manifest, raw, tar, expect = build(self, TREE)
        cache = tmpdir(self)
        with self.assertRaises(C.CacheError) as caught:
            ensure(cache, raw, expect, Calls(raw, tar), rate_bps=1*MiB, fetch_deadline_s=0.5)
        self.assertEqual(caught.exception.phase, 'cache_fill_failed')
        root = cache/'gmv-prepared'/'v1'
        self.assertEqual([p.name.startswith('.staging-') for p in root.iterdir() if p.name != '.lock'], [True])
        event, _, final = ensure(cache, raw, expect, Calls(raw, tar), pod_uid='uid-2')
        self.assertEqual(event['removed_staging'], [f'.staging-{expect["content_sha256"][:16]}-uid-1'])
        self.assertEqual(sorted(os.listdir(root)), ['.lock', final.name])


C_SYNC = C.Writer.sync


class CacheHitTest(unittest.TestCase):
    def filled(self):
        manifest, raw, tar, expect = build(self, TREE)
        cache = tmpdir(self)
        _, _, final = ensure(cache, raw, expect, Calls(raw, tar))
        return cache, raw, tar, expect, final

    def refused_hit(self, cache, raw, tar, expect, phase='cache_corrupt'):
        calls = Calls(raw, tar)
        with self.assertRaises(C.CacheError) as caught:
            ensure(cache, raw, expect, calls)
        self.assertEqual((caught.exception.phase, calls.manifest_calls, calls.tar_calls), (phase, 0, 0))
        return caught.exception

    def test_a_same_size_content_change_is_corrupt_and_nothing_is_deleted_or_transferred(self):
        cache, raw, tar, expect, final = self.filled()
        target = final/'tree'/'models/m/model.safetensors'
        body = bytearray(target.read_bytes())
        body[-1] ^= 1
        target.write_bytes(bytes(body))
        self.refused_hit(cache, raw, tar, expect)
        self.assertEqual(target.read_bytes(), bytes(body))

    def test_extra_missing_symlinked_or_mode_changed_entries_are_corrupt(self):
        for change in (lambda t: (t/'site/pkg/new.py').write_bytes(b''), lambda t: (t/'site/pkg/__init__.py').unlink(),
                       lambda t: ((t/'site/pkg/__init__.py').unlink(), (t/'site/pkg/__init__.py').symlink_to('/etc/hostname')),
                       lambda t: os.chmod(t/'site/pkg/__init__.py', 0o755), lambda t: (t/'site/__pycache__').mkdir()):
            cache, raw, tar, expect, final = self.filled()
            change(final/'tree')
            self.refused_hit(cache, raw, tar, expect)

    def test_a_published_symlink_or_another_manifest_is_refused(self):
        cache, raw, tar, expect, final = self.filled()
        moved = final.parent/'elsewhere'
        final.rename(moved)
        final.symlink_to(moved)
        self.refused_hit(cache, raw, tar, expect)
        cache, raw, tar, expect, final = self.filled()
        (final/'prepared-manifest.json').write_bytes(raw + b' ')
        self.refused_hit(cache, raw, tar, expect, phase='cache_mismatch')

    def test_a_second_writer_fails_closed_while_the_lock_is_held(self):
        manifest, raw, tar, expect = build(self, TREE)
        cache = tmpdir(self)
        root = cache/'gmv-prepared'/'v1'
        root.mkdir(parents=True)
        holder = os.open(root/'.lock', os.O_RDWR | os.O_CREAT)
        fcntl.flock(holder, fcntl.LOCK_EX)
        self.addCleanup(os.close, holder)
        calls = Calls(raw, tar)
        with self.assertRaises(C.CacheError) as caught:
            ensure(cache, raw, expect, calls)
        self.assertEqual((caught.exception.phase, calls.manifest_calls, calls.tar_calls), ('cache_locked', 0, 0))

    def test_a_rejected_manifest_or_abi_writes_nothing(self):
        manifest, raw, tar, expect = build(self, TREE)
        cache = tmpdir(self)
        with self.assertRaises(C.CacheError) as caught:
            ensure(cache, raw, dict(expect, machine='x86_64'), Calls(raw, tar))
        self.assertEqual(caught.exception.phase, 'manifest_rejected')
        self.assertEqual(os.listdir(cache/'gmv-prepared'/'v1'), ['.lock'])


class StorageGateTest(unittest.TestCase):
    def test_sd_emmc_unknown_or_unapproved_backing_is_refused_before_any_write(self):
        for device, approved in ((('mmcblk0p1', 179), 'mmcblk0p1'), (('nvme0n1p1', 259), 'sda1'), (('sdb1', 8), None)):
            with mock.patch.object(C, 'block_device', return_value=device):
                self.assertTrue(C.storage_problem('/x', approved), device)
        with mock.patch.object(C, 'block_device', side_effect=OSError('no sysfs')):
            self.assertTrue(C.storage_problem('/x', 'nvme0n1p1'))
        with mock.patch.object(C, 'block_device', return_value=('nvme0n1p1', 259)):
            self.assertIsNone(C.storage_problem('/x', 'nvme0n1p1'))
        manifest, raw, tar, expect = build(self, TREE)
        cache = tmpdir(self)
        calls = Calls(raw, tar)
        with mock.patch.object(C, 'block_device', return_value=('mmcblk0p1', 179)), self.assertRaises(C.CacheError) as caught:
            C.ensure(cache, hashlib.sha256(raw).hexdigest(), expect, 'mmcblk0p1', calls.fetch_manifest, calls.open_tar, 1 << 30, 16*MiB, 60, 60, 'u')
        self.assertEqual((caught.exception.phase, os.listdir(cache), calls.manifest_calls), ('cache_storage_rejected', [], 0))

    def test_the_real_sysfs_lookup_names_this_hosts_device(self):
        name, major = C.block_device(tmpdir(self))
        self.assertTrue(name and isinstance(major, int))


class Cache(http.server.ThreadingHTTPServer):
    pass


class PreparedBootstrapTest(unittest.TestCase):
    """vlm_prepared_bootstrap.main in-process against a loopback cache: no subprocess/pip/zip at all, the driver gate is
    vlm_bootstrap.choose, a miss fills, a later Pod hits with zero HTTP requests, and /work holds only contract/link/logs."""

    @classmethod
    def setUpClass(cls):
        cls.files, cls.requests = {}, []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                cls.requests.append(self.path)
                body = cls.files[self.path]
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        cls.server = Cache(('127.0.0.1', 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def run_main(self, cache, raw, expect, choose=('cu130-aarch64-cp311', None), env_extra=None):
        import vlm_bootstrap as B
        import vlm_prepared_bootstrap as PB
        work, config = tmpdir(self), tmpdir(self)
        (config/'fakeworker.py').write_text(
            'class _S:\n    def serve_forever(self):\n        pass\n'
            'def serve(proxy, port):\n    return _S()\n'
            'class Worker:\n    seen = None\n'
            '    def __init__(self, backend, stages, **kw):\n        Worker.seen = dict(kw, backend=backend, stages=stages)\n'
            '    def health(self):\n        return {}\n    def run(self, body):\n        return 200, {}\n')
        sys.modules.pop('fakeworker', None)
        env = dict(RECIPE='cu130-aarch64-cp311', BACKEND='cuda', STAGES='vlm', WORKER_MODULE='fakeworker', POD_UID='pod-uid',
                   CACHE_URL=f'http://127.0.0.1:{self.server.server_address[1]}', ASSET_MANIFEST_SHA256=expect['asset_manifest_sha256'],
                   PREPARED_MANIFEST_SHA256=hashlib.sha256(raw).hexdigest(), PREPARED_CONTENT_SHA256=expect['content_sha256'],
                   CACHE_DIR=str(cache), CACHE_BACKING_DEVICE=C.block_device(cache)[0], PREPARED_FETCH_DEADLINE_S='60',
                   CACHE_VERIFY_DEADLINE_S='60')
        env.update(env_extra or {})
        finished = {}
        refuse = mock.Mock(side_effect=AssertionError('no subprocess/zip on the node in prepared mode'))
        with mock.patch.dict(os.environ, env), mock.patch.object(B, 'CONFIG', str(config)), mock.patch.object(B, 'WORK', work), \
                mock.patch.object(B, 'discover', return_value={}), mock.patch.object(B, 'choose', return_value=choose) as gate, \
                mock.patch.object(B, 'finish', side_effect=lambda proxy, state: finished.update(state=state, proxy=proxy)), \
                mock.patch('subprocess.run', refuse), mock.patch('subprocess.Popen', refuse), mock.patch('zipfile.ZipFile', refuse), \
                mock.patch.object(PB.platform, 'machine', return_value='aarch64'), \
                mock.patch.object(PB.sys, 'version_info', (3, 11, 0)), mock.patch.object(PB.sys, 'path', list(sys.path)):
            PB.main()
        gate.assert_called_once_with('cu130-aarch64-cp311', {})
        import fakeworker
        return finished['state'], work, fakeworker.Worker.seen

    def test_a_miss_fills_from_the_bound_tar_and_a_later_pod_hits_without_any_request(self):
        contents = dict(TREE, **{'models/qwen3vl-2b-89644892/model.safetensors': b'w'})
        manifest, raw, tar, expect = build(self, contents)
        manifest['model'] = dict(key='qwen3vl-2b-89644892')
        raw = json.dumps(manifest).encode()
        self.files.update({'/prepared/prepared-manifest.json': raw, '/prepared/runtime.tar': tar})
        cache = tmpdir(self)
        state, work, seen = self.run_main(cache, raw, expect)
        self.assertEqual((state['phase'], state['events'][0]['kind']), ('loaded', 'cache_fill'), state.get('error'))
        self.assertEqual(self.requests, ['/prepared/prepared-manifest.json', '/prepared/runtime.tar'])
        final = cache/'gmv-prepared'/'v1'/expect['content_sha256']
        self.assertEqual(seen['model_root'], str(work/'models'))
        self.assertEqual(os.readlink(work/'models'/'qwen3vl-2b-89644892'), str(final/'tree'/'models'/'qwen3vl-2b-89644892'))
        self.assertEqual((work/'contract.json').read_bytes(), contents['contract.json'])
        self.requests.clear()
        state, _, _ = self.run_main(cache, raw, expect)
        self.assertEqual((state['phase'], state['events'][0]['kind'], state['events'][0]['transfers'], self.requests),
                         ('loaded', 'cache_hit', 0, []))

    def test_the_driver_gate_still_stops_before_the_cache_is_touched(self):
        manifest, raw, tar, expect = build(self, TREE)
        cache = tmpdir(self)
        state, _, _ = self.run_main(cache, raw, expect, choose=(None, 'CUDA driver API version 0 outside the gate'))
        self.assertEqual((state['phase'], os.listdir(cache)), ('dependency_unavailable', []))

    def test_the_bootstrap_passes_the_bound_sd_profile_and_this_host_refuses_it_before_any_write(self):
        manifest, raw, tar, expect = build(self, TREE)
        cache = tmpdir(self)
        extra = dict(CACHE_IO_PROFILE=json.dumps(C.SD_PROFILE), CACHE_BACKING_DISK='mmcblk0', CACHE_BACKING_DEVICE='mmcblk0p1')
        state, _, _ = self.run_main(cache, raw, expect, env_extra=extra)
        self.assertEqual((state['phase'], os.listdir(cache), self.requests), ('cache_storage_rejected', [], []))
        self.assertEqual(state['fetch_io']['io_profile'], C.SD_PROFILE)


def live_module():
    for name in ('live', 'protocol'):
        sys.modules.pop(name, None)
    import live
    return live


def snapshot_live():
    """The pre-change collector (source-before snapshot) for the old-mode regression."""
    import types
    module, path = types.ModuleType('live_before'), list(sys.path)
    module.__file__ = str(HERE/'live.py')  # same HERE/ROOT as the live collector, so the same shipped files are read
    try:
        exec(compile((SNAPSHOT/'live.py').read_text(), str(SNAPSHOT/'live.py'), 'exec'), module.__dict__)
    finally:
        sys.path[:] = path
    return module


PVC = dict(name='gmv-cuda-cache', uid='pvc-uid', pv='pv-1', path='/mnt/nvme/gmv-cuda-cache', backing_device='nvme0n1p1',
           verified_by='coordinator: node_disk + mountinfo')


def prepared_fixture(test, pvc=None, io_profile_name=None):
    live = live_module()
    frozen = ROOT/'.scratch'/'gpu-mobilint-vlm-20261028'/'developer-frozen-r1'
    contents = dict(TREE, **{'contract.json': (frozen/'contract.json').read_bytes()})
    manifest, _, tar, _ = build(test, contents)
    manifest['frozen'] = {n: live.P.file_sha(frozen/n) for n in ('contract.json', 'assets-manifest.json')}
    out = tmpdir(test)
    (out/'runtime.tar').write_bytes(tar)
    (out/'prepared-manifest.json').write_text(json.dumps(manifest))
    return live, frozen, out, live.load_prepared(out, frozen, dict(pvc or PVC), io_profile_name=io_profile_name)


class CollectorTest(unittest.TestCase):
    def test_old_mode_plan_is_identical_to_the_pre_change_collector(self):
        live, before = live_module(), snapshot_live()
        for name in ('vlm_bootstrap.py', 'vlm_stage.py', 'vlm_worker.py', 'protocol.py', 'prepare.py'):
            self.assertEqual((HERE/name).read_bytes(), (SNAPSHOT/name).read_bytes(), name)
        for arms in (None, ('cuda',), ('mobilint',)):
            self.assertEqual(live.plan('gmv-x', 'http://c', 'a'*64, dict(common=1, total=2, **{'cu130-aarch64-cp311': 3, 'mobilint-x86_64-cp311': 4}),
                                       arms),
                             before.plan('gmv-x', 'http://c', 'a'*64, dict(common=1, total=2, **{'cu130-aarch64-cp311': 3, 'mobilint-x86_64-cp311': 4}),
                                         arms))

    def test_prepared_cuda_pod_mounts_only_the_bound_claim_and_runs_the_prepared_bootstrap(self):
        live, frozen, out, prepared = prepared_fixture(self)
        items = live.plan('gmv-x', 'http://c', 'a'*64, dict(common=1, total=2, **{'cu130-aarch64-cp311': 3, 'mobilint-x86_64-cp311': 4}), None, prepared)
        by_name = {i['metadata']['name']: i for i in items}
        self.assertEqual(sorted(i['kind'] for i in items), ['ConfigMap', 'Pod', 'Pod', 'Pod'])
        cuda = by_name['gmv-x-cuda']['spec']
        container = cuda['containers'][0]
        self.assertEqual(container['command'], ['python3', '-u', '/config/vlm_prepared_bootstrap.py'])
        self.assertEqual([v for v in cuda['volumes'] if 'persistentVolumeClaim' in v], [dict(name='cache', persistentVolumeClaim=dict(claimName=PVC['name']))])
        self.assertIn(dict(name='cache', mountPath='/cache'), container['volumeMounts'])
        env = {e['name']: e.get('value') for e in container['env']}
        self.assertEqual((env['PREPARED_MANIFEST_SHA256'], env['PREPARED_CONTENT_SHA256'], env['CACHE_BACKING_DEVICE'], env['CACHE_DIR']),
                         (prepared['binding']['manifest_sha256'], prepared['binding']['content_sha256'], 'nvme0n1p1', '/cache'))
        self.assertEqual(cuda['affinity'], live.affinity(live.P.JETSON))
        old = {i['metadata']['name']: i for i in live.plan('gmv-x', 'http://c', 'a'*64, dict(common=1, total=2, **{'cu130-aarch64-cp311': 3,
                                                                                                                      'mobilint-x86_64-cp311': 4}))}
        self.assertEqual(by_name['gmv-x-mobilint'], old['gmv-x-mobilint'])
        self.assertEqual(sorted(by_name['gmv-x-code']['data']), sorted([*old['gmv-x-code']['data'], 'vlm_prepared_bootstrap.py', 'vlm_runtime_cache.py']))
        size = prepared['binding']['tar_bytes'] + prepared['binding']['manifest_bytes'] + 1 + 4
        self.assertEqual(by_name['gmv-x-cache']['spec']['volumes'][0]['emptyDir']['sizeLimit'], live.cache_size(dict(total=size)))

    def test_release_binds_prepared_content_and_pvc_exactly_and_old_mode_releases_stay_valid(self):
        live, frozen, out, prepared = prepared_fixture(self)
        base = dict(frozen={n: live.P.file_sha(frozen/n) for n in ('contract.json', 'assets-manifest.json')}, code={'x': 'y'}, approved_by='r', stages=['probe'])
        self.assertEqual(live.release_problems(base, frozen, {'x': 'y'}), [])
        release = dict(base, prepared=prepared['binding'], cuda_cache_pvc=prepared['pvc'], prepared_deadlines=prepared['deadlines'])
        self.assertEqual(live.release_problems(release, frozen, {'x': 'y'}, None, prepared), [])
        self.assertTrue(live.release_problems(base, frozen, {'x': 'y'}, None, prepared))
        self.assertTrue(live.release_problems(release, frozen, {'x': 'y'}))
        for key, value in (('prepared', dict(prepared['binding'], content_sha256='0'*64)), ('cuda_cache_pvc', dict(PVC, uid='other'))):
            self.assertTrue(live.release_problems(dict(release, **{key: value}), frozen, {'x': 'y'}, None, prepared), key)

    def test_sd_backing_incomplete_pvc_binding_or_a_changed_tar_is_rejected(self):
        for pvc in (dict(PVC, backing_device='mmcblk0p1'), {k: v for k, v in PVC.items() if k != 'verified_by'}, dict(PVC, uid='')):
            with self.assertRaisesRegex(ValueError, 'prepared runtime rejected'):
                prepared_fixture(self, pvc)
        live, frozen, out, prepared = prepared_fixture(self)
        with (out/'runtime.tar').open('r+b') as handle:
            handle.seek(600)
            handle.write(b'X')
        with self.assertRaisesRegex(ValueError, 'differs from the prepared manifest'):
            live.load_prepared(out, frozen, dict(PVC))

    def objects(self, live):
        pvc = dict(metadata=dict(name=PVC['name'], namespace=live.P.NS, uid=PVC['uid']), spec=dict(volumeName='pv-1', accessModes=['ReadWriteOnce']),
                   status=dict(phase='Bound', capacity=dict(storage='40Gi')))
        pv = dict(metadata=dict(name='pv-1'), status=dict(phase='Bound'), spec=dict(
            claimRef=dict(namespace=live.P.NS, name=PVC['name'], uid=PVC['uid']), local=dict(path=PVC['path']),
            nodeAffinity=dict(required=dict(nodeSelectorTerms=[dict(matchExpressions=[
                dict(key='kubernetes.io/hostname', operator='In', values=['dev0009-host'])])]))))
        node = dict(metadata=dict(name=live.P.JETSON, labels={'kubernetes.io/hostname': 'dev0009-host'}))
        return pvc, pv, node

    def test_pvc_preflight_accepts_only_the_bound_unused_dev0009_claim(self):
        live = live_module()
        pvc, pv, node = self.objects(live)
        self.assertEqual(live.pvc_problems(pvc, pv, node, [], PVC, 20*2**30), [])
        user = dict(metadata=dict(namespace=live.P.NS, name='other'), status=dict(phase='Running'),
                    spec=dict(volumes=[dict(name='v', persistentVolumeClaim=dict(claimName=PVC['name']))]))
        cases = dict(
            uid=lambda c, v: c['metadata'].update(uid='replaced'), pending=lambda c, v: c['status'].update(phase='Pending'),
            deleting=lambda c, v: c['metadata'].update(deletionTimestamp='now'), small=lambda c, v: c['status'].update(capacity=dict(storage='10Gi')),
            rwx=lambda c, v: c['spec'].update(accessModes=['ReadWriteMany']), path=lambda c, v: v['spec'].update(local=dict(path='/var/lib')),
            unpinned=lambda c, v: v['spec'].pop('nodeAffinity'),
            other_node=lambda c, v: v['spec']['nodeAffinity']['required']['nodeSelectorTerms'][0]['matchExpressions'][0].update(values=['dev0001']),
            extra_term=lambda c, v: v['spec']['nodeAffinity']['required']['nodeSelectorTerms'].append(dict(matchExpressions=[])),
            claim_ref=lambda c, v: v['spec']['claimRef'].update(uid='old'))
        for name, change in cases.items():
            pvc, pv, node = self.objects(live)
            change(pvc, pv)
            self.assertTrue(live.pvc_problems(pvc, pv, node, [], PVC, 20*2**30), name)
        pvc, pv, node = self.objects(live)
        self.assertTrue(live.pvc_problems(pvc, pv, node, [user], PVC, 20*2**30))

    def test_deadlines_follow_the_prepared_bytes_and_an_oversized_runtime_is_refused(self):
        live = live_module()
        actual = dict(tar_bytes=8868372480, total_bytes=8853614095, file_count=19003)
        d = live.prepared_deadlines(actual)
        self.assertEqual(d['fetch_s'], math.ceil(8868372480 / (8*MiB) * 1.25 + 19003*0.02) + 300)
        self.assertGreaterEqual(d['worker_ready_s'], d['fetch_s'] + live.P.DEADLINES['load_s']['cuda'])
        with self.assertRaises(ValueError):
            live.prepared_deadlines(dict(actual, tar_bytes=40*2**30))

    def test_the_bootstrap_must_report_the_bound_content(self):
        live, frozen, out, prepared = prepared_fixture(self)
        ok = dict(bootstrap=dict(events=[dict(kind='cache_hit', content_sha256=prepared['binding']['content_sha256'])]))
        self.assertIsNone(live.prepared_event_problem(ok, prepared))
        self.assertTrue(live.prepared_event_problem(dict(bootstrap=dict(events=[dict(kind='cache_hit', content_sha256='0'*64)])), prepared))

    def test_release_code_hashes_cover_the_new_shipped_and_build_code(self):
        code = live_module().dependency_hashes()
        for name in ('vlm_prepared_bootstrap.py', 'vlm_runtime_cache.py', 'prepare_runtime.py', 'vlm_bootstrap.py'):
            self.assertIn(f'experiments/gpu-mobilint-vlm/{name}', code)


SD = C.SD_PROFILE
SD_PVC = dict(PVC, path='/var/lib/edgeai/gmv-cuda-cache', backing_device='mmcblk0p1')  # coordinator's binding shape is unchanged


class FakeDisk:
    """Synthetic monotonic clock + /proc/diskstats for mmcblk0: busy(t) % of wall time is credited to io_ticks while the
    writer sleeps; every sleep must be one bounded step (<= sample_s)."""

    def __init__(self, test, busy, t0=1000.0):
        self.test, self.busy, self.t, self.ticks, self.rows = test, busy, t0, 0.0, None

    def clock(self):
        return self.t

    def sleep(self, s):
        self.test.assertTrue(0 < s <= SD['sample_s'] + 1e-9, s)
        self.advance(s)

    def advance(self, s):
        """Time spent inside a syscall (fsync, unlink, rmdir): credited to io_ticks like a sleep, but not a bounded step."""
        self.ticks += 1000 * s * self.busy(self.t) / 100
        self.t += s

    def read(self):
        if self.rows is not None:
            return self.rows(self)
        return f' 179 0 mmcblk0 1 0 2 3 4 0 6 5 0 {int(self.ticks)} 7 0 0 0 0\n 179 1 mmcblk0p1 1 0 2 3 4 0 6 5 0 0 7 0 0 0 0\n'

    def monitor(self):
        m = C.DiskMonitor('mmcblk0', self.clock, self.read)
        m.sample()
        return m


class AdaptiveWriterTest(unittest.TestCase):
    """AdaptiveWriter under SD_PROFILE on synthetic time/diskstats. os.write only counts bytes (no real I/O volume);
    every chunk, the unsynced bytes and the pending fds are checked at the moment of the write."""

    def run_writer(self, busy, mib, files=1, fsync_s=0.0, deadline_s=10**6, rows=None):
        disk = FakeDisk(self, busy)
        disk.rows = rows
        trace, chunks, disk.fsyncs = [], [], []
        writer = C.AdaptiveWriter(SD, disk.monitor(), disk.t + deadline_s, lambda io: trace.append((disk.t, io)), disk.clock, disk.sleep)
        writer.observed, observe = [], writer.observe

        def spy():  # (time, rate) after every observation, i.e. at the moment a sample can change the rate
            observe()
            writer.observed.append((disk.t, writer.rate))
        writer.observe = spy

        def write(fd, data):
            self.assertLessEqual(len(data), SD['chunk_max'])
            self.assertLessEqual(writer.unsynced + len(data), SD['unsynced_max'])
            self.assertLessEqual(len(writer.pending), SD['fds_max'])
            self.assertFalse(writer.paused)
            chunks.append((disk.t, len(data)))
            return len(data)
        real_fsync = os.fsync

        def fsync(fd):
            disk.fsyncs.append(disk.t)
            real_fsync(fd)
            disk.advance(fsync_s)
        out, error = tmpdir(self), None
        with mock.patch.object(C.os, 'write', write), mock.patch.object(C.os, 'fsync', fsync):
            try:
                for i in range(files):
                    fd = os.open(out/f'f{i}', os.O_WRONLY | os.O_CREAT)
                    for _ in range(mib):
                        writer.write(fd, b'\0' * MiB)
                    writer.close_file(fd)
                    self.assertLessEqual(len(writer.pending), SD['fds_max'])
                writer.sync()
            except (TimeoutError, C.IOStatsError) as caught:
                error = caught
        return writer, trace, chunks, disk, error

    def assert_no_credit(self, trace):
        """Each chunk is followed by at least its own duration at the rate in force (at most one ramp step may fall between
        the two published rates); no saved-up idle/pause credit."""
        for (t0, a), (t1, b) in zip(trace, trace[1:]):
            if b['written'] > a['written']:
                self.assertGreaterEqual(t1 - t0 + 1e-6, (b['written'] - a['written']) / (max(a['rate_bps'], b['rate_bps']) + SD['step_bps']))

    def test_an_idle_disk_ramps_by_one_step_per_ten_quiet_seconds_and_never_above_the_cap(self):
        writer, trace, chunks, disk, error = self.run_writer(lambda t: 10, 400)
        self.assertIsNone(error)
        rates = writer.observed
        self.assertEqual(rates[0][1], SD['start_bps'])
        changes = [(t, r) for (_, p), (t, r) in zip(rates, rates[1:]) if r != p]
        self.assertTrue(all(b - a == SD['step_bps'] for (_, a), (_, b) in zip([rates[0], *changes], changes)), changes)
        self.assertTrue(all(t1 - t0 >= SD['ramp_hold_s'] - 1e-6 for (t0, _), (t1, _) in zip(changes, changes[1:])))
        self.assertEqual(max(r for _, r in rates), SD['max_bps'])
        self.assertGreaterEqual(changes[0][0] - 1000.0, SD['ramp_hold_s'])
        self.assert_no_credit(trace)
        self.assertEqual(writer.io['written'], 400 * MiB)

    def test_busy_above_seventy_halves_the_rate_down_to_the_floor_without_pausing(self):
        writer, trace, _, _, error = self.run_writer(lambda t: 75, 6)
        self.assertIsNone(error)
        self.assertEqual(writer.rate, SD['min_bps'])
        self.assertFalse(any(io['paused'] for _, io in trace))
        self.assertEqual(writer.io['pause_s'], 0.0)

    def test_busy_above_eightyfive_pauses_with_live_progress_and_resumes_only_after_three_quiet_seconds(self):
        def busy(t):
            return 20 if t < 1010 else 90 if t < 1020 else 45
        writer, trace, chunks, disk, error = self.run_writer(busy, 30)
        self.assertIsNone(error)
        paused = [(t, io) for t, io in trace if io['paused']]
        self.assertTrue(paused)
        self.assertTrue(all(t1 - t0 <= SD['sample_s'] + 1e-6 for (t0, _), (t1, _) in zip(paused, paused[1:])))
        self.assertEqual(len({io['written'] for _, io in paused}), 1)  # nothing written while paused
        first_pause = paused[0][0]
        self.assertLessEqual(first_pause, 1010 + SD['sample_s'] + 1e-6)
        resumed = min(t for t, _ in chunks if t > first_pause)
        self.assertGreaterEqual(resumed, 1020 + SD['resume_hold_s'] - 1e-6)
        self.assertLessEqual(resumed, 1020 + SD['resume_hold_s'] + 2 * SD['sample_s'] + 1e-6)
        self.assertGreaterEqual(writer.io['pause_s'], 1020 + SD['resume_hold_s'] - first_pause - SD['sample_s'])
        self.assertEqual(writer.io['pauses'], 1)
        after = sum(n for t, n in chunks if resumed <= t < resumed + 2)
        self.assertLessEqual(after, writer.rate * 2 + SD['chunk_max'])  # no burst for the paused time
        self.assertLess(writer.rate, SD['start_bps'])
        self.assert_no_credit(trace)

    def test_a_long_fsync_halves_the_rate(self):
        writer, _, _, _, error = self.run_writer(lambda t: 10, 3, fsync_s=1.5)
        self.assertIsNone(error)
        self.assertGreaterEqual(writer.io['max_fsync_s'], 1.5)
        self.assertGreaterEqual(writer.io['slowdowns'], 1)
        self.assertLess(writer.rate, SD['start_bps'])

    def test_unsynced_bytes_and_pending_fds_stay_bounded(self):
        writer, _, chunks, _, error = self.run_writer(lambda t: 10, 1, files=80)
        self.assertIsNone(error)
        self.assertEqual(writer.io['files'], 80)
        self.assertGreaterEqual(writer.io['syncs'], 80)  # 1 MiB per file: every file crosses the unsynced bound
        writer, _, _, _, error = self.run_writer(lambda t: 10, 0, files=100)  # empty files: only the fd bound syncs
        self.assertIsNone(error)
        self.assertEqual(writer.io['syncs'], 100 // SD['fds_max'] + 1)

    def test_the_absolute_deadline_ends_the_fill(self):
        writer, _, _, disk, error = self.run_writer(lambda t: 10, 50, deadline_s=5)
        self.assertIsInstance(error, TimeoutError)
        self.assertIn('deadline', str(error))
        self.assertLessEqual(disk.t, 1005 + 1e-6)
        self.assertLess(writer.io['written'], 50 * MiB)

    def test_a_disk_that_never_calms_stalls_out_after_three_hundred_seconds_without_a_byte(self):
        writer, trace, chunks, disk, error = self.run_writer(lambda t: 20 if t < 1002 else 95, 20)
        self.assertIsInstance(error, TimeoutError)
        self.assertIn('no written byte', str(error))
        last_byte = max(t for t, _ in chunks)
        self.assertLessEqual(disk.t - last_byte, SD['stall_s'] + SD['sample_s'] + 1e-6)
        self.assertGreaterEqual(disk.t - last_byte, SD['stall_s'] - SD['sample_s'])

    def test_missing_malformed_or_regressed_diskstats_stop_before_the_next_write(self):
        def after(t_bad, bad):
            return lambda d: bad(d) if d.t >= t_bad else FakeDisk.read(dict_disk(d))
        cases = dict(
            missing=lambda d: ' 8 0 sda 1 0 2 3 4 0 6 5 0 9 7\n',
            malformed=lambda d: ' 179 0 mmcblk0 1 0 2 3 4 0 6 5 0 x 7\n',
            regressed=lambda d: ' 179 0 mmcblk0 1 0 2 3 4 0 6 5 0 0 7\n',
            duplicate=lambda d: FakeDisk.read(dict_disk(d)) * 2)
        for name, bad in cases.items():
            writer, trace, chunks, disk, error = self.run_writer(lambda t: 30, 10, rows=after(1004.5, bad))
            self.assertIsInstance(error, C.IOStatsError, name)
            self.assertFalse([t for t, _ in chunks if t >= 1005], name)  # the first sample at/after 1004.5 is at 1005

    def test_each_fsync_of_a_batch_is_its_own_latency_feedback(self):
        writer, _, _, disk, error = self.run_writer(lambda t: 10, 0, files=3, fsync_s=1.5)  # one batch of 3 pending fds
        self.assertIsNone(error)
        self.assertEqual(len(disk.fsyncs), 3)
        self.assertEqual((writer.io['slowdowns'], writer.io['last_fsync_s'], writer.io['max_fsync_s']), (3, 1.5, 1.5))
        self.assertEqual(writer.rate, SD['min_bps'])

    def test_stats_lost_during_an_fsync_stop_the_rest_of_the_batch_and_the_next_write(self):
        def lost(t_bad):
            return lambda d: '' if d.t >= t_bad else FakeDisk.read(dict_disk(d))
        writer, _, _, disk, error = self.run_writer(lambda t: 10, 0, files=5, fsync_s=1.5, rows=lost(1001))
        self.assertIsInstance(error, C.IOStatsError)
        self.assertEqual(disk.fsyncs, [1000.0])  # the sample after the first fsync fails; no further fsync is started
        # 1 MiB written by ~1001, then the unsynced bound forces an fsync of the open file that runs 1001 -> 1003
        writer, _, chunks, disk, error = self.run_writer(lambda t: 10, 2, fsync_s=2.0, rows=lost(1002))
        self.assertIsInstance(error, C.IOStatsError)
        self.assertTrue(disk.fsyncs and all(t < 1002 for t in disk.fsyncs))
        self.assertFalse([t for t, _ in chunks if t >= 1002])

    def test_a_deadline_passing_inside_an_fsync_stops_before_the_next_fsync_or_write(self):
        writer, _, _, disk, error = self.run_writer(lambda t: 10, 0, files=5, fsync_s=1.5, deadline_s=2)
        self.assertIsInstance(error, TimeoutError)
        self.assertEqual(disk.fsyncs, [1000.0, 1001.5])
        writer, _, chunks, disk, error = self.run_writer(lambda t: 10, 2, fsync_s=2.0, deadline_s=2.5)
        self.assertIsInstance(error, TimeoutError)
        self.assertIn('deadline', str(error))
        self.assertFalse([t for t, _ in chunks if t >= 1002.5] + [t for t in disk.fsyncs if t >= 1002.5])

    def test_a_disk_turning_busy_during_an_fsync_pauses_before_the_next_write(self):
        def busy(t):
            return 95 if 1001 <= t < 1010 else 10  # the fsync runs 1001 -> 1003
        writer, trace, chunks, disk, error = self.run_writer(busy, 2, fsync_s=2.0)
        self.assertIsNone(error)
        fsync = disk.fsyncs[0]
        after = [t for t, _ in chunks if t > fsync]
        self.assertGreaterEqual(min(after), 1010 + SD['resume_hold_s'] - 1e-6)  # no chunk between the fsync and the resume
        self.assertTrue([io for t, io in trace if io['paused'] and fsync < t < 1010])
        self.assertEqual(writer.io['written'], 2 * MiB)


def dict_disk(d):
    """FakeDisk view without the row override (for the healthy rows before a fault)."""
    view = FakeDisk.__new__(FakeDisk)
    view.__dict__.update(d.__dict__, rows=None)
    return view


class SdStorageGateTest(unittest.TestCase):
    def gate(self, device=('mmcblk0p1', 179), disk='mmcblk0', approved='mmcblk0p1', backing='mmcblk0', profile=SD):
        whole = mock.patch.object(C, 'whole_disk', side_effect=disk) if isinstance(disk, Exception) else \
            mock.patch.object(C, 'whole_disk', return_value=disk)
        with mock.patch.object(C, 'block_device', return_value=device), whole:
            return C.storage_problem('/x', approved, backing, profile)

    def test_sd_is_accepted_only_as_the_exact_bound_partition_disk_and_profile(self):
        self.assertIsNone(self.gate())
        for case in (dict(device=('mmcblk0p2', 179)), dict(disk='mmcblk1'), dict(approved='mmcblk0p2'), dict(backing=None), dict(approved=None),
                     dict(profile=dict(SD, max_bps=8*MiB)), dict(profile=dict(SD, name='other')), dict(disk=OSError('not a partition'))):
            self.assertTrue(self.gate(**case), case)
        self.assertTrue(self.gate(profile=None))  # without the profile the SD ban stands

    def test_this_hosts_real_sysfs_disk_is_not_the_bound_sd(self):
        tmp = tmpdir(self)
        self.assertTrue(C.storage_problem(tmp, 'mmcblk0p1', 'mmcblk0', SD))
        try:
            disk = C.whole_disk(tmp)
        except OSError:
            self.skipTest('scratch filesystem is not on a partition')
        self.assertTrue(disk and '/' not in disk)

    def sd_ensure(self, cache, raw, expect, calls, disk, **kw):
        return ensure(cache, raw, expect, calls, approved_device='mmcblk0p1', io_profile=SD, backing_disk='mmcblk0',
                      monitor=C.DiskMonitor('mmcblk0', disk.clock, disk.read), clock=disk.clock, sleep=disk.sleep, **kw)

    def test_an_sd_fill_publishes_with_io_feedback_and_a_hit_transfers_nothing(self):
        manifest, raw, tar, expect = build(self, TREE)
        cache, calls, disk, seen = tmpdir(self), Calls(raw, tar), FakeDisk(self, lambda t: 20), []
        event, _, final = self.sd_ensure(cache, raw, expect, calls, disk, progress=seen.append)
        self.assertEqual(event['kind'], 'cache_fill')
        for key in ('busy_pct', 'rate_bps', 'pause_s', 'paused', 'unsynced', 'pending_fds', 'last_fsync_s', 'syncs', 'written', 'synced'):
            self.assertIn(key, event['io'])
        self.assertEqual(event['io']['written'], event['io']['synced'])
        self.assertGreaterEqual(disk.t - 1000, manifest['total_bytes'] / SD['max_bps'])
        self.assertTrue(seen and all(p['busy_pct'] is None or 0 <= p['busy_pct'] <= 100 for p in seen))
        for path, body in TREE.items():
            self.assertEqual((final/'tree'/path).read_bytes(), body)
        event, _, _ = self.sd_ensure(cache, raw, expect, calls, disk)
        self.assertEqual((event['kind'], calls.tar_calls), ('cache_hit', 1))

    def test_missing_baseline_stats_write_nothing_and_a_fault_mid_fill_publishes_nothing(self):
        manifest, raw, tar, expect = build(self, TREE)
        cache, calls, disk = tmpdir(self), Calls(raw, tar), FakeDisk(self, lambda t: 20)
        disk.rows = lambda d: ' 8 0 sda 1 0 2 3 4 0 6 5 0 9 7\n'
        with self.assertRaises(C.CacheError) as caught:
            self.sd_ensure(cache, raw, expect, calls, disk)
        self.assertEqual((caught.exception.phase, os.listdir(cache), calls.manifest_calls), ('cache_io_stats_rejected', [], 0))
        disk = FakeDisk(self, lambda t: 20)
        disk.rows = lambda d: FakeDisk.read(dict_disk(d)) if d.t < 1001.5 else ''
        with self.assertRaises(C.CacheError) as caught:
            self.sd_ensure(cache, raw, expect, calls, disk)
        self.assertEqual(caught.exception.phase, 'cache_fill_failed')
        self.assertIn('IOStatsError', caught.exception.detail)
        self.assertFalse((cache/'gmv-prepared'/'v1'/expect['content_sha256']).exists())

    def stale_cache(self, entries=40):
        """Cache root holding an interrupted fill: nested files/dirs, a symlink to an outside directory and a stray file."""
        cache, outside = tmpdir(self), tmpdir(self)
        (outside/'keep.txt').write_bytes(b'k')
        root = cache/'gmv-prepared'/'v1'
        stale = root/'.staging-0123456789abcdef-old-uid'/'tree'
        for i in range(entries):
            (stale/f'site/d{i % 4}').mkdir(parents=True, exist_ok=True)
            (stale/f'site/d{i % 4}/f{i}.py').write_bytes(b'x' * 100)
        (stale/'site'/'link').symlink_to(outside)
        (root/'.staging-stray').write_bytes(b's')
        return cache, root, outside

    def removing(self, disk, seconds=0.3):
        """os.unlink/os.rmdir that each take `seconds` of synthetic time and record (start, path)."""
        calls, real_unlink, real_rmdir = [], os.unlink, os.rmdir

        def slow(real):
            def op(path, *a, **kw):
                calls.append((disk.t, Path(path)))
                disk.advance(seconds)
                return real(path, *a, **kw)
            return op
        return calls, mock.patch.multiple(C.os, unlink=slow(real_unlink), rmdir=slow(real_rmdir))

    def test_stale_cleanup_is_gated_one_entry_at_a_time_and_pauses_while_the_disk_is_busy(self):
        manifest, raw, tar, expect = build(self, TREE)
        cache, root, outside = self.stale_cache()
        disk, seen = FakeDisk(self, lambda t: 95 if 1001 <= t < 1010 else 10), []
        calls, patch = self.removing(disk)
        with patch:
            event, _, final = self.sd_ensure(cache, raw, expect, Calls(raw, tar), disk, progress=lambda io: seen.append((disk.t, io)))
        self.assertEqual(event['kind'], 'cache_fill')
        self.assertEqual(event['removed_staging'], ['.staging-0123456789abcdef-old-uid', '.staging-stray'])
        self.assertEqual(sorted(os.listdir(root)), ['.lock', final.name])
        self.assertEqual((outside/'keep.txt').read_bytes(), b'k')  # the symlink itself was unlinked, never followed
        self.assertEqual(event['io']['removed'], len(calls))
        self.assertEqual(len(calls), 40 + 1 + 7 + 1)  # files, link, d0-d3/site/tree/staging dirs, stray file
        self.assertFalse([t for t, _ in calls if 1003 <= t < 1010 + SD['resume_hold_s'] - 1e-6])
        self.assertTrue([io for t, io in seen if io['paused'] and io['written'] == 0])
        self.assertGreaterEqual(event['io']['pauses'], 1)
        self.assertIsNotNone(event['io']['max_remove_s'])

    def test_stats_lost_or_the_deadline_passing_during_stale_cleanup_stop_it_before_any_fill(self):
        manifest, raw, tar, expect = build(self, TREE)
        for case in ('stats', 'deadline'):
            cache, root, _ = self.stale_cache()
            disk, tar_calls = FakeDisk(self, lambda t: 10), Calls(raw, tar)
            kw = dict(fetch_deadline_s=2) if case == 'deadline' else {}
            if case == 'stats':
                disk.rows = lambda d: FakeDisk.read(dict_disk(d)) if d.t < 1002 else ''
            calls, patch = self.removing(disk)
            with patch, self.assertRaises(C.CacheError) as caught:
                self.sd_ensure(cache, raw, expect, tar_calls, disk, **kw)
            self.assertEqual(caught.exception.phase, 'cache_cleanup_failed', case)
            self.assertIn('IOStatsError' if case == 'stats' else 'deadline', caught.exception.detail, case)
            self.assertFalse([t for t, _ in calls if t >= 1002 + (SD['sample_s'] if case == 'stats' else 0)], case)
            self.assertEqual(tar_calls.tar_calls, 0, case)
            left = sorted(os.listdir(root))
            self.assertIn('.staging-0123456789abcdef-old-uid', left, case)  # partially removed, still unpublished staging
            self.assertFalse([n for n in left if n == expect['content_sha256'] or n.endswith('-uid-1')], case)


class SdCollectorTest(unittest.TestCase):
    FOOT = dict(common=1, total=2, **{'cu130-aarch64-cp311': 3, 'mobilint-x86_64-cp311': 4})

    def test_sd_binding_needs_the_explicit_profile_and_an_mmcblk_partition(self):
        live, frozen, out, prepared = prepared_fixture(self, SD_PVC, SD['name'])
        self.assertEqual((prepared['io_profile'], prepared['backing_disk'], prepared['pvc']), (SD, 'mmcblk0', SD_PVC))
        self.assertEqual(prepared_fixture(self, dict(SD_PVC, backing_device='mmcblk1p3'), SD['name'])[3]['backing_disk'], 'mmcblk1')
        for pvc, name in ((SD_PVC, None), (SD_PVC, 'sd-fast'), (dict(SD_PVC, backing_device='mmcblk0'), SD['name']),
                          (dict(SD_PVC, backing_device='mmcblk0boot0'), SD['name']), (PVC, SD['name']), (dict(SD_PVC, extra='x'), SD['name'])):
            with self.assertRaisesRegex(ValueError, 'prepared runtime rejected'):
                prepared_fixture(self, pvc, name)

    def test_sd_deadlines_use_the_explicit_budget_and_extend_only_prepared_lifetimes(self):
        live = live_module()
        actual = dict(tar_bytes=8868372480, total_bytes=8853614095, file_count=19003)
        d = live.prepared_deadlines(actual, SD)
        D = live.P.DEADLINES
        self.assertEqual((d['fetch_s'], d['worker_ready_s']), (SD['fill_budget_s'], SD['fill_budget_s'] + D['load_s']['cuda'] + 300))
        self.assertEqual(d['pod_lifetime_s'] - d['worker_ready_s'], D['pod_lifetime_s'] - D['worker_ready_s'])  # same schedule window
        self.assertEqual(d['cache_lifetime_s'] - d['pod_lifetime_s'], D['cache_lifetime_s'] - D['pod_lifetime_s'])
        self.assertEqual((live.prepared_deadlines(actual)['pod_lifetime_s'], live.prepared_deadlines(actual)['cache_lifetime_s']),
                         (D['pod_lifetime_s'], D['cache_lifetime_s']))
        with self.assertRaisesRegex(ValueError, 'fill budget'):
            live.prepared_deadlines(dict(actual, tar_bytes=12*2**30), SD)

    def test_sd_plan_binds_profile_disk_budget_and_both_arm_lifetimes(self):
        live, frozen, out, prepared = prepared_fixture(self, SD_PVC, SD['name'])
        by_name = {i['metadata']['name']: i for i in live.plan('gmv-x', 'http://c', 'a'*64, self.FOOT, None, prepared)}
        d = prepared['deadlines']
        cuda = by_name['gmv-x-cuda']['spec']
        env = {e['name']: e.get('value') for e in cuda['containers'][0]['env']}
        self.assertEqual(json.loads(env['CACHE_IO_PROFILE']), SD)
        self.assertEqual((env['CACHE_BACKING_DEVICE'], env['CACHE_BACKING_DISK'], env['PREPARED_FETCH_DEADLINE_S']),
                         ('mmcblk0p1', 'mmcblk0', str(SD['fill_budget_s'])))
        self.assertEqual([by_name[n]['spec']['activeDeadlineSeconds'] for n in ('gmv-x-cuda', 'gmv-x-mobilint', 'gmv-x-cache')],
                         [d['pod_lifetime_s'], d['pod_lifetime_s'], d['cache_lifetime_s']])
        old = {i['metadata']['name']: i for i in live.plan('gmv-x', 'http://c', 'a'*64, self.FOOT)}
        mobilint = json.loads(json.dumps(by_name['gmv-x-mobilint']))
        mobilint['spec']['activeDeadlineSeconds'] = old['gmv-x-mobilint']['spec']['activeDeadlineSeconds']
        self.assertEqual(mobilint, old['gmv-x-mobilint'])  # image/resources/env unchanged; only the lifetime
        _, _, _, plain = prepared_fixture(self)
        self.assertNotIn('CACHE_IO_PROFILE', {e['name'] for e in live.worker_pod('gmv-x', 'cuda', 'http://c', 'a'*64, plain)['spec']['containers'][0]['env']})

    def test_sd_release_binds_profile_and_deadlines_exactly(self):
        live, frozen, out, prepared = prepared_fixture(self, SD_PVC, SD['name'])
        base = dict(frozen={n: live.P.file_sha(frozen/n) for n in ('contract.json', 'assets-manifest.json')}, code={'x': 'y'}, approved_by='r', stages=['probe'])
        release = dict(base, prepared=prepared['binding'], cuda_cache_pvc=prepared['pvc'], prepared_deadlines=prepared['deadlines'], io_profile=SD)
        self.assertEqual(live.release_problems(release, frozen, {'x': 'y'}, None, prepared), [])
        for change in (dict(io_profile=None), dict(io_profile=dict(SD, max_bps=8*MiB)), dict(prepared_deadlines=dict(prepared['deadlines'], fetch_s=99)),
                       dict(cuda_cache_pvc=dict(SD_PVC, backing_device='mmcblk0p2'))):
            self.assertTrue(live.release_problems(dict(release, **change), frozen, {'x': 'y'}, None, prepared), change)
        _, _, _, plain = prepared_fixture(self)
        self.assertTrue(live.release_problems(release, frozen, {'x': 'y'}, None, plain))
        self.assertEqual(live.release_problems(base, frozen, {'x': 'y'}), [])  # old mode unchanged


if __name__ == '__main__':
    unittest.main()
