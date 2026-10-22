import unittest

import numpy as np

import infer
import neural


class SplitIsolation(unittest.TestCase):
    def setUp(self):
        self.rows = [{'sequence': i, 'value': float(i)} for i in range(900)]

    def test_chronological_counts(self):
        split = neural.split_rows(self.rows)
        self.assertEqual([len(split[k]) for k in ('training', 'validation', 'test')], [540, 180, 180])
        self.assertEqual(split['validation'][0]['sequence'], 540)
        self.assertEqual(split['test'][0]['sequence'], 720)

    def test_windows_never_cross_segment(self):
        split = neural.split_rows(self.rows)
        for name, seg in split.items():
            values = [r['value'] for r in seg]
            pairs = neural.windows(values)
            self.assertEqual(len(pairs), 180 - 80 + 1 if name != 'training' else 540 - 80 + 1)
            lo, hi = values[0], values[-1]
            for x, y in pairs:
                self.assertEqual((len(x), len(y)), (64, 16))
                self.assertTrue(lo <= min(x) and max(y) <= hi)

    def test_wrong_row_count_rejected(self):
        with self.assertRaises(ValueError):
            neural.split_rows(self.rows[:-1])

    def test_training_independent_of_heldout_values(self):
        # Changing validation/test values must not change normalization inputs or the trained model.
        base = {k: [float(np.sin(i / 7.0)) for i in range(n)]
                for k, n in (('training', 540), ('validation', 180), ('test', 180))}
        changed = dict(base, validation=[100.0] * 180, test=[-100.0] * 180)
        a, b = neural.fit(base, steps=3), neural.fit(changed, steps=3)
        self.assertEqual(a[:2], b[:2])
        self.assertTrue(all(np.array_equal(p, q) for p, q in zip(a[2], b[2])))


class ModelParity(unittest.TestCase):
    def test_pack_roundtrip_same_bytes_and_outputs(self):
        params = neural.init_params(np.random.default_rng(1))
        raw = infer.pack(55.0, 2.0, params)
        mean, std, back = infer.unpack(raw)
        self.assertEqual(infer.pack(mean, std, back), raw)
        window = list(np.linspace(50, 60, 64))
        self.assertTrue(np.array_equal(infer.predict(55.0, 2.0, params, window), infer.predict(mean, std, back, window)))

    def test_unpack_rejects_wrong_size(self):
        with self.assertRaises(ValueError):
            infer.unpack(b'\0' * 8)

    def test_parity_tolerance(self):
        ref = {'w': [1.0, 2.0]}
        self.assertTrue(neural.parity(ref, {'w': [1.0005, 2.0]})['ok'])
        self.assertFalse(neural.parity(ref, {'w': [1.01, 2.0]})['ok'])
        self.assertFalse(neural.parity(ref, {'x': [1.0, 2.0]})['ok'])


if __name__ == '__main__':
    unittest.main()
