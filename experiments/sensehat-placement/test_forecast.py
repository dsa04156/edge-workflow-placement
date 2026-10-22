"""Slice A: source validation, chronological split, learned line, held-out metrics."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import forecast


def make_rows(values, channel='humidity', unit='%RH', start_seq=100, uid='uid-1', session='s1'):
    base = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
    rows = []
    for i, value in enumerate(values):
        observed = base + timedelta(seconds=i)
        received = observed + timedelta(milliseconds=11)
        seq = start_seq + i
        observed_ms = int(observed.timestamp() * 1000)
        raw = {'apiVersion': 'edgeai.observation/v1', 'configurationSha256': 'sha256:cfg', 'deviceName': 'dev-' + channel,
               'namespace': 'edgeai-devices', 'observedAt': observed_ms, 'property': channel,
               'receivedAt': observed_ms + 11, 'sequence': seq, 'sessionId': session, 'sourceGeneration': 'gen-1',
               'timeBasis': 'SOURCE', 'type': 'float64', 'unit': unit, 'value': value}
        rows.append({'configurationSha256': 'sha256:cfg', 'deviceId': 'dev-' + channel, 'deviceUid': uid,
                     'namespace': 'edgeai-devices', 'observedAt': observed.strftime('%Y-%m-%dT%H:%M:%S.') + f'{observed.microsecond // 1000:03d}Z',
                     'property': channel, 'quality': 'QUALIFIED',
                     'rawPayload': json.dumps(raw, separators=(',', ':')),
                     'receivedAt': received.strftime('%Y-%m-%dT%H:%M:%S.') + f'{received.microsecond // 1000:03d}Z',
                     'sequence': seq, 'sessionId': session, 'sourceGeneration': 'gen-1', 'timeBasis': 'SOURCE',
                     'type': 'float64', 'unit': unit, 'value': value})
    return rows


def write_root(root, series):
    """series: {channel: rows}; writes discovery-style files + manifest with hashes."""
    root.mkdir(parents=True, exist_ok=True)
    files = {}
    for channel, rows in series.items():
        doc = {'deviceUid': rows[0]['deviceUid'], 'property': channel, 'items': rows, 'truncated': False}
        raw = json.dumps(doc, indent=1).encode()
        (root / (channel + '.json')).write_bytes(raw)
        files['history/' + channel + '.json'] = {'deviceUid': rows[0]['deviceUid'], 'property': channel,
                                                 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                                                 'items': len(rows), 'truncated': False}
    (root / 'manifest.json').write_text(json.dumps({'files': files}))


def ramp(n, slope=2.0, intercept=1.0):
    return [slope * i + intercept for i in range(n)]


class SourceValidation(unittest.TestCase):
    def test_hash_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_root(root, {'humidity': make_rows(ramp(100))})
            (root / 'humidity.json').write_bytes((root / 'humidity.json').read_bytes() + b' ')
            with self.assertRaisesRegex(forecast.SourceError, 'sha256 mismatch'):
                forecast.load_channel(root, 'humidity')

    def test_sequence_gap_is_rejected_and_reported(self):
        rows = make_rows(ramp(100))
        del rows[50]
        with tempfile.TemporaryDirectory() as tmp:
            write_root(Path(tmp), {'humidity': rows})
            with self.assertRaisesRegex(forecast.SourceError, r'sequence gap.*149.*151'):
                forecast.load_channel(Path(tmp), 'humidity')

    def test_metadata_change_is_rejected(self):
        rows = make_rows(ramp(60)) + make_rows(ramp(40), start_seq=160, session='s2')
        with tempfile.TemporaryDirectory() as tmp:
            write_root(Path(tmp), {'humidity': rows})
            with self.assertRaisesRegex(forecast.SourceError, 'sessionId'):
                forecast.load_channel(Path(tmp), 'humidity')

    def test_non_increasing_time_is_rejected(self):
        rows = make_rows(ramp(100))
        rows[30]['observedAt'] = rows[29]['observedAt']
        with tempfile.TemporaryDirectory() as tmp:
            write_root(Path(tmp), {'humidity': rows})
            with self.assertRaisesRegex(forecast.SourceError, 'observedAt'):
                forecast.load_channel(Path(tmp), 'humidity')

    def test_too_short_series_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_root(Path(tmp), {'humidity': make_rows(ramp(60))})
            with self.assertRaisesRegex(forecast.SourceError, 'insufficient'):
                forecast.build_channel(forecast.load_channel(Path(tmp), 'humidity'))


class SplitAndModel(unittest.TestCase):
    def test_split_is_floor_60_20_20_and_windows_stay_inside(self):
        rows = make_rows(ramp(101))
        split = forecast.split_rows(rows)
        self.assertEqual([len(split[k]) for k in ('training', 'calibration', 'evaluation')], [60, 20, 21])
        pairs = forecast.window_pairs(split['evaluation'])
        self.assertEqual(len(pairs), 21 - 8)
        for window, target in pairs:
            self.assertEqual(len(window), 8)
            self.assertEqual(target['sequence'], window[-1]['sequence'] + 1)
            self.assertIn(target, split['evaluation'])

    def test_linear_ramp_is_predicted_exactly(self):
        # value = 2i+1, mean(window i..i+7) = 2i+8, target = 2i+17 -> y = 1*x + 9.
        channel = forecast.build_channel(rows_doc(make_rows(ramp(200))))
        self.assertAlmostEqual(channel['model']['a'], 1.0, places=12)
        self.assertAlmostEqual(channel['model']['b'], 9.0, places=9)
        self.assertFalse(channel['model']['zero_variance_x'])
        for row in channel['evaluation']['rows']:
            self.assertAlmostEqual(row['forecast'], row['truth'], places=9)
            self.assertEqual(row['persistence'], row['truth'] - 2.0)
        self.assertLess(channel['evaluation']['model']['mae'], 1e-9)
        self.assertAlmostEqual(channel['evaluation']['persistence']['mae'], 2.0)

    def test_zero_variance_training_uses_recorded_target_mean(self):
        values = [5.0] * 120 + ramp(80)
        channel = forecast.build_channel(rows_doc(make_rows(values)))
        self.assertTrue(channel['model']['zero_variance_x'])
        self.assertEqual(channel['model']['a'], 0.0)
        self.assertEqual(channel['model']['b'], 5.0)

    def test_held_out_values_cannot_change_model_or_frozen_inputs(self):
        rows = make_rows(ramp(200))
        base = forecast.build_channel(rows_doc(rows))
        changed = copy.deepcopy(rows)
        last = changed[-1]
        last['value'] = 1e6
        raw = json.loads(last['rawPayload'])
        raw['value'] = 1e6
        last['rawPayload'] = json.dumps(raw, separators=(',', ':'))
        other = forecast.build_channel(rows_doc(changed))
        self.assertEqual(base['model'], other['model'])
        base_sel, other_sel = forecast.select_requests(base), forecast.select_requests(other)
        # Last eval target changed; the window payload and its forecast must not.
        self.assertEqual([r['rows'] for r in base_sel], [r['rows'] for r in other_sel])
        self.assertNotEqual(base_sel[-1]['target']['value'], other_sel[-1]['target']['value'])
        for request in other_sel:
            self.assertNotIn(request['target']['sequence'], [r['sequence'] for r in request['rows']])

    def test_selected_pairs_are_disjoint_first_middle_last_plus_warmup(self):
        channel = forecast.build_channel(rows_doc(make_rows(ramp(200))))
        selected = forecast.select_requests(channel)
        kinds = [r['kind'] for r in selected]
        self.assertEqual(kinds, ['warmup', 'evaluation', 'evaluation', 'evaluation'])
        evaluation = channel['split_sequences']['evaluation']
        eval_reqs = selected[1:]
        self.assertEqual(eval_reqs[0]['rows'][0]['sequence'], evaluation[0])
        self.assertEqual(eval_reqs[-1]['target']['sequence'], evaluation[1])
        spans = [set(range(r['rows'][0]['sequence'], r['target']['sequence'] + 1)) for r in eval_reqs]
        self.assertFalse(spans[0] & spans[1] or spans[1] & spans[2] or spans[0] & spans[2])
        calibration = channel['split_sequences']['calibration']
        self.assertEqual(selected[0]['rows'][0]['sequence'], calibration[0])


def rows_doc(rows):
    return {'channel': rows[0]['property'], 'rows': rows, 'source': {'file': 'x', 'sha256': 'x'}}


if __name__ == '__main__':
    unittest.main()
