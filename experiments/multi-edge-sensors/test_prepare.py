"""Public prepare CLI: hash-pinned sources -> new prepared directory; never overwrites."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SOURCES = HERE/'sources-20261022.json'
CACHE = Path(json.loads(SOURCES.read_text())['cache_hint'])


def prepare(*args):
    return subprocess.run([sys.executable, str(HERE/'prepare.py'), *map(str, args)], capture_output=True, text=True, timeout=600)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class PrepareRefusals(unittest.TestCase):
    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'prepared'
            out.mkdir()
            (out/'keep.txt').write_text('old')
            result = prepare('--sources', SOURCES, '--cache', CACHE, '--output', out)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((out/'keep.txt').read_text(), 'old')
            self.assertEqual(sorted(p.name for p in out.iterdir()), ['keep.txt'])

    def test_source_hash_mismatch_is_rejected_before_any_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp/'cache/downloads/images').mkdir(parents=True)
            (tmp/'cache/downloads/images/x.jpg').write_bytes(b'not the pinned bytes')
            manifest = json.loads(SOURCES.read_text())
            image = next(s for s in manifest['sources'] if s['kind'] == 'image')
            manifest['sources'] = [dict(image, path='downloads/images/x.jpg')]
            (tmp/'sources.json').write_text(json.dumps(manifest))
            result = prepare('--sources', tmp/'sources.json', '--cache', tmp/'cache', '--output', tmp/'prepared')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(image['id'], result.stderr)
            self.assertFalse((tmp/'prepared').exists())


@unittest.skipUnless((CACHE/'export-20261022c/yolo11n.onnx').is_file(), 'dedicated model/wheel cache not present')
class PrepareBundles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.outs = [Path(cls.tmp.name)/name for name in ('a', 'b')]
        cls.results = [prepare('--sources', SOURCES, '--cache', CACHE, '--output', out) for out in cls.outs]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_bundles_are_recorded_with_their_real_hashes(self):
        self.assertEqual(self.results[0].returncode, 0, self.results[0].stderr)
        prepared = json.loads((self.outs[0]/'prepared.json').read_text())
        self.assertEqual(set(prepared['bundles']), {'wheels-x86_64.tar', 'wheels-aarch64.tar', 'mediamtx-x86_64.tar',
                                                    'mediamtx-aarch64.tar', 'models.tar', 'fixtures.tar'})
        for name, entry in prepared['bundles'].items():
            self.assertEqual(sha(self.outs[0]/'bundles'/name), entry['sha256'], name)

    def test_shipped_model_is_the_pinned_export_linked_to_original_weights(self):
        prepared = json.loads((self.outs[0]/'prepared.json').read_text())
        with tarfile.open(self.outs[0]/'bundles/models.tar') as archive:
            onnx = archive.extractfile('models/yolo11n.onnx').read()
            names = json.loads(archive.extractfile('models/class-names.json').read())
        self.assertEqual(hashlib.sha256(onnx).hexdigest(), '8ba5e06557b835909f8dca86d28c95ad74c160a44951ff3a63f1ec9077d9dca2')
        self.assertEqual(len(names), 80)
        self.assertEqual(names[0], 'person')
        model = prepared['models']['V']
        self.assertEqual(model['derived_from']['sha256'], '0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1')

    def test_wheel_requirements_pin_every_wheel_by_hash(self):
        with tarfile.open(self.outs[0]/'bundles/wheels-aarch64.tar') as archive:
            requirements = archive.extractfile('requirements.txt').read().decode()
            wheels = [m.name for m in archive.getmembers() if m.name.endswith('.whl')]
        self.assertEqual(len(wheels), 5)
        for name in ('numpy==2.2.6', 'onnxruntime==1.23.2', 'pillow==11.3.0', 'tokenizers==0.23.3', 'av==18.1.0'):
            self.assertIn(name+' --hash=sha256:', requirements)

    def test_inputs_cover_every_source_and_split(self):
        prepared = json.loads((self.outs[0]/'prepared.json').read_text())
        self.assertEqual(sorted(prepared['inputs']['video']), ['cam-e0', 'cam-e6', 'cam-e8'])
        self.assertEqual(sorted(prepared['inputs']['text']), ['txt-e1', 'txt-e9'])
        self.assertEqual(sorted(prepared['inputs']['video']['cam-e6']), ['0', '1', '2', '3', '4', '5'])
        self.assertTrue(prepared['inputs']['text']['txt-e9']['3']['text'].startswith(('Event', 'Shift', 'Alert')))

    def test_rebuild_into_a_new_directory_is_byte_identical(self):
        self.assertEqual(self.results[1].returncode, 0, self.results[1].stderr)
        first, second = (json.loads((out/'prepared.json').read_text())['bundles'] for out in self.outs)
        self.assertEqual({k: v['sha256'] for k, v in first.items()}, {k: v['sha256'] for k, v in second.items()})


if __name__ == '__main__':
    unittest.main()
