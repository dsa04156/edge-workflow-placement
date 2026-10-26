"""Stage backend contracts on real models (CPU host): extraction == frozen legacy adapter, no device fallback,
torchvision-free preprocessing == torchvision, device mismatch is an error. Accelerator execution itself is NOT
exercised here (no local accelerator may be used); see qualification receipts for that.

Environment: MODEL_ROOT (frozen model-assets) and JELLYBEAN_SOURCE (frozen fusion.tar jellybean/) or the defaults.
"""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import stages  # noqa: E402

LIVE = ROOT/'artifacts/topology-profile-live'
os.environ.setdefault('MODEL_ROOT', str(LIVE/'model-assets'))
os.environ.setdefault('JELLYBEAN_SOURCE', str(ROOT/'.scratch/quality-network-heterogeneous/work/jellybean'))
CORPUS = LIVE/'load-cache/corpus'
HAVE = Path(os.environ['MODEL_ROOT'], 'resnet18-f37072fd.pth').exists() and Path(os.environ['JELLYBEAN_SOURCE'], 'vqa').exists()


def pairs():
    return json.loads((LIVE/'load-pairs.json').read_text())['pairs']


def raw(pair, kind):
    return (CORPUS/Path(pair['raw_'+kind+'_path']).name).read_bytes()


def legacy(task):
    spec = importlib.util.spec_from_file_location('legacy_model_adapter', stages.LEGACY_ADAPTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load(task)[0]


class Provenance(unittest.TestCase):
    def test_legacy_adapter_is_the_frozen_file(self):
        check = stages.legacy_check()
        self.assertEqual(check['legacy_adapter_present_sha256'], stages.LEGACY_ADAPTER_SHA256)


class NoFallback(unittest.TestCase):
    def test_cuda_backend_without_a_cuda_build_raises_instead_of_using_cpu(self):
        import torch
        if torch.cuda.is_available():
            self.skipTest('host exposes CUDA; this contract is checked on a CPU-only torch build')
        with self.assertRaises(stages.BackendUnavailable):
            stages.torch_device('cuda-torch')

    def test_openvino_refuses_auto_hetero_multi_and_requires_a_real_npu(self):
        for device in ('AUTO', 'HETERO:NPU,CPU', 'MULTI:NPU,CPU', 'CPU', 'AUTO:NPU'):
            with self.assertRaises(ValueError):
                stages.OpenVinoVision('unused.onnx', device=device)
        import openvino as ov
        if 'NPU' in ov.Core().available_devices:
            self.skipTest('host has an NPU')
        onnx = next(iter(sorted((ROOT/'artifacts/quality-network-heterogeneous').glob('export-*/vision-static-448.onnx'))), None)
        if onnx is None:
            self.skipTest('vision export not produced yet')
        with self.assertRaises(stages.BackendUnavailable):
            stages.OpenVinoVision(onnx)

    def test_npu_stage_other_than_vision_is_not_served(self):
        with self.assertRaises(stages.BackendUnavailable):
            stages.load('speech', 'openvino-npu')

    def test_device_mismatch_is_an_error(self):
        import torch
        with self.assertRaises(stages.DeviceMismatch):
            stages.assert_on(torch.device('cuda', 0), [torch.zeros(1)], 'probe')


@unittest.skipUnless(HAVE, 'frozen model assets / jellybean source not present')
class ExtractionEqualsLegacy(unittest.TestCase):
    """Two calibration pairs per stage; bitwise equality of the serialized outputs on the same CPU process."""

    @classmethod
    def setUpClass(cls):
        cls.pairs = [p for p in pairs() if p['split'] == 'calibration'][:2]

    def test_vision(self):
        new, old = stages.load('vision', 'cpu-torch'), legacy('vision')
        for pair in self.pairs:
            out, timing = new.run(raw(pair, 'image'))
            self.assertEqual(out, old(raw(pair, 'image')))
            self.assertEqual(timing['output_device'], 'cpu')

    def test_speech(self):
        new, old = stages.load('speech', 'cpu-torch'), legacy('speech')
        for pair in self.pairs:
            self.assertEqual(new.run(raw(pair, 'audio'))[0], old(raw(pair, 'audio')))

    def test_fusion(self):
        new, old = stages.load('fusion', 'cpu-torch'), legacy('fusion')
        refs = {json.loads(l)['question_id']: json.loads(l) for l in (LIVE/'reference-v1/results.jsonl').read_text().splitlines()}
        for pair in self.pairs:
            q = pair['question_id']
            inputs = dict(vision=(LIVE/f'reference-v1/{q}.npy').read_bytes(), speech=refs[q]['transcript'].encode())
            self.assertEqual(new.run(inputs)[0], old(inputs))
        self.assertEqual(new.describe()['strict_state_keys'], 56)


@unittest.skipUnless(HAVE, 'frozen corpus not present')
class TorchvisionFreePreprocessing(unittest.TestCase):
    def test_numpy_preprocessing_equals_torchvision_on_all_60_images(self):
        worst = 0.0
        for pair in pairs():
            image = stages.decode_image(raw(pair, 'image'))
            a = stages.vision_tensor_torchvision(image).numpy()
            b = stages.vision_array_numpy(image)
            self.assertEqual(a.shape, b.shape)
            worst = max(worst, float(abs(a - b).max()))
        self.assertEqual(worst, 0.0)


if __name__ == '__main__':
    unittest.main()
