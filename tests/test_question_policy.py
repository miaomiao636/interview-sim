"""Regression coverage for question progression; no real models or user data."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException

from backend import config, store
from backend.routers import chat


def envelope(question, blueprint_id, kind='blueprint', followup_focus=None):
    return json.dumps({'question': question, 'blueprint_id': blueprint_id,
                       'kind': kind, 'followup_focus': followup_focus}, ensure_ascii=False)


async def consume(response):
    return ''.join([chunk.decode() if isinstance(chunk, bytes) else chunk
                    async for chunk in response.body_iterator])


class QuestionPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patcher = patch.object(config, 'SESSION_DIR', Path(self.temp.name))
        self.patcher.start()
        self.sid = store.create_session('虚构 JD', '虚构简历', 'HR', '标准')['id']
        self.questions = [
            {'id': 1, 'question': '为什么申请这个岗位？'},
            {'id': 2, 'question': '请介绍你遇到的技术难题，以及具体如何解决？'},
            {'id': 3, 'question': '你会如何验证自动化流程的输出质量？'},
        ]
        store.update_session(self.sid, {'blueprint': self.questions})
        store.set_active_question(self.sid, 'q-1', self.questions[0]['question'], 1)

    async def asyncTearDown(self):
        await chat.shutdown_question_jobs()
        self.patcher.stop()
        self.temp.cleanup()

    def request(self, operation='policy-answer-op'):
        active = store.get_session(self.sid)['active_question']
        return chat.ChatRequest(session_id=self.sid, question_id=active['question_id'],
                                attempt=active['attempt'], operation_id=operation,
                                asr_text='我参与了接口联调，并保留了测试记录。')

    async def test_legacy_dynamic_question_is_not_repeated_after_skip(self):
        store.record_answer(self.sid, '我的动机')
        store.set_active_question(self.sid, 'q-2', self.questions[1]['question'], None)
        result = store.skip_question(self.sid, 'q-2', '暂时想不到')
        self.assertEqual(result['active_question']['blueprint_id'], 3)
        self.assertNotEqual(result['active_question']['question'], self.questions[1]['question'])

    async def test_dynamic_blueprint_mapping_is_saved_and_skip_moves_forward(self):
        async def model(*args, **kwargs):
            yield envelope('讲讲你在接口联调中解决技术问题的过程？', 2)
        with patch.object(chat, 'chat_stream', model):
            text = await consume(await chat.chat(self.request()))
        active = store.get_session(self.sid)['active_question']
        self.assertEqual(text, active['question'])
        self.assertNotIn('blueprint_id', text)
        self.assertEqual(active['blueprint_id'], 2)
        result = store.skip_question(self.sid, active['question_id'])
        self.assertEqual(result['active_question']['blueprint_id'], 3)

    async def test_repeated_question_never_reaches_stream_and_reselection_is_bounded(self):
        calls = []
        async def model(*args, **kwargs):
            calls.append(1)
            yield envelope(self.questions[0]['question'], 2)
        with patch.object(chat, 'chat_stream', model):
            text = await consume(await chat.chat(self.request()))
        self.assertEqual(calls, [1, 1])
        self.assertEqual(text, self.questions[1]['question'])
        self.assertNotIn(self.questions[0]['question'], text)
        self.assertEqual(store.get_session(self.sid)['active_question']['blueprint_id'], 2)

    async def test_followup_can_add_new_detail_without_consuming_next_blueprint(self):
        async def model(*args, **kwargs):
            yield envelope('你在接口联调中独立负责了哪部分工作？', 1, 'followup', 'personal_contribution')
        with patch.object(chat, 'chat_stream', model):
            text = await consume(await chat.chat(self.request()))
        active = store.get_session(self.sid)['active_question']
        self.assertEqual(text, active['question'])
        self.assertEqual(active['blueprint_id'], 1)
        self.assertEqual(active['question_kind'], 'adaptive')
        self.assertEqual(active['followup_focus'], 'personal_contribution')
        result = store.skip_question(self.sid, 'q-2')
        self.assertEqual(result['active_question']['blueprint_id'], 2)

    async def test_same_followup_focus_cannot_be_reasked_with_different_wording(self):
        store.record_answer(self.sid, '我想做业务自动化')
        active = store.get_session(self.sid)
        store._activate(active, 'q-2', '你独立负责了哪部分工作？', 1,
                        question_kind='adaptive', parent_question_id='q-1', followup_focus='personal_contribution')
        async def model(*args, **kwargs):
            yield envelope('具体来说，哪些事情是你本人而非团队完成的？', 1, 'followup', 'personal_contribution')
        with patch.object(chat, 'chat_stream', model):
            text = await consume(await chat.chat(self.request()))
        self.assertEqual(text, self.questions[1]['question'])

    async def test_buffer_does_not_publish_unvalidated_partial_question(self):
        yielded, release = asyncio.Event(), asyncio.Event()
        async def model(*args, **kwargs):
            yield '{"question": "不应提前显示'
            yielded.set()
            await release.wait()
            yield '", "blueprint_id": 2, "kind": "blueprint", "followup_focus": null}'
        with patch.object(chat, 'chat_stream', model):
            response = await chat.chat(self.request())
            first_chunk = asyncio.create_task(anext(response.body_iterator))
            await yielded.wait()
            self.assertFalse(first_chunk.done())
            self.assertIsNone(store.get_session(self.sid)['active_question'])
            release.set()
            self.assertEqual(await first_chunk, '不应提前显示')
            self.assertEqual(await consume(response), '')

    async def test_voice_input_is_preserved_and_conflicting_replay_is_rejected(self):
        request = self.request()
        voice = {'raw_transcript': '我我参与接口联调', 'cleaned_transcript': '我参与接口联调', 'cleanup_status': 'cleaned'}
        request = chat.ChatRequest(**{**request.model_dump(), 'voice_input': voice})
        async def model(*args, **kwargs):
            yield envelope(self.questions[1]['question'], 2)
        with patch.object(chat, 'chat_stream', model):
            first = await consume(await chat.chat(request))
            replay = await consume(await chat.chat(request))
        turn = store.get_session(self.sid)['turns'][0]
        self.assertEqual(turn['voice_input'], voice)
        self.assertEqual(turn['answer'], request.asr_text)
        self.assertEqual(first, replay)
        conflict = chat.ChatRequest(**{**request.model_dump(), 'voice_input': {**voice, 'raw_transcript': '其他原文'}})
        with self.assertRaises(HTTPException) as error:
            await chat.chat(conflict)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(len(store.get_session(self.sid)['turns']), 1)

    async def test_third_followup_is_replaced_with_uncovered_blueprint(self):
        focus_and_question = [('personal_contribution', '你在工作中独立负责了什么？'),
                              ('evidence', '你保留了哪些产出证据？')]
        for number, (focus, question) in enumerate(focus_and_question, 2):
            store.record_answer(self.sid, '真实回答')
            store._activate(store.get_session(self.sid), f'q-{number}', question, 1,
                            question_kind='adaptive', parent_question_id=f'q-{number - 1}', followup_focus=focus)
        async def model(*args, **kwargs):
            yield envelope('你会怎样改进下次实践？', 1, 'followup', 'reflection')
        with patch.object(chat, 'chat_stream', model):
            result = await consume(await chat.chat(self.request()))
        self.assertEqual(result, self.questions[1]['question'])

    async def test_wrongly_typed_model_focus_is_reselected_not_persisted(self):
        outputs = iter([envelope('你独立做了什么？', 1, 'followup', ['personal_contribution']),
                        envelope(self.questions[1]['question'], 2)])
        async def model(*args, **kwargs):
            yield next(outputs)
        with patch.object(chat, 'chat_stream', model):
            result = await consume(await chat.chat(self.request()))
        self.assertEqual(result, self.questions[1]['question'])

    def test_blueprint_question_cannot_be_mislabeled_as_another_blueprint(self):
        from backend.question_policy import validate_candidate
        session = store.get_session(self.sid)
        with self.assertRaises(ValueError):
            validate_candidate(envelope(self.questions[2]['question'], 2), session)

    async def test_empty_model_response_remains_explicitly_recoverable(self):
        async def model(*args, **kwargs):
            yield ''
        with patch.object(chat, 'chat_stream', model):
            result = await consume(await chat.chat(self.request()))
        saved = store.get_session(self.sid)
        self.assertEqual(result, '')
        self.assertEqual(saved['next_question_job']['status'], 'failed')
        self.assertIsNone(saved['active_question'])
        self.assertEqual(len(saved['turns']), 1)

    async def test_no_uncovered_topics_finishes_without_reintroducing_question(self):
        store.update_session(self.sid, {'blueprint': [self.questions[0]]})
        async def model(*args, **kwargs):
            yield envelope(self.questions[0]['question'], 1)
        with patch.object(chat, 'chat_stream', model):
            result = await consume(await chat.chat(self.request()))
        saved = store.get_session(self.sid)
        self.assertEqual(result, '')
        self.assertEqual(saved['status'], 'interview_finished')
        self.assertIsNone(saved['active_question'])

    def test_near_duplicate_punctuation_and_politeness_but_not_new_detail(self):
        from backend.question_policy import questions_repeat
        self.assertTrue(questions_repeat('请介绍你遇到的技术难题，以及具体如何解决？',
                                         '能介绍你遇到的技术难题以及具体如何解决吗？'))
        self.assertFalse(questions_repeat('请介绍你遇到的技术难题，以及具体如何解决？',
                                          '你如何衡量这次解决方案的效果？请给出验证记录。'))

    def test_similar_wording_with_changed_outcome_or_quantity_is_not_a_duplicate(self):
        from backend.question_policy import questions_repeat
        self.assertFalse(questions_repeat('在这个自动化项目成功以后，你会向团队如何说明具体的原因？',
                                          '在这个自动化项目失败以后，你会向团队如何说明具体的原因？'))
        self.assertFalse(questions_repeat('如果系统每天需要处理100个任务，你会怎样估算资源成本？',
                                          '如果系统每天需要处理10000个任务，你会怎样估算资源成本？'))
        self.assertFalse(questions_repeat('如果任务平均成本为10.01元，你会如何安排项目预算？',
                                          '如果任务平均成本为100.1元，你会如何安排项目预算？'))


if __name__ == '__main__':
    unittest.main()
