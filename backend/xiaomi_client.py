"""小米 MIMO API 统一客户端（ASR / LLM / TTS）"""
import base64
import json
from typing import AsyncGenerator, Optional, Union

from openai import AsyncOpenAI

from . import config
from .structured import StructuredOutputError
from urllib.parse import urlparse

# 全局异步客户端
_client = None  # type: Optional[AsyncOpenAI]
_clients = {}


def reset_client():
    """配置更新后丢弃旧客户端，下次请求使用新配置。"""
    global _client
    _client = None
    _clients.clear()


def get_client(role="chat"):
    # type: () -> AsyncOpenAI
    connection = config.get_connection(role)
    identity = (role, connection["base_url"], connection["api_key"])
    if identity not in _clients:
        _clients[identity] = AsyncOpenAI(
            base_url=connection["base_url"],
            api_key=connection["api_key"],
            timeout=180.0,
            max_retries=1,
        )
    return _clients[identity]


# ── LLM（对话） ──────────────────────────────────────────────
async def chat_stream(
    messages,  # type: list[dict]
    model=None,  # type: Optional[str]
    temperature=0.7,  # type: float
    max_tokens=2048,  # type: int
):
    # type: (...) -> AsyncGenerator[str, None]
    """流式调用 LLM，逐 token 产出文本"""
    client = get_client()
    model = model or config.LLM_MODEL
    stream = await client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content


async def chat_once(
    messages,  # type: list[dict]
    model=None,  # type: Optional[str]
    temperature=0.7,  # type: float
    max_tokens=4096,  # type: int
    json_mode=False,
):
    # type: (...) -> str
    """一次性调用 LLM，返回完整文本"""
    client = get_client("analysis")
    model = model or config.LLM_MODEL
    options = {}
    if json_mode:
        client = client.with_options(timeout=90.0, max_retries=0)
        options['response_format'] = {'type': 'json_object'}
        # MiMo enables thinking by default; these bounded extraction/scoring
        # calls use JSON mode with thinking disabled as documented by MiMo.
        host = urlparse(config.get_connection('analysis')['base_url']).hostname
        if host in {'api.xiaomimimo.com'} and model.startswith('mimo-v2.5'):
            options['extra_body'] = {'thinking': {'type': 'disabled'}}
    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        stream=False,
        temperature=temperature,
        max_tokens=max_tokens,
        **options,
    )
    if json_mode and getattr(resp.choices[0], 'finish_reason', None) == 'length':
        raise StructuredOutputError('模型输出长度达到限制，内容格式可能不完整。')
    return resp.choices[0].message.content or ""


# ── ASR（语音识别） ──────────────────────────────────────────
async def transcribe_audio(audio_base64, language="zh"):
    # type: (str, str) -> str
    """
    调用 mimo-v2.5-asr 语音识别。
    audio_base64: 原始音频的 base64 编码（wav/mp3）
    返回识别文本。
    """
    client = get_client("asr")
    if config.get_connection("asr")["protocol"] == "openai":
        result = await client.audio.transcriptions.create(
            model=config.ASR_MODEL,
            file=("recording.wav", base64.b64decode(audio_base64), "audio/wav"),
            language=language,
        )
        return result.text
    resp = await client.chat.completions.create(
        model=config.ASR_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": "data:audio/wav;base64," + audio_base64,
                        },
                    }
                ],
            }
        ],
        extra_body={"asr_options": {"language": language}},
        stream=False,
    )
    return resp.choices[0].message.content or ""


async def transcribe_audio_stream(audio_base64, language="zh"):
    # type: (str, str) -> AsyncGenerator[str, None]
    """流式语音识别，逐块返回文本"""
    if config.get_connection("asr")["protocol"] == "openai":
        yield await transcribe_audio(audio_base64, language)
        return
    client = get_client("asr")
    stream = await client.chat.completions.create(
        model=config.ASR_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": "data:audio/wav;base64," + audio_base64,
                        },
                    }
                ],
            }
        ],
        extra_body={"asr_options": {"language": language}},
        stream=True,
    )
    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content


# ── TTS（语音合成） ──────────────────────────────────────────
async def tts_synthesize(
    text,  # type: str
    voice=None,  # type: Optional[str]
    style="沉稳专业、语速适中",  # type: str
):
    # type: (...) -> bytes
    """
    调用 mimo-v2.5-tts 语音合成。
    返回 pcm16 音频数据（24kHz 单声道）。
    """
    client = get_client("tts")
    connection = config.get_connection("tts")
    voice = connection["voice"] or voice
    if connection["protocol"] == "openai":
        response = await client.audio.speech.create(
            model=config.TTS_MODEL, voice=connection["voice"] or "alloy", input=text, response_format="pcm",
        )
        return response.content
    voice = voice or config.TTS_VOICE
    resp = await client.chat.completions.create(
        model=config.TTS_MODEL,
        messages=[
            {"role": "user", "content": style},
            {"role": "assistant", "content": text},
        ],
        extra_body={"audio": {"format": "pcm16", "voice": voice}},
        stream=False,
    )
    # 从响应中提取音频数据
    choice = resp.choices[0]
    if hasattr(choice.message, "audio") and choice.message.audio:
        audio_data = choice.message.audio.data
        if isinstance(audio_data, str):
            return base64.b64decode(audio_data)
        return audio_data
    return b""


async def tts_synthesize_stream(
    text,  # type: str
    voice=None,  # type: Optional[str]
    style="沉稳专业、语速适中",  # type: str
):
    # type: (...) -> AsyncGenerator[bytes, None]
    """流式语音合成，逐块产出 pcm16 音频"""
    client = get_client("tts")
    connection = config.get_connection("tts")
    voice = connection["voice"] or voice
    if connection["protocol"] == "openai":
        async with client.audio.speech.with_streaming_response.create(
            model=config.TTS_MODEL, voice=connection["voice"] or "alloy", input=text, response_format="pcm",
        ) as response:
            async for chunk in response.iter_bytes():
                yield chunk
        return
    voice = voice or config.TTS_VOICE
    stream = await client.chat.completions.create(
        model=config.TTS_MODEL,
        messages=[
            {"role": "user", "content": style},
            {"role": "assistant", "content": text},
        ],
        extra_body={"audio": {"format": "pcm16", "voice": voice}},
        stream=True,
    )
    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if hasattr(delta, "audio") and delta.audio:
            ad = delta.audio
            # 流式模式下 audio 是 dict，非流式是对象
            if isinstance(ad, dict):
                raw = ad.get("data", "")
            else:
                raw = ad.data if hasattr(ad, "data") else ""
            if raw:
                yield base64.b64decode(raw)
