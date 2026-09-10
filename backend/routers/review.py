"""POST /api/review — 简历优化专家（评分 + 润色 + 导出）"""
from __future__ import annotations
import json
import re
import time
import statistics
import asyncio
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import config, prompts, store
from ..xiaomi_client import chat_once
from ..report_pipeline import build_report, fingerprint, stage_count, PIPELINE_VERSION
from ..structured import public_model_error

router = APIRouter()
_jobs = {}


@router.get("/api/review/estimate")
async def review_estimate(session_id: str = ''):
    durations = []
    for summary in store.list_sessions()[:30]:
        session = store.get_session(summary["id"])
        duration = (session or {}).get("review_duration_seconds")
        if isinstance(duration, (int, float)) and duration > 0 and session.get('report_pipeline_version') == PIPELINE_VERSION:
            durations.append(duration / stage_count(session))
    target = store.get_session(session_id) if session_id else None
    units = stage_count(target) if target else 6
    center = (statistics.median(durations) if durations else 45) * units
    return {
        "low_seconds": max(30, round(center * 0.6)),
        "high_seconds": max(120, round(center * 1.8)),
        "sample_count": len(durations),
        "basis": "按本机近期报告与本次题量估计" if durations else "按本次题量估计，尚无新版报告样本",
    }


class ReviewRequest(BaseModel):
    session_id: str


class ReviewResponse(BaseModel):
    score: dict
    polish_list: list
    interview_tips: list
    question_feedback: list = Field(default_factory=list)
    skill_map: list = Field(default_factory=list)
    practice_plan: list = Field(default_factory=list)
    tailored_resume: str = ""
    tailored_resume_warnings: list = Field(default_factory=list)


@router.post("/api/review", response_model=ReviewResponse)
async def review(req: ReviewRequest):
    """Compatible synchronous entry point; the web UI uses the resumable job API."""
    task = start_review(req.session_id)
    result = await asyncio.shield(task)
    if result is None:
        raise HTTPException(502, store.get_session(req.session_id)['report_job']['error'])
    return ReviewResponse(**result)


def start_review(session_id):
    running = _jobs.get(session_id)
    if running and not running.done():
        return running
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(404, '面试记录不存在。')
    if session.get('active_question'):
        store.record_answer(session_id, '', skipped=True, reason='结束面试时本题尚未回答')
        session = store.get_session(session_id)
    if not session.get('turns'):
        raise HTTPException(422, '尚无作答记录，请先完成至少一道题。')
    signature = fingerprint(session)
    work = session.get('report_work') or {}
    if work.get('signature') != signature:
        work = {'signature': signature, 'stages': {}}
    job = {'status':'running', 'stage':'准备分析', 'completed':len(work['stages']), 'total':stage_count(session), 'started_at':time.time(), 'error':'', 'retrying':False}
    store.update_session(session_id, {'report_job':job, 'report_work':work, 'status':'reviewing'})
    task = asyncio.create_task(_run_review(session, work, job))
    _jobs[session_id] = task
    task.add_done_callback(lambda finished: _jobs.pop(session_id, None) if _jobs.get(session_id) is finished else None)
    return task


@router.post('/api/review/jobs', status_code=202)
async def create_review_job(req: ReviewRequest):
    start_review(req.session_id)
    return await review_job_status(req.session_id)


@router.get('/api/sessions/{session_id}/review-job')
async def review_job_status(session_id: str):
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(404, '面试记录不存在。')
    job = dict(session.get('report_job') or {'status':'idle'})
    if job['status'] == 'running' and session_id not in _jobs:
        job.update(status='failed', error='本地服务曾重新启动；已完成部分仍保留，点击继续生成。')
        store.update_session(session_id, {'report_job':job})
    job['elapsed_seconds'] = round(time.time() - job['started_at']) if job.get('started_at') else 0
    if job.get('finished_at'):
        job['elapsed_seconds'] = round(job['finished_at'] - job['started_at'])
    if job['status'] == 'completed':
        job['report'] = session.get('review')
    return job


async def _run_review(session, work, job):
    session_id = session['id']
    def progress(label, completed, retrying):
        job.update(stage=label, completed=completed, retrying=retrying)
        store.update_session(session_id, {'report_job':dict(job), 'report_work':work})
    try:
        raw = await build_report(session, chat_once, work, progress)
        result = _normalize_review(raw, session['config']['resume'], session['turns'], session.get('blueprint') or [])
        job.update(status='completed', stage='报告已完成', completed=job['total'], finished_at=time.time())
        duration = sum(item['duration'] for item in work['stages'].values())
        store.update_session(session_id, {'review':result, 'status':'completed', 'report_job':job,
            'report_work':work, 'review_duration_seconds':round(duration,2), 'report_pipeline_version':PIPELINE_VERSION})
        return result
    except Exception as exc:
        job.update(status='failed', error=public_model_error(exc), finished_at=time.time())
        store.update_session(session_id, {'report_job':job, 'report_work':work, 'status':'review_failed'})
        return None




class RetryRequest(BaseModel):
    question_id: str


class EndSessionResponse(BaseModel):
    id: str
    status: str
    ended_at: Optional[str] = None
    turn_count: int


@router.post("/api/sessions/{session_id}/end", response_model=EndSessionResponse)
async def end_session(session_id: str):
    """Idempotent local-only finish; does not start or cancel any report job."""
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "面试记录不存在。")
    if (session.get('report_job') or {}).get('status') == 'running':
        raise HTTPException(409, "报告已开始生成；仅结束面试不会取消正在运行的报告，请前往证据报告查看。")
    session = store.end_session(session_id)
    return EndSessionResponse(id=session['id'], status=session['status'],
        ended_at=session.get('ended_at'), turn_count=len(session.get('turns', [])))


@router.post("/api/sessions/{session_id}/retry")
async def retry_question(session_id: str, req: RetryRequest):
    """Prepare a previous question for another deliberate-practice attempt."""
    if session_id in _jobs and not _jobs[session_id].done():
        raise HTTPException(409, '报告正在生成，请完成后再开始专项重答。')
    active = store.prepare_retry(session_id, req.question_id)
    if active is None:
        raise HTTPException(status_code=404, detail="Question not found")
    return active


@router.get("/api/sessions")
async def list_sessions():
    """列出所有面试记录"""
    return store.list_sessions()


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """获取单个面试详情"""
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def _extract_json(text: str) -> dict:
    """从 LLM 输出中提取 JSON"""
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1).strip())
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        return json.loads(text[start : end + 1])
    raise ValueError(f"无法从输出中提取 JSON: {text[:200]}")


SCORE_WEIGHTS = {
    "岗位匹配度": 30,
    "经历说服力": 25,
    "专业深度": 20,
    "表达与结构": 15,
    "面试表现折算": 10,
}


def _normalize_review(review_data: dict, resume: str, turns: list[dict], blueprint: list[dict] | None = None) -> dict:
    """Validate model output, ground quotes in answers, and remove false precision."""
    if not isinstance(review_data, dict):
        review_data = {}

    score = review_data.get("score") if isinstance(review_data.get("score"), dict) else {}
    dimensions = score.get("dimensions") if isinstance(score.get("dimensions"), dict) else {}
    normalized_dimensions = {}
    total = 0
    for name, expected_max in SCORE_WEIGHTS.items():
        raw = dimensions.get(name) if isinstance(dimensions.get(name), dict) else {}
        value = _bounded_number(raw.get("score", 0), 0, expected_max)
        normalized_dimensions[name] = {
            "score": value,
            "max": expected_max,
            "comment": str(raw.get("comment", "暂无充分证据")),
        }
        total += value
    score["dimensions"] = normalized_dimensions
    score["total"] = round(total, 1)
    score["conclusion"] = str(score.get("conclusion", "证据不足，建议完成更多回答后再判断。"))
    score["calibration_note"] = "分数由当前回答与岗位要求推断，仅用于训练对比，不代表真实招聘结果。"
    review_data["score"] = score

    turns_by_question = {}
    for turn in turns:
        turns_by_question.setdefault(turn.get("question_id"), []).append(turn)

    normalized_feedback = []
    for raw_item in review_data.get("question_feedback", []):
        if not isinstance(raw_item, dict):
            continue
        question_id = str(raw_item.get("question_id", ""))
        matched_turn = _match_turn(turns_by_question.get(question_id, []), raw_item.get("attempt"))
        if turns and matched_turn is None:
            continue
        answer = str(matched_turn.get("answer", "")) if matched_turn else str(raw_item.get("answer", ""))
        question = str(matched_turn.get("question", "")) if matched_turn else str(raw_item.get("question", ""))
        evidence = [
            str(quote).strip()
            for quote in raw_item.get("evidence_quotes", [])
            if str(quote).strip() and _contains_quote(answer, str(quote))
        ]
        confidence = str(raw_item.get("confidence", "medium")).lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "medium"
        if not evidence and confidence == "high":
            confidence = "medium"
        normalized_feedback.append({
            "question_id": question_id,
            "question": question,
            "answer": answer,
            "attempt": int(raw_item.get("attempt") or (matched_turn or {}).get("attempt", 1)),
            "score": _bounded_number(raw_item.get("score", 0), 0, 10),
            "max_score": 10,
            "confidence": confidence,
            "evidence_quotes": evidence,
            "covered_points": _string_list(raw_item.get("covered_points")),
            "missed_points": _string_list(raw_item.get("missed_points")),
            "star": raw_item.get("star") if isinstance(raw_item.get("star"), dict) else {},
            "coaching_tip": str(raw_item.get("coaching_tip", "补充具体行动与可验证结果。")),
            "improved_answer_outline": _string_list(raw_item.get("improved_answer_outline")),
        })
    # Skipped turns must remain visible even when the model omitted them.
    for turn in turns:
        if turn.get("status") != "unanswered":
            continue
        item = next((x for x in normalized_feedback if x["question_id"] == turn["question_id"] and x["attempt"] == turn.get("attempt", 1)), None)
        if item is None:
            item = {"question_id": turn["question_id"], "attempt": turn.get("attempt", 1), "missed_points": [], "coaching_tip": "先梳理本题考察点，再选择一个真实经历，用结论、行动与结果重新作答。"}
            normalized_feedback.append(item)
        plan_item = next((q for q in (blueprint or []) if str(q.get("id")) == str(turn.get("blueprint_id"))), {})
        if not item.get("missed_points"):
            item["missed_points"] = _string_list(plan_item.get("expect")) or ["尚无回答，缺少可验证的作答证据"]
        if plan_item.get("dimension"):
            item["coaching_tip"] = f"本题考察「{plan_item['dimension']}」。" + item.get("coaching_tip", "梳理考察点，准备真实行动与结果后重答。")
        item.update({"question": turn["question"], "answer": "", "status": "unanswered", "skip_reason": turn.get("skip_reason", ""), "score": 0, "max_score": 10, "confidence": "low", "evidence_quotes": [], "covered_points": [], "star": {}})
        item["reason_analysis"] = "用户说明：" + turn["skip_reason"] if turn.get("skip_reason") else "未说明跳过原因；目前缺少作答证据，不能据此判断能力或心理原因。"
    normalized_feedback.sort(key=lambda x: next((i for i,t in enumerate(turns) if t["question_id"] == x["question_id"] and t.get("attempt", 1) == x["attempt"]), len(turns)))
    review_data["question_feedback"] = normalized_feedback
    latest = {}
    for turn in turns:
        latest[turn["question_id"]] = turn
    unanswered = sum(t.get("status") == "unanswered" for t in latest.values())
    if unanswered:
        cap = round(100 * (len(latest) - unanswered) / len(latest), 1)
        if total > cap:
            for dimension in normalized_dimensions.values():
                dimension["score"] = round(dimension["score"] * cap / total, 1)
            score["total"] = round(sum(d["score"] for d in normalized_dimensions.values()), 1)
        score["unanswered_count"] = unanswered
        score["coverage_cap"] = cap
        score["calibration_note"] += f" 最新作答中 {unanswered}/{len(latest)} 题未回答，该题计 0 分；总分上限按作答覆盖率设为 {cap} 分（仅封顶，不重复扣分）。"

    normalized_polish = []
    for raw_item in review_data.get("polish_list", []):
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        claims = _new_numeric_claims(str(item.get("example", "")), resume)
        item["_validated"] = not claims
        item["_unverified_claims"] = claims
        normalized_polish.append(item)
    review_data["polish_list"] = normalized_polish

    review_data["interview_tips"] = _string_list(review_data.get("interview_tips"))
    review_data["practice_plan"] = [
        item for item in review_data.get("practice_plan", []) if isinstance(item, dict)
    ]
    review_data["skill_map"] = _normalize_skill_map(review_data.get("skill_map"))
    tailored_resume = str(review_data.get("tailored_resume", ""))
    review_data["tailored_resume"] = tailored_resume
    review_data["tailored_resume_warnings"] = _new_numeric_claims(tailored_resume, resume)
    return review_data


def _validate_review(review_data: dict, resume: str) -> dict:
    """Backward-compatible wrapper for callers outside this module."""
    return _normalize_review(review_data, resume, [])


def _bounded_number(value: Any, minimum: float, maximum: float):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = minimum
    number = float(min(maximum, max(minimum, number)))
    return int(number) if number.is_integer() else round(number, 1)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _contains_quote(answer: str, quote: str) -> bool:
    compact_answer = re.sub(r"\s+", "", answer).lower()
    compact_quote = re.sub(r"\s+", "", quote).lower()
    return compact_quote in compact_answer


def _new_numeric_claims(text: str, source: str) -> list[str]:
    pattern = r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*(?:%|％|万|亿|倍|人|年|个月|ms|毫秒|秒|分钟|小时|QPS)"
    source_claims = {
        re.sub(r"\s+", "", claim).lower()
        for claim in re.findall(pattern, source, re.IGNORECASE)
    }
    claims = []
    for claim in re.findall(pattern, text, re.IGNORECASE):
        if re.sub(r"\s+", "", claim).lower() not in source_claims and claim not in claims:
            claims.append(claim)
    return claims


def _match_turn(turns: list[dict], attempt: Any):
    if not turns:
        return None
    if attempt is not None:
        try:
            wanted = int(attempt)
            for turn in turns:
                if int(turn.get("attempt", 1)) == wanted:
                    return turn
        except (TypeError, ValueError):
            pass
    return turns[-1]


def _normalize_skill_map(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        result.append({
            "skill": str(item.get("skill", "未命名能力")),
            "score": _bounded_number(item.get("score", 0), 0, 100),
            "evidence": str(item.get("evidence", "")),
        })
    return result
