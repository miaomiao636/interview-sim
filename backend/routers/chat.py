"""POST /api/chat — 面试官（流式对话）"""
from __future__ import annotations
import json
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import config, prompts, store
from ..xiaomi_client import chat_stream

router = APIRouter()


class SkipRequest(BaseModel):
    question_id: str
    reason: str = Field(default="", max_length=200)


@router.post("/api/sessions/{session_id}/skip")
async def skip(session_id: str, req: SkipRequest):
    result = store.skip_question(session_id, req.question_id, req.reason)
    if result is None:
        raise HTTPException(status_code=409, detail="当前问题已变化或已处理，请重新载入训练记录。")
    return result


class ChatRequest(BaseModel):
    session_id: str
    asr_text: str  # 用户语音转写或文本输入
    history: list[dict] = Field(default_factory=list)  # 兼容旧客户端，服务端不再信任它


@router.post("/api/chat")
async def chat(req: ChatRequest):
    """面试官流式对话"""
    session = store.get_session(req.session_id)
    if session is None:
        return StreamingResponse(
            iter([b"session not found"]),
            media_type="text/plain",
            status_code=404,
        )

    if (session.get('report_job') or {}).get('status') == 'running':
        raise HTTPException(409, '报告正在生成，请完成后再开始专项重答。')

    # 服务端 active_question 是会话事实源，避免首题丢失和当前回答重复。
    turn = store.record_answer(req.session_id, req.asr_text)
    if turn is None:
        return StreamingResponse(
            iter([b"no active question"]),
            media_type="text/plain",
            status_code=409,
        )

    session = store.get_session(req.session_id)

    # 构造面试官 prompt
    cfg = session["config"]
    interviewer_sys = prompts.build_interviewer_prompt(
        jd=cfg["jd"],
        resume=cfg["resume"],
        persona=cfg["persona"],
        difficulty=cfg["difficulty"],
        job_title=(
            cfg.get("target_role")
            or session.get("jd_parsed", {}).get("job_title")
            or "目标岗位"
        ),
        persona_name=f"面试官（{cfg['persona']}）",
        style="",
        focus_areas="",
        company=cfg.get('company',''),
        company_context=cfg.get('company_context',''),
    )

    # 构造消息列表
    messages = [{"role": "system", "content": interviewer_sys}]

    # 加入题纲上下文（精简版）
    if session.get("blueprint"):
        blueprint_summary = json.dumps(
            [{"id": q.get("id"), "dim": q.get("dimension", q.get("dim", "")), "q": q.get("question", "")} for q in session["blueprint"]],
            ensure_ascii=False,
        )
        messages.append({
            "role": "system",
            "content": f"本次面试题纲概要（仅供你参考，不要直接念出）：{blueprint_summary}",
        })

    # 只使用服务端持久化记录；当前回答已在 transcript 中且只加入一次。
    for dialog in session.get("transcript", []):
        role = "user" if dialog.get("role") == "candidate" else "assistant"
        messages.append({"role": role, "content": dialog.get("content", "")})

    next_number = int(session.get("question_cursor", 0)) + 1
    messages.append({
        "role": "system",
        "content": (
            f"现在生成第 {next_number} 个问题。优先覆盖题纲中尚未考察的维度；"
            "如果刚才的回答缺少关键证据，可以先追问，但仍只输出一个问题。"
            "请明确参考最近一次回答中的具体细节，避免重复已充分回答的问题。题纲只作为覆盖参考，允许自然调整顺序。"
        ),
    })

    # 流式返回
    async def generate():
        full_response = []
        async for token in chat_stream(messages, model=config.LLM_MODEL, temperature=0.7):
            full_response.append(token)
            yield token
        next_question = "".join(full_response).strip()
        if next_question:
            blueprint = session.get("blueprint") or []
            blueprint_item = blueprint[next_number - 1] if next_number <= len(blueprint) else {}
            store.set_active_question(
                req.session_id,
                question_id=f"q-{next_number}",
                question=next_question,
                blueprint_id=blueprint_item.get("id"),
            )

    return StreamingResponse(generate(), media_type="text/plain")


class TranscribeRequest(BaseModel):
    audio_base64: str = Field(max_length=14_000_000)
    language: str = "zh"


@router.post("/api/transcribe")
async def transcribe(req: TranscribeRequest):
    """语音识别（ASR）"""
    from ..xiaomi_client import transcribe_audio

    try:
        text = await transcribe_audio(req.audio_base64, req.language)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="语音识别服务暂不可用，请检查识别模型、连接设置或重试。") from exc
    return {"text": text}


@router.post("/api/transcribe/stream")
async def transcribe_stream(req: TranscribeRequest):
    """流式语音识别"""
    from ..xiaomi_client import transcribe_audio_stream

    async def generate():
        async for chunk in transcribe_audio_stream(req.audio_base64, req.language):
            yield chunk

    return StreamingResponse(generate(), media_type="text/plain")


class TTSRequest(BaseModel):
    text: str
    voice: str = "白桦"


@router.post("/api/tts")
async def tts(req: TTSRequest):
    """语音合成（TTS）"""
    from ..xiaomi_client import tts_synthesize

    audio_bytes = await tts_synthesize(req.text, voice=req.voice)
    return StreamingResponse(
        iter([audio_bytes]),
        media_type="audio/pcm",
        headers={"X-Sample-Rate": str(config.get_connection("tts")["sample_rate"]), "X-Channels": "1", "X-Sample-Format": "pcm16"},
    )


@router.post("/api/tts/stream")
async def tts_stream(req: TTSRequest):
    """流式语音合成"""
    from ..xiaomi_client import tts_synthesize_stream

    async def generate():
        async for chunk in tts_synthesize_stream(req.text, voice=req.voice):
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="audio/pcm",
        headers={"X-Sample-Rate": str(config.get_connection("tts")["sample_rate"]), "X-Channels": "1", "X-Sample-Format": "pcm16"},
    )
