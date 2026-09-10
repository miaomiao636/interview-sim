import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend import config, store
from backend.main import app
from backend.routers.review import start_review, review_job_status, SCORE_WEIGHTS


def feedback():
    return json.dumps({'question_feedback':[{'question_id':'q-1','attempt':1,'score':6,'evidence_quotes':['需求访谈'],'coaching_tip':'补充具体结果'}]}, ensure_ascii=False)


def summary():
    return json.dumps({'score':{'dimensions':{name:{'score':maximum/2,'comment':'有待提升'} for name,maximum in SCORE_WEIGHTS.items()},'conclusion':'继续练习'},'interview_tips':['补充具体结果'],'practice_plan':[]})


class ReportRecoveryTests(unittest.TestCase):
    def test_malformed_model_report_returns_actionable_error_not_bare_500(self):
        with tempfile.TemporaryDirectory() as root, patch.object(config, 'SESSION_DIR', Path(root)):
            session = store.create_session('JD', '真实简历', 'HR', '标准')
            store.set_active_question(session['id'], 'q-1', '做了什么？', 1)
            store.record_answer(session['id'], '我组织过一次需求访谈。')
            async def malformed(**kwargs):
                # The real failure: model output arrives, then JSON parsing fails.
                return '{"question_feedback": [{"question_id":"q-1" "score": 7}]}'
            with patch('backend.routers.review.chat_once', malformed):
                response = TestClient(app, raise_server_exceptions=False).post('/api/review', json={'session_id':session['id']})
            self.assertEqual(response.status_code, 502)
            self.assertIn('格式', response.json()['detail'])
            self.assertEqual(len(store.get_session(session['id'])['turns']), 1)


class ReportJobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(config, 'SESSION_DIR', Path(self.temp.name))
        self.patch.start()
        self.sid = store.create_session('JD', '真实简历', 'HR', '标准')['id']
        store.set_active_question(self.sid, 'q-1', '做了什么？', 1)
        store.record_answer(self.sid, '我组织过需求访谈。')

    async def asyncTearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    async def test_resume_reuses_feedback_after_summary_format_failure(self):
        responses = iter([feedback(), '{broken', '{broken', summary()])
        calls = []
        async def model(**kwargs):
            calls.append(kwargs)
            return next(responses)
        with patch('backend.routers.review.chat_once', model):
            first = start_review(self.sid)
            self.assertIs(start_review(self.sid), first)
            self.assertIsNone(await first)
            failed = await review_job_status(self.sid)
            self.assertEqual(failed['status'], 'failed')
            self.assertEqual(failed['completed'], 1)
            self.assertIn('格式', failed['error'])
            result = await start_review(self.sid)
        self.assertEqual(len(calls), 4)
        self.assertEqual(result['question_feedback'][0]['answer'], '我组织过需求访谈。')
        self.assertEqual((await review_job_status(self.sid))['status'], 'completed')

    async def test_server_restart_marks_orphaned_job_resumable(self):
        store.update_session(self.sid, {'report_job':{'status':'running','started_at':0,'completed':1,'total':2}})
        status = await review_job_status(self.sid)
        self.assertEqual(status['status'], 'failed')
        self.assertIn('重新启动', status['error'])

    async def test_new_answer_invalidates_previous_report_cache(self):
        responses = iter([feedback(), summary(), feedback(), summary()])
        calls = []
        async def model(**kwargs):
            calls.append(kwargs)
            return next(responses)
        with patch('backend.routers.review.chat_once', model):
            await start_review(self.sid)
            session = store.get_session(self.sid)
            session['turns'][0]['answer'] = '修改后的真实回答'
            store.update_session(self.sid, {'turns':session['turns']})
            result = await start_review(self.sid)
        self.assertEqual(len(calls), 4)
        self.assertEqual(result['question_feedback'][0]['answer'], '修改后的真实回答')
