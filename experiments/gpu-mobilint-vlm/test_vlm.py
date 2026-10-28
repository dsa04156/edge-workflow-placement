"""Seams (coordinator-approved 2026-10-28): (1) VlmStage bounded child, (2) heterogeneous Worker wired with this loader,
(3) prepare manifest/frozen contract, (4) live pure plan + preflight refusal. The only fake is the external third-party
model entrypoint (a stand-in `transformers_mblt` package placed first on sys.path); torch, PIL, qbruntime are real.

Run (repo root):
  PYTHONPATH=experiments/gpu-mobilint-vlm:experiments/quality-network-heterogeneous:experiments/real-topology-live:experiments/multi-edge-sensors:experiments/mobilint-dag-pilot:experiments/quality-network-baselines \
    .scratch/gpu-mobilint-vlm-20261028/env/mblt-x86/bin/python -m unittest -v test_vlm
"""
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

HERE = Path(__file__).resolve().parent

FAKE_MODELING = '''
import os, sys, time
import torch


class _Backend:
    def __init__(self, name):
        self.target_device, self.core_mode, self.mxq_path, self.model_dev_no = 'aries-rb', 'global8', name, [0]
        self.mxq_models = [_Mxq()]


class _Mxq:
    def get_model_input_shape(self):
        return [(1, -1, 2048)]

    def get_model_output_shape(self):
        return [(1, -1, 151936)]


class _Part(torch.nn.Module):
    def __init__(self, name, table):
        super().__init__()
        self.npu_backend = _Backend(name)
        self.table = torch.nn.Embedding(*table)
        self.timing = dict(prefill_time=0.0, decode_time=0.0, prefill_calls=0, decode_calls=0)

    def reset_npu_timing(self):
        self.timing = dict(prefill_time=0.0, decode_time=0.0, prefill_calls=0, decode_calls=0)

    def get_npu_timing(self):
        return dict(self.timing, prefill_calls=1, decode_calls=2)


class _Inner(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.visual = _Part('Qwen3-VL-2B-Instruct_vision-W4V8.mxq', (4, 2))
        self.language_model = _Part('Qwen3-VL-2B-Instruct_text-W4V8.mxq', (8, 2))


class MobilintQwen3VLForConditionalGeneration(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.model = _Inner()

    @classmethod
    def from_pretrained(cls, path, **kwargs):
        mode = os.environ['FAKE_MODE']
        print('fake-load-start', path, flush=True)
        print('fake-load-stderr', file=sys.stderr, flush=True)
        if mode == 'load_error':
            raise RuntimeError('Model_DtypeMismatched (fake)')
        if mode == 'load_hang':
            time.sleep(60)
        if mode == 'load_exit':
            os._exit(5)
        return cls()

    def generate(self, input_ids=None, generation_config=None, streamer=None, **kwargs):
        if os.environ['FAKE_MODE'] == 'call_hang':
            print('fake-generate-hang', flush=True)
            time.sleep(60)
        if os.environ['FAKE_MODE'] == 'call_error':
            print('fake-generate-partial', flush=True)
            raise RuntimeError('external runtime inference failed (fake)')
        if os.environ['FAKE_MODE'] == 'call_exit':
            print('fake-generate-exit', flush=True)
            os._exit(7)
        assert generation_config.do_sample is False and generation_config.max_new_tokens == 64
        streamer.put(input_ids)
        new = []
        for token in (11, 12, 151645):
            time.sleep(0.01)
            new.append(token)
            streamer.put(torch.tensor([token]))
        streamer.end()
        return torch.cat([input_ids, torch.tensor([new])], dim=1)
'''

FAKE_PROCESSING = '''
import torch


class _Batch(dict):
    def to(self, device):
        return self


class MobilintQwen3VLProcessor:
    @classmethod
    def from_pretrained(cls, path, **kwargs):
        return cls()

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        assert messages[0]['content'][1]['text'] == 'What is on the mat?\\nAnswer the question using a single word or phrase.'
        return 'PROMPT'

    def __call__(self, text, images, images_kwargs, return_tensors):
        assert images_kwargs == dict(size=dict(shortest_edge=65536, longest_edge=200704))
        w, h = images[0].size
        return _Batch(input_ids=torch.tensor([[1, 2, 3, w % 7]]), attention_mask=torch.ones(1, 4, dtype=torch.long),
                      pixel_values=torch.zeros(4, 1536), image_grid_thw=torch.tensor([[1, 2, 2]]))

    def decode(self, ids, skip_special_tokens=True):
        return 'a fake caption' if list(ids)[:2] == [11, 12] else 'unexpected'
'''


def image_npy():
    import numpy as np
    out = io.BytesIO()
    np.save(out, np.full((40, 70, 3), 7, dtype=np.uint8), allow_pickle=False)
    return out.getvalue()


def contract_input():
    import protocol as P
    return dict(id='smoke-test', question_id=1, role='smoke', shape=[40, 70, 3], question='What is on the mat?',
                prompt='What is on the mat?'+P.PROMPT_SUFFIX, image_sha256=P.sha256(image_npy()))


class FakeVendor:
    """Places a stand-in third-party `transformers_mblt` package first on sys.path (spawned children inherit sys.path)."""

    def __init__(self, mode):
        self.mode = mode

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)/'site'
        pkg = root/'transformers_mblt/models/qwen3_vl'
        pkg.mkdir(parents=True)
        for d in (root/'transformers_mblt', root/'transformers_mblt/models', pkg):
            (d/'__init__.py').write_text('')
        (pkg/'modeling_qwen3_vl.py').write_text(textwrap.dedent(FAKE_MODELING))
        (pkg/'processing_qwen3_vl.py').write_text(textwrap.dedent(FAKE_PROCESSING))
        sys.path.insert(0, str(root))
        os.environ['FAKE_MODE'] = self.mode
        for name in [m for m in sys.modules if m.startswith('transformers_mblt')]:
            del sys.modules[name]
        self.work = Path(self.tmp.name)/'work'
        self.model_dir = self.work/'models/mobilint-qwen3vl-2b-f4be563'
        self.model_dir.mkdir(parents=True)
        for name in ('Qwen3-VL-2B-Instruct_vision-W4V8.mxq', 'Qwen3-VL-2B-Instruct_text-W4V8.mxq', 'model.safetensors'):
            (self.model_dir/name).write_bytes(name.encode())
        import protocol as P
        weights = {n: P.sha256(n.encode()) for n in ('Qwen3-VL-2B-Instruct_vision-W4V8.mxq', 'Qwen3-VL-2B-Instruct_text-W4V8.mxq',
                                                      'model.safetensors')}
        (self.work/'contract.json').write_text(json.dumps(dict(P.CONTRACT, inputs=[contract_input()],
                                                               models={'mobilint-qwen3vl-2b-f4be563': dict(weights=weights)})))
        return self

    def __exit__(self, *exc):
        sys.path.remove(str(Path(self.tmp.name)/'site'))
        os.environ.pop('FAKE_MODE', None)
        self.tmp.cleanup()


class BoundedChildTest(unittest.TestCase):
    def test_load_failure_is_reported_with_partial_child_output(self):
        import vlm_stage
        with FakeVendor('load_error') as fake:
            with self.assertRaises(vlm_stage.LoadFailed) as caught:
                vlm_stage.VlmStage('mobilint', fake.work/'models', load_s=30, call_s=5, log_dir=fake.work/'logs')
            record = caught.exception.record
        self.assertEqual(record['state'], 'dead')
        self.assertEqual(record['reason'], 'load_failed')
        self.assertIn('Model_DtypeMismatched', record['error'])
        self.assertIn('fake-load-start', record['output_tail'])
        self.assertIn('fake-load-stderr', record['output_tail'])
        self.assertFalse(record['child_alive_after_kill'])

    def test_load_deadline_kills_the_child_and_keeps_its_output(self):
        import vlm_stage
        with FakeVendor('load_hang') as fake:
            with self.assertRaises(vlm_stage.LoadFailed) as caught:
                vlm_stage.VlmStage('mobilint', fake.work/'models', load_s=3, call_s=5, log_dir=fake.work/'logs')
            record = caught.exception.record
        self.assertEqual(record['reason'], 'load_timeout')
        self.assertIn('fake-load-start', record['output_tail'])
        self.assertFalse(record['child_alive_after_kill'])

    def test_call_deadline_kills_the_child_and_it_is_never_reused(self):
        import vlm_stage
        with FakeVendor('call_hang') as fake:
            stage = vlm_stage.VlmStage('mobilint', fake.work/'models', load_s=30, call_s=2, log_dir=fake.work/'logs')
            with self.assertRaises(TimeoutError):
                stage.run(image_npy())
            with self.assertRaises(RuntimeError) as again:
                stage.run(image_npy())
        self.assertIn('not reused', str(again.exception))
        self.assertEqual(stage.describe()['process']['reason'], 'call_timeout')
        self.assertIn('fake-generate-hang', stage.describe()['process']['output_tail'])
        self.assertIsNone(stage.describe()['device'])

    def test_load_eof_kills_and_records_the_child_output(self):
        import vlm_stage
        with FakeVendor('load_exit') as fake:
            with self.assertRaises(vlm_stage.LoadFailed) as caught:
                vlm_stage.VlmStage('mobilint', fake.work/'models', load_s=30, call_s=5, log_dir=fake.work/'logs')
            record = caught.exception.record
        self.assertEqual(record['reason'], 'load_eof')
        self.assertIn('fake-load-stderr', record['output_tail'])
        self.assertFalse(record['child_alive_after_kill'])

    def test_runtime_exception_kills_the_child_keeps_its_output_and_it_is_never_reused(self):
        import vlm_worker
        with FakeVendor('call_error') as fake:
            worker = vlm_worker.Worker('mobilint', ['vlm'], model_root=str(fake.work/'models'), source_root=None, onnx={}, onnx_sha256={})
            first = worker.run(dict(run_id='gmv-test', request_id='r1', stage='vlm', data=image_npy()))
            second = worker.run(dict(run_id='gmv-test', request_id='r2', stage='vlm', data=image_npy()))
            process = worker.health()['stages']['vlm']['process']
        self.assertEqual(first[0], 500)
        self.assertIn('external runtime inference failed', first[1]['error'])
        self.assertIn('fake-generate-partial', first[1]['error'])
        self.assertEqual((process['state'], process['reason'], process['child_alive_after_kill']), ('dead', 'call_failed', False))
        self.assertIn('fake-generate-partial', process['output_tail'])
        self.assertIn('external runtime inference failed', process['trace'])
        self.assertEqual(second[0], 500)
        self.assertIn('not reused', second[1]['error'])

    def test_child_exit_during_a_call_is_recorded_dead_and_never_reused(self):
        import vlm_stage
        with FakeVendor('call_exit') as fake:
            stage = vlm_stage.VlmStage('mobilint', fake.work/'models', load_s=30, call_s=10, log_dir=fake.work/'logs')
            with self.assertRaises(RuntimeError):
                stage.run(image_npy())
            with self.assertRaises(RuntimeError) as again:
                stage.run(image_npy())
        self.assertIn('not reused', str(again.exception))
        self.assertEqual(stage.describe()['process']['reason'], 'call_eof')
        self.assertIn('fake-generate-exit', stage.describe()['process']['output_tail'])

    def test_successful_call_returns_caption_tokens_and_finite_ordered_timing(self):
        import vlm_stage
        with FakeVendor('ok') as fake:
            stage = vlm_stage.VlmStage('mobilint', fake.work/'models', load_s=30, call_s=10, log_dir=fake.work/'logs')
            output, timing = stage.run(image_npy())
            described = stage.describe()
            stage.kill('test-end')
        result = json.loads(output)
        self.assertEqual(result['text'], 'a fake caption')
        self.assertEqual(result['token_ids'], [11, 12, 151645])
        self.assertEqual(result['prompt_tokens'], 4)
        self.assertEqual(len(timing['token_offsets_ms']), 3)
        self.assertEqual(timing['token_offsets_ms'], sorted(timing['token_offsets_ms']))
        self.assertTrue(all(math.isfinite(timing[k]) and timing[k] >= 0 for k in ('preprocess_ms', 'generate_ms', 'ttft_ms', 'total_ms')))
        self.assertGreaterEqual(timing['total_ms'], timing['generate_ms'])
        npu_timing = timing['device_stats']['npu_timing']
        self.assertIs(npu_timing['available'], False)  # pinned non-batch Qwen3-VL path never fills the vendor counters
        self.assertNotIn('decode_calls', npu_timing)
        self.assertIn('count_npu_time', npu_timing['reason'])
        self.assertEqual(timing['device_stats']['output_tensor_device'], 'cpu')
        self.assertIn('host', timing['device_stats']['output_tensor_note'])
        self.assertNotIn('output_device', timing)  # the Worker's accelerator output-device check is for CUDA tensors only
        self.assertGreater(timing['host']['cpu_s_generate'] + timing['host']['cpu_s_preprocess'], 0)
        self.assertEqual(described['device'], 'npu:aries-rb')
        self.assertEqual(described['evidence']['components']['text']['mxq'], 'Qwen3-VL-2B-Instruct_text-W4V8.mxq')
        self.assertEqual(described['evidence']['components']['vision']['core_mode'], 'global8')
        self.assertEqual(described['evidence']['runtime_version'], 'v1.4.0')  # the real qbruntime 1.4.0 wheel in the test env
        self.assertEqual(sorted(described['evidence']['host_parameters']), ['model.language_model.table.weight', 'model.visual.table.weight'])

    def test_input_outside_the_frozen_contract_is_refused(self):
        import vlm_stage
        with FakeVendor('ok') as fake:
            stage = vlm_stage.VlmStage('mobilint', fake.work/'models', load_s=30, call_s=10, log_dir=fake.work/'logs')
            with self.assertRaises(RuntimeError) as caught:
                stage.run(image_npy()[:-1]+b'\x08')
            stage.kill('test-end')
        self.assertIn('not one of the frozen contract inputs', str(caught.exception))


class WorkerReceiptTest(unittest.TestCase):
    def test_receipt_binds_pod_identity_input_output_and_model(self):
        import protocol as P
        import vlm_worker
        image = image_npy()
        os.environ.update(NODE_NAME='etri-ser0004-cgnms0', POD_UID='uid-123')
        with FakeVendor('ok') as fake:
            worker = vlm_worker.Worker('mobilint', ['vlm'], model_root=str(fake.work/'models'), source_root=None, onnx={}, onnx_sha256={})
            status, body = worker.run(dict(run_id='gmv-test', request_id='r1', stage='vlm', data=image))
            health = worker.health()
            worker.stages['vlm'].kill('test-end')
        self.assertEqual(status, 200, body)
        receipt = body['receipt']
        self.assertEqual((receipt['node'], receipt['pod_uid'], receipt['backend'], receipt['device']),
                         ('etri-ser0004-cgnms0', 'uid-123', 'mobilint', 'npu:aries-rb'))
        self.assertEqual(receipt['input_sha256'], P.sha256(image))
        output = __import__('base64').b64decode(body['data'])
        self.assertEqual(receipt['output_sha256'], P.sha256(output))
        self.assertEqual(json.loads(output)['text'], 'a fake caption')
        self.assertEqual(receipt['model_sha256']['model.safetensors'], P.sha256(b'model.safetensors'))
        self.assertEqual(health['status'], 'ready')

    def test_cuda_backend_without_a_gpu_fails_closed(self):
        import protocol as P
        import vlm_worker
        with FakeVendor('ok') as fake:
            gpu_dir = fake.work/'models/qwen3vl-2b-89644892'
            gpu_dir.mkdir()
            (gpu_dir/'model.safetensors').write_bytes(b'gpu')
            contract = json.loads((fake.work/'contract.json').read_text())
            contract['models']['qwen3vl-2b-89644892'] = dict(weights={'model.safetensors': P.sha256(b'gpu')})
            (fake.work/'contract.json').write_text(json.dumps(contract))
            worker = vlm_worker.Worker('cuda', ['vlm'], model_root=str(fake.work/'models'), source_root=None, onnx={}, onnx_sha256={})
            status, body = worker.run(dict(run_id='gmv-test', request_id='r1', stage='vlm', data=image_npy()))
        self.assertEqual(worker.health()['status'], 'failed')
        self.assertEqual(worker.errors['vlm']['status'], 'load_failed')
        self.assertIn('cuda.is_available() is False', worker.errors['vlm']['error'])
        self.assertEqual(status, 503)

    def test_load_failure_partial_output_is_public_in_health_and_the_503_reply(self):
        import vlm_worker
        with FakeVendor('load_error') as fake:
            worker = vlm_worker.Worker('mobilint', ['vlm'], model_root=str(fake.work/'models'), source_root=None, onnx={}, onnx_sha256={})
            health = worker.health()
            status, body = worker.run(dict(run_id='gmv-test', request_id='r1', stage='vlm', data=image_npy()))
        self.assertEqual(status, 503)
        for public in (health['errors']['vlm'], body['load_error']):
            self.assertEqual(public['record']['reason'], 'load_failed')
            self.assertIn('fake-load-stderr', public['record']['output_tail'])
            self.assertIn('fake-load-start', public['record']['output_tail'])
            self.assertFalse(public['record']['child_alive_after_kill'])
        self.assertLess(len(json.dumps(body)), 20000)


REPO = HERE.parents[1]


class PrepareTest(unittest.TestCase):
    def test_frozen_inputs_are_the_intel_smoke_four_with_their_prompts(self):
        import prepare
        rows = prepare.frozen_inputs(REPO/'artifacts/topology-profile-live/load-cache/corpus')
        self.assertEqual([r['id'] for r in rows], ['smoke-1713820', 'cal-3733440', 'cal-4556100', 'cal-3355890'])
        self.assertEqual(rows[0]['prompt'], 'What is this gentlemen jumping with?\nAnswer the question using a single word or phrase.')
        self.assertEqual(rows[3]['image_sha256'], 'd5fbd6dc6b5aca024643e02c1a001c3e6f125553245cd68a179f52d021182e45')

    def test_corpus_file_with_another_hash_is_refused(self):
        import prepare
        with tempfile.TemporaryDirectory() as tmp:
            for q in (1713820, 3733440, 4556100, 3355890):
                (Path(tmp)/f'{q}-image.npy').write_bytes(b'not the pinned array')
            with self.assertRaises(ValueError):
                prepare.frozen_inputs(Path(tmp))

    def test_schedule_is_smoke_first_then_three_and_seeded(self):
        import prepare
        first, second = prepare.schedule(), prepare.schedule()
        self.assertEqual(first, second)
        self.assertEqual([row['input'] for row in first['probe']], ['smoke-1713820', 'smoke-1713820'])
        self.assertEqual(sorted(row['arm'] for row in first['probe']), ['cuda', 'mobilint'])
        self.assertEqual([row['input'] for row in first['qualification']][::2], ['cal-3733440', 'cal-4556100', 'cal-3355890'])
        for pair in zip(first['qualification'][::2], first['qualification'][1::2]):
            self.assertEqual(sorted(r['arm'] for r in pair), ['cuda', 'mobilint'])

    def test_manifest_verification_detects_a_changed_file(self):
        import prepare
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'a.whl'
            source.write_bytes(b'wheel-bytes')
            manifest = dict(files=[prepare.entry('mobilint-x86_64-cp311', 'wheels/mobilint-x86_64-cp311/a.whl', source)])
            self.assertEqual(prepare.verify_manifest(manifest), [])
            source.write_bytes(b'wheel-bytez')
            self.assertEqual(len(prepare.verify_manifest(manifest)), 1)

    def test_freeze_refuses_an_existing_output_and_mismatched_weights(self):
        import prepare
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'out'
            out.mkdir()
            with self.assertRaises(FileExistsError):
                prepare.freeze(out, Path(tmp), Path(tmp), REPO/'artifacts/topology-profile-live/load-cache/corpus')
            assets = Path(tmp)/'assets'
            for key, model in __import__('protocol').MODELS.items():
                (assets/key).mkdir(parents=True)
                for name in model['weights']:
                    (assets/key/name).write_bytes(b'x')
            with self.assertRaises(ValueError):
                prepare.freeze(Path(tmp)/'new', assets, Path(tmp), REPO/'artifacts/topology-profile-live/load-cache/corpus')
            self.assertFalse((Path(tmp)/'new').exists())


def live_module():
    """Import live.py without leaking its collector sys.path into later tests: the reused collect -> topology chain adds
    experiments/real-topology-profile, whose profile.py shadows the stdlib `profile` (cProfile -> torch._dynamo) in the
    spawned stage children of other tests. Pods ship only /config, so this is a test-process concern."""
    saved = list(sys.path)
    try:
        import live
    finally:
        sys.path[:] = saved
    return live


def node(name, ready='True', taints=None, allocatable=None):
    return dict(metadata=dict(name=name), spec=dict(taints=taints or []),
                status=dict(conditions=[dict(type='Ready', status=ready)], allocatable=dict(dict(cpu='16', memory='32Gi'), **(allocatable or {}))))


def holder(node_name, resource, uid='u1', phase='Running', run='other'):
    return dict(metadata=dict(name='p-'+uid, namespace='x', uid=uid, labels={'run-id': run}), status=dict(phase=phase),
                spec=dict(nodeName=node_name, containers=[dict(resources=dict(requests={resource: '1', 'memory': '1Gi'}, limits={resource: '1'}))]))


class LivePlanTest(unittest.TestCase):
    FOOTPRINT = dict(common=3949, total=9955125218)

    def plan(self):
        live = live_module()
        return live.plan('gmv-20261028t000000', 'http://10.0.0.9:8080', 'a'*64, self.FOOTPRINT)

    def test_workers_are_pinned_and_hold_exactly_their_one_accelerator(self):
        items = {i['metadata']['name']: i for i in self.plan()}
        mob, gpu = items['gmv-20261028t000000-mobilint'], items['gmv-20261028t000000-cuda']
        for pod, node_name, resource in ((mob, 'etri-ser0004-cgnms0', 'mobilint.com/npu'), (gpu, 'etri-dev0009-jetorn', 'nvidia.com/gpu.shared')):
            terms = pod['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']
            self.assertEqual(terms, [dict(matchFields=[dict(key='metadata.name', operator='In', values=[node_name])])])
            res = pod['spec']['containers'][0]['resources']
            accel = {k: v for k, v in res['limits'].items() if '/' in k}
            self.assertEqual(accel, {resource: '1'})
            self.assertEqual({k: v for k, v in res['requests'].items() if '/' in k}, {resource: '1'})
            self.assertEqual(pod['metadata']['labels'], {'experiment': 'gpu-mobilint-vlm', 'run-id': 'gmv-20261028t000000', 'component': pod['metadata']['name'].rsplit('-', 1)[1]})
            env = {e['name']: e.get('value') for e in pod['spec']['containers'][0]['env']}
            self.assertEqual(env['WORKER_MODULE'], 'vlm_worker')
            self.assertEqual(env['STAGES'], 'vlm')
            self.assertEqual(env['ASSET_MANIFEST_SHA256'], 'a'*64)
            work = next(v for v in pod['spec']['volumes'] if v['name'] == 'work')
            self.assertEqual(work['emptyDir']['sizeLimit'], res['limits']['ephemeral-storage'])
        self.assertEqual({e['name']: e.get('value') for e in mob['spec']['containers'][0]['env']}['RECIPE'], 'mobilint-x86_64-cp311')
        self.assertEqual({e['name']: e.get('value') for e in gpu['spec']['containers'][0]['env']}['RECIPE'], 'cu130-aarch64-cp311')
        self.assertEqual(gpu['spec']['containers'][0]['resources']['limits']['memory'], '6656Mi')

    def test_cache_holds_the_whole_bundle_on_the_server(self):
        items = {i['metadata']['name']: i for i in self.plan()}
        cache = items['gmv-20261028t000000-cache']
        size = next(v for v in cache['spec']['volumes'] if v['name'] == 'work')['emptyDir']['sizeLimit']
        self.assertEqual(size, '12Gi')  # 9,955,125,218 B = 9.27 GiB bundle -> ceil + 2 GiB margin
        self.assertNotIn('mobilint.com/npu', json.dumps(cache))
        self.assertIn('etri-ser0004-cgnms0', json.dumps(cache['spec']['affinity']))

    def test_preflight_accepts_free_nodes(self):
        live = live_module()
        nodes = [node('etri-ser0004-cgnms0', allocatable={'mobilint.com/npu': '1'}),
                 node('etri-dev0009-jetorn', allocatable={'nvidia.com/gpu.shared': '2', 'memory': '7562888Ki'})]
        self.assertEqual(live.preflight_problems(nodes, [holder('etri-ser0001-cg0msb', 'nvidia.com/gpu')], 'gmv-x'), [])

    def test_preflight_refuses_foreign_holders_and_unready_nodes(self):
        live = live_module()
        free = [node('etri-ser0004-cgnms0', allocatable={'mobilint.com/npu': '1'}),
                node('etri-dev0009-jetorn', allocatable={'nvidia.com/gpu.shared': '2', 'memory': '7562888Ki'})]
        self.assertEqual(len(live.preflight_problems(free, [holder('etri-ser0004-cgnms0', 'mobilint.com/npu')], 'gmv-x')), 1)
        self.assertEqual(len(live.preflight_problems(free, [holder('etri-dev0009-jetorn', 'nvidia.com/gpu.shared')], 'gmv-x')), 1)
        self.assertEqual(live.preflight_problems(free, [holder('etri-dev0009-jetorn', 'nvidia.com/gpu.shared', phase='Succeeded')], 'gmv-x'), [])
        tainted = [free[0], node('etri-dev0009-jetorn', taints=[dict(key='node.kubernetes.io/unreachable', effect='NoSchedule')],
                                 allocatable={'nvidia.com/gpu.shared': '2', 'memory': '7562888Ki'})]
        self.assertEqual(len(live.preflight_problems(tainted, [], 'gmv-x')), 1)
        self.assertEqual(len(live.preflight_problems([free[0], node('etri-dev0009-jetorn', ready='Unknown')], [], 'gmv-x')), 1)



class ArmSelectionTest(unittest.TestCase):
    """--arm: one explicitly selected arm (arm-only qualification); the default stays the frozen pair."""
    FOOTPRINT = {'common': 3949, 'cu130-aarch64-cp311': 7166259111, 'mobilint-x86_64-cp311': 2788862158, 'total': 9955125218}
    NODES = [node('etri-ser0004-cgnms0', allocatable={'mobilint.com/npu': '1'}),
             node('etri-dev0009-jetorn', allocatable={'nvidia.com/gpu.shared': '2', 'memory': '7562888Ki'})]

    def test_default_is_the_pair_and_unknown_or_empty_selections_are_refused(self):
        live = live_module()
        self.assertEqual(live.selection(None), (('mobilint', 'cuda'), {'common', 'mobilint-x86_64-cp311', 'cu130-aarch64-cp311'}))
        self.assertEqual(live.selection(('cuda',)), (('cuda',), {'common', 'cu130-aarch64-cp311'}))
        pair = live.plan('gmv-x', 'http://c', 'a'*64, self.FOOTPRINT)
        self.assertEqual(pair, live.plan('gmv-x', 'http://c', 'a'*64, self.FOOTPRINT, ('cuda', 'mobilint')))
        for bad in ((), ('intel',), ('mobilint', 'intel')):
            with self.assertRaises(ValueError):
                live.selection(bad)

    def test_mobilint_only_plan_has_one_worker_and_a_cache_sized_for_its_groups(self):
        live = live_module()
        items = live.plan('gmv-x', 'http://c', 'a'*64, self.FOOTPRINT, ('mobilint',))
        self.assertEqual([i['metadata']['name'] for i in items], ['gmv-x-code', 'gmv-x-cache', 'gmv-x-mobilint'])
        size = next(v for v in items[1]['spec']['volumes'] if v['name'] == 'work')['emptyDir']['sizeLimit']
        self.assertEqual(size, '5Gi')  # common + mobilint group 2,788,866,107 B = 2.60 GiB -> ceil + 2 GiB margin
        self.assertEqual(items[2], live.plan('gmv-x', 'http://c', 'a'*64, self.FOOTPRINT)[2])  # the same worker Pod as in the pair

    def test_one_arm_preflight_checks_only_its_node_and_accelerator(self):
        live = live_module()
        gpu_held = [holder('etri-dev0009-jetorn', 'nvidia.com/gpu.shared')]
        self.assertEqual(live.preflight_problems(self.NODES, gpu_held, 'gmv-x', ('mobilint',)), [])
        self.assertEqual(len(live.preflight_problems(self.NODES, gpu_held, 'gmv-x')), 1)
        self.assertEqual(len(live.preflight_problems(self.NODES, [holder('etri-ser0004-cgnms0', 'mobilint.com/npu')], 'gmv-x', ('mobilint',))), 1)
        self.assertEqual(len(live.preflight_problems([self.NODES[0], node('etri-dev0009-jetorn', ready='Unknown')], [], 'gmv-x', ('cuda',))), 1)

    def test_release_must_name_the_selected_arms(self):
        live = live_module()
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('contract.json', 'assets-manifest.json'):
                (Path(tmp)/name).write_text('{}')
            frozen = {n: live.P.file_sha(Path(tmp)/n) for n in ('contract.json', 'assets-manifest.json')}
            pair = dict(frozen=frozen, code={'x': '1'}, approved_by='root', stages=['qualification'])
            self.assertEqual(live.release_problems(pair, tmp, {'x': '1'}), [])
            self.assertEqual(len(live.release_problems(pair, tmp, {'x': '1'}, ('mobilint',))), 1)
            arm = dict(pair, arms=['mobilint'])
            self.assertEqual(live.release_problems(arm, tmp, {'x': '1'}, ('mobilint',)), [])
            self.assertEqual(len(live.release_problems(arm, tmp, {'x': '1'})), 1)


class VlmBootstrapProvenanceTest(unittest.TestCase):
    """vlm_bootstrap.py = the shared quality-network-heterogeneous bootstrap.py plus a provenance header and the bounded
    fetch IO (pacing, segment fsync + fadvise, /health progress); nothing else differs and the shared helper is unchanged."""
    SHARED = HERE.parent/'quality-network-heterogeneous'/'bootstrap.py'
    OWNED = HERE/'vlm_bootstrap.py'
    REMOVED = ['def fetch(url, expected, dest=None, limit=8 * 1024**3, deadline_s=None):', '        started = time.perf_counter()',
               "            fetch(cache+'/'+item['path'], item['sha256'], dest)"]
    ADDED = [
        "FETCH_RATE_BPS = 8 * 1024**2  # vlm_bootstrap: bounded fetch IO (see header)",
        "SYNC_EVERY_BYTES = 16 * 1024**2",
        "FADVISE = hasattr(os, 'posix_fadvise') and hasattr(os, 'POSIX_FADV_DONTNEED')",
        "def sync_segment(out, io):",
        '    """flush + fsync the task-owned file, then drop its newly synced range from the page cache when supported."""',
        "    out.flush()",
        "    os.fsync(out.fileno())",
        "    io['syncs'] += 1",
        "    if FADVISE and io['written'] > io['synced']:",
        "        try:",
        "            os.posix_fadvise(out.fileno(), io['synced'], io['written'] - io['synced'], os.POSIX_FADV_DONTNEED)",
        "            io['fadvise_calls'] += 1",
        "        except OSError as error:",
        "            io['fadvise_error'] = repr(error)[:200]",
        "    io['synced'] = io['written']",
        "",
        "",
        "def fetch(url, expected, dest=None, limit=8 * 1024**3, deadline_s=None, progress=None):",
        "        pace, io = time.monotonic(), dict(written=0, synced=0, syncs=0, fadvise_calls=0)",
        "                if out:",
        "                    io['written'] = size",
        "                    if size - io['synced'] >= SYNC_EVERY_BYTES:",
        "                        sync_segment(out, io)",
        "                step = len(block) / FETCH_RATE_BPS  # credit never exceeds one block: idle time is not saved up",
        "                pace = max(pace, time.monotonic() - step) + step",
        "                time.sleep(max(0.0, min(pace, deadline) - time.monotonic()))",
        "                if time.monotonic() >= deadline:  # a sync/pacing wait spent the budget: start no further read",
        "                    raise TimeoutError(f'{url} exceeded fetch deadline after {size} bytes (pacing/sync included)')",
        "                if progress:",
        "                    progress(dict(io, bytes=size))",
        "            if out:",
        "                if time.monotonic() >= deadline:  # already expired (e.g. the EOF read): no final sync",
        "                    raise TimeoutError(f'{url} exceeded fetch deadline after {size} bytes (pacing/sync included)')",
        "                sync_segment(out, io)",
        "                if progress:",
        "                    progress(dict(io, bytes=size))",
        "            if time.monotonic() > deadline:",
        "                raise TimeoutError(f'{url} exceeded fetch deadline after {size} bytes (pacing/sync included)')",
        "        # keys exist before the HTTP thread reads them; each update replaces the value (no in-place mutation)",
        "        state.update(fetch_io=dict(rate_cap_bps=FETCH_RATE_BPS, sync_every_bytes=SYNC_EVERY_BYTES, fadvise_dontneed=FADVISE), fetch_progress=None)",
        "        started, done = time.perf_counter(), dict(files=0, bytes=0)",
        "",
        "            def progress(io, path=item['path']):",
        "                state['fetch_progress'] = dict(io, path=path, files_done=done['files'], total_bytes=done['bytes']+io['bytes'],",
        "                                               elapsed_s=round(time.perf_counter()-started, 3))",
        "            fetch(cache+'/'+item['path'], item['sha256'], dest, progress=progress)",
        "            done.update(files=done['files']+1, bytes=done['bytes']+dest.stat().st_size)"]

    def test_only_the_header_and_the_bounded_fetch_lines_differ_from_the_unchanged_shared_helper(self):
        import difflib
        import hashlib
        self.assertEqual(hashlib.sha256(self.SHARED.read_bytes()).hexdigest(), '20029fed8e6f2c6c4a83529b476ce99743d258f30571f2fba7035711d3dbee4a')
        shared, owned = self.SHARED.read_text().splitlines(), self.OWNED.read_text().splitlines()
        diff = [d for d in difflib.ndiff(shared, owned) if d[:2] in ('+ ', '- ')]
        removed = [d[2:] for d in diff if d.startswith('- ')]
        added = [d[2:] for d in diff if d.startswith('+ ')]
        self.assertEqual(removed, [shared[0]]+self.REMOVED)  # the first docstring line moves below the header, unchanged
        body = added[added.index(shared[0][3:]):]
        self.assertEqual(body, [shared[0][3:]]+self.ADDED)
        self.assertTrue(added[0].startswith('"""Task-owned copy of experiments/quality-network-heterogeneous/bootstrap.py (sha256 20029fed'))

    def test_only_the_cuda_worker_starts_the_owned_bootstrap_and_both_files_are_shipped(self):
        live = live_module()
        items = {i['metadata']['name']: i for i in live.plan('gmv-x', 'http://c', 'a'*64, dict(common=1, total=2))}
        self.assertEqual(items['gmv-x-cuda']['spec']['containers'][0]['command'], ['python3', '-u', '/config/vlm_bootstrap.py'])
        self.assertEqual(items['gmv-x-mobilint']['spec']['containers'][0]['command'], ['python3', '-u', '/config/bootstrap.py'])
        data = items['gmv-x-code']['data']
        self.assertEqual(data['vlm_bootstrap.py'], self.OWNED.read_text())
        self.assertEqual(data['bootstrap.py'], self.SHARED.read_text())

class BoundedFetchTest(unittest.TestCase):
    """vlm_bootstrap.fetch against a loopback cache: exact bytes + sha256, 8 MiB/s pacing without carried credit,
    flush+fsync+fadvise per <=16 MiB segment, every wait inside the per-file deadline, and progress in /health."""
    MiB = 1 << 20

    @classmethod
    def setUpClass(cls):
        import http.server
        import threading
        sys.path.insert(0, str(HERE.parent/'quality-network-heterogeneous'))
        import vlm_bootstrap
        cls.vb = vlm_bootstrap
        cls.files = {}  # path -> (body, declared Content-Length)

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body, length = cls.files[self.path]
                self.send_response(200)
                self.send_header('Content-Length', str(length))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def put(self, path, body, length=None):
        self.files[path] = (body, len(body) if length is None else length)
        return self.base+path

    def tmp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def test_the_bounds_are_8_mib_per_s_and_16_mib_segments(self):
        self.assertEqual((self.vb.FETCH_RATE_BPS, self.vb.SYNC_EVERY_BYTES), (8 * self.MiB, 16 * self.MiB))
        self.assertEqual(self.vb.FADVISE, hasattr(os, 'posix_fadvise'))  # Linux CPython: available

    def test_exact_bytes_and_each_segment_is_synced_then_dropped_from_the_page_cache(self):
        import hashlib
        from unittest import mock
        payload = os.urandom(5 * self.MiB + 123)
        url, seen = self.put('/seg', payload), []
        dest = self.tmp()/'seg.bin'
        with mock.patch.multiple(self.vb, FETCH_RATE_BPS=64 * self.MiB, SYNC_EVERY_BYTES=2 * self.MiB), \
                mock.patch.object(self.vb.os, 'fsync', wraps=os.fsync) as fsync, \
                mock.patch.object(self.vb.os, 'posix_fadvise', wraps=os.posix_fadvise) as fadvise:
            self.assertIsNone(self.vb.fetch(url, hashlib.sha256(payload).hexdigest(), dest, progress=seen.append))
        self.assertEqual(dest.read_bytes(), payload)
        self.assertEqual(fsync.call_count, 3)
        self.assertEqual([c.args[1:] for c in fadvise.call_args_list],
                         [(0, 2 * self.MiB, os.POSIX_FADV_DONTNEED), (2 * self.MiB, 2 * self.MiB, os.POSIX_FADV_DONTNEED),
                          (4 * self.MiB, self.MiB + 123, os.POSIX_FADV_DONTNEED)])
        self.assertTrue(all(p['written'] - p['synced'] < 2 * self.MiB for p in seen))
        self.assertEqual(seen[-1], dict(written=len(payload), synced=len(payload), syncs=3, fadvise_calls=3, bytes=len(payload)))
        with mock.patch.object(self.vb.os, 'fsync') as fsync:  # in-memory manifest fetch: nothing written, nothing synced
            self.assertEqual(self.vb.fetch(url, hashlib.sha256(payload).hexdigest()), payload)
        fsync.assert_not_called()

    def test_without_or_with_a_failing_fadvise_the_file_is_still_synced_and_exact(self):
        import hashlib
        from unittest import mock
        payload = os.urandom(3 * self.MiB)
        url, sha = self.put('/nofadvise', payload), hashlib.sha256(payload).hexdigest()
        seen = []
        with mock.patch.multiple(self.vb, FETCH_RATE_BPS=64 * self.MiB, SYNC_EVERY_BYTES=2 * self.MiB, FADVISE=False), \
                mock.patch.object(self.vb.os, 'fsync', wraps=os.fsync) as fsync, mock.patch.object(self.vb.os, 'posix_fadvise') as fadvise:
            self.vb.fetch(url, sha, self.tmp()/'a.bin', progress=seen.append)
        fadvise.assert_not_called()
        self.assertEqual((fsync.call_count, seen[-1]['fadvise_calls'], seen[-1]['synced']), (2, 0, len(payload)))
        seen = []
        with mock.patch.multiple(self.vb, FETCH_RATE_BPS=64 * self.MiB, SYNC_EVERY_BYTES=2 * self.MiB), \
                mock.patch.object(self.vb.os, 'posix_fadvise', side_effect=OSError(22, 'Invalid argument')):
            dest = self.tmp()/'b.bin'
            self.vb.fetch(url, sha, dest, progress=seen.append)
        self.assertEqual(dest.read_bytes(), payload)
        self.assertIn('Invalid argument', seen[-1]['fadvise_error'])

    def test_pacing_caps_the_rate_and_idle_time_or_a_new_file_gives_no_burst_credit(self):
        import hashlib
        import time
        payload = os.urandom(3 * self.MiB)
        url, sha = self.put('/paced', payload), hashlib.sha256(payload).hexdigest()
        for name in ('first.bin', 'second.bin'):  # the second fetch follows a 0.5 s idle gap
            seen, start = [], time.monotonic()
            self.vb.fetch(url, sha, self.tmp()/name, progress=lambda p: seen.append((time.monotonic()-start, p['bytes'])))
            elapsed = time.monotonic()-start
            self.assertGreaterEqual(elapsed, (len(payload)-self.MiB) / self.vb.FETCH_RATE_BPS)
            self.assertTrue(all(n <= self.vb.FETCH_RATE_BPS*t + self.MiB for t, n in seen), seen)
            time.sleep(0.5)

    def test_pacing_and_sync_waits_count_against_the_absolute_deadline(self):
        import hashlib
        import time
        from unittest import mock
        payload = os.urandom(3 * self.MiB)  # 0.375 s at 8 MiB/s
        url, sha = self.put('/deadline', payload), hashlib.sha256(payload).hexdigest()
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.vb.fetch(url, sha, self.tmp()/'d.bin', deadline_s=0.2)
        self.assertLess(time.monotonic()-start, 0.2+0.15)  # never slept past the deadline
        small = os.urandom(1000)
        url = self.put('/slowsync', small)
        with mock.patch.object(self.vb.os, 'fsync', side_effect=lambda fd: time.sleep(0.3)), self.assertRaises(TimeoutError):
            self.vb.fetch(url, hashlib.sha256(small).hexdigest(), self.tmp()/'s.bin', deadline_s=0.2)

    def test_no_further_read_or_final_sync_starts_once_a_wait_spends_the_deadline(self):
        import hashlib
        import time
        from unittest import mock
        block, small = os.urandom(self.MiB), os.urandom(1000)

        def run(blocks, deadline_s, fsync=lambda fd: None, **bounds):  # stand-in response: each read pops (delay_s, data)
            reads, opener = [], mock.MagicMock()

            def read(n):
                reads.append(time.monotonic())
                delay, data = blocks.pop(0)
                time.sleep(delay)
                return data
            opener.open.return_value.__enter__.return_value.read.side_effect = read
            with mock.patch.object(self.vb.urllib.request, 'build_opener', return_value=opener), \
                    mock.patch.multiple(self.vb, **bounds), mock.patch.object(self.vb.os, 'fsync', side_effect=fsync) as synced, \
                    self.assertRaises(TimeoutError):
                self.vb.fetch('http://cache/x', hashlib.sha256(block).hexdigest(), self.tmp()/'x.bin', deadline_s=deadline_s)
            return len(reads), synced.call_count
        # pacing: one 1 MiB block = 0.125 s at 8 MiB/s > 0.05 s budget; the second (slow) read must never start
        self.assertEqual(run([(0, block), (0.3, block)], 0.05, FETCH_RATE_BPS=8 * self.MiB), (1, 0))
        # segment sync: a 0.3 s fsync after the first 1 MiB segment spends the 0.2 s budget
        self.assertEqual(run([(0, block), (0, block)], 0.2, lambda fd: time.sleep(0.3), FETCH_RATE_BPS=1024 * self.MiB,
                             SYNC_EVERY_BYTES=self.MiB), (1, 1))
        # the EOF read returns after expiry: no final sync
        self.assertEqual(run([(0, small), (0.3, b'')], 0.2, FETCH_RATE_BPS=1024 * self.MiB), (2, 0))

    def test_hash_mismatch_size_limit_and_a_truncated_body_fail(self):
        import hashlib
        payload = os.urandom(2 * self.MiB)
        url, sha = self.put('/err', payload), hashlib.sha256(payload).hexdigest()
        with self.assertRaisesRegex(ValueError, 'sha256 mismatch'):
            self.vb.fetch(url, '0'*64, self.tmp()/'bad.bin')
        with self.assertRaisesRegex(ValueError, 'exceeds limit'):
            self.vb.fetch(url, sha, self.tmp()/'big.bin', limit=self.MiB)
        url = self.put('/short', payload[:-5], length=len(payload))  # connection closes early: read() returns short, the hash refuses it
        with self.assertRaisesRegex(ValueError, 'sha256 mismatch'):
            self.vb.fetch(url, sha, self.tmp()/'short.bin')

    def test_health_reports_fetch_bounds_and_per_file_progress_while_fetching(self):
        import hashlib
        import socket
        import threading
        import time
        import urllib.request
        from unittest import mock
        big, small = os.urandom(3 * self.MiB), os.urandom(1000)
        manifest = json.dumps(dict(files=[dict(group='common', path='models/a.bin', sha256=hashlib.sha256(big).hexdigest(), bytes=len(big)),
                                          dict(group='other', path='models/skip.bin', sha256='0'*64, bytes=1),
                                          dict(group='common', path='models/b.bin', sha256=hashlib.sha256(small).hexdigest(), bytes=len(small))],
                                   recipes={'loopback': dict(requirements=[], preload=['missing.so'])})).encode()
        self.put('/assets-manifest.json', manifest)
        self.put('/models/a.bin', big)
        self.put('/models/b.bin', small)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        work, observed = self.tmp(), []
        env = dict(RECIPE='loopback', BACKEND='none', STAGES='x', CACHE_URL=self.base, ASSET_MANIFEST_SHA256=hashlib.sha256(manifest).hexdigest(),
                   NODE_NAME='n', POD_UID='u', WORKER_PORT=str(port), TMPDIR=str(work/'tmp'))
        with mock.patch.dict(os.environ, env), mock.patch.multiple(self.vb, WORK=work, CONFIG=str(work), FETCH_RATE_BPS=2 * self.MiB):
            threading.Thread(target=self.vb.main, daemon=True).start()  # ends blocked in finish(); daemon
            deadline = time.monotonic()+90
            while time.monotonic() < deadline:
                try:
                    health = json.loads(urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=5).read())
                except OSError:
                    time.sleep(0.05)
                    continue
                observed.append(health)
                if health['status'] not in ('discovery', 'fetching', 'installing'):
                    break
                time.sleep(0.05)
        during = [h['bootstrap']['fetch_progress'] for h in observed if h['status'] == 'fetching' and h['bootstrap'].get('fetch_progress')]
        self.assertTrue(any(p['path'] == 'models/a.bin' and 0 < p['total_bytes'] < len(big) for p in during), during)
        final = observed[-1]['bootstrap']
        self.assertIn(observed[-1]['status'], ('install_failed', 'preload_failed'))  # fetch finished; the loopback recipe stops here
        self.assertEqual(final['fetch_io'], dict(rate_cap_bps=2 * self.MiB, sync_every_bytes=16 * self.MiB, fadvise_dontneed=self.vb.FADVISE))
        self.assertEqual({k: final['fetch_progress'][k] for k in ('path', 'files_done', 'total_bytes', 'bytes', 'synced')},
                         dict(path='models/b.bin', files_done=1, total_bytes=len(big)+len(small), bytes=len(small), synced=len(small)))
        self.assertGreaterEqual(final['fetch_progress']['elapsed_s'], (len(big)-self.MiB) / (2 * self.MiB))
        self.assertTrue(any(e['kind'] == 'fetched' for e in final['events']))
        self.assertEqual((work/'models'/'a.bin').read_bytes(), big)
        self.assertFalse((work/'models'/'skip.bin').exists())


class Peer:
    """Loopback HTTP peer standing in for a worker Pod: handler(request) -> None after writing its own reply."""

    def __init__(self, handler):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        self.requests = []
        peer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
                peer.requests.append((self.path, self.headers.get('Content-Type'), raw))
                handler(self, raw)

            do_GET = do_POST

            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def reply_json(handler, status, value):
    raw = json.dumps(value).encode()
    handler.send_response(status)
    handler.send_header('Content-Length', str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


class CollectorHttpTest(unittest.TestCase):
    ROW = dict(arm='cuda', input='smoke-1713820')

    def session(self, tmp):
        return live_module().Session(tmp, 'gmv-test')

    def test_a_trickling_worker_reply_is_cut_at_the_absolute_deadline(self):
        import time

        def trickle(handler, raw):
            handler.send_response(200)
            handler.send_header('Content-Length', '40')
            handler.end_headers()
            for _ in range(20):
                handler.wfile.write(b' ')
                handler.wfile.flush()
                time.sleep(0.05)
        live = live_module()
        with tempfile.TemporaryDirectory() as tmp, Peer(trickle) as peer:
            started = time.monotonic()
            reply = live.call_arm(self.session(tmp), 'cuda', peer.url, self.ROW, b'img', None, timeout=0.3)
            elapsed = time.monotonic() - started
        self.assertEqual(reply['status'], 'error')
        self.assertIn('absolute transfer deadline', reply['error'])
        self.assertLess(elapsed, 0.9)

    def test_an_oversized_error_body_is_refused_unread_and_framing_is_binary(self):
        def huge(handler, raw):
            handler.send_response(500)
            handler.send_header('Content-Length', str(64*1024*1024))
            handler.end_headers()
            handler.wfile.write(b'x'*1024)
        live = live_module()
        with tempfile.TemporaryDirectory() as tmp, Peer(huge) as peer:
            reply = live.call_arm(self.session(tmp), 'cuda', peer.url, self.ROW, b'img', None, timeout=5)
            path, content_type, raw = peer.requests[0]
        self.assertEqual(reply['status'], 'error')
        self.assertIn('exceeds', reply['error'])
        self.assertEqual((path, content_type), ('/run', 'application/x-edge-binary'))
        from http_runtime import decode_body
        body = decode_body(raw, content_type)
        self.assertEqual((body['data'], body['request_id'], body['stage']), (b'img', 'smoke-1713820-cuda', 'vlm'))

    def test_a_non_2xx_error_reply_is_kept_bounded(self):
        live = live_module()
        with tempfile.TemporaryDirectory() as tmp, Peer(lambda h, raw: reply_json(h, 503, dict(status='error', error='e'*50000))) as peer:
            reply = live.call_arm(self.session(tmp), 'cuda', peer.url, self.ROW, b'img', None, timeout=5)
        self.assertEqual((reply['status'], reply['http_status']), ('error', 503))
        self.assertLessEqual(len(reply['error']), 20000)



def verified(arm):
    import protocol as P
    return dict(status='ready', pod_uid=f'uid-{arm}', node=P.ARMS[arm]['node'], device={'cuda': 'cuda:0', 'mobilint': 'npu:aries-rb'}[arm],
                contract_sha256='c'*64, model_sha256={'model.safetensors': 'm'*64})


def worker_reply(arm, row, data, run='gmv-test', **receipt_changes):
    """A /run reply as the heterogeneous Worker builds it for the verified arm; receipt_changes/result_device alter it."""
    import base64
    import protocol as P
    want = verified(arm)
    device = receipt_changes.pop('result_device', None) or (
        dict(output_device='cuda:0', max_memory_allocated=1) if arm == 'cuda'
        else dict(output_tensor_device='cpu', output_tensor_note='host tensor expected', npu_timing=dict(available=False)))
    output = json.dumps(dict(input_id=row['input'], text='skis', token_ids=[1], device=device)).encode()
    receipt = dict(stage='vlm', backend=arm, device=want['device'], node=want['node'], pod_uid=want['pod_uid'], run_id=run,
                   request_id=f"{row['input']}-{arm}", input_sha256=P.sha256(data), output_sha256=P.sha256(output),
                   model_sha256=want['model_sha256'], static=dict(contract_sha256=want['contract_sha256']),
                   timing=dict(output_device='cuda:0') if arm == 'cuda' else {})
    receipt.update(receipt_changes)
    return dict(status='ok', data=base64.b64encode(output).decode(), receipt=receipt)


class ReceiptBindingTest(unittest.TestCase):
    ROW = dict(input='smoke-1713820')

    def call(self, arm, reply):
        live = live_module()
        with tempfile.TemporaryDirectory() as tmp, Peer(lambda h, raw: reply_json(h, 200, reply)) as peer:
            return live.call_arm(live.Session(tmp, 'gmv-test'), arm, peer.url, dict(self.ROW, arm=arm), b'img', verified(arm), timeout=5)

    def test_receipts_from_the_verified_arm_are_accepted_with_output_device_evidence(self):
        cuda = self.call('cuda', worker_reply('cuda', self.ROW, b'img'))
        self.assertEqual(cuda['status'], 'ok', cuda.get('error'))
        self.assertEqual(cuda['output_evidence'], dict(output_device='cuda:0'))
        npu = self.call('mobilint', worker_reply('mobilint', self.ROW, b'img'))
        self.assertEqual(npu['status'], 'ok', npu.get('error'))
        self.assertEqual(npu['output_evidence']['output_tensor_device'], 'cpu')
        self.assertIn('host', npu['output_evidence']['output_tensor_note'])

    def test_receipts_not_bound_to_the_verified_arm_are_refused(self):
        wrong = dict(pod_uid='replacement-uid', node='etri-dev0001-jetorn', device='cpu', backend='cpu', model_sha256={'bad': 'b'},
                     static=dict(contract_sha256='d'*64), run_id='gmv-other', request_id='other', stage='other', input_sha256='0'*64)
        for key, value in wrong.items():
            with self.subTest(key=key):
                reply = self.call('cuda', worker_reply('cuda', self.ROW, b'img', **{key: value}))
                self.assertEqual(reply['status'], 'error')
                self.assertIn(key, reply['error'])

    def test_a_cuda_receipt_whose_output_tensor_is_not_on_cuda_is_refused(self):
        reply = self.call('cuda', worker_reply('cuda', self.ROW, b'img', result_device=dict(output_device='cpu')))
        self.assertEqual(reply['status'], 'error')
        self.assertIn('output_device', reply['error'])
        reply = self.call('cuda', worker_reply('cuda', self.ROW, b'img', result_device=dict(max_memory_allocated=1)))
        self.assertEqual(reply['status'], 'error')



class StopOnFailureTest(unittest.TestCase):
    def test_a_failed_call_is_appended_and_the_rest_of_the_schedule_never_runs(self):
        live = live_module()
        rows = [dict(arm='mobilint', input='cal-3733440'), dict(arm='cuda', input='cal-3733440'), dict(arm='mobilint', input='cal-4556100')]

        def failing(handler, raw):
            if handler.path == '/run':
                reply_json(handler, 500, dict(status='error', error='call_failed: external runtime; output tail: driver-partial'))
            else:
                reply_json(handler, 200, dict(status='ready', stages=dict(vlm=dict(process=dict(state='dead', output_tail='driver-partial')))))
        with tempfile.TemporaryDirectory() as tmp, Peer(failing) as npu, Peer(lambda h, raw: reply_json(h, 500, {})) as gpu:
            session = live.Session(tmp, 'gmv-test')
            with self.assertRaises(RuntimeError):
                live.collect_stage(session, 'qualification', rows, dict(mobilint=npu.url, cuda=gpu.url),
                                   {'cal-3733440': b'a', 'cal-4556100': b'b'}, dict(mobilint=verified('mobilint'), cuda=verified('cuda')))
            ledger = [json.loads(line) for line in (session.out/'ledger.jsonl').read_text().splitlines()]
            saved = json.loads((session.out/'ledger-qualification.json').read_text())
            health = json.loads((session.out/'health-after-failure-mobilint.json').read_text())
            run_calls = [r for r in npu.requests if r[0] == '/run']
        self.assertEqual(len(run_calls), 1)
        self.assertEqual(gpu.requests, [])
        self.assertEqual([(r['arm'], r['status'], r['http_status']) for r in ledger], [('mobilint', 'error', 500)])
        self.assertEqual(saved, ledger)
        self.assertIn('driver-partial', ledger[0]['error'])
        self.assertEqual(health['stages']['vlm']['process']['output_tail'], 'driver-partial')



KUBECTL = """#!/usr/bin/env python3
import json, os, sys
args = [a for a in sys.argv[1:] if not a.startswith('--request-timeout')]
with open(os.environ['STUB_TRACE'], 'a') as f:
    f.write(json.dumps(args) + '\\n')
if args[0] == 'get':
    print(os.environ['STUB_POD'])
else:
    print('STREAM-REACHED')
"""


class StreamFreshnessTest(unittest.TestCase):
    """External kubectl stand-in on PATH; Session.call is the collector's public kubectl boundary."""

    RUN = 'gmv-test'

    def pod(self, name='gmv-test-cache', uid='uid-cache', node_name='etri-ser0004-cgnms0', labels=None):
        return dict(metadata=dict(name=name, uid=uid, labels=labels or {'experiment': 'gpu-mobilint-vlm', 'run-id': self.RUN, 'component': name.rsplit('-', 1)[1]}),
                    spec=dict(nodeName=node_name))

    def stream(self, verb_args, pod, owned=('cache', 'uid-cache')):
        live = live_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp/'bin').mkdir()
            (tmp/'bin/kubectl').write_text(KUBECTL)
            (tmp/'bin/kubectl').chmod(0o755)
            saved = dict(os.environ)
            os.environ.update(PATH=f"{tmp/'bin'}:{os.environ['PATH']}", STUB_TRACE=str(tmp/'trace.jsonl'), STUB_POD=json.dumps(pod))
            try:
                session = live.Session(tmp/'runs', self.RUN)
                session.resources.append(dict(kind='Pod', name=f'{self.RUN}-{owned[0]}', uid=owned[1], owned_labels=True))
                try:
                    outcome = session.call('stream', verb_args)
                except RuntimeError as error:
                    outcome = error
                trace = [json.loads(line) for line in (tmp/'trace.jsonl').read_text().splitlines()] if (tmp/'trace.jsonl').exists() else []
                saved_files = sorted(f.name for f in session.out.iterdir())
            finally:
                os.environ.clear()
                os.environ.update(saved)
        return outcome, trace, saved_files

    EXEC = ['exec', '-i', '-n', 'topology-profile-lab', 'gmv-test-cache', '-c', 'worker', '--', 'true']

    def test_exec_into_the_owned_server_cache_follows_a_fresh_get(self):
        outcome, trace, _ = self.stream(self.EXEC, self.pod())
        self.assertIn('STREAM-REACHED', outcome)
        self.assertEqual([t[0] for t in trace], ['get', 'exec'])

    def test_a_same_name_replacement_is_refused_without_streaming(self):
        outcome, trace, saved = self.stream(self.EXEC, self.pod(uid='replacement-uid'))
        self.assertIsInstance(outcome, RuntimeError)
        self.assertEqual([t[0] for t in trace], ['get'])
        self.assertTrue(any(f.endswith('.refused.json') for f in saved))

    def test_the_owned_pod_on_an_edge_node_or_with_foreign_labels_is_refused(self):
        for pod in (self.pod(node_name='etri-dev0009-jetorn'), self.pod(labels={'experiment': 'other', 'run-id': self.RUN, 'component': 'cache'})):
            with self.subTest(pod=pod):
                outcome, trace, _ = self.stream(self.EXEC, pod)
                self.assertIsInstance(outcome, RuntimeError)
                self.assertNotIn('exec', [t[0] for t in trace])

    def test_server_logs_of_a_replacement_are_skipped_and_edge_logs_never_touch_kubectl(self):
        outcome, trace, saved = self.stream(['logs', '-n', 'topology-profile-lab', 'gmv-test-mobilint'], self.pod('gmv-test-mobilint', uid='replacement'),
                                            owned=('mobilint', 'uid-mob'))
        self.assertEqual((outcome, [t[0] for t in trace]), ('', ['get']))
        self.assertTrue(any(f.endswith('.skipped.json') for f in saved))
        outcome, trace, _ = self.stream(['logs', '-n', 'topology-profile-lab', 'gmv-test-cuda'], self.pod('gmv-test-cuda', uid='uid-cuda'), owned=('cuda', 'uid-cuda'))
        self.assertEqual((outcome, trace), ('', []))
        outcome, trace, _ = self.stream(['logs', '-n', 'topology-profile-lab', 'gmv-test-mobilint'], self.pod('gmv-test-mobilint', uid='uid-mob'), owned=('mobilint', 'uid-mob'))
        self.assertEqual([t[0] for t in trace], ['get', 'logs'])


if __name__ == '__main__':
    unittest.main()
