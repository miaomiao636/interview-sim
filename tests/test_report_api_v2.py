import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from backend import config, store
from backend.main import app
from backend.routers import review
from tests.test_report_v2 import feedback


class ReportAPIv2Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = patch.object(config, 'SESSION_DIR', Path(self.temp.name))
        self.directory.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1')
        self.sid = store.create_session('负责验证', '真实简历示例', 'HR', '标准')['id']
        store.set_active_question(self.sid, 'q-1', '请说明个人贡献', 1)
        store.record_answer(self.sid, '我负责需求访谈。')
        store.end_session(self.sid)
        self.calls = []

    async def asyncTearDown(self):
        await review.shutdown_report_jobs()
        await self.client.aclose()
        self.directory.stop()
        self.temp.cleanup()

    async def model(self, **kwargs):
        prompt = kwargs['messages'][1]['content']
        self.calls.append(prompt)
        if '本批作答：' in prompt:
            batch = json.loads(prompt.split('本批作答：', 1)[1])
            return json.dumps({'question_feedback': [feedback(t) for t in batch]}, ensure_ascii=False)
        return json.dumps({'interview_tips': ['补充可验证证据'], 'practice_plan': []}, ensure_ascii=False)

    async def test_sync_async_history_keep_schema_and_report_versions(self):
        with patch.object(review, 'chat_once', self.model):
            response = await self.client.post('/api/review', json={'session_id': self.sid})
            self.assertEqual(response.status_code, 200, response.text)
            report = response.json()
            self.assertEqual(report['schema_version'], 2)
            self.assertNotIn('score', report)
            self.assertEqual(report['interview_performance']['first_attempt']['total'], 60)
            saved = store.get_session(self.sid)
            self.assertEqual(saved['review'], report)
            job = await self.client.get(f'/api/sessions/{self.sid}/review-job')
            self.assertEqual(job.json()['report'], report)
            summary = (await self.client.get('/api/sessions')).json()[0]
            self.assertEqual(summary['score'], 60)
            self.assertEqual(summary['score_kind'], 'interview_first_attempt')
            # A deliberate regeneration archives the immutable previous version.
            second = await self.client.post('/api/review', json={'session_id': self.sid})
            self.assertEqual(second.status_code, 200, second.text)
            saved = store.get_session(self.sid)
            self.assertEqual(saved['previous_reviews'][0]['report'], report)
            self.assertNotEqual(saved['review']['report_id'], report['report_id'])
            self.assertEqual(len(self.calls), 2, 'Validated stage cache should avoid repeated API calls')

    async def test_retry_does_not_replace_original_assessment_or_regenerate_first(self):
        with patch.object(review, 'chat_once', self.model):
            first = await review.start_review(self.sid)
            previous = copy.deepcopy(first)
            store.prepare_retry(self.sid, 'q-1')
            store.record_answer(self.sid, '我组织访谈并保存原始记录。')
            before = len(self.calls)
            second = await review.start_review(self.sid)
        self.assertEqual(second['interview_performance']['first_attempt'], previous['interview_performance']['first_attempt'])
        self.assertEqual(second['interview_performance']['latest_retry']['question_count'], 1)
        self.assertEqual(len(self.calls) - before, 2)
        self.assertEqual(store.get_session(self.sid)['previous_reviews'][0]['report'], previous)

    async def test_retry_after_failed_summary_preserves_already_scored_first_attempt(self):
        async def fail_summary(**kwargs):
            prompt = kwargs['messages'][1]['content']
            if '本批作答：' not in prompt:
                raise ConnectionError('synthetic summary failure')
            return await self.model(**kwargs)
        with patch.object(review, 'chat_once', fail_summary):
            self.assertIsNone(await review.start_review(self.sid))
        self.assertTrue(store.get_session(self.sid)['report_work']['stages'])
        store.prepare_retry(self.sid, 'q-1')
        store.record_answer(self.sid, '我组织需求访谈并保存原始记录。')
        self.calls.clear()
        async def higher_score(**kwargs):
            result = json.loads(await self.model(**kwargs))
            for item in result.get('question_feedback', []):
                for dimension in item['dimensions'].values():
                    dimension['score'] = 9
            return json.dumps(result, ensure_ascii=False)
        with patch.object(review, 'chat_once', higher_score):
            result = await review.start_review(self.sid)
        self.assertEqual(result['interview_performance']['first_attempt']['total'], 60)
        self.assertEqual(result['interview_performance']['latest_retry']['total'], 90)
        batches = [json.loads(p.split('本批作答：', 1)[1]) for p in self.calls if '本批作答：' in p]
        self.assertEqual([[t['attempt'] for t in batch] for batch in batches], [[2]])
