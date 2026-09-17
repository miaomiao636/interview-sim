"""Independent HTTP probes for stale clients and question provenance."""
import asyncio
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
from backend import config, store
from backend.main import app
from backend.routers import review


class LiveAPIContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        change = patch.object(config, 'SESSION_DIR', Path(self.temp.name))
        change.start()
        self.addCleanup(change.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1')
        self.addAsyncCleanup(self.client.aclose)
        self.sid = store.create_session('虚构工具工程师', '曾组织需求访谈。', 'HR', '标准')['id']
        store.update_session(self.sid, {'blueprint': [
            {'id': 1, 'dimension': '动机', 'question': '为什么选择这个岗位？'},
            {'id': 2, 'dimension': '项目', 'question': '介绍实际项目。'},
            {'id': 3, 'dimension': '协作', 'question': '怎样与他人协作？'},
        ]})
        store.set_active_question(self.sid, 'q-1', '为什么选择这个岗位？', 1)

    def answer(self, question_id='q-1', attempt=1, text='希望开发业务工具。', operation_id=None):
        return {'session_id': self.sid, 'question_id': question_id, 'attempt': attempt,
                'asr_text': text, 'operation_id': operation_id or str(uuid.uuid4())}

    async def test_legacy_http_cannot_silently_answer_new_question(self):
        before = store.get_session(self.sid)
        async def fake(*args, **kwargs):
            yield '未经允许的问题'
        with patch('backend.routers.chat.chat_stream', fake):
            response = await self.client.post('/api/chat', json={'session_id': self.sid, 'asr_text': '旧页面回答'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(store.get_session(self.sid), before)

    async def test_adaptive_turns_do_not_consume_next_blueprint_item(self):
        async def fake(*args, **kwargs):
            yield '你刚才所说的需求访谈，能举一个具体例子吗？'
        with patch('backend.routers.chat.chat_stream', fake):
            for number in (1, 2):
                response = await self.client.post('/api/chat', json=self.answer(f'q-{number}'))
                self.assertEqual(response.status_code, 200, response.text)
                active = store.get_session(self.sid)['active_question']
                self.assertEqual(active['question_kind'], 'adaptive')
                self.assertIsNone(active['blueprint_id'])
            response = await self.client.post(f'/api/sessions/{self.sid}/skip', json={
                'question_id': 'q-3', 'attempt': 1, 'operation_id': str(uuid.uuid4()), 'reason': '暂时想不到例子',
            })
        self.assertEqual(response.status_code, 200, response.text)
        active = response.json()['active_question']
        self.assertEqual(active['question'], '介绍实际项目。')
        self.assertEqual(active['blueprint_id'], 2)
        self.assertEqual(active['question_id'], 'q-4')
        turns = store.get_session(self.sid)['turns']
        self.assertEqual([turn['blueprint_id'] for turn in turns], [1, None, None])

    async def test_completed_operation_replay_does_not_recall_model(self):
        calls = []
        async def fake(*args, **kwargs):
            calls.append(1)
            yield '请具体说明你的个人工作。'
        body = self.answer()
        with patch('backend.routers.chat.chat_stream', fake):
            first = await self.client.post('/api/chat', json=body)
            replay = await self.client.post('/api/chat', json=body)
            conflict = await self.client.post('/api/chat', json={**body, 'asr_text': '另一份内容'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.text, first.text)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(calls, [1])
        self.assertEqual(len(store.get_session(self.sid)['turns']), 1)

    async def test_failed_next_question_is_explicitly_recoverable(self):
        async def broken(*args, **kwargs):
            raise RuntimeError('synthetic transport failure')
            yield ''
        with patch('backend.routers.chat.chat_stream', broken):
            try:
                await self.client.post('/api/chat', json=self.answer())
            except RuntimeError:
                pass  # ASGITransport propagates failures after streaming headers.
        saved = store.get_session(self.sid)
        self.assertEqual(saved['next_question_job']['status'], 'failed')
        self.assertEqual(len(saved['turns']), 1)
        calls = []
        async def restored(*args, **kwargs):
            calls.append(1)
            yield '具体承担了哪一部分工作？'
        body = {'operation_id': str(uuid.uuid4())}
        with patch('backend.routers.chat.chat_stream', restored):
            first = await self.client.post(f'/api/sessions/{self.sid}/next-question', json=body)
            replay = await self.client.post(f'/api/sessions/{self.sid}/next-question', json=body)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(calls, [1])
        self.assertEqual(len(store.get_session(self.sid)['turns']), 1)
        self.assertEqual(store.get_session(self.sid)['active_question']['question_id'], 'q-2')

    async def test_skip_wrong_attempt_does_not_write(self):
        before = store.get_session(self.sid)
        response = await self.client.post(f'/api/sessions/{self.sid}/skip', json={
            'question_id': 'q-1', 'attempt': 2, 'operation_id': str(uuid.uuid4()),
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(store.get_session(self.sid), before)

    async def test_invalid_id_rejected_before_any_file_mutation(self):
        before = {p.name: p.read_bytes() for p in config.SESSION_DIR.glob('*.json')}
        for suffix in ('end', 'skip', 'retry', 'next-question'):
            payload = {'question_id': 'q-1', 'attempt': 1, 'operation_id': str(uuid.uuid4())}
            response = await self.client.post(f'/api/sessions/not-a-session/{suffix}', json=payload)
            self.assertEqual(response.status_code, 422, (suffix, response.text))
        self.assertEqual({p.name: p.read_bytes() for p in config.SESSION_DIR.glob('*.json')}, before)

    async def test_late_question_after_report_completed_does_not_reopen_session(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            yield '报告已经完成后才迟到的问题'
        async def report_stub(*args, **kwargs):
            return {}
        with patch('backend.routers.chat.chat_stream', delayed), patch('backend.routers.review.build_report', report_stub):
            sending = asyncio.create_task(self.client.post('/api/chat', json=self.answer()))
            await asyncio.wait_for(entered.wait(), 3)
            try:
                await review.start_review(self.sid)
            finally:
                release.set()
                await asyncio.wait_for(sending, 3)
        saved = store.get_session(self.sid)
        self.assertEqual(saved['status'], 'completed')
        self.assertIsNone(saved['active_question'])
        self.assertEqual(len(saved['turns']), 1)

    async def test_stale_report_error_cannot_replace_new_job_state(self):
        store.record_answer(self.sid, '我组织了需求访谈。')
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            raise RuntimeError('synthetic old generation failure')
        with patch('backend.routers.review.build_report', delayed):
            task = review.start_review(self.sid)
            await asyncio.wait_for(entered.wait(), 3)
            previous = store.get_session(self.sid)['report_job']
            replacement = {**previous, 'id': str(uuid.uuid4()), 'generation': previous.get('generation', 0) + 1,
                           'status': 'completed'}
            store.update_session(self.sid, {'report_job': replacement, 'status': 'completed', 'review': {'synthetic': True}})
            release.set()
            await task
        saved = store.get_session(self.sid)
        self.assertEqual(saved['report_job'], replacement)
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual(saved['review'], {'synthetic': True})
