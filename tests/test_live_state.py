"""P3 state transitions, replay safety and delayed-provider regressions."""
import asyncio
import tempfile
import unittest
import uuid
import json
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from backend import config, store
from backend.main import app
from backend.routers import chat, review, plan


async def consume(response):
    parts = []
    async for part in response.body_iterator:
        parts.append(part.decode() if isinstance(part, bytes) else part)
    return ''.join(parts)


class LiveStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patcher = patch.object(config, 'SESSION_DIR', Path(self.temp.name))
        self.patcher.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1')
        self.sid = store.create_session('JD', '参与真实项目', 'HR', '标准')['id']
        store.update_session(self.sid, {'blueprint': [{'id': 1, 'question': '为何应聘？'}, {'id': 2, 'question': '项目怎么做？'}, {'id': 3, 'question': '如何验证？'}]})
        store.set_active_question(self.sid, 'q-1', '为何应聘？', 1)

    async def asyncTearDown(self):
        for module, name in ((chat, 'shutdown_question_jobs'), (review, 'shutdown_report_jobs')):
            if hasattr(module, name):
                await getattr(module, name)()
        await self.client.aclose()
        self.patcher.stop()
        self.temp.cleanup()

    def request(self, text='我的回答', **updates):
        active = store.get_session(self.sid)['active_question']
        payload = {'session_id': self.sid, 'asr_text': text, 'question_id': active['question_id'], 'attempt': active['attempt'], 'operation_id': str(uuid.uuid4())}
        payload.update(updates)
        return chat.ChatRequest(**payload)

    async def test_two_adaptive_followups_do_not_consume_blueprint_before_skip(self):
        async def model(*args, **kwargs):
            yield '你刚才提到的行动，具体如何落实？'
        with patch.object(chat, 'chat_stream', model):
            await consume(await chat.chat(self.request()))
            second = store.get_session(self.sid)['active_question']
            self.assertIsNone(second['blueprint_id'])
            self.assertEqual(second['question_kind'], 'adaptive')
            self.assertEqual(second['parent_question_id'], 'q-1')
            await consume(await chat.chat(self.request()))
        response = await self.client.post(f'/api/sessions/{self.sid}/skip', json={'question_id': 'q-3', 'attempt': 1, 'operation_id': str(uuid.uuid4()), 'reason': '暂时没有思路'})
        self.assertEqual(response.status_code, 200, response.text)
        next_question = response.json()['active_question']
        self.assertEqual(next_question['blueprint_id'], 2)
        self.assertEqual(next_question['question_id'], 'q-4')
        turns = store.get_session(self.sid)['turns']
        self.assertEqual([t['blueprint_id'] for t in turns], [1, None, None])

    async def test_concurrent_duplicate_submission_records_and_calls_only_once(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def model(*args, **kwargs):
            calls.append(1)
            entered.set()
            await release.wait()
            yield '请补充过程。'
        request = self.request()
        with patch.object(chat, 'chat_stream', model):
            original = await chat.chat(request)
            original_stream = asyncio.create_task(consume(original))
            await entered.wait()
            replay = await chat.chat(request)
            self.assertEqual(await consume(replay), '')
            release.set()
            await original_stream
        self.assertEqual(calls, [1])
        self.assertEqual(len(store.get_session(self.sid)['turns']), 1)
        conflicting = request.model_copy(update={'asr_text': '另一份内容'})
        with self.assertRaises(HTTPException) as error:
            await chat.chat(conflicting)
        self.assertEqual(error.exception.status_code, 409)

    async def test_failed_next_question_can_recover_without_resubmitting_answer(self):
        async def fail(*args, **kwargs):
            raise ConnectionError('private endpoint credential must not leak')
            yield ''
        request = self.request()
        with patch.object(chat, 'chat_stream', fail):
            await consume(await chat.chat(request))
        saved = store.get_session(self.sid)
        self.assertEqual(saved['next_question_job']['status'], 'failed')
        self.assertNotIn('credential', saved['next_question_job']['error'])
        self.assertEqual(len(saved['turns']), 1)
        async def succeed(*args, **kwargs):
            yield '你如何完成它？'
        operation = str(uuid.uuid4())
        with patch.object(chat, 'chat_stream', succeed):
            response = await self.client.post(f'/api/sessions/{self.sid}/next-question', json={'operation_id': operation})
            replay = await self.client.post(f'/api/sessions/{self.sid}/next-question', json={'operation_id': operation})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(len(store.get_session(self.sid)['turns']), 1)
        self.assertEqual(store.get_session(self.sid)['active_question']['question_id'], 'q-2')

    async def test_late_question_success_or_failure_cannot_reopen_ended_session(self):
        for fail in (False, True):
            if fail:
                self.sid = store.create_session('JD', '简历', 'HR', '标准')['id']
                store.set_active_question(self.sid, 'q-1', '问题？', 1)
            entered, release = asyncio.Event(), asyncio.Event()
            async def delayed(*args, **kwargs):
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                if fail:
                    raise ConnectionError('late error')
                yield '迟到的问题'
            with patch.object(chat, 'chat_stream', delayed):
                response = await chat.chat(self.request())
                reading = asyncio.create_task(consume(response))
                await entered.wait()
                await review.end_session(self.sid)
                release.set()
                await reading
            saved = store.get_session(self.sid)
            self.assertEqual(saved['status'], 'ended')
            self.assertIsNone(saved['active_question'])
            self.assertEqual(saved['next_question_job']['status'], 'cancelled')

    async def test_report_completion_cannot_be_reopened_by_late_question(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            yield '过期下一题'
        async def report(*args):
            return {}
        with patch.object(chat, 'chat_stream', delayed), patch.object(review, 'build_report', report):
            reading = asyncio.create_task(consume(await chat.chat(self.request())))
            await entered.wait()
            await review.start_review(self.sid)
            release.set()
            await reading
        self.assertEqual(store.get_session(self.sid)['status'], 'completed')
        self.assertIsNone(store.get_session(self.sid)['active_question'])

    async def test_retry_is_one_question_idempotent_and_keeps_old_report(self):
        store.record_answer(self.sid, '首次回答')
        store.update_session(self.sid, {'status': 'completed', 'review': {'score': {'total': 50}}, 'report_job': {'status': 'completed', 'id': 'old', 'generation': 1}})
        first = await review.retry_question(self.sid, review.RetryRequest(question_id='q-1'))
        second = await review.retry_question(self.sid, review.RetryRequest(question_id='q-1'))
        self.assertEqual(first, second)
        self.assertNotIn('focus', first)
        self.assertEqual((await review.get_session(self.sid))['active_question']['attempt'], 2)
        with patch.object(chat, 'chat_stream', side_effect=AssertionError('retry must not generate ordinary question')):
            await consume(await chat.chat(self.request('第二次回答')))
        saved = store.get_session(self.sid)
        self.assertEqual(saved['status'], 'interview_finished')
        self.assertIsNone(saved['active_question'])
        self.assertEqual([t['attempt'] for t in saved['turns']], [1, 2])
        self.assertTrue(saved['review_is_stale'])
        self.assertEqual(saved['previous_reviews'][0]['report']['score']['total'], 50)
        retry = await review.retry_question(self.sid, review.RetryRequest(question_id='q-1'))
        skipped = await self.client.post(f'/api/sessions/{self.sid}/skip', json={'question_id': 'q-1', 'attempt': retry['attempt'], 'operation_id': str(uuid.uuid4())})
        self.assertTrue(skipped.json()['finished'])
        self.assertEqual(len(store.get_session(self.sid)['turns']), 3)

    async def test_retry_cannot_replace_another_active_question(self):
        store.record_answer(self.sid, '原回答')
        store.set_active_question(self.sid, 'q-2', '未回答的问题', 2)
        response = await self.client.post(f'/api/sessions/{self.sid}/retry', json={'question_id': 'q-1'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(store.get_session(self.sid)['active_question']['question_id'], 'q-2')

    async def test_orphan_report_recovery_is_consistent_for_retry_end_and_chat(self):
        store.update_session(self.sid, {'report_job': {'status': 'running'}, 'status': 'reviewing'})
        ended = await self.client.post(f'/api/sessions/{self.sid}/end')
        self.assertEqual(ended.status_code, 200)
        retry = await self.client.post(f'/api/sessions/{self.sid}/retry', json={'question_id': 'q-1'})
        self.assertEqual(retry.status_code, 200)
        request = self.request()
        with patch.object(chat, 'chat_stream', side_effect=AssertionError('single retry')):
            response = await self.client.post('/api/chat', json=request.model_dump())
        self.assertEqual(response.status_code, 200)

    async def test_replaced_report_callbacks_do_not_overwrite_new_generation(self):
        store.record_answer(self.sid, '第一次回答')
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def model(session, call, work, progress):
            calls.append(session['turns'][0]['answer'])
            if len(calls) == 1:
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                progress('迟到进度', 0, False)
                raise ConnectionError('迟到失败')
            return {}
        with patch.object(review, 'build_report', model):
            old = review.start_review(self.sid)
            await entered.wait()
            changed = store.get_session(self.sid)['turns']
            changed[0]['answer'] = '变更后的回答'
            store.update_session(self.sid, {'turns': changed})
            new = review.start_review(self.sid)
            self.assertIsNot(old, new)
            await new
            latest = store.get_session(self.sid)['report_job']
            release.set()
            await old
        final = store.get_session(self.sid)
        self.assertEqual(final['status'], 'completed')
        self.assertEqual(final['report_job']['generation'], latest['generation'])
        self.assertEqual(final['report_job']['id'], latest['id'])

    async def test_session_id_is_validated_before_filesystem_access(self):
        with self.assertRaises(ValueError):
            store.get_session('../outside')
        response = await self.client.post('/api/chat', json={'session_id': '../outside', 'question_id': 'q-1', 'attempt': 1, 'operation_id': str(uuid.uuid4()), 'asr_text': 'x'})
        self.assertEqual(response.status_code, 422)

    async def test_shutdown_before_producer_first_schedule_still_closes_stream(self):
        with patch.object(chat, 'chat_stream', side_effect=AssertionError('never scheduled')):
            response = await chat.chat(self.request())
            await chat.shutdown_question_jobs()
            body = await asyncio.wait_for(consume(response), timeout=0.2)
        self.assertEqual(body, '')
        self.assertEqual(store.get_session(self.sid)['next_question_job']['status'], 'interrupted')

    async def test_generic_update_cannot_reopen_terminal_session(self):
        store.end_session(self.sid)
        ended = store.get_session(self.sid)
        with self.assertRaises(store.SessionConflict):
            store.update_session(self.sid, {'status': 'interviewing'})
        self.assertEqual(store.get_session(self.sid), ended)
        self.assertIsNone(store.set_active_question(self.sid, 'q-2', '不应再出现的问题', 2))

    async def test_crash_after_saved_answer_before_job_is_explicitly_recoverable(self):
        store.submit_answer(self.sid, 'q-1', 1, 'saved-before-crash', '已保存的真实回答')
        with patch.object(chat, 'chat_stream', side_effect=AssertionError('GET must not run model')):
            loaded = (await self.client.get(f'/api/sessions/{self.sid}')).json()
        self.assertEqual(loaded['next_question_job']['status'], 'interrupted')
        self.assertEqual(len(loaded['turns']), 1)
        async def model(*args, **kwargs):
            yield '请补充具体行动。'
        with patch.object(chat, 'chat_stream', model):
            response = await self.client.post(f'/api/sessions/{self.sid}/next-question', json={'operation_id': 'recover-crash-op'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(store.get_session(self.sid)['turns']), 1)
        self.assertEqual(store.get_session(self.sid)['active_question']['question_id'], 'q-2')

    async def test_plan_assigns_unique_server_ids_to_missing_or_duplicate_model_ids(self):
        async def model(**kwargs):
            return json.dumps({'jd_parsed': {}, 'gap_analysis': [], 'blueprint': [{'question': '第一题'}, {'id': 8, 'question': '第二题'}, {'id': 8, 'question': '第三题'}]})
        with patch.object(plan, 'chat_once', model):
            result = await plan.plan(plan.PlanRequest(jd='JD', resume='简历'))
        self.assertEqual([q['id'] for q in result.blueprint], [1, 2, 3])
        self.assertEqual(result.active_question['blueprint_id'], result.blueprint[0]['id'])
        skipped = await self.client.post(f'/api/sessions/{result.session_id}/skip', json={'question_id': 'q-1', 'attempt': 1, 'operation_id': 'skip-canonical-plan'})
        self.assertEqual(skipped.json()['active_question']['blueprint_id'], 2)

    async def test_late_plan_cannot_reopen_explicitly_ended_session_or_retry_model(self):
        for invalid in (False, True):
            entered, release = asyncio.Event(), asyncio.Event()
            created, calls = [], []
            original = store.create_session
            def capture(*args, **kwargs):
                session = original(*args, **kwargs)
                created.append(session['id'])
                return session
            async def delayed(**kwargs):
                calls.append(kwargs)
                entered.set()
                await release.wait()
                return 'invalid-json' if invalid else json.dumps({'jd_parsed': {}, 'gap_analysis': [], 'blueprint': [{'id': 1, 'question': '迟到首题'}]})
            with patch.object(store, 'create_session', side_effect=capture), patch.object(plan, 'chat_once', side_effect=delayed):
                planning = asyncio.create_task(plan.plan(plan.PlanRequest(jd='JD', resume='简历')))
                await asyncio.wait_for(entered.wait(), 3)
                await review.end_session(created[0])
                release.set()
                with self.assertRaises(HTTPException) as error:
                    await planning
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(len(calls), 1)
            self.assertEqual(store.get_session(created[0])['status'], 'ended')
            self.assertIsNone(store.get_session(created[0])['active_question'])
