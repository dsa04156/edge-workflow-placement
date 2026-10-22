"""Deterministic virtual camera frames, event sentences and the frozen source mapping.

Frames are seeded crops/brightness changes of a public still image plus a
12-cell sequence barcode in the top-left 192x16 pixels. Sentences come from a
seeded template generator. Both are synthetic inputs generated on edge Pods;
they are not real sensor collection.
"""
import hashlib
import json
from pathlib import Path
import random

WIDTH, HEIGHT = 640, 480
CELL, BITS = 16, 10
SYNC = (1, 0)
OBJECTS = ('person', 'bus', 'car', 'truck', 'bicycle', 'cat', 'dog', 'backpack', 'handbag', 'tie', 'remote',
           'couch', 'chair', 'bottle', 'sports ball', 'traffic light')
PLACES = ('loading dock', 'gate 2', 'assembly line', 'parking lane', 'storage rack', 'control room', 'crosswalk')
STATUS = ('normal', 'check requested', 'operator notified', 'awaiting inspection')
TEMPLATES = (
    'Event {seq:04d} at {place}: operator reports a {obj} near the {place2}, vibration {v:.1f} mm/s, status {status}.',
    'Shift log {seq:04d}: a {obj} was seen close to the {place}; temperature {t:.1f} C, {status}.',
    'Alert {seq:04d}: {obj} detected by staff at the {place} while line speed was {v:.1f} m/min; {status}.')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    """Validated scenario: >=3 distinct video sources, >=2 text sources, >=3 workflows, sources on edge nodes."""
    raw = json.loads(Path(path).read_text())
    return validate(raw)


def validate(raw, edge_nodes=None):
    video, text, flows = raw.get('video_sources', {}), raw.get('text_sources', {}), raw.get('workflows', [])
    if len(video) < 3 or len({v['image'] for v in video.values()}) < 3:
        raise ValueError('At least 3 video sources with distinct images required')
    if len(text) < 2:
        raise ValueError('At least 2 text sources required')
    if len(flows) < 3:
        raise ValueError('At least 3 workflows required')
    nodes = [s['node_alias'] for s in [*video.values(), *text.values()]]
    if len(set(nodes)) != len(nodes):
        raise ValueError('Each source must sit on a distinct edge node')
    for name, flow in [(f['id'], f) for f in flows]:
        if flow['video'] not in video or flow['text'] not in text:
            raise ValueError('Unknown source in workflow '+name)
        if flow.get('destination', 'video') != 'video':
            raise ValueError('Result destination is the video source edge')
    if len({(f['video'], f['text']) for f in flows}) != len(flows):
        raise ValueError('Duplicate source combination')
    if edge_nodes is not None and not set(nodes) <= set(edge_nodes):
        raise ValueError('Sources must be on edge-role nodes: '+repr(sorted(set(nodes) - set(edge_nodes))))
    split = raw['inputs']
    if len(split['calibration']) != 3 or len(split['evaluation']) != 3 or set(split['calibration']) & set(split['evaluation']):
        raise ValueError('Exactly 3 calibration and 3 disjoint evaluation sequence ids required')
    loop = raw['stream']['loop_frames']
    if max(split['calibration'] + split['evaluation']) >= loop or loop >= 2**BITS:
        raise ValueError('Sequence ids must fit in one stream loop')
    return raw


def read_barcode(array):
    """Sequence id from a decoded HxWx3 numpy uint8 frame, or None if sync cells do not match."""
    bits = []
    for index in range(len(SYNC) + BITS):
        cell = array[4:CELL-4, index*CELL+4:(index+1)*CELL-4]
        bits.append(1 if float(cell.mean()) > 127.5 else 0)
    if tuple(bits[:len(SYNC)]) != SYNC:
        return None
    value = 0
    for bit in bits[len(SYNC):]:
        value = value*2 + bit
    return value


def frame(image_path, seed, seq):
    """RGB uint8 numpy frame for one sequence id: seeded crop/zoom and brightness, then barcode."""
    import numpy as np
    from PIL import Image, ImageEnhance
    rng = random.Random(f'{seed}:{seq}')
    image = Image.open(image_path).convert('RGB')
    w, h = image.size
    aspect = WIDTH/HEIGHT
    base_w, base_h = (w, w/aspect) if w/h <= aspect else (h*aspect, h)
    scale = rng.uniform(0.8, 0.95)
    cw, ch = base_w*scale, base_h*scale
    x0, y0 = rng.uniform(0, w-cw), rng.uniform(0, h-ch)
    crop = image.crop((round(x0), round(y0), round(x0+cw), round(y0+ch))).resize((WIDTH, HEIGHT), Image.Resampling.BILINEAR)
    crop = ImageEnhance.Brightness(crop).enhance(rng.uniform(0.9, 1.1))
    array = np.array(crop, dtype=np.uint8)
    # Barcode: sync cells then BITS sequence bits, MSB first, white=1.
    array[:CELL, :(len(SYNC)+BITS)*CELL] = 0
    bits = list(SYNC) + [(seq >> (BITS-1-i)) & 1 for i in range(BITS)]
    for index, bit in enumerate(bits):
        if bit:
            array[:CELL, index*CELL:(index+1)*CELL] = 255
    return array


def sentence(seed, seq):
    rng = random.Random(f'{seed}:{seq}')
    template = TEMPLATES[rng.randrange(len(TEMPLATES))]
    place, place2 = rng.sample(PLACES, 2)
    return template.format(seq=seq, obj=rng.choice(OBJECTS), place=place, place2=place2,
                           v=rng.uniform(0.5, 12.0), t=rng.uniform(15.0, 45.0), status=rng.choice(STATUS))
