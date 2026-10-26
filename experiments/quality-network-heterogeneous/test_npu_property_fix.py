"""Regressions for the first-live-run defects (npu-property-fix-spec.md + coordinator amendments, run qnh-20261023t090355).
Fake OpenVINO cores, sessions, clocks and disks are plumbing evidence only; no device, driver or kubelet behaviour is shown."""
import errno
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import unittest.mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bootstrap  # noqa: E402
import catalog  # noqa: E402
import proc  # noqa: E402
import qualify  # noqa: E402
import stages  # noqa: E402
from test_fix_r1 import PAIRS, REFS  # noqa: E402
from test_fix_r2 import FakeLive, pod  # noqa: E402

AUDIT = catalog.load_audit()
RUN1 = qualify.ROOT/qualify.RUN1


def core_returning(devices):
    import numpy as np

    class Compiled:
        def get_property(self, name):
            if name == 'EXECUTION_DEVICES':
                return devices
            if name == 'NPU_COMPILER_TYPE':
                return 'DRIVER'
            raise KeyError(name)

        def create_infer_request(self):
            class Request:
                def infer(self, inputs):
                    return {0: np.zeros((1, 50, 32), dtype=np.float32)}
            return Request()

    class Core:
        available_devices = ['NPU']

        def read_model(self, path):
            class Model:
                inputs = ['in']

                def reshape(self, shape):
                    pass
            return Model()

        def compile_model(self, model, device, config=None):
            return Compiled()

        def get_property(self, device, name):
            return 'Intel(R) AI Boost'
    return Core()


class NpuNormalization(unittest.TestCase):
    def test_string_and_sequence_npu_accepted_others_rejected(self):
        self.assertEqual(stages.require_direct_npu('NPU'), (['NPU'], dict(type='str', value='NPU')))
        self.assertEqual(stages.require_direct_npu(['NPU'])[0], ['NPU'])
        self.assertEqual(stages.require_direct_npu(('NPU',))[1]['type'], 'tuple')
        for bad in ('CPU', ['NPU', 'CPU'], 'NPU,CPU', '', [], ['GPU'], 'N'):
            with self.assertRaises(stages.DeviceMismatch, msg=repr(bad)):
                stages.require_direct_npu(bad)

    def build_vision(self, devices):
        tmp = tempfile.mkdtemp()
        onnx = Path(tmp)/'v.onnx'
        onnx.write_bytes(b'fake')
        import openvino
        with unittest.mock.patch.object(openvino, 'Core', lambda: core_returning(devices)):
            return stages.OpenVinoVision(onnx)

    def test_vision_init_accepts_runtime_string_and_records_raw(self):
        v = self.build_vision('NPU')
        self.assertEqual(v.execution_devices, ['NPU'])
        self.assertEqual(v.describe()['execution_devices_raw'], dict(type='str', value='NPU'))
        self.assertEqual(self.build_vision(['NPU']).describe()['execution_devices_raw'], dict(type='list', value="['NPU']"))  # repr: JSON-safe raw

    def test_vision_init_rejects_cpu_mixed_empty(self):
        for bad in ('CPU', ['NPU', 'CPU'], '', []):
            with self.assertRaises(stages.DeviceMismatch, msg=repr(bad)):
                self.build_vision(bad)

    def probe(self, devices):
        tmp = tempfile.mkdtemp()
        onnx = Path(tmp)/'s.onnx'
        onnx.write_bytes(b'fake')
        return stages.OpenVinoSpeechProbe(onnx, core=core_returning(devices), ov_version='fake')

    def payload(self):
        import numpy as np
        buf = io.BytesIO()
        np.save(buf, np.zeros(16000, dtype=np.float32), allow_pickle=False)
        return buf.getvalue()

    def test_speech_request_time_string_and_list_npu(self):
        for devices, raw in (('NPU', dict(type='str', value='NPU')), (['NPU'], dict(type='list', value="['NPU']"))):
            out, timing = self.probe(devices).run(self.payload())
            self.assertEqual(timing['execution_devices'], ['NPU'])
            self.assertEqual(timing['execution_devices_raw'], raw)

    def test_speech_cpu_mixed_empty_are_device_verification_errors_not_compile(self):
        for bad in ('CPU', ['NPU', 'CPU'], ''):
            with self.assertRaises(stages.PhaseError, msg=repr(bad)) as caught:
                self.probe(bad).run(self.payload())
            self.assertEqual(caught.exception.phase, 'device_verification')
            self.assertIsInstance(caught.exception.original, stages.DeviceMismatch)

    def test_analysis_maps_device_mismatch_to_runtime_device_verification(self):
        # the raw run-1 load error (str 'NPU' split by list()) is an adapter/runtime check failure, not a model compile failure
        raw = json.loads((RUN1/'health-npu-c-002.json').read_text())['health']['errors']['vision']
        npu = next(w for w in qualify.workers_for(AUDIT) if w['key'] == 'npu-c')
        r = qualify.stage_load_failure(npu, 'vision', dict(health=dict(errors=dict(vision=raw))))
        self.assertEqual((r['execution'], r['failure_phase']), ('runtime_failed', 'device_verification'))
        self.assertIn("['N', 'P', 'U']", r['execution_evidence']['error'])
        other = qualify.stage_load_failure(npu, 'vision', dict(health=dict(errors=dict(vision=dict(type='RuntimeError', error='x', status='load_failed')))))
        self.assertEqual(other['execution'], 'compile_failed')
        rows = [dict(index=i, question_id=p['question_id'], split=p['split'], status='error', error_phase='device_verification')
                for i, p in enumerate(PAIRS)]
        s = qualify.stage_result(npu, 'speech', rows, 'uid', REFS)
        self.assertEqual((s['execution'], s['failure_phase']), ('runtime_failed', 'device_verification'))


class ProcRetention(unittest.TestCase):
    def test_short_fast_exit_stderr_is_retained_in_tail_file_and_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(25):  # the run-1 race: 89-byte stderr, child exits immediately
                event = proc.run([sys.executable, '-c', "import sys; sys.stderr.write('E'*89); sys.exit(1)"], tmp, f'short-{i}', 30)
                self.assertEqual(event['returncode'], 1)
                self.assertEqual(event['stderr_tail'], 'E'*89)
                self.assertEqual(event['retention']['stderr']['retained_head_bytes'], 89)
                self.assertEqual(event['retention']['stderr']['truncated_bytes'], 0)
                self.assertEqual(Path(event['stderr']).read_text(), 'E'*89)
                self.assertEqual(event['log_write_errors'], {})

    def test_failed_disk_writes_are_recorded_and_draining_continues(self):
        class FullDisk(io.RawIOBase):
            def writable(self):
                return True

            def write(self, b):
                raise OSError(errno.ENOSPC, 'No space left on device')
        real_open = Path.open

        def fake_open(self, mode='r', *a, **k):
            return FullDisk() if mode == 'wb' else real_open(self, mode, *a, **k)
        script = "import sys\nfor _ in range(64): sys.stdout.write('o'*8192); sys.stderr.write('e'*8192)\nsys.stderr.write('LAST-LINE')"
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.object(Path, 'open', fake_open):
            event = proc.run([sys.executable, '-c', script], tmp, 'enospc', 60, tail_bytes=1 << 16)
        self.assertEqual((event['returncode'], event['timed_out']), (0, False))  # never blocked on a full pipe
        self.assertEqual(event['stderr_bytes'], 64*8192 + 9)
        self.assertTrue(event['stderr_tail'].endswith('LAST-LINE'))
        for stream in ('stdout', 'stderr'):
            self.assertIn('No space left', event['log_write_errors'][stream])
            self.assertTrue(event['retention'][stream]['drained'])
            self.assertEqual(event['retention'][stream]['retained_head_bytes'], 0)


def live_health(by_key):
    def health(key, t):
        return dict(status='ready', bootstrap=dict(phase='loaded'), stages={s: {} for s in by_key[key]['stages']})
    return health


def processor():
    from transformers import Wav2Vec2Processor
    return Wav2Vec2Processor.from_pretrained(qualify.ASSETS/'model-assets/asr', local_files_only=True)


class InWindowCollection(unittest.TestCase):
    def test_ready_npu_gpu_cpu_are_collected_before_the_window_ends_despite_a_never_ready_device(self):
        workers = qualify.workers_for(AUDIT)
        keys = {w['key'] for w in workers}
        jet = next(w['key'] for w in workers if w['backend'] == 'cuda-torch')  # current wave's single Jetson worker
        self.assertTrue({'npu-c', jet, 'cpu-c', 'cpu-e0'} <= keys)
        never = 'cpu-e0'
        script = {k: (lambda t, k=k: pod(k, phase='Pending')) if k == never else (lambda t, k=k: pod(k, ready=t >= 20)) for k in keys}
        by_key = {w['key']: w for w in workers}
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeLive(tmp, script, live_health(by_key))
            calls, original = [], fake.http

            def timed(url, body=None, timeout=None):
                if body is not None:
                    fake.t += 0.1
                    calls.append((fake.t, body['request_id']))
                return original(url, body, timeout)
            window = 1500
            results = qualify.collect_live(fake, dict(workers=workers), PAIRS, REFS, processor(), started=0.0, clock=fake.clock,
                                           sleep=fake.sleep, http_fn=timed, budgets=dict(ready_window_s=window, collection_s=3000, poll_s=10))
            self.assertEqual(results[never]['deployment'], 'not_ready_at_window')
            mixed = [t for t, rid in calls if rid.startswith(f'e2e-npu-c_{jet}_cpu-c')]
            self.assertEqual(len(mixed), 180)  # vision + speech + fusion for each of the 60 pairs
            self.assertLess(max(mixed), window)
            for key, stage in (('npu-c', 'vision'), (jet, 'vision'), (jet, 'speech'), ('cpu-c', 'fusion')):
                times = [t for t, rid in calls if rid.startswith(f'{key}-{stage}-')]
                self.assertEqual(len(times), 60, (key, stage))
                self.assertLess(max(times), window, (key, stage))
            ids = [rid for _, rid in calls]
            self.assertEqual(len(ids), len(set(ids)))  # ledger: nothing collected twice
            first_probe = min(t for t, rid in calls if rid.startswith('npu-c-speech-'))
            self.assertGreater(first_probe, max(mixed))  # long NPU speech probe still deferred

    def test_interrupt_leaves_explicit_pending_and_uncollected_states_and_reanalysis_is_separate(self):
        workers = [w for w in qualify.workers_for(AUDIT) if w['key'] in ('cpu-c', 'cpu-e0')]
        script = {'cpu-c': lambda t: pod('cpu-c', ready=t >= 20), 'cpu-e0': lambda t: pod('cpu-e0', phase='Pending')}
        by_key = {w['key']: w for w in workers}
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)/'qnh-t'
            run.mkdir()
            fake = FakeLive(run, script, live_health(by_key))
            original, sent = fake.http, []

            def interrupting(url, body=None, timeout=None):
                if body is not None:
                    sent.append(body['request_id'])
                    if len(sent) > 4:
                        raise KeyboardInterrupt()
                return original(url, body, timeout)
            with self.assertRaises(KeyboardInterrupt):
                qualify.collect_live(fake, dict(workers=workers), PAIRS, REFS, None, started=0.0, clock=fake.clock, sleep=fake.sleep,
                                     http_fn=interrupting, budgets=dict(ready_window_s=1500, collection_s=3000, poll_s=10))
            saved = json.loads((run/'collector-results.json').read_text())
            self.assertEqual(saved['cpu-e0']['deployment'], 'interrupted_pending')
            self.assertEqual(saved['cpu-e0']['snapshot_files'], ['health-cpu-e0-001.json'])
            self.assertTrue((run/'health-cpu-e0-001.json').exists())
            self.assertEqual(saved['cpu-c']['interrupted_before_collection'], ['speech', 'fusion'])
            (run/'wave.json').write_text(json.dumps(dict(workers=workers)))
            (run/'workers.created.json').write_text(json.dumps(dict(confirmed=[dict(kind='Pod', name='qnh-t-'+k, uid='uid-'+k) for k in by_key])))
            (run/'run-status.json').write_text(json.dumps(dict(status='error', error='KeyboardInterrupt()')))
            before = {p.name: p.read_bytes() for p in run.iterdir()}
            out = qualify.analyze(run, out_dir=Path(tmp)/'reanalysis')
            self.assertEqual({p.name: p.read_bytes() for p in run.iterdir()}, before)  # original run untouched
            self.assertTrue((Path(tmp)/'reanalysis/analysis.json').exists() and (Path(tmp)/'reanalysis/catalog.json').exists())
            res = out['stage_results']
            pending = res['etri-dev0003-raspi5|cpu-torch|vision']
            self.assertEqual((pending['execution'], pending['failure_phase']), ('interrupted_pending', 'pending_at_interrupt'))
            self.assertEqual(pending['execution_evidence']['snapshot_files'], ['health-cpu-e0-001.json'])
            self.assertEqual(res['etri-ser0002-cgnmsb|cpu-torch|vision']['coverage']['rows'], 4)  # the rows sent before the interrupt
            self.assertEqual(res['etri-ser0002-cgnmsb|cpu-torch|speech']['execution_evidence']['note'], 'ready; interrupted before collection')


class WavePolicy(unittest.TestCase):
    def test_one_driver_matched_jetson_recipe_blocked_nodes_and_no_repeated_compile_probe(self):
        # current selection details (dev0009 cu130, dev0001 held) are in test_dev0009_wave
        workers = qualify.workers_for(AUDIT)
        jet = [w for w in workers if w['backend'] == 'cuda-torch']
        self.assertEqual(len(jet), 1)
        self.assertIn('driver_api', jet[0]['recipe_selection'])
        self.assertNotIn('mblt-compile', [w['key'] for w in workers])
        blocked = [r for r in catalog.build(AUDIT)['rows'] if r['node'] == 'etri-dev0007-jetagx' and r['backend'] == 'cuda-torch']
        self.assertEqual({r['status'] for r in blocked}, {'deployment_blocked'})
        self.assertTrue(all('revalidation' in r['deployment_blocker'] for r in blocked))

    def test_plan_ships_only_used_recipes_sysroot_preload_and_per_recipe_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            wave = qualify.plan(Path(tmp)/'p', qualify.OUT_ROOT/'export-20261023T075427Z',
                                [qualify.OUT_ROOT/'deps-20261023T075919Z', qualify.OUT_ROOT/'deps-20261023T080028Z'], hash_files=False)
            assets = json.loads((Path(tmp)/'p/assets-manifest.json').read_text())
        groups = {e['group'] for e in assets['files']}
        used = {w['recipe'] for w in wave['workers'] if w['recipe'] in qualify.REQUIREMENTS}
        self.assertEqual({g for g in groups if g in qualify.REQUIREMENTS}, used)  # only recipes the wave uses
        self.assertNotIn('calib', groups)
        self.assertEqual(set(assets['recipes']), used)
        # JP6.1 sysroot shipping (plan 094437Z behaviour; recipe now held) still resolves to the hashed sysroot files
        jp_files = {p: s for g, p, s in qualify.asset_files(qualify.OUT_ROOT/'export-20261023T075427Z',
                                                            [qualify.OUT_ROOT/'deps-20261023T075919Z'], recipes={'jp61-aarch64-cp310'},
                                                            calibration_inputs=False) if p.startswith('sysroot/')}
        sysroot = json.loads((qualify.JP61_SYSROOT/'sysroot-manifest.json').read_text())
        self.assertEqual(sysroot['closure']['unresolved'], [])
        self.assertTrue(sysroot['closure']['image_checked'])
        for soname, real in qualify.SYSROOT_PRELOAD['jp61-aarch64-cp310']:
            self.assertEqual(qualify.sha(jp_files[f'sysroot/jp61-aarch64-cp310/{soname}']), sysroot['sysroot'][real]['sha256'])
        self.assertIn('prepare_jp61_sysroot.py', wave['planner_sources'])
        pods = {p['metadata']['name'].split('PLACEHOLDER-')[1]: p for p in wave['manifests_template']['pods']}
        for w in wave['workers']:
            spec = pods[w['key']]['spec']
            res = spec['containers'][0]['resources']
            env = {e['name']: e.get('value') for e in spec['containers'][0]['env']}
            self.assertEqual(env['TMPDIR'], '/work/tmp')
            if w['recipe'] not in qualify.REQUIREMENTS:
                continue
            st = w['storage']
            self.assertGreaterEqual(int(st['quantity'][:-2]) * qualify.GIB, st['total_bytes'])
            self.assertGreaterEqual(st['pip_staging_bytes'], st['largest_unpacked_wheel_bytes'])
            self.assertEqual(res['requests']['ephemeral-storage'], st['quantity'])
            self.assertEqual(res['limits']['ephemeral-storage'], st['quantity'])
            self.assertEqual(next(v for v in spec['volumes'] if v['name'] == 'work')['emptyDir']['sizeLimit'], st['quantity'])
        jw = next(w for w in wave['workers'] if w['backend'] == 'cuda-torch')
        self.assertGreater(jw['storage']['install_unpacked_bytes'], 1 << 30)  # the multi-GB torch wheel is accounted


class BootstrapPreload(unittest.TestCase):
    def test_preload_loads_by_absolute_path_and_reports_failure(self):
        import ctypes.util
        libm = ctypes.util.find_library('m')
        path = next(p for d in ('/lib/x86_64-linux-gnu', '/usr/lib/x86_64-linux-gnu', '/lib64', '/usr/lib64') for p in [Path(d)/libm]
                    if p.exists())
        self.assertEqual(bootstrap.preload([path]), dict(kind='preload', libraries=[str(path)]))
        with self.assertRaises(OSError):
            bootstrap.preload([Path('/nonexistent/libopenblas.so.0')])

    def test_preload_failure_is_a_deployment_failure_phase(self):
        w = dict(key='jgpu-dev0001-jp61', node='etri-dev0001-jetorn', backend='cuda-torch', recipe='jp61-aarch64-cp310')
        entry = dict(deployment='ready', health=dict(status='preload_failed', bootstrap=dict(phase='preload_failed', error='OSError')))
        self.assertEqual(qualify.worker_failure(w, entry, None)['failure_phase'], 'preload')


if __name__ == '__main__':
    unittest.main()
