"""Preprocessing split on REAL models (CPU host, torch CPU): image_preprocess -> vision(tensor) and audio_preprocess ->
speech(tensor) must equal the in-branch qualification path bitwise (feature bytes / transcript), Wav2Vec2 normalization
is applied exactly once, preprocessing never runs on an accelerator backend, and the Worker's tensor input path
records the queue fields. Accelerator execution is NOT exercised (no local GPU/NPU may be used).
Run with the frozen reference env: .scratch/quality-network-heterogeneous/venv-ref/bin/python -m unittest test_stages_dag
"""
import base64
import io
import json
import os
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
QNH = ROOT/'experiments/quality-network-heterogeneous'
sys.path.insert(0, str(QNH))
sys.path.insert(1, str(ROOT/'experiments/real-topology-live'))
import stages  # noqa: E402

LIVE = ROOT/'artifacts/topology-profile-live'
os.environ.setdefault('MODEL_ROOT', str(LIVE/'model-assets'))
os.environ.setdefault('JELLYBEAN_SOURCE', str(ROOT/'.scratch/quality-network-heterogeneous/work/jellybean'))
CORPUS = LIVE/'load-cache/corpus'
PAIRS = json.loads((LIVE/'load-pairs.json').read_text())['pairs']
REFERENCE = LIVE/'reference-v1'
try:
    import torch  # noqa: F401
    HAVE = Path(os.environ['MODEL_ROOT'], 'resnet18-f37072fd.pth').exists()
except ImportError:
    HAVE = False


def raw(pair, kind):
    return (CORPUS/Path(pair['raw_'+kind+'_path']).name).read_bytes()


@unittest.skipUnless(HAVE, 'needs torch + frozen model assets (venv-ref)')
class Split(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vision = stages.load('vision', 'cpu-torch')
        cls.speech = stages.load('speech', 'cpu-torch')
        cls.img = stages.load('image_preprocess', 'cpu-torch')
        cls.aud = stages.load('audio_preprocess', 'cpu-torch')
        cls.pairs = PAIRS[:3]

    def test_vision_split_is_bitwise_equal_to_the_in_branch_path(self):
        for pair in self.pairs:
            joined, _ = self.vision.run(raw(pair, 'image'))
            tensor, t1 = self.img.run(raw(pair, 'image'))
            split, t2 = self.vision.run_tensor(tensor)
            self.assertEqual(split, joined, pair['question_id'])
            self.assertEqual(t1['output_shape'], [1, 3, 448, 448])
            self.assertEqual(t2['input_kind'], 'tensor')

    def test_speech_split_normalizes_once_and_equals_the_in_branch_transcript(self):
        import numpy as np
        for pair in self.pairs:
            joined, _ = self.speech.run(raw(pair, 'audio'))
            values, _ = self.aud.run(raw(pair, 'audio'))
            split, _ = self.speech.run_tensor(values)
            self.assertEqual(split, joined, pair['question_id'])
            x = np.load(io.BytesIO(values))
            self.assertLess(abs(float(x.mean())), 1e-3)  # zero-mean/unit-var applied once by audio_preprocess
            twice = self.aud.processor(x, sampling_rate=16000, return_tensors='np', padding='longest').input_values[0]
            self.assertFalse(np.array_equal(twice.astype(np.float32), x), 'a second normalization must change the input (it is not applied)')

    def test_reference_feature_matches_split_path_on_a_reference_pair(self):
        import numpy as np
        pair = self.pairs[0]
        tensor, _ = self.img.run(raw(pair, 'image'))
        feature = np.load(io.BytesIO(self.vision.run_tensor(tensor)[0]))
        reference = np.load(REFERENCE/f"{pair['question_id']}.npy")
        self.assertTrue(np.allclose(feature, reference, rtol=1e-4, atol=1e-5))

    def test_preprocessing_is_cpu_only_and_tensor_inputs_are_validated(self):
        for backend in ('cuda-torch', 'openvino-npu'):
            with self.assertRaises(stages.BackendUnavailable):
                stages.load('image_preprocess', backend)
        with self.assertRaises(ValueError):
            self.vision.run_tensor(stages.npy_bytes(__import__('numpy').zeros((1, 3, 224, 224), dtype='float32')))
        with self.assertRaises(ValueError):
            self.speech.run_tensor(stages.npy_bytes(__import__('numpy').zeros((2, 10), dtype='float32')))
        self.assertFalse(hasattr(self.img, 'run_tensor'))

    def test_worker_tensor_path_receipt_has_queue_fields(self):
        import worker
        w = worker.Worker('cpu-torch', ['image_preprocess', 'vision'],
                          loader=lambda name, backend, **kw: {'image_preprocess': self.img, 'vision': self.vision}[name])
        status, reply = w.run(dict(run_id='r', request_id='q', stage='image_preprocess', data=raw(self.pairs[0], 'image')))
        self.assertEqual(status, 200)
        status, reply = w.run(dict(run_id='r', request_id='q', stage='vision', input='tensor', data=base64.b64decode(reply['data'])))
        self.assertEqual(status, 200)
        receipt = reply['receipt']
        self.assertEqual(receipt['input_kind'], 'tensor')
        self.assertEqual(receipt['queue_at_arrival'], dict(waiting=0, active=0))
        self.assertGreaterEqual(receipt['queue_wait_ms'], 0)
        self.assertEqual(w.health()['queue'], dict(waiting=0, active=0, completed=2))
        status, reply = w.run(dict(run_id='r', request_id='q', stage='image_preprocess', input='tensor', data=b'x'))
        self.assertEqual(status, 400)


if __name__ == '__main__':
    unittest.main()
