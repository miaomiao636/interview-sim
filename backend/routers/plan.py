"""POST /api/plan — 面试模拟专家（出题纲 + JD 解析 + 差距分析）"""
from __future__ import annotations
import json
import re
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

from .. import prompts, store
from ..xiaomi_client import chat_once
from ..structured import generate_object, public_model_error, StructuredOutputError

router = APIRouter()


class PlanRequest(BaseModel):
    jd: str = Field(default="", max_length=50000)
    resume: str = Field(default="", max_length=50000)
    preset_id: str = Field(default="", max_length=64)
    resume_version_id: str = Field(default="", max_length=64)
    target_role: str = ""
    coaching_goal: str = ""
    persona: str = "技术负责人"
    difficulty: str = "标准"
    company: str = Field(default='', max_length=160)
    company_context: str = Field(default='', max_length=6000)
    interviewer_gender: Literal['', '女性', '男性'] = ''
    voice: str = Field(default='', max_length=100)


class PlanResponse(BaseModel):
    session_id: str
    jd_parsed: dict
    gap_analysis: list
    blueprint: list
    active_question: dict | None = None
    preset_id: str = ""
    resume_version_id: str = ""


@router.post("/api/plan", response_model=PlanResponse)
async def plan(req: PlanRequest):
    """解析 JD+简历，生成面试蓝图"""
    if bool(req.preset_id) != bool(req.resume_version_id):
        raise HTTPException(422, "选择岗位开练时必须同时指定岗位与简历版本。")
    preparation_snapshot = None
    if req.preset_id:
        from ..preparation_tasks import resolve_launch
        from ..preparation_store import PreparationError, PreparationConflict, PreparationNotFound
        try:
            resolved = resolve_launch(req.preset_id, req.resume_version_id)
        except PreparationNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except PreparationConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (PreparationError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        job = resolved['job']
        # Identity and materials come from the same server snapshot. Browser
        # overrides remain limited to this run's interviewer/voice/difficulty.
        req = req.model_copy(update={
            'jd': job['jd'], 'resume': resolved['version']['resume'],
            'company': job.get('company', ''), 'target_role': job.get('target_role', ''),
            'company_context': job.get('company_context', ''),
            'coaching_goal': job.get('coaching_goal', ''),
        })
        preparation_snapshot = resolved['preparation_snapshot']
    if not req.jd.strip() or not req.resume.strip():
        raise HTTPException(422, "请填写岗位描述和真实简历后再开始。")
    # 创建 session
    session = store.create_session(
        req.jd,
        req.resume,
        req.persona,
        req.difficulty,
        req.target_role,
        req.coaching_goal,
        req.company,
        req.company_context,
        req.interviewer_gender,
        req.voice,
    )
    if req.preset_id:
        session['config'].update(preset_id=req.preset_id, resume_version_id=req.resume_version_id)
        store.update_session(session['id'], {
            'config': session['config'], 'preparation_snapshot': preparation_snapshot,
        })
    plan_token = uuid.uuid4().hex
    store.update_session(session['id'], {'status': 'planning', 'plan_token': plan_token})

    # 构造提示词
    system_prompt = prompts.build_plan_prompt(
        req.jd,
        req.resume,
        req.persona,
        req.difficulty,
        req.target_role,
        req.coaching_goal,
        req.company,
        req.company_context,
    )

    # 调用 LLM（用 pro 模型，推理更强）
    from .. import config
    connection_snapshot = dict(config.get_connection('analysis'))
    model_snapshot = config.LLM_MODEL_PRO
    def still_planning():
        current = store.get_session(session['id'])
        return current and current.get('status') == 'planning' and current.get('plan_token') == plan_token
    async def call(**kwargs):
        with store.locked_session(session['id']):
            if not still_planning():
                raise HTTPException(409, '本场面试已结束或状态已改变，首题生成已取消。')
        return await chat_once(**kwargs, connection_snapshot=connection_snapshot)
    def validate_plan(data):
        if not isinstance(data.get('jd_parsed'), dict) or not isinstance(data.get('gap_analysis'), list):
            raise StructuredOutputError('岗位分析格式不完整。')
        if (not isinstance(data.get('blueprint'), list) or not 1 <= len(data['blueprint']) <= 30
                or any(not isinstance(q, dict) or not isinstance(q.get('question'), str) or not q['question'].strip() or len(q['question']) > 6000 for q in data['blueprint'])):
            raise StructuredOutputError('题纲格式不完整。')
        # IDs denote this persisted list, not arbitrary model labels. Assign the
        # same canonical IDs to both the public blueprint and active questions.
        data['blueprint'] = [{**item, 'id': index} for index, item in enumerate(data['blueprint'], 1)]
        return data
    try:
        data = await generate_object(call, [
            {"role": "system", "content": "你是面试模拟专家，只输出结构化 JSON，不要输出其他内容。"},
            {"role": "user", "content": system_prompt},
        ], model=model_snapshot, validate=validate_plan, max_tokens=6000)
        jd_parsed = data.get("jd_parsed", {})
        gap_analysis = data.get("gap_analysis", [])
        blueprint = data.get("blueprint", [])
    except HTTPException:
        raise
    except Exception as e:
        with store.locked_session(session['id']):
            if still_planning():
                store.update_session(session['id'], {'status': 'plan_failed'})
        raise HTTPException(status_code=502, detail=public_model_error(e)) from e

    # 保存到 session
    with store.locked_session(session['id']):
        if not still_planning():
            raise HTTPException(409, '本场面试已结束或状态已改变，首题生成已取消。')
        store.update_session(session["id"], {
            "status": "planned", "blueprint": blueprint,
            "jd_parsed": jd_parsed, "gap_analysis": gap_analysis,
        })
        active = None
        if blueprint:
            first_question = blueprint[0]
            active = store.set_active_question(
                session["id"], question_id="q-1",
                question=first_question.get("question", "请先做一个简短的自我介绍。"),
                blueprint_id=first_question['id'],
            )

    return PlanResponse(
        session_id=session["id"],
        jd_parsed=jd_parsed,
        gap_analysis=gap_analysis,
        blueprint=blueprint,
        active_question=active,
        preset_id=req.preset_id,
        resume_version_id=req.resume_version_id,
    )


def _extract_json(text: str) -> dict:
    """从 LLM 输出中提取 JSON"""
    # 尝试直接解析
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    # 尝试从 ```json ... ``` 中提取
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1).strip())
    # 尝试找第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        return json.loads(text[start : end + 1])
    raise ValueError("无法从输出中提取 JSON")
