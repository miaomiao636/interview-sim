"""POST /api/plan — 面试模拟专家（出题纲 + JD 解析 + 差距分析）"""
from __future__ import annotations
import json
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

from .. import prompts, store
from ..xiaomi_client import chat_once
from ..structured import generate_object, public_model_error, StructuredOutputError

router = APIRouter()


class PlanRequest(BaseModel):
    jd: str
    resume: str
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


@router.post("/api/plan", response_model=PlanResponse)
async def plan(req: PlanRequest):
    """解析 JD+简历，生成面试蓝图"""
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
    def validate_plan(data):
        if not isinstance(data.get('jd_parsed'), dict) or not isinstance(data.get('gap_analysis'), list):
            raise StructuredOutputError('岗位分析格式不完整。')
        if not isinstance(data.get('blueprint'), list) or not data['blueprint'] or any(not isinstance(q, dict) or not q.get('question') for q in data['blueprint']):
            raise StructuredOutputError('题纲格式不完整。')
        return data
    try:
        data = await generate_object(chat_once, [
            {"role": "system", "content": "你是面试模拟专家，只输出结构化 JSON，不要输出其他内容。"},
            {"role": "user", "content": system_prompt},
        ], model=config.LLM_MODEL_PRO, validate=validate_plan, max_tokens=6000)
        jd_parsed = data.get("jd_parsed", {})
        gap_analysis = data.get("gap_analysis", [])
        blueprint = data.get("blueprint", [])
    except Exception as e:
        raise HTTPException(status_code=502, detail=public_model_error(e)) from e

    # 保存到 session
    store.update_session(session["id"], {
        "status": "planned",
        "blueprint": blueprint,
        "jd_parsed": jd_parsed,
        "gap_analysis": gap_analysis,
    })

    if blueprint:
        first_question = blueprint[0]
        store.set_active_question(
            session["id"],
            question_id="q-1",
            question=first_question.get("question", "请先做一个简短的自我介绍。"),
            blueprint_id=first_question.get("id", 1),
        )

    return PlanResponse(
        session_id=session["id"],
        jd_parsed=jd_parsed,
        gap_analysis=gap_analysis,
        blueprint=blueprint,
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
