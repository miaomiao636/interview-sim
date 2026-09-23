"""Removing cleanup must also stop stale pages from invoking a rewrite model."""
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from backend.main import app
from backend import store, voice_cleanup, xiaomi_client


class RawVoiceContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_retired_cleanup_returns_gone_without_model_or_session_access(self):
        with (patch.object(voice_cleanup, 'chat_once', new=AsyncMock(), create=True) as old_model,
              patch.object(xiaomi_client, 'chat_once', new=AsyncMock()) as model,
              patch.object(store, 'get_session', side_effect=AssertionError('must not read sessions')) as read,
              patch.object(store, '_save', side_effect=AssertionError('must not write sessions')) as write):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
                response = await client.post('/api/sessions/00000000/voice-cleanup', json={
                    'question_id': 'q-1', 'attempt': 1, 'operation_id': 'old-page-operation',
                    'raw_text': '我，我，嗯，参与接口对接。',
                })
        self.assertEqual(response.status_code, 410)
        self.assertIn('已取消', response.json()['detail'])
        old_model.assert_not_awaited()
        model.assert_not_awaited()
        read.assert_not_called()
        write.assert_not_called()

    async def test_workbench_assets_revalidate_instead_of_reusing_stale_behavior(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
            for path in ('/', '/assets/app.js', '/assets/voice.js', '/assets/styles.css'):
                with self.subTest(path=path):
                    response = await client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers.get('cache-control'), 'no-cache')
            schema = (await client.get('/openapi.json')).json()
            self.assertNotIn('/api/sessions/{session_id}/voice-cleanup', schema['paths'])


if __name__ == '__main__':
    unittest.main()
