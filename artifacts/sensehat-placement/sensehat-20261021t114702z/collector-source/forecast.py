"""Frozen Sense HAT forecast preparation: validate source, split by time, fit line, freeze inputs.

Channels are independent series (no cross-channel time alignment). No interpolation,
outlier removal or padding: gaps/metadata changes fail explicitly. The held-out
evaluation segment is scored after the model is frozen and never feeds back.
"""
import hashlib
import json
import math
from pathlib import Path
import random
import shutil
import statistics

import stage_runtime as sr

CHANNELS = ('temp_humidity', 'humidity', 'pressure')
SEED = 20261024
NODES = {'edge': 'etri-dev0003-raspi5', 'server': 'etri-ser0002-cgnmsb'}
FIXED_PLACEMENTS = {'edge-all': {'t1': 'edge', 't2': 'edge', 't3': 'edge'},
                    'server-all': {'t1': 'server', 't2': 'server', 't3': 'server'},
                    'mixed': {'t1': 'edge', 't2': 'server', 't3': 'server'}}
PLACEMENT_LABELS = (*FIXED_PLACEMENTS, 'default-scheduler')
HERE = Path(__file__).resolve().parent
CODE_FILES = ('forecast.py', 'stage_runtime.py', 'run.py', 'verify.py')


class SourceError(RuntimeError):
    pass


def load_channel(root, channel):
    root = Path(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    entry = manifest['files'].get('history/' + channel + '.json')
    if entry is None:
        raise SourceError('manifest has no entry for ' + channel)
    raw = (root / (channel + '.json')).read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != entry['sha256'] or len(raw) != entry['bytes']:
        raise SourceError(f'{channel} sha256 mismatch: manifest {entry["sha256"]} actual {actual}')
    doc = json.loads(raw)
    rows = doc['items']
    if doc.get('truncated') is not False or entry.get('truncated') is not False or len(rows) != entry['items']:
        raise SourceError(channel + ' history is truncated or row count differs from manifest')
    if doc.get('property') != channel or any(row.get('property') != channel for row in rows):
        raise SourceError(channel + ' file contains another property')
    try:
        sr.validate_rows(rows)
    except sr.InputError as error:
        raise SourceError(f'{channel}: {error}') from None
    return {'channel': channel, 'rows': rows,
            'source': {'file': 'history/' + channel + '.json', 'sha256': actual, 'bytes': len(raw)}}


def split_rows(rows):
    n = len(rows)
    a, b = math.floor(n * 0.6), math.floor(n * 0.8)
    return {'training': rows[:a], 'calibration': rows[a:b], 'evaluation': rows[b:]}


def window_pairs(segment):
    """Every (8 consecutive rows, next row) pair fully inside one split segment."""
    return [(segment[i:i + sr.WINDOW], segment[i + sr.WINDOW]) for i in range(len(segment) - sr.WINDOW)]


def metrics(errors):
    return {'count': len(errors), 'mae': math.fsum(abs(e) for e in errors) / len(errors),
            'rmse': math.sqrt(math.fsum(e * e for e in errors) / len(errors))}


def build_channel(doc):
    rows = doc['rows']
    split = split_rows(rows)
    # Training needs >=2 pairs, calibration one warmup pair, evaluation 3 disjoint 9-row spans.
    need = {'training': sr.WINDOW + 2, 'calibration': sr.WINDOW + 1, 'evaluation': 3 * (sr.WINDOW + 1)}
    short = {k: len(split[k]) for k in need if len(split[k]) < need[k]}
    if short:
        raise SourceError(f'{doc["channel"]}: insufficient contiguous rows per split {short} (need {need})')
    train = window_pairs(split['training'])
    x = [sr.window_mean([r['value'] for r in w]) for w, _ in train]
    y = [t['value'] for _, t in train]
    first = rows[0]
    model = {key: first[key] for key in sr.MODEL_IDENTITY}
    if len(set(x)) == 1:
        model.update(a=0.0, b=statistics.fmean(y), zero_variance_x=True)
    else:
        fit = statistics.linear_regression(x, y)
        model.update(a=fit.slope, b=fit.intercept, zero_variance_x=False)
    training_rows = split['training']
    model.update(method='statistics.linear_regression(mean(window8), next value)', training_pairs=len(train),
                 training_rows=len(training_rows), training_rows_sha256=sr.digest(sr.canonical(training_rows)),
                 training_first_sequence=training_rows[0]['sequence'], training_last_sequence=training_rows[-1]['sequence'],
                 training_first_observedAt=training_rows[0]['observedAt'],
                 training_last_observedAt=training_rows[-1]['observedAt'], training_target_mean=statistics.fmean(y))
    scored = []
    for window, target in window_pairs(split['evaluation']):
        mean = sr.window_mean([r['value'] for r in window])
        forecast = sr.predict(model, mean)
        persistence = window[-1]['value']
        scored.append({'window_first_sequence': window[0]['sequence'], 'target_sequence': target['sequence'],
                       'target_observedAt': target['observedAt'], 'truth': target['value'], 'forecast': forecast,
                       'persistence': persistence, 'model_error': forecast - target['value'],
                       'persistence_error': persistence - target['value']})
    evaluation = {'unit': first['unit'], 'rows': scored,
                  'model': metrics([r['model_error'] for r in scored]),
                  'persistence': metrics([r['persistence_error'] for r in scored])}
    return {'channel': doc['channel'], 'source': doc['source'], 'model': model, 'evaluation': evaluation,
            'split': split,
            'split_sequences': {k: [v[0]['sequence'], v[-1]['sequence']] for k, v in split.items()},
            'split_counts': {k: len(v) for k, v in split.items()}}


def select_requests(channel):
    """Warmup: first calibration pair. Evaluation: first, middle, last pairs (disjoint 9-row spans)."""
    name = channel['channel']
    calibration = window_pairs(channel['split']['calibration'])
    evaluation = window_pairs(channel['split']['evaluation'])
    last = len(evaluation) - 1
    middle = last // 2
    if middle < sr.WINDOW + 1 or last - middle < sr.WINDOW + 1:
        raise SourceError(name + ': evaluation pairs cannot be made disjoint')
    chosen = [('warmup', 'calibration', calibration[0])] + [('evaluation', 'evaluation', evaluation[i])
                                                            for i in (0, middle, last)]
    return [{'window_id': f'{name}-{split}-{window[0]["sequence"]}', 'channel': name, 'kind': kind, 'split': split,
             'rows': window, 'target': target} for kind, split, (window, target) in chosen]


def write_json(path, value):
    with Path(path).open('x') as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write('\n')


def reference_chain(payload, model_doc, model_sha256):
    t1 = sr.t1_features(payload)
    t2 = sr.t2_forecast(t1, model_doc, model_sha256)
    t3 = sr.t3_package(t2, model_sha256)
    return {name: {'output': out, 'output_sha256': sr.digest(sr.canonical(out))}
            for name, out in (('t1', t1), ('t2', t2), ('t3', t3))}


def make_plan(warmups, evaluations):
    rng = random.Random(SEED)
    order = list(PLACEMENT_LABELS)
    rng.shuffle(order)
    blocks = []
    for label in order:
        ids = list(evaluations)
        rng.shuffle(ids)
        blocks.append({'label': label, 'warmup': list(warmups), 'evaluation': ids})
    return {'seed': SEED, 'nodes': NODES, 'fixed_placements': FIXED_PLACEMENTS,
            'scheduler_placement': 'observed from default-scheduler, then re-pinned to the matching pinned pool Pods',
            'blocks': blocks, 'requests_per_unique_placement': len(warmups) + len(evaluations),
            'max_workflows': len(PLACEMENT_LABELS) * (len(warmups) + len(evaluations)),
            'network': 'single native-network block; no injected delay/qdisc; no inference retry',
            'shared_observations': 'a scheduler placement equal to a fixed placement reuses that block'}


def prepare(input_root, output):
    input_root, output = Path(input_root), Path(output)
    channels = [build_channel(load_channel(input_root, name)) for name in CHANNELS]
    output.mkdir(parents=True, exist_ok=False)
    # Byte copies of the immutable originals; the verifier rebuilds everything from these.
    (output / 'source' / 'history').mkdir(parents=True)
    for name in ('manifest.json', *(c + '.json' for c in CHANNELS)):
        shutil.copyfile(input_root / name, output / 'source' / 'history' / name)
    if (input_root.parent / 'SHA256SUMS').exists():
        shutil.copyfile(input_root.parent / 'SHA256SUMS', output / 'source' / 'SHA256SUMS')
    for channel in channels:
        if sr.digest((output / 'source' / channel['source']['file']).read_bytes()) != channel['source']['sha256']:
            raise SourceError('source copy differs from validated original: ' + channel['channel'])
    model_doc = {'schema': 'sensehat-linear-model/v1', 'window_size': sr.WINDOW,
                 'split_rule': 'per channel raw rows: floor(60%) training, next floor(80%)-floor(60%) calibration, rest evaluation',
                 'channels': {c['channel']: c['model'] for c in channels}}
    write_json(output / 'model.json', model_doc)
    model_sha256 = sr.digest((output / 'model.json').read_bytes())
    windows, targets, reference, warmups, evaluations = {}, {}, {}, [], []
    for channel in channels:
        for request in select_requests(channel):
            payload = {'rows': request['rows']}
            wid = request['window_id']
            windows[wid] = {'channel': request['channel'], 'kind': request['kind'], 'split': request['split'],
                            'payload': payload, 'payload_sha256': sr.digest(sr.canonical(payload)),
                            'payload_bytes': len(sr.canonical(payload))}
            target = request['target']
            targets[wid] = {'sequence': target['sequence'], 'observedAt': target['observedAt'], 'value': target['value'],
                            'unit': target['unit'], 'row_sha256': sr.digest(sr.canonical(target))}
            reference[wid] = reference_chain(payload, model_doc, model_sha256)
            (warmups if request['kind'] == 'warmup' else evaluations).append(wid)
    write_json(output / 'windows.json', {'schema': 'sensehat-windows/v1', 'model_sha256': model_sha256,
                                         'note': 'target-free stage inputs; shipped to cluster', 'windows': windows})
    write_json(output / 'targets.json', {'note': 'local only; never shipped to stage Pods', 'targets': targets})
    write_json(output / 'reference.json', {'backend': 'local CPU python stdlib', 'model_sha256': model_sha256,
                                           'tolerance': {'abs': 1e-9, 'rel': 1e-9}, 'windows': reference})
    write_json(output / 'evaluation.json', {
        'scope': 'whole held-out evaluation segment per channel, scored locally after freezing; no cross-unit aggregate',
        'baseline': 'persistence (last observed value)',
        'channels': {c['channel']: c['evaluation'] for c in channels}})
    write_json(output / 'source-manifest.json', {
        'input_root': str(input_root), 'replay': 'frozen physical Sense HAT history; not continuous live sensor E2E',
        'channels': {c['channel']: {**c['source'], 'rows': len(c['split']['training']) + len(c['split']['calibration'])
                                    + len(c['split']['evaluation']), 'split_counts': c['split_counts'],
                                    'split_sequences': c['split_sequences'], 'gaps': [], 'metadata_changes': []}
                     for c in channels}})
    write_json(output / 'plan.json', make_plan(warmups, evaluations))
    files = sorted(p for p in output.rglob('*') if p.is_file())
    write_json(output / 'prepared-manifest.json', {
        'model_sha256': model_sha256,
        'files': {str(p.relative_to(output)): sr.digest(p.read_bytes()) for p in files},
        'code_sha256': {name: sr.digest((HERE / name).read_bytes()) for name in CODE_FILES if (HERE / name).exists()}})
    return output


def check_code(manifest, root=HERE):
    """Raise unless the code under root is byte-identical to the code frozen at preparation."""
    frozen = manifest.get('code_sha256', {})
    for name in CODE_FILES:
        path = Path(root) / name
        if name not in frozen or not path.exists() or sr.digest(path.read_bytes()) != frozen[name]:
            raise SourceError(f'{name} changed after preparation; run --prepare into a new directory')


def check_prepared(prepared):
    """Raise unless every prepared file still matches its frozen hash."""
    prepared = Path(prepared)
    manifest = json.loads((prepared / 'prepared-manifest.json').read_text())
    for name, expected in manifest['files'].items():
        if sr.digest((prepared / name).read_bytes()) != expected:
            raise SourceError('prepared file changed after freeze: ' + name)
    return manifest
