import base64
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend import xiaomi_client as client


class ModelConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_standard_asr_routes_audio_to_its_own_client(self):
        create = AsyncMock(return_value=SimpleNamespace(text='测试转写'))
        fake = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
        with patch.object(client, 'get_client', return_value=fake) as get, patch.object(client.config, 'get_connection', return_value={'protocol':'openai'}):
            result = [chunk async for chunk in client.transcribe_audio_stream(base64.b64encode(b'wav-test').decode())]
        get.assert_called_once_with('asr')
        self.assertEqual(result, ['测试转写'])
        self.assertEqual(create.call_args.kwargs['file'], ('recording.wav', b'wav-test', 'audio/wav'))

    async def test_standard_tts_does_not_send_mimo_voice_names(self):
        create = AsyncMock(return_value=SimpleNamespace(content=b'pcm-test'))
        fake = SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(create=create)))
        with patch.object(client, 'get_client', return_value=fake) as get, patch.object(client.config, 'get_connection', return_value={'protocol':'openai','voice':''}):
            result = await client.tts_synthesize('测试', voice='白桦')
        get.assert_called_once_with('tts')
        self.assertEqual(result, b'pcm-test')
        self.assertEqual(create.call_args.kwargs['voice'], 'alloy')
        self.assertEqual(create.call_args.kwargs['response_format'], 'pcm')

    async def test_mimo_asr_ignores_empty_usage_chunks(self):
        async def stream():
            yield SimpleNamespace(choices=[])
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='有效文字'))])
        create = AsyncMock(return_value=stream())
        fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.object(client, 'get_client', return_value=fake), patch.object(client.config, 'get_connection', return_value={'protocol':'mimo'}):
            result = [part async for part in client.transcribe_audio_stream('test')]
        self.assertEqual(result, ['有效文字'])

    async def test_report_calls_analysis_connection(self):
        create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{}'))]))
        fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.object(client, 'get_client', return_value=fake) as get:
            self.assertEqual(await client.chat_once([], model='analysis-test'), '{}')
        get.assert_called_once_with('analysis')
