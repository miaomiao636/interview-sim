"""System duplicate skips and ASR cleanup must not masquerade as ability."""
import copy
import json
import unittest

from backend import report_pipeline
from tests.test_report_v2 import feedback, session, turn


class ReportVoiceBoundaries(unittest.IsolatedAsyncioTestCase):
    async def build(self, value):
        calls = []
        async def model(**kwargs):
            text = kwargs['messages'][1]['content']
            calls.append(text)
            if '本批作答：' in text:
                batch = json.loads(text.split('本批作答：', 1)[1])
                return json.dumps({'question_feedback': [feedback(t, 10 if t.get('attempt', 1) > 1 else 8) for t in batch]}, ensure_ascii=False)
            return json.dumps({'interview_tips': [], 'practice_plan': []})
        report = await report_pipeline.build_report(value, model, {}, lambda *_: None)
        return report, calls

    async def test_legacy_adaptive_then_blueprint_duplicate_skip_is_not_double_penalized(self):
        first = turn(question_kind='adaptive', blueprint_id=None, question='请讲一个你解决技术难题的具体经历。')
        duplicate = turn('q-2', answer='', status='unanswered', question='请讲一个你解决技术难题的具体经历。')
        source = session([first, duplicate])
        unchanged = copy.deepcopy(source)
        report, calls = await self.build(source)
        perf = report['interview_performance']['first_attempt']
        self.assertEqual(perf['total'], 80)
        self.assertEqual(perf['question_count'], 1)
        self.assertEqual(perf['excluded_duplicate_count'], 1)
        item = report['question_feedback'][1]
        self.assertTrue(item['scoring_excluded'])
        self.assertIsNone(item['score'])
        self.assertEqual(item['duplicate_of'], 'q-1')
        self.assertEqual(source, unchanged)
        sent = [json.loads(c.split('本批作答：', 1)[1]) for c in calls if '本批作答：' in c]
        self.assertEqual([t['question_id'] for batch in sent for t in batch], ['q-1'])

    async def test_genuinely_different_detail_and_explicit_retry_still_count(self):
        source = session([turn(question_kind='adaptive', blueprint_id=None),
                          turn('q-2', answer='', status='unanswered', question='项目中如何验证结果？')])
        report, _ = await self.build(source)
        self.assertEqual(report['interview_performance']['first_attempt']['total'], 40)
        retried = session([turn(), turn(attempt=2, answer='', status='unanswered', is_retry=True)])
        report, _ = await self.build(retried)
        self.assertEqual(report['interview_performance']['latest_retry']['total'], 0)

    async def test_voice_source_is_retained_and_report_does_not_claim_spoken_fluency(self):
        voice = {'raw_transcript': '我，我负责需求访谈。', 'cleaned_transcript': '我负责需求访谈。', 'cleanup_status': 'cleaned'}
        source = session([turn(voice_input=voice)])
        report, calls = await self.build(source)
        self.assertEqual(report['question_feedback'][0]['voice_input'], voice)
        self.assertIn('不评估真实口吃', report['interview_performance']['note'])
        self.assertTrue(any('原始转写' in c and '不推断口吃' in c for c in calls))
        changed = copy.deepcopy(source)
        changed['turns'][0]['voice_input']['raw_transcript'] = '另一份原始转写'
        self.assertNotEqual(report_pipeline.turn_fingerprint(source, source['turns'][0]),
                            report_pipeline.turn_fingerprint(changed, changed['turns'][0]))

    async def test_retry_of_excluded_duplicate_is_not_promoted_to_first_attempt(self):
        original = turn(question_kind='adaptive', blueprint_id=None)
        duplicate = turn('q-2', answer='', status='unanswered')
        retry = turn('q-2', attempt=2, is_retry=True)
        report, _ = await self.build(session([original, duplicate, retry]))
        perf = report['interview_performance']
        self.assertEqual(perf['first_attempt']['total'], 80)
        self.assertEqual(perf['first_attempt']['question_count'], 1)
        self.assertEqual(perf['latest_retry']['total'], 100)
        self.assertEqual(perf['latest_retry']['question_count'], 1)
        self.assertEqual(perf['comparison']['paired_question_count'], 0)
        self.assertIsNone(perf['comparison']['delta'])

        # An unpaired duplicate retry must not affect another valid comparison.
        report, _ = await self.build(session([original, duplicate, retry, turn(attempt=2, is_retry=True, answer='', status='unanswered')]))
        perf = report['interview_performance']
        self.assertEqual(perf['first_attempt']['total'], 80)
        self.assertEqual(perf['latest_retry']['total'], 50)
        self.assertEqual(perf['comparison']['paired_question_count'], 1)
        self.assertEqual(perf['comparison']['original_total'], 80)
        self.assertEqual(perf['comparison']['latest_total'], 0)
        self.assertEqual(perf['comparison']['delta'], -80)


if __name__ == '__main__':
    unittest.main()
