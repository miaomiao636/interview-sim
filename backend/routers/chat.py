"""POST /api/chat — 面试官（流式对话）"""
from __future__ import annotations
import json
import re
import asyncio
import copy
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ConfigDict

from .. import config, prompts, store
from .. import question_policy
from ..voice_cleanup import VoiceInput
from ..xiaomi_client import chat_stream

router = APIRouter()
SessionId = Annotated[str, Path(pattern=store.SESSION_ID_PATTERN)]
_next_jobs = {}
MAX_QUESTION_CHARS = 16000
MAX_STREAM_CHUNKS = 4096


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    operation_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$')


class SkipRequest(OperationRequest):
    question_id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$')
    attempt: int = Field(ge=1)
    reason: str = Field(default="", max_length=200)


@router.post("/api/sessions/{session_id}/skip")
async def skip(session_id: SessionId, req: SkipRequest):
    from .review import recover_report_job
    try:
        with store.locked_session(session_id):
            recover_report_job(session_id)
            recover_question_job(session_id)
            result = store.submit_answer(session_id, req.question_id, req.attempt, req.operation_id, '', skipped=True, reason=req.reason)
            if result is None:
                raise HTTPException(404, '面试记录不存在。')
            return result
    except store.SessionConflict as exc:
        raise HTTPException(409, str(exc)) from exc


class ChatRequest(SkipRequest):
    session_id: str = Field(pattern=store.SESSION_ID_PATTERN)
    asr_text: str = Field(min_length=1, max_length=50000)
    history: list[dict] = Field(default_factory=list, max_length=100)
    voice_input: VoiceInput | None = None


def _empty_response(text=''):
    return StreamingResponse(iter([text]), media_type='text/plain')


def recover_question_job(session_id):
    with store.locked_session(session_id):
        session = store.get_session(session_id)
        if session is None:
            return None
        job = session.get('next_question_job') or {}
        runtime = _next_jobs.get(store.session_key(session_id))
        last = (session.get('turns') or [{}])[-1]
        if (session.get('status') == 'interviewing' and not session.get('active_question')
                and last.get('status') == 'answered' and not last.get('is_retry')
                and job.get('status') in {None, 'completed'}):
            # A process can stop after saving the accepted answer and before
            # reserving its next model job. GET repairs only local state.
            generation = int(session.get('question_generation', 0)) + 1
            job = {'id': uuid.uuid4().hex, 'generation': generation, 'status': 'interrupted',
                   'input_signature': store.question_input_signature(session),
                   'question_id': f"q-{int(session.get('question_cursor', 0)) + 1}",
                   'parent_question_id': last['question_id'],
                   'error': '回答已保存，下一题生成曾中断。请点击重新生成下一题。'}
            session.update(next_question_job=job, question_generation=generation)
            store._save(session)
        if job.get('status') == 'running' and (not runtime or runtime['job']['generation'] != job.get('generation') or runtime['task'].done()):
            job.update(status='interrupted', error='本地服务已中断，请点击重新生成下一题；原回答已保存。')
            session['next_question_job'] = job
            store._save(session)
        return session


def _question_current(session, job):
    current = session.get('next_question_job') or {}
    return (session.get('status') == 'interviewing' and not session.get('active_question')
            and current.get('status') == 'running' and current.get('id') == job['id']
            and current.get('generation') == job['generation']
            and session.get('question_generation') == job['generation']
            and store.question_input_signature(session) == job['input_signature'])


@router.post("/api/chat")
async def chat(req: ChatRequest):
    """Save one fenced answer, then start at most one next-question generation."""
    from .review import recover_report_job
    try:
        with store.locked_session(req.session_id):
            recover_report_job(req.session_id)
            recover_question_job(req.session_id)
            if not req.asr_text.strip():
                raise HTTPException(422, '回答不能为空；不想回答可使用跳过。')
            result = store.submit_answer(req.session_id, req.question_id, req.attempt, req.operation_id, req.asr_text,
                                         voice_input=req.voice_input.model_dump() if req.voice_input else None)
            if result is None:
                raise HTTPException(404, '面试记录不存在。')
            if result['reused']:
                return _empty_response(result.get('response_text', ''))
            if result['finished']:
                return _empty_response()
            return _start_question(req.session_id, req.operation_id, 'answer_operations')
    except store.SessionConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/api/sessions/{session_id}/next-question')
async def recover_next_question(session_id: SessionId, req: OperationRequest):
    from .review import recover_report_job
    with store.locked_session(session_id):
        recover_report_job(session_id)
        session = recover_question_job(session_id)
        if session is None:
            raise HTTPException(404, '面试记录不存在。')
        if req.operation_id in session.get('answer_operations', {}):
            raise HTTPException(409, '该操作编号已用于提交回答，请使用新的恢复操作编号。')
        previous = session.get('recovery_operations', {}).get(req.operation_id)
        if previous:
            return _empty_response(previous.get('result', {}).get('response_text', ''))
        job = session.get('next_question_job') or {}
        if (job.get('status') not in {'failed', 'interrupted'} or session.get('active_question')
                or session.get('status') in store.TERMINAL_STATES or not session.get('turns')):
            raise HTTPException(409, '当前状态不可重新生成下一题，请刷新面试记录。')
        session.setdefault('recovery_operations', {})[req.operation_id] = {'result': {}}
        store._save(session)
        return _start_question(session_id, req.operation_id, 'recovery_operations')


def _messages(session):
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

    # Bound context while retaining the latest real answer and company materials.
    for dialog in session.get("transcript", [])[-30:]:
        role = "user" if dialog.get("role") == "candidate" else "assistant"
        messages.append({"role": role, "content": str(dialog.get("content", ""))[:12000]})

    next_number = int(session.get("question_cursor", 0)) + 1
    messages.append({
        "role": "system",
        "content": (
            f"现在生成第 {next_number} 个问题。优先覆盖题纲中尚未考察的维度；"
            "如果刚才的回答缺少关键证据，可以先追问，但仍只输出一个问题。"
            "请明确参考最近一次回答中的具体细节，避免重复已充分回答的问题。题纲只作为覆盖参考，允许自然调整顺序。"
        ),
    })

    messages.append({'role': 'system', 'content': question_policy.selection_instruction(session)})

    return messages


def _start_question(session_id, operation_id, operation_table):
    session = store.get_session(session_id)
    generation = int(session.get('question_generation', 0)) + 1
    job = {'id': uuid.uuid4().hex, 'generation': generation, 'status': 'running', 'error': '',
           'input_signature': store.question_input_signature(session), 'question_id': f"q-{int(session.get('question_cursor', 0)) + 1}",
           'parent_question_id': session['turns'][-1]['question_id'], 'operation_id': operation_id, 'operation_table': operation_table}
    session.update(question_generation=generation, next_question_job=job)
    store._save(session)
    queue = asyncio.Queue(maxsize=MAX_STREAM_CHUNKS + 1)
    closed = False
    def close_stream():
        nonlocal closed
        if not closed:
            closed = True
            queue.put_nowait(None)
    snapshot = copy.deepcopy(session)
    connection = dict(config.get_connection('chat'))
    task = asyncio.create_task(_run_question(snapshot, copy.deepcopy(job), queue, connection, config.LLM_MODEL, close_stream))
    key = store.session_key(session_id)
    _next_jobs[key] = {'job': job, 'task': task}
    def finished(done):
        # Also runs if shutdown cancels the task before its first instruction.
        close_stream()
        if (_next_jobs.get(key) or {}).get('task') is done:
            _next_jobs.pop(key, None)
    task.add_done_callback(finished)
    async def stream():
        while True:
            token = await queue.get()
            if token is None:
                break
            yield token
    return StreamingResponse(stream(), media_type='text/plain')


async def _run_question(snapshot, job, queue, connection, model, close_stream):
    session_id = snapshot['id']
    async def receive(messages):
        parts, size = [], 0
        with store.locked_session(session_id):
            if not _question_current(store.get_session(session_id) or {}, job):
                return
        async for token in chat_stream(messages, model=model, temperature=0.7, connection_snapshot=connection):
            with store.locked_session(session_id):
                if not _question_current(store.get_session(session_id) or {}, job):
                    return
            if not isinstance(token, str) or not token:
                continue
            size += len(token)
            if size > MAX_QUESTION_CHARS or len(parts) >= MAX_STREAM_CHUNKS:
                raise ValueError('question stream exceeded local bound')
            parts.append(token)
        return ''.join(parts).strip()

    async def choose():
        messages = _messages(snapshot)
        for attempt in range(2):
            raw = await receive(messages)
            if raw is None:
                return None
            if not raw:
                # Empty/transport failures retain the explicit recovery behavior.
                raise ValueError('empty next question')
            try:
                return question_policy.validate_candidate(raw, snapshot)
            except ValueError as exc:
                if attempt == 0:
                    messages = [*messages, {'role': 'system', 'content':
                        f'刚才的候选题已被本地校验拒绝（{exc}），未展示给用户。请重新选择一次；必须遵守上面的 JSON 协议与已问题约束。'}]
        return question_policy.fallback_candidate(snapshot)

    try:
        # Both generation and its one optional reselection share a total budget.
        candidate = await asyncio.wait_for(choose(), timeout=100)
        with store.locked_session(session_id):
            session = store.get_session(session_id)
            if not session or not _question_current(session, job):
                return
            if candidate is None:
                session['status'] = 'interview_finished'
                text = ''
            else:
                text = candidate['question']
                store._activate(session, job['question_id'], **candidate, parent_question_id=job['parent_question_id'])
                session = store.get_session(session_id)
            session['next_question_job'].update(status='completed', error='')
            operation = session[job['operation_table']][job['operation_id']]
            operation['result']['response_text'] = text
            operation['result']['finished'] = candidate is None
            store._save(session)
            # Never expose raw provider tokens or rejected repeated questions.
            if text:
                queue.put_nowait(text)
    except (Exception, asyncio.CancelledError) as exc:
        with store.locked_session(session_id):
            session = store.get_session(session_id)
            if session and _question_current(session, job):
                session['next_question_job'].update(status='interrupted' if isinstance(exc, asyncio.CancelledError) else 'failed',
                                                   error='下一题暂未生成，原回答已保存。请点击重新生成下一题，或结束面试。')
                store._save(session)
    finally:
        close_stream()


async def shutdown_question_jobs():
    active = list(_next_jobs.items())
    for (_, session_id), runtime in active:
        with store.locked_session(session_id):
            session = store.get_session(session_id)
            if session and _question_current(session, runtime['job']):
                session['next_question_job'].update(status='interrupted', error='服务已关闭，原回答已保存。请重新生成下一题。')
                store._save(session)
        runtime['task'].cancel()
    if active:
        await asyncio.gather(*(runtime['task'] for _, runtime in active), return_exceptions=True)
    _next_jobs.clear()


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
