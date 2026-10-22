"""Frozen Sense HAT humidity -> NumPy MLP (64 -> 512 -> 512 -> 16) preparation.

Source: frozen 2026-10-21 history replay, validated by sensehat-placement forecast.load_channel
(manifest sha256/bytes/rows + gap-free single-metadata series). Chronological 540/180/180 split;
every (64 input, next 16 target) window lies inside one segment; normalization uses training rows only.
Training: fixed seed, exactly 50 Adam updates of batch 32, no hyperparameter search.
"""
import hashlib
import json
import math
from pathlib import Path
import sys
import time

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'sensehat-placement'))
import forecast  # noqa: E402  (validation reused, not modified)

import infer  # noqa: E402

CHANNEL = 'humidity'
SPLIT = (540, 180, 180)
INPUT, HORIZON = infer.LAYERS[0], infer.LAYERS[-1]
SEED = 20261022
STEPS, BATCH, LR = 50, 32, 1e-3
# Same float32 model and inputs; different BLAS kernels (aarch64/x86_64) only reorder sums.
PARITY_ATOL = 1e-3  # %RH, on denormalized predictions


def split_rows(rows):
    if len(rows) != sum(SPLIT):
        raise ValueError(f'expected {sum(SPLIT)} rows, got {len(rows)}')
    a, b = SPLIT[0], SPLIT[0] + SPLIT[1]
    return {'training': rows[:a], 'validation': rows[a:b], 'test': rows[b:]}


def windows(values):
    """All (64 inputs, next 16 targets) inside one segment; never crosses its boundary."""
    span = INPUT + HORIZON
    return [(values[i:i + INPUT], values[i + INPUT:i + span]) for i in range(len(values) - span + 1)]


def arrays(pairs, mean, std):
    x = (np.array([p[0] for p in pairs], dtype=np.float32) - np.float32(mean)) / np.float32(std)
    y = (np.array([p[1] for p in pairs], dtype=np.float32) - np.float32(mean)) / np.float32(std)
    return x, y


def init_params(rng):
    params = []
    for fan_in, fan_out in zip(infer.LAYERS, infer.LAYERS[1:]):
        params.append((rng.standard_normal((fan_in, fan_out)) * math.sqrt(2.0 / fan_in)).astype(np.float32))
        params.append(np.zeros(fan_out, dtype=np.float32))
    return params


def train(x, y, seed=SEED, steps=STEPS, batch=BATCH, lr=LR):
    """MSE on normalized targets, manual backprop, Adam(beta 0.9/0.999, eps 1e-8)."""
    rng = np.random.default_rng(seed)
    params = init_params(rng)
    m = [np.zeros_like(p) for p in params]
    v = [np.zeros_like(p) for p in params]
    losses = []
    for step in range(1, steps + 1):
        idx = rng.choice(len(x), size=batch, replace=False)
        xb, yb = x[idx], y[idx]
        acts = [xb]
        for i in range(0, len(params) - 2, 2):
            acts.append(np.maximum(acts[-1] @ params[i] + params[i + 1], np.float32(0)))
        out = acts[-1] @ params[-2] + params[-1]
        diff = out - yb
        losses.append(float(np.mean(diff * diff)))
        grad = (np.float32(2.0 / diff.size) * diff).astype(np.float32)
        grads = [None] * len(params)
        for layer in range(len(params) // 2 - 1, -1, -1):
            grads[2 * layer] = acts[layer].T @ grad
            grads[2 * layer + 1] = grad.sum(axis=0)
            if layer:
                grad = (grad @ params[2 * layer].T) * (acts[layer] > 0)
        for i, g in enumerate(grads):
            m[i] = 0.9 * m[i] + 0.1 * g
            v[i] = 0.999 * v[i] + 0.001 * g * g
            mhat, vhat = m[i] / (1 - 0.9 ** step), v[i] / (1 - 0.999 ** step)
            params[i] = (params[i] - lr * mhat / (np.sqrt(vhat) + 1e-8)).astype(np.float32)
    return params, losses


def mae(pred, truth):
    return float(np.mean(np.abs(np.asarray(pred, dtype=np.float64) - np.asarray(truth, dtype=np.float64))))


def select(pairs):
    """Deterministic first / middle / last test windows (they overlap; not independent samples)."""
    last = len(pairs) - 1
    return [0, last // 2, last]


def parity(reference, observed, atol=PARITY_ATOL):
    """Max |diff| per window between two prediction lists keyed by window_id; ok if all <= atol."""
    if set(reference) != set(observed):
        return {'ok': False, 'reason': 'window ids differ', 'max_abs_diff': None}
    worst = 0.0
    for wid, ref in reference.items():
        obs = observed[wid]
        if len(ref) != len(obs):
            return {'ok': False, 'reason': f'{wid} length differs', 'max_abs_diff': None}
        worst = max(worst, max(abs(a - b) for a, b in zip(ref, obs)))
    return {'ok': worst <= atol, 'max_abs_diff': worst, 'atol': atol}


def fit(values, **kw):
    """Uses only values['training']: normalization statistics and training windows."""
    train_values = np.array(values['training'], dtype=np.float32)
    mean, std = float(np.mean(train_values)), float(np.std(train_values))
    if not std > 0:
        raise ValueError('training humidity has zero variance')
    x, y = arrays(windows(values['training']), mean, std)
    params, losses = train(x, y, **kw)
    return mean, std, params, losses


def prepare(history_root, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    doc = forecast.load_channel(history_root, CHANNEL)
    rows = doc['rows']
    split = split_rows(rows)
    values = {k: [r['value'] for r in seg] for k, seg in split.items()}
    pairs = {k: windows(v) for k, v in values.items()}
    begin = time.monotonic()
    mean, std, params, losses = fit(values)
    train_s = time.monotonic() - begin
    raw = infer.pack(mean, std, params)
    (out / 'model.bin').write_bytes(raw)
    mean32, std32, params32 = infer.unpack(raw)  # evaluate exactly the shipped bytes

    xt, _ = arrays(pairs['test'], mean32, std32)
    pred = infer.denormalize(mean32, std32, infer.forward(params32, xt))
    truth = [p[1] for p in pairs['test']]
    last_value = [[p[0][-1]] * HORIZON for p in pairs['test']]
    chosen = select(pairs['test'])
    seq = [r['sequence'] for r in split['test']]
    inputs = {'warmup': {'window_id': f'validation-{split["validation"][0]["sequence"]}', 'kind': 'warmup',
                         'window': pairs['validation'][0][0]},
              'evaluation': [{'window_id': f'test-{seq[i]}', 'kind': 'evaluation', 'window': pairs['test'][i][0]}
                             for i in chosen]}
    (out / 'inputs.json').write_text(json.dumps(inputs))
    reference = {item['window_id']: [float(v) for v in infer.denormalize(
                     mean32, std32, infer.predict(mean32, std32, params32, item['window']))[0]]
                 for item in [inputs['warmup'], *inputs['evaluation']]}
    targets = {f'test-{seq[i]}': pairs['test'][i][1] for i in chosen}
    selected = {wid: {'model_mae': mae(reference[wid], targets[wid]),
                      'last_value_mae': mae([inputs['evaluation'][k]['window'][-1]] * HORIZON, targets[wid]),
                      'target': targets[wid]}
                for k, wid in enumerate(targets)}
    summary = {
        'source': doc['source'], 'channel': CHANNEL, 'unit': rows[0]['unit'],
        'replay': 'frozen 2026-10-21 10:36:40-10:51:40Z history, not fresh live sensor data',
        'split_rows': {k: len(v) for k, v in split.items()},
        'split_sequences': {k: [v[0]['sequence'], v[-1]['sequence']] for k, v in split.items()},
        'window_counts': {k: len(v) for k, v in pairs.items()},
        'normalization': {'source': 'training rows only', 'mean': mean32, 'std': std32},
        'model': {'layers': infer.LAYERS, 'activation': 'ReLU', 'dtype': 'float32',
                  'parameters': sum(int(p.size) for p in params32), 'bytes': len(raw),
                  'sha256': hashlib.sha256(raw).hexdigest()},
        'training': {'host': 'local coordinator host (not a cluster Pod)', 'numpy': np.__version__,
                     'seed': SEED, 'adam_updates': STEPS, 'batch': BATCH, 'lr': LR, 'wall_s': train_s,
                     'loss_first': losses[0], 'loss_last': losses[-1], 'hyperparameter_search': False},
        'test': {'windows': len(pairs['test']), 'model_mae': mae(pred, truth),
                 'last_value_mae': mae(last_value, truth),
                 'note': 'MAE over all test windows x 16 horizons; overlapping windows from one 180-row span'},
        'selected': {'indices': chosen, 'per_window': selected,
                     'note': 'first/middle/last test windows overlap; 3 inputs are not a significance sample'},
        'reference_predictions': reference}
    (out / 'prepared.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
    return summary
