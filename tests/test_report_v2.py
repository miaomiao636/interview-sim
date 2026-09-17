"""Report boundaries: actual evidence, stable first attempts, separate preparation."""
import copy
import json
import unittest
from unittest.mock import patch

from backend import report_pipeline
from backend.structured import StructuredOutputError

KEYS = ('relevance', 'evidence', 'professional_content', 'expression')


def turn(qid='q-1', attempt=1, answer='我负责需求访谈。', **kwargs):
    return {'question_id': qid, 'attempt': attempt, 'answer': answer,
            'question': '请介绍你的贡献', 'status': 'answered', 'skip_reason': '',
            'question_kind': 'retry' if attempt > 1 else 'blueprint', 'parent_question_id': qid if attempt > 1 else None,
            'blueprint_id': 1, **kwargs}


def feedback(t, score=6):
    unanswered = t.get('status') == 'unanswered'
    return {'question_id': t['question_id'], 'attempt': t['attempt'],
            'dimensions': {k: {'score': 0 if unanswered else score, 'comment': '依据原回答'} for k in KEYS},
            'confidence': 'medium', 'evidence_quotes': [] if unanswered else [t['answer'][:80]],
            'covered_points': [] if unanswered else ['说明个人行动'], 'missed_points': ['可验证结果'],
            'question_explanation': '了解个人贡献', 'coaching_tip': '补充真实结果',
            'improved_answer_outline': ['个人行动', '真实结果'], 'reason_analysis': '原因未知' if unanswered else '结果证据不足'}


def session(turns=None):
    return {'id': 'abcdef01', 'config': {'jd': '负责产品验证', 'resume': '我负责需求访谈。', 'company': '示例公司'},
            'turns': turns if turns is not None else [turn()], 'blueprint': [{'id': 1}, {'id': 2}, {'id': 3}]}


class ReportV2Tests(unittest.IsolatedAsyncioTestCase):
    async def build(self, s, mutate=None, work=None, summary_mutate=None, score=6):
        calls = []
        async def call(**kwargs):
            content = kwargs['messages'][1]['content']
            calls.append(content)
            if '本批作答：' in content:
                batch = json.loads(content.split('本批作答：', 1)[1])
                data = {'question_feedback': [feedback(t, score) for t in batch]}
                if mutate:
                    mutate(data)
            else:
                data = {'interview_tips': ['只补充真实经历'], 'practice_plan': []}
                if summary_mutate:
                    summary_mutate(data)
            return json.dumps(data, ensure_ascii=False)
        report = await report_pipeline.build_report(s, call, work if work is not None else {}, lambda *args: None)
        return report, calls

    async def test_first_and_retry_are_separate_with_paired_comparison(self):
        first, _ = await self.build(session([turn(), turn('q-2', answer='', status='unanswered')]))
        self.assertEqual(first['schema_version'], 2)
        self.assertEqual(first['interview_performance']['first_attempt']['total'], 30)
        self.assertEqual(first['interview_performance']['first_attempt']['question_count'], 2)
        self.assertNotIn('score', first)
        s = session([turn(), turn('q-2', answer='', status='unanswered'), turn(attempt=2, answer='我组织访谈并记录验证结果。')])
        s['previous_reviews'] = [{'report': first}]
        second, calls = await self.build(s, score=8)
        perf = second['interview_performance']
        self.assertEqual(perf['first_attempt']['total'], 30)
        self.assertEqual(perf['latest_retry']['total'], 80)
        self.assertEqual(perf['comparison']['original_total'], 60)
        self.assertEqual(perf['comparison']['delta'], 20)
        batch_calls = [c for c in calls if '本批作答：' in c]
        self.assertEqual(len(batch_calls), 1)
        self.assertEqual(len(json.loads(batch_calls[0].split('本批作答：', 1)[1])), 1)

    async def test_forged_quote_never_reaches_summary_or_stage_cache(self):
        work = {}
        with self.assertRaises(StructuredOutputError):
            await self.build(session(), lambda d: d['question_feedback'][0].update(evidence_quotes=['伪造事实']), work)
        self.assertFalse(work.get('stages'))

    async def test_answered_high_score_without_evidence_rejected(self):
        with self.assertRaises(StructuredOutputError):
            await self.build(session(), lambda d: d['question_feedback'][0].update(evidence_quotes=[]))
        with self.assertRaises(StructuredOutputError):
            await self.build(session([turn(answer='我 负责需求访谈')]), lambda d: d['question_feedback'][0].update(evidence_quotes=[' ']))

    async def test_duplicate_wrong_attempt_extra_fields_and_nonfinite_rejected(self):
        mutations = [
            lambda d: d['question_feedback'].append(copy.deepcopy(d['question_feedback'][0])),
            lambda d: d['question_feedback'][0].update(attempt=2),
            lambda d: d['question_feedback'][0].update(answer='模型改写的答案'),
            lambda d: d['question_feedback'][0]['dimensions']['evidence'].update(score=float('nan')),
            lambda d: d['question_feedback'][0]['dimensions']['evidence'].update(score=True),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.assertRaises(StructuredOutputError):
                await self.build(session(), mutate)

    async def test_unanswered_cannot_receive_positive_score_or_invent_reason(self):
        s = session([turn(answer='', status='unanswered')])
        with self.assertRaises(StructuredOutputError):
            await self.build(s, lambda d: d['question_feedback'][0]['dimensions']['evidence'].update(score=5))
        report, _ = await self.build(s, lambda d: d['question_feedback'][0].update(reason_analysis='他一定很懒'))
        self.assertNotIn('懒', report['question_feedback'][0]['reason_analysis'])
        self.assertEqual(report['interview_performance']['first_attempt']['total'], 0)

    async def test_missing_resume_assessment_is_null_and_unasked_requirements_not_scored(self):
        report, _ = await self.build(session())
        self.assertEqual(report['resume_quality']['status'], 'not_assessed')
        self.assertIsNone(report['resume_quality']['assessment'])
        self.assertEqual(report['requirement_coverage']['status'], 'not_assessed')
        self.assertEqual(report['interview_performance']['first_attempt']['question_count'], 1)

    async def test_no_actual_turns_are_not_evaluated_as_zero(self):
        report, _ = await self.build(session([]))
        self.assertIsNone(report['interview_performance']['first_attempt']['total'])

    async def test_oversized_input_fails_before_any_remote_request(self):
        calls = []
        async def remote(**kwargs):
            calls.append(kwargs)
            raise AssertionError('Oversized input must not be billed')
        s = session([turn(answer='长' * 180001)])
        with self.assertRaises(StructuredOutputError):
            await report_pipeline.build_report(s, remote, {}, lambda *args: None)
        self.assertEqual(calls, [])

    async def test_resume_snapshot_is_bound_to_exact_materials(self):
        from backend.preparation_store import launch_material_fingerprint
        s = session()
        s['config'].update(preset_id='preset', resume_version_id='v1')
        assessment = {'rubric_version': 'resume-v1', 'total': 70, 'weights': {k: 25 for k in ('clarity', 'structure', 'relevance', 'evidence')},
                      'dimensions': {k: {'score': 7, 'comment': '依据文字'} for k in ('clarity', 'structure', 'relevance', 'evidence')}}
        s['preparation_snapshot'] = {'preset_id': 'preset', 'resume_version_id': 'v1',
            'material_fingerprint': launch_material_fingerprint(s['config']), 'resume_assessment': assessment,
            'requirements': [{'id': 'req1', 'requirement': '验证', 'source_quote': '负责产品验证'}],
            'provenance': {'resume': {'task_id': 'task1'}}}
        report, _ = await self.build(s)
        self.assertEqual(report['resume_quality']['assessment']['total'], 70)
        self.assertEqual(report['requirement_coverage']['requirements'][0]['interview_status'], 'not_observed')
        s['config']['resume'] += '已修改'
        changed, _ = await self.build(s)
        self.assertEqual(changed['resume_quality']['status'], 'not_assessed')
        self.assertEqual(changed['requirement_coverage']['status'], 'not_assessed')

    async def test_cached_feedback_is_revalidated_and_totals_recomputed(self):
        report, _ = await self.build(session())
        cached = copy.deepcopy(report)
        cached['question_feedback'][0]['score'] = 10
        s = session()
        s['review'] = cached
        rebuilt, calls = await self.build(s, score=8)
        self.assertEqual(rebuilt['interview_performance']['first_attempt']['total'], 60)
        self.assertFalse(any('本批作答：' in c for c in calls))
        s['review']['question_feedback'][0]['evidence_quotes'] = ['伪造缓存']
        rebuilt, calls = await self.build(s, score=8)
        self.assertEqual(rebuilt['interview_performance']['first_attempt']['total'], 80)
        self.assertTrue(any('本批作答：' in c for c in calls))

    async def test_provider_or_actual_question_change_invalidates_turn_cache(self):
        report, _ = await self.build(session())
        s = session()
        s['review'] = report
        with patch('backend.config.LLM_MODEL_PRO', 'synthetic-other-model'):
            changed, calls = await self.build(s, score=8)
        self.assertTrue(any('本批作答：' in c for c in calls))
        self.assertEqual(changed['interview_performance']['first_attempt']['total'], 80)
        s['turns'][0]['question'] = '另一个实际问题'
        _, calls = await self.build(s)
        self.assertTrue(any('本批作答：' in c for c in calls))

    async def test_legacy_score_is_not_promoted_and_unknown_plan_question_rejected(self):
        s = session()
        s['review'] = {'score': {'total': 100}, 'question_feedback': [{'question_id': 'q-1', 'attempt': 1, 'score': 10}]}
        report, calls = await self.build(s)
        self.assertEqual(report['interview_performance']['first_attempt']['total'], 60)
        self.assertTrue(any('本批作答：' in c for c in calls))
        with self.assertRaises(StructuredOutputError):
            await self.build(s, summary_mutate=lambda d: d.update(practice_plan=[{'question_id': 'invented', 'focus': 'test', 'reason': 'test'}]))
