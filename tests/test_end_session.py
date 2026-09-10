import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from backend import config, store
from backend.main import app
from backend.routers.review import ReviewRequest, review


class EndSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = patch.object(config, 'SESSION_DIR', Path(self.temp.name))
        self.directory.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1')
        self.session = store.create_session('测试岗位', '测试简历', 'HR', '标准')
        self.sid = self.session['id']
        store.set_active_question(self.sid, 'q-1', '请介绍你的经历', 1)

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.stop()
        self.temp.cleanup()

    async def finish(self):
        return await self.client.post(f'/api/sessions/{self.sid}/end')

    async def test_end_saves_records_without_calling_model_and_is_idempotent(self):
        store.record_answer(self.sid, '我负责需求访谈。')
        store.set_active_question(self.sid, 'q-2', '如何验证结果？', 2)
        with patch('backend.routers.review.chat_once', new_callable=AsyncMock) as model:
            first = await self.finish()
            second = await self.finish()
            model.assert_not_awaited()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), second.json())
        saved = store.get_session(self.sid)
        self.assertEqual(saved['status'], 'ended')
        self.assertTrue(saved['ended_at'])
        self.assertEqual(saved['turns'][0]['answer'], '我负责需求访谈。')
        self.assertEqual(len(saved['turns']), 2)
        self.assertEqual(saved['turns'][1]['status'], 'unanswered')
        self.assertIsNone(saved['active_question'])
        self.assertIsNone(saved['review'])
        self.assertNotIn('report_job', saved)
        self.assertEqual(store.list_sessions()[0]['status'], 'ended')

    async def test_can_end_after_next_question_connection_failure(self):
        store.record_answer(self.sid, '已发送的回答')
        response = await self.finish()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(store.get_session(self.sid)['turns']), 1)
        self.assertEqual(store.get_session(self.sid)['status'], 'ended')

    async def test_can_end_before_any_answer(self):
        self.assertEqual((await self.finish()).status_code, 200)
        self.assertEqual(store.get_session(self.sid)['turns'][0]['status'], 'unanswered')

    async def test_missing_session_returns_404(self):
        self.assertEqual((await self.client.post('/api/sessions/00000000/end')).status_code, 404)

    async def test_running_report_is_not_silently_cancelled(self):
        store.update_session(self.sid, {'report_job': {'status': 'running'}, 'status': 'reviewing'})
        self.assertEqual((await self.finish()).status_code, 409)
        self.assertEqual(store.get_session(self.sid)['status'], 'reviewing')
        self.assertIsNotNone(store.get_session(self.sid)['active_question'])

    async def test_completed_report_is_not_overwritten(self):
        store.record_answer(self.sid, '已回答')
        store.update_session(self.sid, {'status': 'completed', 'review': {'score': {'total': 60}}})
        before = store.get_session(self.sid)
        self.assertEqual((await self.finish()).status_code, 200)
        self.assertEqual(store.get_session(self.sid), before)

    async def test_late_question_does_not_reopen_ended_session(self):
        await self.finish()
        self.assertIsNone(store.set_active_question(self.sid, 'q-2', '延迟返回的问题', 2))
        self.assertEqual(store.get_session(self.sid)['status'], 'ended')
        self.assertIsNone(store.get_session(self.sid)['active_question'])

    async def test_report_can_be_generated_later_from_saved_answers(self):
        store.record_answer(self.sid, '我负责需求访谈。')
        self.assertEqual((await self.finish()).status_code, 200)
        with patch('backend.routers.review.build_report', new_callable=AsyncMock, return_value={}) as model:
            await review(ReviewRequest(session_id=self.sid))
        self.assertEqual(model.await_args.args[0]['turns'][0]['answer'], '我负责需求访谈。')
        self.assertEqual(store.get_session(self.sid)['status'], 'completed')
        self.assertIsNotNone(store.get_session(self.sid)['review'])


if __name__ == '__main__':
    unittest.main()
