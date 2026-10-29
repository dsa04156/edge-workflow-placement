"""Seams of the live diagnostic: vlm_diag_stage.Engine (both branches) over the REAL base vlm_stage.Engine.generate and the
real upstream transformers 4.57.1 Qwen3VLForConditionalGeneration.generate (tiny random config, CPU), VlmStage.run
propagation, the Pod ConfigMap layout in a fresh process with a real spawn child, and diag_live's release/argument gates.

Fakes, and only these: the processor (fixed tiny batch), and for the CUDA branch on a CPU-only host torch.cuda.synchronize /
reset_peak_memory_stats / max_memory_allocated / Event and a generated tensor that reports device cuda:0. These tests say
nothing about Jetson or ARIES latency and are not GPU/NPU runs. The prepared tar is not re-hashed here (hash_tar=False).

Run (experiments/gpu-mobilint-vlm):
  PYTHONDONTWRITEBYTECODE=1 CUDA_VISIBLE_DEVICES= HF_HUB_OFFLINE=1 \
    ../../.scratch/gpu-mobilint-vlm-20261028/env/mblt-x86/bin/python -m unittest -v test_live_diagnostic
"""
import hashlib
import io
import json
import os
import re
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import cProfile  # noqa: F401  stdlib `profile` first: live's sys.path adds experiments/real-topology-profile/profile.py

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import live  # noqa: E402  (puts the heterogeneous/live/collect directories on sys.path)
import collect  # noqa: E402
import diag_live  # noqa: E402
import protocol as P  # noqa: E402
import ttft_probe  # noqa: E402
import vlm_diag_stage  # noqa: E402
import vlm_stage  # noqa: E402

S = live.ROOT/'.scratch'
FROZEN = S/'gpu-mobilint-vlm-20261028/developer-frozen-r1'
PREPARED = S/'vlm-persistent-ready-20261028/prepared-r1'
PVC = S/'vlm-persistent-ready-20261028/cuda-cache-pvc-sd.json'
LIVE_ORIGINAL = (live.SHIPPED, live.dependency_hashes)
COMMON = ['--frozen', str(FROZEN), '--prepared', str(PREPARED), '--cuda-cache-pvc', str(PVC), '--io-profile', 'sd-adaptive-v1']


def tiny_model():
    import torch
    from transformers import Qwen3VLConfig, Qwen3VLForConditionalGeneration
    torch.manual_seed(0)
    config = Qwen3VLConfig(
        vision_config=dict(depth=2, hidden_size=16, intermediate_size=32, num_heads=2, patch_size=4, temporal_patch_size=2,
                           spatial_merge_size=2, out_hidden_size=32, num_position_embeddings=16, deepstack_visual_indexes=[1]),
        text_config=dict(hidden_size=32, intermediate_size=64, num_hidden_layers=2, num_attention_heads=2, num_key_value_heads=1,
                         head_dim=16, rope_scaling=dict(mrope_interleaved=True, mrope_section=[4, 2, 2], rope_type='default')))
    return Qwen3VLForConditionalGeneration(config).eval(), config


class Processor:
    """Stand-in for the HF processor: a fixed tiny Qwen3-VL batch (one image of 16 patches -> 4 merged tokens)."""

    def __init__(self, config):
        import torch
        from transformers import BatchFeature
        ids = [config.vision_start_token_id] + [config.image_token_id]*4 + [config.vision_end_token_id] + [11, 12, 13]
        self.prompt_len = len(ids)
        self.batch = lambda: BatchFeature(dict(input_ids=torch.tensor([ids]), attention_mask=torch.ones(1, len(ids), dtype=torch.long),
                                               pixel_values=torch.randn(16, 3*2*4*4, generator=torch.Generator().manual_seed(1)),
                                               image_grid_thw=torch.tensor([[1, 4, 4]])))

    def apply_chat_template(self, messages, tokenize, add_generation_prompt):
        return messages[0]['content'][1]['text']

    def __call__(self, text, images, images_kwargs, return_tensors):
        return self.batch()

    def decode(self, ids, skip_special_tokens):
        return ' '.join(map(str, ids))


class CudaLooking:
    """The generated ids as base Engine.generate reads them, reporting device cuda:0 (CPU-only host)."""

    def __init__(self, tensor):
        import torch
        self.tensor, self.device = tensor, torch.device('cuda:0')

    def __getitem__(self, key):
        return self.tensor[key]


class FakeEvent:
    def __init__(self, enable_timing):
        self.at = None

    def record(self):
        self.at = time.perf_counter()

    def elapsed_time(self, end):
        return (end.at-self.at)*1000


def image():
    import numpy as np
    buffer = io.BytesIO()
    np.save(buffer, np.zeros((8, 8, 3), dtype=np.uint8))
    return buffer.getvalue()


def make_engine(cls, backend, model, config):
    data = image()
    contract = dict(messages_template=P.CONTRACT['messages_template'], image_size=P.CONTRACT['image_size'],
                    generation=dict(do_sample=False, num_beams=1, max_new_tokens=3, min_new_tokens=3, pad_token_id=0))
    engine = object.__new__(cls)
    engine.backend, engine.contract, engine.model, engine.processor, engine.input_device = backend, contract, model, Processor(config), 'cpu'
    engine.inputs = {hashlib.sha256(data).hexdigest(): dict(id='in-0', shape=[8, 8, 3], prompt='q?')}
    return engine, data


def hook_count(model):
    return sum(len(m._forward_hooks)+len(m._forward_pre_hooks) for m in model.modules())


class CudaHost:
    """CPU stand-ins for the torch.cuda calls of the CUDA branch (base generate + probe events)."""

    def __enter__(self):
        import torch
        self.patches = [mock.patch.object(torch.cuda, 'synchronize', lambda *a: None),
                        mock.patch.object(torch.cuda, 'reset_peak_memory_stats', lambda *a: None),
                        mock.patch.object(torch.cuda, 'max_memory_allocated', lambda *a: 0),
                        mock.patch.object(torch.cuda, 'Event', FakeEvent)]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()


class Adapter(unittest.TestCase):
    def setUp(self):
        self.model, self.config = tiny_model()
        self.inner = self.model.generate

    def cuda_looking(self, raise_after=None):
        def wrapper(*args, **kwargs):
            out = self.inner(*args, **kwargs)
            if raise_after:
                raise raise_after
            return CudaLooking(out)
        self.model.__dict__['generate'] = wrapper
        return wrapper

    def test_cuda_branch_calls_the_base_generate_once_and_adds_the_split(self):
        wrapper = self.cuda_looking()
        engine, data = make_engine(vlm_diag_stage.Engine, 'cuda', self.model, self.config)
        reference, _ = make_engine(vlm_stage.Engine, 'cuda', self.model, self.config)
        with CudaHost():
            expected = reference.generate(data)
            with mock.patch.object(vlm_stage.Engine, 'generate', autospec=True, side_effect=vlm_stage.Engine.generate) as spy:
                result = engine.generate(data)
        spy.assert_called_once_with(engine, data)
        diagnostic = result.pop('diagnostic')
        self.assertEqual(result['token_ids'], expected['token_ids'])
        self.assertEqual(result['text'], expected['text'])
        self.assertEqual(result['timing'].keys(), expected['timing'].keys())
        self.assertEqual(result['device'], dict(max_memory_allocated=0, output_device='cuda:0'))
        split = diagnostic['split']
        self.assertEqual((split['vision_calls'], split['language_calls'], split['lm_head_calls']), (1, 3, 3))
        self.assertEqual(split['prefill_seq'], engine.processor.prompt_len)
        self.assertEqual(split['decode_seqs'], [1])
        self.assertEqual(set(split['prefill']), {'host_ms', 'thread_cpu_ms', 'proc_cpu_ms', 'cuda_event_span_ms'})
        self.assertGreaterEqual(split['ttft_ms'], split['prefill_start_ms'] + split['prefill']['host_ms'])
        self.assertEqual(diagnostic['probe_errors'], [])
        self.assertEqual(set(diagnostic['environment']), {'before', 'after'})
        self.assertIn('cpu.stat', diagnostic['environment']['before']['cgroup'])
        self.assertIn('not kernel-active', diagnostic['event_note'])
        self.assertEqual(hook_count(self.model), 0)
        self.assertIs(self.model.__dict__['generate'], wrapper)  # a pre-existing instance attribute is restored, not dropped

    def test_mobilint_branch_is_the_base_call_with_environment_only(self):
        seen = []

        def wrapper(*args, **kwargs):
            seen.append(hook_count(self.model))
            return self.inner(*args, **kwargs)
        self.model.__dict__['generate'] = wrapper
        engine, data = make_engine(vlm_diag_stage.Engine, 'mobilint', self.model, self.config)
        with mock.patch.object(vlm_stage.Engine, 'generate', autospec=True, side_effect=vlm_stage.Engine.generate) as spy:
            result = engine.generate(data)
        spy.assert_called_once_with(engine, data)
        self.assertEqual(seen, [0])  # no hooks on the vendor model during the call
        self.assertEqual(set(result['diagnostic']), {'scope', 'environment'})
        self.assertEqual(set(result['diagnostic']['environment']), {'before', 'after'})
        self.assertEqual(result['device']['npu_timing'], vlm_stage.NPU_TIMING_UNAVAILABLE)
        self.assertIs(self.model.__dict__['generate'], wrapper)

    def test_original_exception_propagates_and_hooks_are_removed(self):
        engine, data = make_engine(vlm_diag_stage.Engine, 'cuda', self.model, self.config)
        with CudaHost(), self.assertRaisesRegex(vlm_stage.DeviceMismatch, 'generated output tensor on cpu'):
            engine.generate(data)  # base CPU-fallback check: the real tensor is on cpu
        self.assertEqual(hook_count(self.model), 0)
        self.assertNotIn('generate', self.model.__dict__)
        wrapper = self.cuda_looking(raise_after=RuntimeError('boom inside generate'))
        with CudaHost(), self.assertRaisesRegex(RuntimeError, '^boom inside generate$'):
            engine.generate(data)
        self.assertEqual(hook_count(self.model), 0)
        self.assertIs(self.model.__dict__['generate'], wrapper)

    def test_cleanup_problems_never_mask_the_original_exception(self):
        real = ttft_probe.Probe.detach
        self.cuda_looking(raise_after=ValueError('original'))
        engine, data = make_engine(vlm_diag_stage.Engine, 'cuda', self.model, self.config)
        with CudaHost(), mock.patch.object(ttft_probe.Probe, 'detach', lambda p: real(p)+['handle stuck']):
            with self.assertRaises(ValueError) as caught:
                engine.generate(data)
            self.assertEqual(str(caught.exception), 'original')
            self.assertIn('diagnostic cleanup: handle stuck', caught.exception.__notes__)
            self.cuda_looking()
            with self.assertRaisesRegex(RuntimeError, 'diagnostic cleanup failed'):
                engine.generate(data)  # no original exception: a cleanup problem fails the call instead of passing silently
        self.assertEqual(hook_count(self.model), 0)

    def test_a_failing_hook_never_changes_the_call(self):
        self.cuda_looking()
        engine, data = make_engine(vlm_diag_stage.Engine, 'cuda', self.model, self.config)
        with CudaHost():
            expected = engine.generate(data)
            with mock.patch.object(ttft_probe, '_seq_len', side_effect=KeyError('layout')):
                result = engine.generate(data)
        self.assertEqual(result['token_ids'], expected['token_ids'])
        self.assertTrue(result['diagnostic']['probe_errors'])
        self.assertIn('no language_model call', result['diagnostic']['split_error'])

    def test_diagnostic_travels_through_the_unchanged_run(self):
        self.cuda_looking()
        engine, data = make_engine(vlm_diag_stage.Engine, 'cuda', self.model, self.config)
        with CudaHost():
            result = engine.generate(data)

        class Conn:
            def send_bytes(self, raw):
                self.sent = raw

            def poll(self, timeout):
                return True

            def recv(self):
                return dict(ok=True, result=result)
        stage = object.__new__(vlm_diag_stage.VlmStage)
        stage.dead, stage.calls, stage.call_s, stage.record, stage.conn = None, 0, 5, {}, Conn()
        output, timing = stage.run(data)
        self.assertEqual(json.loads(output)['diagnostic'], json.loads(json.dumps(result['diagnostic'])))
        self.assertEqual(set(timing), set(result['timing']) | {'host', 'device_stats', 'output_device'})
        self.assertLess(len(output), live.RUN_REPLY_MAX)


class PodLayout(unittest.TestCase):
    def test_configmap_names_and_fresh_spawn_child_use_the_diagnostic_engine(self):
        with mock.patch.object(live, 'SHIPPED', diag_live.SHIPPED):
            data = live.config_map('gmv-test', prepared=True)['data']
        self.assertEqual(data['vlm_stage.py'], (HERE/'vlm_diag_stage.py').read_text())
        self.assertEqual(data['vlm_stage_base.py'], (HERE/'vlm_stage.py').read_text())
        self.assertEqual(data['ttft_probe.py'], (HERE/'ttft_probe.py').read_text())
        self.assertEqual(set(data) - {'vlm_stage_base.py', 'ttft_probe.py'}, set(live.SHIPPED) | set(live.SHIPPED_PREPARED))
        self.assertLess(sum(len(v.encode()) for v in data.values()), 900*1024)  # ConfigMap object limit is 1 MiB
        with tempfile.TemporaryDirectory() as tmp:
            config, work = Path(tmp, 'config'), Path(tmp, 'work')
            config.mkdir()
            for name, text in data.items():
                (config/name).write_text(text)
            (work/'models'/P.ARMS['cuda']['model']).mkdir(parents=True)
            (work/'contract.json').write_text(json.dumps(dict(host_threads=1, inputs=[], models={P.ARMS['cuda']['model']: dict(weights={})})))
            script = ("import json,sys;sys.path.insert(0,sys.argv[1]);import importlib;"
                      "w=importlib.import_module('vlm_worker');import vlm_stage;"
                      "assert vlm_stage.__file__==sys.argv[1]+'/vlm_stage.py' and vlm_stage.base.__name__=='vlm_stage_base',vlm_stage.__file__;"
                      "assert vlm_stage.base.child_main is vlm_stage._base_child_main;"
                      "x=w.Worker('cuda',['vlm'],model_root=sys.argv[2]+'/models');"
                      "assert vlm_stage.base.child_main is vlm_stage._base_child_main;"
                      "print(json.dumps(x.errors['vlm']))")
            done = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(config), str(work)], capture_output=True, text=True,
                                  timeout=300, env=dict(os.environ, CUDA_VISIBLE_DEVICES=''))
            self.assertEqual(done.returncode, 0, done.stderr[-3000:])
            error = json.loads(done.stdout.strip().splitlines()[-1])
        record = error['record']
        self.assertEqual((record['reason'], record['child_alive_after_kill']), ('load_failed', False))
        self.assertIn('torch.cuda.is_available() is False', record['error'])
        # the spawned child ran the unchanged base child loop, which built the diagnostic Engine (vlm_stage.py = this file under its
        # ConfigMap name) whose __init__ is the frame above the unchanged base Engine.__init__
        frames = re.findall(r'File "([^"]+)", line \d+, in (\w+)', record['trace'])
        self.assertEqual(frames, [(f'{config}/vlm_stage_base.py', 'child_main'), (f'{config}/vlm_stage.py', '__init__'),
                                  (f'{config}/vlm_stage_base.py', '__init__')])


class Runner(unittest.TestCase):
    """diag_live gates. The prepared tar is bound by its manifest size here (hash_tar=False); the draft command re-hashes it."""

    @classmethod
    def setUpClass(cls):
        cls.code = diag_live.dependency_hashes()

    def setUp(self):
        real = live.load_prepared
        self.patches = [mock.patch.object(live, 'load_prepared', lambda *a, **k: real(*a, **dict(k, hash_tar=False))),
                        mock.patch.object(diag_live, 'dependency_hashes', lambda: dict(self.code))]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def draft(self):
        out = io.StringIO()
        with mock.patch('sys.stdout', out):
            diag_live.main(COMMON + ['--print-release-draft'])
        return json.loads(out.getvalue())

    def run_main(self, release):
        Path(self.tmp, 'release.json').write_text(json.dumps(release))
        calls = []
        with mock.patch.object(collect.Session, 'call', lambda s, *a, **k: calls.append(a) or self.fail('kubectl reached')), \
                mock.patch.object(live, 'Session', side_effect=AssertionError('session reached')) as session:
            try:
                diag_live.main(COMMON + ['--runs', str(self.tmp/'runs'), '--release', str(self.tmp/'release.json'),
                                         '--stage', 'qualification'])
            finally:
                self.assertEqual(calls, [])
                self.assertIs(live.SHIPPED, LIVE_ORIGINAL[0])  # restored on every exit path
                self.assertIs(live.dependency_hashes, LIVE_ORIGINAL[1])
        return session

    def test_release_binding_covers_every_new_and_shipped_file(self):
        for name in ('diag_live.py', 'vlm_diag_stage.py', 'ttft_probe.py', 'vlm_stage.py', 'vlm_worker.py', 'live.py',
                     'vlm_prepared_bootstrap.py', 'vlm_runtime_cache.py', 'protocol.py'):
            key = f'experiments/gpu-mobilint-vlm/{name}'
            self.assertEqual(self.code.get(key), P.file_sha(HERE/name), key)
        draft = self.draft()
        self.assertEqual(draft['code'], self.code)
        self.assertEqual(draft['io_profile'], live.C.SD_PROFILE)
        self.assertEqual(draft['cuda_cache_pvc'], json.loads(PVC.read_text()))
        self.assertNotIn('arms', draft)  # the pair

    def test_stale_or_plain_live_release_fails_before_kubectl(self):
        release = dict(self.draft(), approved_by='test', stages=['probe', 'qualification'])
        stale = dict(release, code=dict(release['code'], **{'experiments/gpu-mobilint-vlm/vlm_diag_stage.py': '0'*64}))
        plain = dict(release, code=live.dependency_hashes())  # what live.py itself would bind
        for bad in (stale, plain, dict(release, arms=['cuda'])):
            with self.assertRaisesRegex(SystemExit, 'release check failed'):
                self.run_main(bad)
            self.assertFalse((self.tmp/'runs').exists())
        with self.assertRaisesRegex(AssertionError, 'session reached'):
            self.run_main(release)  # the exact release passes the same gate and reaches the session

    def test_required_flags_and_pair_only(self):
        cases = [
            (['--frozen', str(FROZEN), '--print-release-draft'], 'are required'),
            (COMMON[:4] + ['--print-release-draft'], 'io-profile sd-adaptive-v1 is required'),
            (COMMON + ['--arm', 'cuda', '--print-release-draft'], 'both arms'),
            (COMMON + ['--arm', 'mobilint', '--print-release-draft'], 'both arms'),
            (['--frozen', str(FROZEN), '--prepared', str(self.tmp), '--cuda-cache-pvc', str(PVC), '--io-profile', 'sd-adaptive-v1',
              '--print-release-draft'], 'prepared-manifest.json'),
        ]
        with mock.patch.object(live, 'main', side_effect=AssertionError('live.main reached')):
            for argv, message in cases:
                with self.assertRaisesRegex(SystemExit, message):
                    diag_live.main(argv)
            with mock.patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit) as caught:
                diag_live.main(COMMON + ['--ar', 'cuda', '--print-release-draft'])  # no abbreviation can smuggle --arm in
            self.assertEqual(caught.exception.code, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
