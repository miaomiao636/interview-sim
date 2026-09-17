"""Resume advice must be useful without inventing candidate facts."""
import copy
import json
import unittest

from backend.preparation_models import validate_result, build_messages


def resume_output():
    return {
        'assessment': {'dimensions': {name: {'score': 6, 'comment': '已有行动，缺验证结果'}
            for name in ('clarity', 'structure', 'relevance', 'evidence')}},
        'suggestions': [], 'missing_information': [],
    }


def substantive_advice():
    return {'category': 'evidence', 'priority': 'high', 'title': '补充验证依据',
            'problem': '材料写了需求访谈，但没有说明结论如何被验证。',
            'action': '先写已验证的交付物；如果尚未验证，保留待验证边界，不填写虚构收益。',
            'questions': ['访谈结论由谁确认？有何可核实的交付物？'],
            'evidence': [{'source_id': 'resume', 'quote': '负责需求访谈'}]}


class ResumeAdvisorTests(unittest.TestCase):
    def setUp(self):
        self.resume = '工作描述：负责需求访谈，使用 Python\n  工具整理反馈。'
        self.inputs = {'kind': 'resume', 'job': {'jd': '需要需求分析与效果验证。'},
            'version_id': 'a' * 32, 'resume_version': {'id': 'a' * 32, 'resume': self.resume},
            'sources': {'resume': self.resume}, 'confirmed_facts': [],
            'provider': {'prompt_version': 'resume-advisor-v2'}}

    def test_cosmetic_only_response_is_not_a_complete_new_diagnosis(self):
        raw = resume_output()
        raw['suggestions'] = [{'target': '负责需求访谈', 'replacement': '负责需求访谈。', 'reason': '补标点'}]
        with self.assertRaises(ValueError):
            validate_result('resume', raw, self.inputs)

    def test_substantive_advice_is_retained_separate_from_automatic_edits(self):
        raw = resume_output()
        raw['advice'] = [substantive_advice()]
        result = validate_result('resume', raw, self.inputs)
        self.assertEqual(result['advice'][0]['action'], raw['advice'][0]['action'])
        self.assertEqual(result['suggestions'], [])

    def test_whitespace_only_anchor_is_restored_to_exact_original(self):
        raw = resume_output()
        raw['advice'] = [substantive_advice()]
        raw['suggestions'] = [{'target': '使用 Python 工具整理反馈',
            'replacement': '用 Python 工具归整反馈', 'reason': '突出交付动作'}]
        result = validate_result('resume', raw, self.inputs)
        item = result['suggestions'][0]
        self.assertTrue(item['applicable'])
        self.assertEqual(item['target'], '使用 Python\n  工具整理反馈')
        self.assertIn(item['target'], self.resume)

    def test_ambiguous_or_meaning_changed_anchors_are_not_fuzzy_adopted(self):
        for resume, target in [('负责  访谈；负责\n访谈', '负责 访谈'),
                               ('参与项目', '主导项目'), ('熟悉 C++', '熟悉 C#')]:
            inputs = copy.deepcopy(self.inputs)
            inputs['sources']['resume'] = inputs['resume_version']['resume'] = resume
            raw = resume_output()
            advice = substantive_advice()
            advice['evidence'] = [{'source_id': 'resume', 'quote': resume}]
            raw['advice'] = [advice]
            raw['suggestions'] = [{'target': target, 'replacement': '修改后的描述', 'reason': '测试边界'}]
            with self.subTest(target=target):
                self.assertFalse(validate_result('resume', raw, inputs)['suggestions'][0]['applicable'])

    def test_advice_cannot_quote_jd_as_candidate_experience_or_invent_quotes(self):
        raw = resume_output()
        for evidence in ([{'source_id': 'resume', 'quote': '提升效率90%'}],
                         [{'source_id': 'jd', 'quote': '不存在的岗位要求'}]):
            raw['advice'] = [{**substantive_advice(), 'evidence': evidence}]
            with self.assertRaises(ValueError):
                validate_result('resume', raw, self.inputs)

    def test_new_prompt_requests_diagnosis_not_only_polishing(self):
        system = build_messages('resume', self.inputs)[0]['content']
        self.assertIn('个人贡献', system)
        self.assertIn('内容取舍', system)
        self.assertIn('不能只', system)
        schema = json.loads(system.split('\n')[-1])
        self.assertIn('advice', schema['required'])
        self.assertEqual(schema['properties']['advice']['minItems'], 1)
