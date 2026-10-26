"""Official VQA1 scoring seam: module API, CLI, and parity with the executed upstream file."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import vqa_eval  # noqa: E402

ROOT = HERE.parents[1]
PAIRS = ROOT/'artifacts/topology-profile-assets/20261019T091725Z/selected-data/pairs.json'
REFERENCE = ROOT/'artifacts/topology-profile-live/reference-v1/results.jsonl'


def pair(qid, answers, split='calibration'):
    return dict(question_id=qid, split=split, question_type='what', answer_type='other',
                human_answers=[dict(answer=a, answer_confidence='yes', answer_id=i+1) for i, a in enumerate(answers)])


class OfficialAccuracy(unittest.TestCase):
    def test_one_to_four_matching_humans_give_point3_point6_point9_and_one(self):
        # Worked leave-one-out values: k matches -> (k*min(1,(k-1)/3) + (10-k)*min(1,k/3))/10.
        for k, expected in ((0, 0.0), (1, 0.3), (2, 0.6), (3, 0.9), (4, 1.0), (10, 1.0)):
            gt = ['cat']*k + ['dog', 'bird', 'cow', 'fox', 'owl', 'pig', 'rat', 'emu', 'ant', 'bee'][:10-k]
            score = vqa_eval.score_answer(pair(1, gt), 'cat')
            self.assertAlmostEqual(score, expected, places=12, msg=f'k={k}')

    def test_normalization_maps_number_words_strips_articles_and_punctuation(self):
        gt = ['2', '2', '2', 'two', 'a dog', 'dog', 'dog', "dont", 'yes', 'no']
        self.assertAlmostEqual(vqa_eval.score_answer(pair(1, gt), 'two'), 1.0)
        self.assertAlmostEqual(vqa_eval.score_answer(pair(1, gt), 'The dog.'), 0.9)
        self.assertAlmostEqual(vqa_eval.score_answer(pair(1, gt), "don't"), 0.3)
        self.assertAlmostEqual(vqa_eval.score_answer(pair(1, gt), 'yes!'), 0.3)

    def test_identical_humans_skip_normalization_like_upstream(self):
        # Upstream normalizes only if len(set(gtAnswers)) > 1; then 'two' never equals '2'.
        self.assertEqual(vqa_eval.score_answer(pair(1, ['2']*10), 'two'), 0.0)
        self.assertEqual(vqa_eval.score_answer(pair(1, ['2']*10), ' 2\n'), 1.0)

    def test_comma_between_digits_removes_punctuation_instead_of_spacing(self):
        gt = ['1,000', '1000', '1000', 'many', 'lots', 'x', 'y', 'z', 'w', 'v']
        self.assertAlmostEqual(vqa_eval.score_answer(pair(1, gt), '1000'), 0.9)

    def test_python2_ascii_digit_semantics_for_period_strip(self):
        # Python2 str regex without re.UNICODE: '\\d' is ASCII only, so the period before an
        # Arabic-Indic digit is stripped (a Python3 str regex would keep it and score 0).
        gt = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', '٣٣']
        self.assertAlmostEqual(vqa_eval.score_answer(pair(1, gt), '٣.٣'), 0.3)

    def test_missing_or_malformed_ground_truth_is_rejected(self):
        bad = pair(1, ['x']*9)
        with self.assertRaisesRegex(ValueError, '10 human answers'):
            vqa_eval.score_answer(bad, 'x')
        dup = pair(1, ['x']*10)
        dup['human_answers'][3]['answer_id'] = 1
        with self.assertRaisesRegex(ValueError, 'answer_id'):
            vqa_eval.score_answer(dup, 'x')
        with self.assertRaisesRegex(ValueError, 'human_answers'):
            vqa_eval.score_answer(dict(question_id=1), 'x')

    def test_score_rejects_duplicate_or_unknown_question_ids(self):
        pairs = [pair(1, ['a']*10), pair(2, ['b']*10, 'evaluation')]
        with self.assertRaisesRegex(ValueError, 'Duplicate question_id'):
            vqa_eval.score([*pairs, pair(1, ['a']*10)], [dict(question_id=1, answer='a')])
        with self.assertRaisesRegex(ValueError, 'Duplicate answer'):
            vqa_eval.score(pairs, [dict(question_id=1, answer='a'), dict(question_id=1, answer='b')])
        with self.assertRaisesRegex(ValueError, 'Unknown question_id'):
            vqa_eval.score(pairs, [dict(question_id=3, answer='a')])

    def test_score_reports_split_means_with_missing_answers_counted_separately(self):
        pairs = [pair(1, ['a']*10), pair(2, ['b']*10), pair(3, ['c']*10, 'evaluation')]
        out = vqa_eval.score(pairs, [dict(question_id=1, answer='a'), dict(question_id=3, answer='x')])
        cal = out['splits']['calibration']
        self.assertEqual((cal['questions'], cal['answered'], cal['missing']), (2, 1, 1))
        self.assertAlmostEqual(cal['answered_mean'], 1.0)
        self.assertAlmostEqual(cal['all_questions_zero_imputed_mean'], 0.5)
        self.assertEqual(out['splits']['evaluation']['answered_mean'], 0.0)


@unittest.skipUnless(PAIRS.exists() and REFERENCE.exists(), 'frozen VQA assets not present')
class RealPairsParity(unittest.TestCase):
    def test_port_equals_executed_upstream_on_all_60_reference_answers_and_human_rotations(self):
        pairs = json.loads(PAIRS.read_text())['pairs']
        reference = [json.loads(line) for line in REFERENCE.read_text().splitlines()]
        candidates = {}
        for p in pairs:
            # Model answer plus every distinct human answer and a few perturbed spellings.
            answers = {r['answers'][0] for r in reference if r['question_id'] == p['question_id']}
            for h in p['human_answers']:
                answers |= {h['answer'], h['answer'].upper()+'.', 'the '+h['answer'], h['answer']+'?'}
            candidates[p['question_id']] = sorted(answers)
        port = {q: [vqa_eval.score_answer(p, a) for a in candidates[q]] for q, p in ((p['question_id'], p) for p in pairs)}
        oracle = vqa_eval.upstream_oracle(pairs, candidates)
        self.assertEqual(len(oracle), 60)
        for qid, values in port.items():
            self.assertEqual(values, oracle[qid], msg=str(qid))


class Cli(unittest.TestCase):
    def test_cli_scores_jsonl_answers_and_refuses_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp/'pairs.json').write_text(json.dumps(dict(pairs=[pair(7, ['two']*3+['2']*7), pair(8, ['red']*10, 'evaluation')])))
            (tmp/'answers.jsonl').write_text(json.dumps(dict(question_id=7, answers=['2', 'x']))+'\n')
            run = lambda: subprocess.run([sys.executable, str(HERE/'vqa_eval.py'), 'score', '--pairs', str(tmp/'pairs.json'),
                                          '--answers', str(tmp/'answers.jsonl'), '--out', str(tmp/'score.json')],
                                         capture_output=True, text=True)
            result = run()
            self.assertEqual(result.returncode, 0, result.stderr)
            out = json.loads((tmp/'score.json').read_text())
            self.assertEqual(out['per_question']['7'], 1.0)
            self.assertEqual(out['splits']['evaluation']['missing'], 1)
            self.assertEqual(out['upstream']['sha256'], vqa_eval.UPSTREAM_SHA256)
            self.assertNotEqual(run().returncode, 0)


if __name__ == '__main__':
    unittest.main()
