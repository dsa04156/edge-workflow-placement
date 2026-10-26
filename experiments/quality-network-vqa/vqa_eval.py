"""Official VQA1 accuracy (GT-Vision-Lab/VQA vqaEval.py), Python3 port plus executed-upstream oracle.

Upstream: https://github.com/GT-Vision-Lab/VQA commit a013f0043c1e2cdc995922dfe257f7149aa9af06,
PythonEvaluationTools/vqaEvaluation/vqaEval.py (vendored byte-for-byte in upstream/, BSD-2 license.txt).

Kept exactly: newline/tab/strip cleanup; punctuation and digit/article/contraction normalization ONLY
when the 10 human answers are not all identical (len(set) > 1); leave-one-out over the 10 human answer
dicts (a dict equal to the left-out one is excluded, so answer_id uniqueness matters); min(1, matches/3);
mean of the 10. periodStrip.sub's third positional argument is re.UNICODE used as count=32, kept.

Python3 behaviour differences, stated: (1) upstream is Python2; its str patterns applied to the unicode
JSON answers match '\\d' against ASCII digits only, so the port compiles the patterns with re.ASCII. The
executed-upstream oracle under Python3 matches Unicode digits instead; the two agree on ASCII digits.
(2) per-question values are returned unrounded in 0..1; upstream rounds 100*acc to n=2 for reporting.
Inputs must carry exactly 10 human answers with unique answer_id; missing or duplicated data is refused.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
UPSTREAM = HERE/'upstream/vqaEval.py'
UPSTREAM_COMMIT = 'a013f0043c1e2cdc995922dfe257f7149aa9af06'
UPSTREAM_SHA256 = 'f08edfcad5be0112500993e245c706b6cb928eadebe203f89f838e5e0d04bec8'

CONTRACTIONS = {"aint": "ain't", "arent": "aren't", "cant": "can't", "couldve": "could've", "couldnt": "couldn't",
    "couldn'tve": "couldn't've", "couldnt've": "couldn't've", "didnt": "didn't", "doesnt": "doesn't", "dont": "don't", "hadnt": "hadn't",
    "hadnt've": "hadn't've", "hadn'tve": "hadn't've", "hasnt": "hasn't", "havent": "haven't", "hed": "he'd", "hed've": "he'd've",
    "he'dve": "he'd've", "hes": "he's", "howd": "how'd", "howll": "how'll", "hows": "how's", "Id've": "I'd've", "I'dve": "I'd've",
    "Im": "I'm", "Ive": "I've", "isnt": "isn't", "itd": "it'd", "itd've": "it'd've", "it'dve": "it'd've", "itll": "it'll", "let's": "let's",
    "maam": "ma'am", "mightnt": "mightn't", "mightnt've": "mightn't've", "mightn'tve": "mightn't've", "mightve": "might've",
    "mustnt": "mustn't", "mustve": "must've", "neednt": "needn't", "notve": "not've", "oclock": "o'clock", "oughtnt": "oughtn't",
    "ow's'at": "'ow's'at", "'ows'at": "'ow's'at", "'ow'sat": "'ow's'at", "shant": "shan't", "shed've": "she'd've", "she'dve": "she'd've",
    "she's": "she's", "shouldve": "should've", "shouldnt": "shouldn't", "shouldnt've": "shouldn't've", "shouldn'tve": "shouldn't've",
    "somebody'd": "somebodyd", "somebodyd've": "somebody'd've", "somebody'dve": "somebody'd've", "somebodyll": "somebody'll",
    "somebodys": "somebody's", "someoned": "someone'd", "someoned've": "someone'd've", "someone'dve": "someone'd've",
    "someonell": "someone'll", "someones": "someone's", "somethingd": "something'd", "somethingd've": "something'd've",
    "something'dve": "something'd've", "somethingll": "something'll", "thats": "that's", "thered": "there'd", "thered've": "there'd've",
    "there'dve": "there'd've", "therere": "there're", "theres": "there's", "theyd": "they'd", "theyd've": "they'd've",
    "they'dve": "they'd've", "theyll": "they'll", "theyre": "they're", "theyve": "they've", "twas": "'twas", "wasnt": "wasn't",
    "wed've": "we'd've", "we'dve": "we'd've", "weve": "we've", "werent": "weren't", "whatll": "what'll", "whatre": "what're",
    "whats": "what's", "whatve": "what've", "whens": "when's", "whered": "where'd", "wheres": "where's", "whereve": "where've",
    "whod": "who'd", "whod've": "who'd've", "who'dve": "who'd've", "wholl": "who'll", "whos": "who's", "whove": "who've", "whyll": "why'll",
    "whyre": "why're", "whys": "why's", "wont": "won't", "wouldve": "would've", "wouldnt": "wouldn't", "wouldnt've": "wouldn't've",
    "wouldn'tve": "wouldn't've", "yall": "y'all", "yall'll": "y'all'll", "y'allll": "y'all'll", "yall'd've": "y'all'd've",
    "y'alld've": "y'all'd've", "y'all'dve": "y'all'd've", "youd": "you'd", "youd've": "you'd've", "you'dve": "you'd've",
    "youll": "you'll", "youre": "you're", "youve": "you've"}
MANUAL_MAP = {'none': '0', 'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5',
              'six': '6', 'seven': '7', 'eight': '8', 'nine': '9', 'ten': '10'}
ARTICLES = ['a', 'an', 'the']
# Upstream pattern text kept verbatim, including the '(?!<=' typo.
PERIOD_STRIP = re.compile(r"(?!<=\d)(\.)(?!\d)", re.ASCII)
COMMA_STRIP = re.compile(r"(\d)(\,)(\d)", re.ASCII)
PUNCT = [';', r"/", '[', ']', '"', '{', '}', '(', ')', '=', '+', '\\', '_', '-', '>', '<', '@', '`', ',', '?', '!']


def process_punctuation(text):
    out = text
    for p in PUNCT:
        if (p+' ' in text or ' '+p in text) or (re.search(COMMA_STRIP, text) is not None):
            out = out.replace(p, '')
        else:
            out = out.replace(p, ' ')
    return PERIOD_STRIP.sub('', out, re.UNICODE)  # upstream passes re.UNICODE as count


def process_digit_article(text):
    words = [MANUAL_MAP.get(w, w) for w in text.lower().split()]
    words = [w for w in words if w not in ARTICLES]
    return ' '.join(CONTRACTIONS.get(w, w) for w in words)


def _clean(text):
    return text.replace('\n', ' ').replace('\t', ' ').strip()


def human_answers(pair):
    if 'human_answers' not in pair:
        raise ValueError(f"question {pair.get('question_id')}: human_answers missing")
    answers = pair['human_answers']
    if len(answers) != 10:
        raise ValueError(f"question {pair.get('question_id')}: expected 10 human answers, got {len(answers)}")
    ids = [a.get('answer_id') for a in answers]
    if len(set(ids)) != 10 or None in ids:
        raise ValueError(f"question {pair.get('question_id')}: human answer_id must be 10 unique values")
    if not all(isinstance(a.get('answer'), str) for a in answers):
        raise ValueError(f"question {pair.get('question_id')}: human answer text missing")
    return answers


def score_answer(pair, answer):
    """Official per-question accuracy in 0..1 for one returned answer string."""
    gts = [dict(a, answer=_clean(a['answer'])) for a in human_answers(pair)]
    res = _clean(answer)
    if len({a['answer'] for a in gts}) > 1:
        for a in gts:
            a['answer'] = process_digit_article(process_punctuation(a['answer']))
        res = process_digit_article(process_punctuation(res))
    accuracies = []
    for left_out in gts:
        others = [a for a in gts if a != left_out]
        accuracies.append(min(1, float(sum(a['answer'] == res for a in others))/3))
    return float(sum(accuracies))/len(accuracies)


def _answer_text(row):
    if 'answer' in row:
        return row['answer']
    return row['answers'][0]


def score(pairs, answers):
    """Per-question and per-split official accuracy; missing answers are reported, never dropped."""
    by_id = {}
    for p in pairs:
        if p['question_id'] in by_id:
            raise ValueError(f"Duplicate question_id in ground truth: {p['question_id']}")
        human_answers(p)
        by_id[p['question_id']] = p
    returned = {}
    for row in answers:
        qid = row['question_id']
        if qid not in by_id:
            raise ValueError(f'Unknown question_id in answers: {qid}')
        if qid in returned:
            raise ValueError(f'Duplicate answer for question_id {qid}')
        returned[qid] = _answer_text(row)
    per_question = {qid: score_answer(by_id[qid], text) for qid, text in returned.items()}
    splits = {}
    for p in pairs:
        s = splits.setdefault(p['split'], dict(questions=0, answered=0, missing=0, sum=0.0))
        s['questions'] += 1
        if p['question_id'] in per_question:
            s['answered'] += 1
            s['sum'] += per_question[p['question_id']]
        else:
            s['missing'] += 1
    for s in splits.values():
        total = s.pop('sum')
        s['answered_mean'] = total/s['answered'] if s['answered'] else None
        s['all_questions_zero_imputed_mean'] = total/s['questions']
    return dict(per_question=per_question, splits=splits)


def upstream_oracle(pairs, candidates):
    """Execute the vendored upstream VQAEval (Python2 prints neutralized) on {qid: [answer, ...]}."""
    source = UPSTREAM.read_bytes()
    if hashlib.sha256(source).hexdigest() != UPSTREAM_SHA256:
        raise ValueError('Vendored upstream vqaEval.py hash changed')
    text = re.sub(r'^(\s*)print "[^"\n]*"\s*$', r'\1pass', source.decode(), flags=re.M)
    namespace = {'__name__': 'upstream_vqaEval'}
    exec(compile(text, str(UPSTREAM), 'exec'), namespace)

    class Capture(namespace['VQAEval']):
        def setEvalQA(self, quesId, acc):
            self.raw = acc  # unrounded avgGTAcc; scoring path untouched

        def updateProgress(self, progress):
            pass

    class Store:
        def __init__(self, qa):
            self.qa = qa

        def getQuesIds(self):
            return list(self.qa)

    out = {}
    for p in pairs:
        qid = p['question_id']
        gt = dict(answers=human_answers(p), question_type=p.get('question_type'), answer_type=p.get('answer_type'))
        values = []
        for answer in candidates[qid]:
            evaluator = Capture(Store({qid: copy.deepcopy(gt)}), Store({qid: dict(answer=answer)}))
            evaluator.evaluate()
            values.append(evaluator.raw)
        out[qid] = values
    return out


def provenance():
    return dict(repository='https://github.com/GT-Vision-Lab/VQA', commit=UPSTREAM_COMMIT,
                path='PythonEvaluationTools/vqaEvaluation/vqaEval.py', sha256=UPSTREAM_SHA256,
                license='upstream/license.txt (BSD 2-clause, Aishwarya Agrawal 2014)',
                port_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                port='Python3 port; ASCII \\d as in Python2; unrounded 0..1 per question')


def main(argv=None):
    ap = argparse.ArgumentParser(description='Official VQA1 accuracy of returned answers (Python3 port).')
    sub = ap.add_subparsers(dest='command', required=True)
    sc = sub.add_parser('score', help='score a JSONL of {question_id, answer | answers[0]} against pairs.json human answers')
    sc.add_argument('--pairs', required=True)
    sc.add_argument('--answers', required=True)
    sc.add_argument('--out', required=True, help='new JSON file; an existing file is refused')
    args = ap.parse_args(argv)
    pairs = json.loads(Path(args.pairs).read_text())['pairs']
    rows = [json.loads(line) for line in Path(args.answers).read_text().splitlines() if line.strip()]
    result = score(pairs, rows)
    result['per_question'] = {str(k): v for k, v in result['per_question'].items()}
    result.update(upstream=provenance(), pairs_sha256=hashlib.sha256(Path(args.pairs).read_bytes()).hexdigest(),
                  answers_sha256=hashlib.sha256(Path(args.answers).read_bytes()).hexdigest())
    with Path(args.out).open('x') as handle:
        json.dump(result, handle, indent=1)
        handle.write('\n')
    print(json.dumps(result['splits']))


if __name__ == '__main__':
    sys.exit(main())
