import json
import tempfile
import unittest
from tests.test_question_policy import envelope
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend import config, store
from backend.main import app
from backend.routers.plan import plan, PlanRequest
from backend.routers.chat import chat, ChatRequest


class JobContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_company_and_run_choices_reach_plan_and_adaptive_chat(self):
        with tempfile.TemporaryDirectory() as root, patch.object(config, 'SESSION_DIR', Path(root)):
            plan_messages, chat_messages = [], []
            async def fake_plan(**kwargs):
                plan_messages.extend(kwargs['messages'])
                return json.dumps({'jd_parsed':{},'gap_analysis':[], 'blueprint':[{'id':1,'question':'如何服务内容团队？'}]})
            with patch('backend.routers.plan.chat_once', fake_plan):
                result = await plan(PlanRequest(jd='开发内容工具',resume='参与需求分析',company='示例公司',company_context='服务内容创作者',persona='CEO',interviewer_gender='男性',voice='白桦',difficulty='压力'))
            saved = store.get_session(result.session_id)
            self.assertEqual(saved['config']['company'], '示例公司')
            self.assertEqual(saved['config']['interviewer_gender'], '男性')
            self.assertIn('示例公司', plan_messages[1]['content'])
            async def fake_chat(messages, **kwargs):
                chat_messages.extend(messages)
                yield envelope('你刚才提到的访谈，怎样帮助内容创作者？', 1, 'followup', 'evidence')
            with patch('backend.routers.chat.chat_stream', fake_chat):
                response = await chat(ChatRequest(session_id=result.session_id,asr_text='我访谈过三位创作者。', question_id=result.active_question['question_id'], attempt=result.active_question['attempt'], operation_id='job-context-answer'))
                text = ''.join([part async for part in response.body_iterator])
            self.assertIn('访谈', text)
            self.assertIn('示例公司', chat_messages[0]['content'])
            self.assertIn('服务内容创作者', chat_messages[0]['content'])
            self.assertIn('CEO', chat_messages[0]['content'])
            self.assertEqual(sum(m['content']=='我访谈过三位创作者。' for m in chat_messages), 1)

    async def test_preset_structured_fields_round_trip_and_legacy_preservation(self):
        with tempfile.TemporaryDirectory() as root, patch.object(config,'DATA_DIR',Path(root)):
            client = TestClient(app)
            body = {'name':'示例岗位','target_role':'AI工程师','company':'示例公司','jd':'JD','resume':'简历','city':'无锡','salary_min':7,'salary_max':16,'salary_months':13,'education':'本科','responsibilities':'实现工具','requirements':'Python','company_context':'服务内容团队','source_jd':'原始岗位文字'}
            result = client.post('/api/presets',json=body)
            self.assertEqual(result.status_code,201)
            self.assertEqual(result.json()['salary_min'],7)
            self.assertEqual(result.json()['source_jd'],'原始岗位文字')
            bad = client.post('/api/presets',json={**body,'salary_max':2})
            self.assertEqual(bad.status_code,422)
            legacy = client.post('/api/presets',json={'name':'旧岗位','target_role':'后端','jd':'旧JD','resume':'旧简历'})
            self.assertEqual(legacy.status_code,201)
