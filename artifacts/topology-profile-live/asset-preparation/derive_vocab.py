"""Reconstruct inference maps using the pinned upstream preprocessing functions.

Usage: python derive_vocab.py --source-root PATH_TO_PINNED_JELLYBEAN --assets THIS_DIR
Only the five upstream functions relevant to vocabulary creation are executed;
this avoids importing unrelated numpy/scipy training dependencies.
"""
import argparse
import ast
import contextlib
import hashlib
import json
import pathlib
import pickle
import re
import sys
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('--source-root', type=pathlib.Path, required=True)
parser.add_argument('--assets', type=pathlib.Path, default=pathlib.Path(__file__).parent)
args = parser.parse_args()
source = args.source_root / 'VQA/python/vqa/datasets/vqa_processed.py'
assert hashlib.sha256(source.read_bytes()).hexdigest() == '99565a5af59604f1d8ccabf74663af8d9105f50e7732b1d3dcb249ba8828b7c2'
with zipfile.ZipFile(args.assets / 'Questions_Train_mscoco.zip') as archive:
    questions = json.loads(archive.read('MultipleChoice_mscoco_train2014_questions.json'))['questions']
with zipfile.ZipFile(args.assets / 'Annotations_Train_mscoco.zip') as archive:
    annotations = json.loads(archive.read('mscoco_train2014_annotations.json'))['annotations']
assert len(questions) == len(annotations)
assert all(q['question_id'] == a['question_id'] for q, a in zip(questions, annotations))
examples = [{'question': q['question'], 'answer': a['multiple_choice_answer']}
            for q, a in zip(questions, annotations)]
names = {'get_top_answers', 'remove_examples', 'tokenize_mcb',
         'preprocess_questions', 'remove_long_tail_train'}
tree = ast.parse(source.read_text())
functions = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[])
namespace = {'sys': sys, 're': re}
exec(compile(functions, str(source), 'exec'), namespace)
output = args.assets / 'derived-vocab'
output.mkdir(exist_ok=True)
with (output / 'generation.log').open('w') as log, contextlib.redirect_stdout(log):
    answers = namespace['get_top_answers'](examples, 2000)
    filtered = namespace['remove_examples'](examples, {a: i for i, a in enumerate(answers)})
    filtered = namespace['preprocess_questions'](filtered, 'mcb')
    _, words = namespace['remove_long_tail_train'](filtered, 0)
vocab = {'word_to_wid': {w: i + 1 for i, w in enumerate(words)},
         'wid_to_word': {i + 1: w for i, w in enumerate(words)},
         'aid_to_ans': answers, 'ans_to_aid': {a: i for i, a in enumerate(answers)}}
data = json.dumps(vocab, ensure_ascii=False, indent=2).encode()
expected = 'de86a3596f0bfdce3e733b03a810acae0f8527e49b6fa052001ed86d5cfbedda'
assert hashlib.sha256(data).hexdigest() == expected
(output / 'vocab.json').write_bytes(data)
for name, value in vocab.items():
    (output / (name + '.pickle')).write_bytes(pickle.dumps(value, protocol=4))
print(json.dumps({'words': len(words), 'answers': len(answers), 'vocab_sha256': expected}))
