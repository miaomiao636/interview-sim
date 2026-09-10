"""Bounded JSON generation with schema validation and a single corrective retry."""
import asyncio
import json
from pydantic import ValidationError


class StructuredOutputError(ValueError):
    pass


def parse_object(text):
    text = text.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    try:
        value = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise StructuredOutputError('模型返回的内容格式不完整，请重试当前步骤。') from exc
    if not isinstance(value, dict):
        raise StructuredOutputError('模型返回的内容格式不正确，需要 JSON 对象。')
    return value


async def generate_object(call, messages, *, model, validate, max_tokens=4096, on_retry=None):
    for attempt in range(2):
        try:
            text = await asyncio.wait_for(call(
                messages=messages, model=model, temperature=0.2,
                max_tokens=max_tokens, json_mode=True,
            ), timeout=100)
            return validate(parse_object(text))
        except (StructuredOutputError, ValidationError, KeyError, TypeError, ValueError) as exc:
            if attempt:
                raise StructuredOutputError('模型连续返回不完整的内容格式；已保存完成的部分，可重试此步骤。') from exc
            if on_retry:
                on_retry()
            messages = [*messages, {'role':'user', 'content':'上次输出未通过结构校验。请重新生成完整 JSON，覆盖要求的每个条目，严格使用指定字段和类型。精简短句，不重复原问题和原回答，不输出 Markdown 或解释。'}]


def public_model_error(error):
    if isinstance(error, StructuredOutputError):
        return str(error)
    code = getattr(error, 'status_code', None)
    if code in (401, 403):
        return '分析服务拒绝访问，请在系统设置检查 API Key 和模型权限。'
    if code == 429:
        return '分析服务限流或额度不足，请检查额度后重试。'
    if code == 400:
        return '分析服务不接受当前请求格式或模型参数，请检查该服务是否支持 JSON 输出。'
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)) or 'Timeout' in type(error).__name__:
        return '当前步骤等待模型超过 100 秒，已保存完成的部分，请稍后重试或更换分析模型。'
    if 'Connection' in type(error).__name__:
        return '无法连接分析服务，请检查网络和 API 地址后重试。'
    return '当前步骤生成失败，已保存完成的部分，请重试；若持续失败，请检查分析模型设置。'
