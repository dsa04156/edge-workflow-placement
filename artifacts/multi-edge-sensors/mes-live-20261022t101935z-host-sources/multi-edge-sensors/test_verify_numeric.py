"""Public verify CLI on copies of the frozen offline run: malformed/nonfinite/wrong-shape stage outputs fail closed.

The source run (artifacts/multi-edge-sensors/offline-fake-20261022b) predates the candidate-quality
evidence, so these tests assert on the specific numeric diagnostics, not on overall pass/fail of the copy.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUN = ROOT/'artifacts/multi-edge-sensors/offline-fake-20261022b'
PREPARED = Path('/mnt/data3tb/multi-edge-sensors-20261022/prepared-20261022b')


def verify(run):
    result = subprocess.run([sys.executable, str(HERE/'verify.py'), '--run', run, '--prepared', PREPARED],
                            capture_output=True, text=True, timeout=300)
    return result, json.loads(result.stdout)


@unittest.skipUnless(RUN.is_dir() and PREPARED.is_dir(), 'frozen offline run or prepared bundle not present')
class NumericAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.baseline = cls.copy('baseline', lambda row, outputs: None)
        cls.base_result, cls.base_summary = verify(cls.baseline)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def copy(cls, name, mutate):
        run = Path(cls.tmp.name)/name/RUN.name
        shutil.copytree(RUN, run)
        rows = [json.loads(line) for line in (run/'attempts.jsonl').read_text().splitlines()]
        row = next(r for r in rows if r.get('status') == 'ok' and not r['warmup'] and r['outputs'][r['request_id']+'/V'])
        mutate(row, {t: f"{row['request_id']}/{t}" for t in 'VTF'})
        (run/'attempts.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        return run

    def assert_invalid(self, mutate, fragment):
        result, summary = verify(self.copy(fragment.replace(' ', '-'), mutate))
        self.assertNotEqual(result.returncode, 0)
        hits = [e for e in summary['errors'] if 'invalid' in e and fragment in e]
        self.assertTrue(hits, summary['errors'])
        self.assertFalse([e for e in self.base_summary['errors'] if 'invalid' in e and fragment in e])

    def test_unmutated_copy_has_no_numeric_errors(self):
        self.assertFalse([e for e in self.base_summary['errors'] if 'invalid' in e], self.base_summary['errors'])
        self.assertEqual(self.base_summary['quality']['checked_rows'], 56)

    def test_nan_detection_score_is_rejected(self):
        self.assert_invalid(lambda row, k: row['outputs'][k['V']][0].__setitem__('score', float('nan')), 'score')

    def test_extra_box_coordinate_is_rejected(self):
        self.assert_invalid(lambda row, k: row['outputs'][k['V']][0]['box'].append(1.0), 'box must be 4')

    def test_scaled_embedding_is_rejected(self):
        def scale(row, k):
            out = row['outputs'][k['T']]
            out['embedding'] = [2*v for v in out['embedding']]
        self.assert_invalid(scale, 'L2-normalized')

    def test_truncated_similarities_are_rejected(self):
        self.assert_invalid(lambda row, k: row['outputs'][k['T']].__setitem__('similarities', row['outputs'][k['T']]['similarities'][:79]),
                            'similarities must be 80')

    def test_unsupported_fusion_class_name_is_rejected(self):
        self.assert_invalid(lambda row, k: row['outputs'][k['F']]['top3'][1].__setitem__('name', 'not-a-class'), 'unsupported class names')

    def test_boolean_detection_count_is_rejected(self):
        self.assert_invalid(lambda row, k: row['outputs'][k['F']].__setitem__('detections', True), 'non-bool integer')

    def test_fusion_count_differing_from_v_detections_is_rejected(self):
        self.assert_invalid(lambda row, k: row['outputs'][k['F']].__setitem__('detections', len(row['outputs'][k['V']])+1),
                            'V detections of this row')

    def test_top_class_not_first_ranked_name_is_rejected(self):
        def swap(row, k):
            out = row['outputs'][k['F']]
            out['top_class'] = out['top3'][1]['name']
        self.assert_invalid(swap, 'first ranked top3 name')

    def test_detection_name_not_matching_class_id_is_rejected(self):
        def rename(row, k):
            det = row['outputs'][k['V']][0]
            det['name'] = 'zebra' if det['name'] != 'zebra' else 'person'
        self.assert_invalid(rename, 'is not class')

    def test_infinite_fused_score_is_rejected(self):
        self.assert_invalid(lambda row, k: row['outputs'][k['F']].__setitem__('top_fused', float('inf')), 'top_fused')


if __name__ == '__main__':
    unittest.main()
