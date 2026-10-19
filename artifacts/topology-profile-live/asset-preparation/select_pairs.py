"""Reproduce the pre-inference image-disjoint 20/40 input selection.

Uses the archived ZIP central-directory index. Reads completed local ZIP bytes
where available, otherwise requests a public HTTP byte range. Downloads no
authentication/session material. Requires requests; media validation uses
Pillow and soundfile if --validate is passed.
"""
import argparse
import concurrent.futures
import hashlib
import json
import pathlib
import random
import struct
import zipfile
import zlib

import requests

parser = argparse.ArgumentParser()
parser.add_argument('--assets', type=pathlib.Path, default=pathlib.Path(__file__).parent)
parser.add_argument('--validate', action='store_true')
args = parser.parse_args()
assets = args.assets.resolve()
output = assets / 'selected-data'
output.mkdir(exist_ok=True)
index_path = assets / 'data-zip-index.json'
if not index_path.exists():
    index_path = assets / 'selected-data-zip-index.json'
index = {i['name']: i for i in json.loads(index_path.read_text())}
base = 'https://duke.app.box.com/shared/static/wu7o721wrqu0ei75ob8yojcw1ntg3tfj.zip'


def extract(name):
    item = index[name]
    offset = item['offset']
    length = item['compressed_bytes'] + len(name.encode()) + 200
    blob = None
    local = assets / 'data.zip'
    if local.exists() and local.stat().st_size >= offset + length:
        with local.open('rb') as handle:
            handle.seek(offset)
            blob = handle.read(length)
    if blob is None:
        response = requests.get(base + '?asset_offset=' + str(offset),
                                headers={'Range': f'bytes={offset}-{offset + length - 1}'}, timeout=90)
        response.raise_for_status()
        assert response.status_code == 206
        blob = response.content
    fields = struct.unpack('<IHHHHHIIIHH', blob[:30])
    assert fields[0] == 0x04034b50
    start = 30 + fields[-2] + fields[-1]
    data = blob[start:start + item['compressed_bytes']]
    data = zlib.decompress(data, -15) if fields[3] == 8 else data
    assert len(data) == item['bytes'] and zlib.crc32(data) == item['crc32']
    return data


questions = json.loads(extract('VQA_Workflow_Datasets/MultipleChoice_mscoco_val2014_questions.json'))['questions']
by_image = {}
for question in questions:
    by_image.setdefault(question['image_id'], []).append(question)
image_ids = sorted(by_image)
random.Random(20261019).shuffle(image_ids)
with zipfile.ZipFile(assets / 'Annotations_Val_mscoco.zip') as archive:
    annotations = {v['question_id']: v for v in json.loads(archive.read('mscoco_val2014_annotations.json'))['annotations']}


def save_pair(entry):
    position, image_id = entry
    question = min(by_image[image_id], key=lambda v: v['question_id'])
    question_id = question['question_id']
    item = {'index': position, 'split': 'calibration' if position < 20 else 'evaluation',
            'question_id': question_id, 'image_id': image_id,
            'question': question['question'], 'multiple_choices': question['multiple_choices'],
            'image_zip_name': f'VQA_Workflow_Datasets/coco_images/val2014/COCO_val2014_{image_id:012d}.jpg',
            'audio_zip_name': f'VQA_Workflow_Datasets/questions_speech/val2014/{question_id}.flac'}
    for kind in ['image', 'audio']:
        name = item[kind + '_zip_name']
        data = extract(name)
        path = output / pathlib.Path(name).name
        if path.exists():
            assert path.read_bytes() == data
        else:
            path.write_bytes(data)
        item[kind + '_path'] = str(path)
        item[kind + '_bytes'] = len(data)
        item[kind + '_sha256'] = hashlib.sha256(data).hexdigest()
    annotation = annotations[question_id]
    assert annotation['image_id'] == image_id
    for name in ['multiple_choice_answer', 'question_type', 'answer_type']:
        item[name] = annotation[name]
    item['human_answers'] = annotation['answers']
    if args.validate:
        import soundfile
        from PIL import Image
        audio = soundfile.info(item['audio_path'])
        assert audio.channels == 1 and audio.samplerate == 16000
        item['audio_info'] = {'samplerate': audio.samplerate, 'channels': audio.channels,
                              'frames': audio.frames, 'duration_s': audio.duration}
        with Image.open(item['image_path']) as image:
            image.verify()
        with Image.open(item['image_path']) as image:
            item['image_dimensions'] = list(image.size)
    return item


with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
    pairs = list(executor.map(save_pair, enumerate(image_ids[:60])))
manifest = {'source': 'https://duke.box.com/s/wu7o721wrqu0ei75ob8yojcw1ntg3tfj',
            'ground_truth_source': 'https://s3.amazonaws.com/cvmlp/vqa/mscoco/vqa/Annotations_Val_mscoco.zip',
            'selection': 'seed20261019 shuffle of sorted unique image_ids;lowest question_id per image;first20calibration,next40evaluation;before inference;disjoint images',
            'pairs': pairs}
path = output / 'pairs.reproduced.json'
path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
print(path)
