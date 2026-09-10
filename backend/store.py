"""面试 Session 持久化（本地 JSON 文件）"""
from __future__ import annotations
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config


def _session_dir() -> Path:
    return config.SESSION_DIR


def create_session(
    jd: str,
    resume: str,
    persona: str,
    difficulty: str,
    target_role: str = "",
    coaching_goal: str = "",
    company: str = "",
    company_context: str = "",
    interviewer_gender: str = "",
    voice: str = "",
) -> dict:
    """创建新面试 session"""
    session_id = str(uuid.uuid4())[:8]
    session = {
        "id": session_id,
        "created_at": datetime.now().isoformat(),
        "config": {
            "jd": jd,
            "resume": resume,
            "persona": persona,
            "difficulty": difficulty,
            "target_role": target_role,
            "coaching_goal": coaching_goal,
            "company": company,
            "company_context": company_context,
            "interviewer_gender": interviewer_gender,
            "voice": voice,
        },
        "status": "created",
        "blueprint": None,
        "transcript": [],
        "turns": [],
        "active_question": None,
        "question_cursor": 0,
        "minutes": None,
        "review": None,
    }
    _save(session)
    return session


def get_session(session_id: str) -> Optional[dict]:
    """读取 session"""
    path = _session_dir() / f"{session_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def update_session(session_id: str, updates: dict) -> Optional[dict]:
    """更新 session 字段"""
    session = get_session(session_id)
    if session is None:
        return None
    session.update(updates)
    _save(session)
    return session


def add_dialog(session_id: str, role: str, content: str) -> Optional[dict]:
    """向 session 添加一轮对话"""
    session = get_session(session_id)
    if session is None:
        return None
    session["transcript"].append({
        "role": role,
        "content": content,
        "time": datetime.now().isoformat(),
    })
    _save(session)
    return session


def set_active_question(
    session_id: str,
    question_id: str,
    question: str,
    blueprint_id=None,
    *,
    attempt: int = 1,
    is_retry: bool = False,
    focus: str = "",
) -> Optional[dict]:
    """Set the visible question and persist it before the candidate can answer."""
    session = get_session(session_id)
    if session is None:
        return None
    if session.get("status") == "ended" and not is_retry:
        return None  # A late model response must not reopen a finished interview.

    active_question = {
        "question_id": question_id,
        "question": question.strip(),
        "blueprint_id": blueprint_id,
        "attempt": max(1, attempt),
        "is_retry": is_retry,
        "focus": focus,
        "asked_at": datetime.now().isoformat(),
    }
    session["active_question"] = active_question
    session["question_cursor"] = max(
        int(session.get("question_cursor", 0)),
        _question_number(question_id),
    )
    session.setdefault("transcript", []).append({
        "role": "interviewer",
        "content": active_question["question"],
        "question_id": question_id,
        "attempt": active_question["attempt"],
        "is_retry": is_retry,
        "time": active_question["asked_at"],
    })
    session["status"] = "interviewing"
    _save(session)
    return active_question


def record_answer(session_id: str, answer: str, *, skipped: bool = False, reason: str = "") -> Optional[dict]:
    """Attach one candidate answer to the question currently visible to them."""
    session = get_session(session_id)
    if session is None:
        return None
    active = session.get("active_question")
    if not active:
        return None

    answered_at = datetime.now().isoformat()
    turn = {
        "turn_id": f"{active['question_id']}-attempt-{active.get('attempt', 1)}",
        "question_id": active["question_id"],
        "blueprint_id": active.get("blueprint_id"),
        "question": active["question"],
        "answer": answer.strip(),
        "status": "unanswered" if skipped else "answered",
        "skip_reason": reason if skipped else "",
        "attempt": active.get("attempt", 1),
        "is_retry": bool(active.get("is_retry", False)),
        "focus": active.get("focus", ""),
        "asked_at": active.get("asked_at"),
        "answered_at": answered_at,
    }
    session.setdefault("turns", []).append(turn)
    session.setdefault("transcript", []).append({
        "role": "candidate",
        "content": f"[未回答；原因：{reason or '未说明'}]" if skipped else turn["answer"],
        "question_id": turn["question_id"],
        "attempt": turn["attempt"],
        "is_retry": turn["is_retry"],
        "time": answered_at,
    })
    session["active_question"] = None
    _save(session)
    return turn


def skip_question(session_id: str, question_id: str, reason: str = "") -> Optional[dict]:
    session = get_session(session_id)
    active = (session or {}).get("active_question")
    if not active or active["question_id"] != question_id:
        return None
    turn = record_answer(session_id, "", skipped=True, reason=reason)
    blueprint = session.get("blueprint") or []
    cursor = session.get("question_cursor", 0)
    if cursor < len(blueprint) and not active.get("is_retry"):
        item = blueprint[cursor]
        next_question = set_active_question(session_id, f"q-{cursor + 1}", item["question"], item.get("id"))
    else:
        next_question = None
        update_session(session_id, {"status": "interview_finished"})
    return {"turn": turn, "active_question": next_question, "finished": next_question is None}


def end_session(session_id: str) -> Optional[dict]:
    """Finish locally without analysis. Repeated calls do not duplicate turns."""
    session = get_session(session_id)
    if session is None or session.get("status") in {"ended", "completed"}:
        return session
    if session.get("active_question"):
        record_answer(session_id, "", skipped=True, reason="结束面试时本题尚未回答")
    return update_session(session_id, {"status": "ended", "ended_at": datetime.now().isoformat()})


def prepare_retry(session_id: str, question_id: str) -> Optional[dict]:
    """Re-open a previous question as a deliberate-practice attempt."""
    session = get_session(session_id)
    if session is None:
        return None

    attempts = [
        turn for turn in session.get("turns", [])
        if turn.get("question_id") == question_id
    ]
    if not attempts:
        return None
    previous = attempts[-1]
    feedback = next(
        (
            item for item in (session.get("review") or {}).get("question_feedback", [])
            if item.get("question_id") == question_id
        ),
        {},
    )
    focus = feedback.get("coaching_tip") or "补齐遗漏要点，并用更具体的证据作答。"
    return set_active_question(
        session_id,
        question_id=question_id,
        question=previous["question"],
        blueprint_id=previous.get("blueprint_id"),
        attempt=max(turn.get("attempt", 1) for turn in attempts) + 1,
        is_retry=True,
        focus=focus,
    )


def _question_number(question_id: str) -> int:
    try:
        return int(str(question_id).rsplit("-", 1)[-1])
    except (TypeError, ValueError):
        return 0


def list_sessions() -> list[dict]:
    """列出所有 session（摘要）"""
    sessions = []
    for path in _session_dir().glob("*.json"):
        try:
            s = json.loads(path.read_text(encoding="utf-8"))
            if not (s.get("blueprint") or s.get("turns") or s.get("review")):
                continue
            sessions.append({
                "id": s["id"],
                "created_at": s["created_at"],
                "status": s.get("status", "unknown"),
                "persona": s["config"].get("persona", ""),
                "difficulty": s["config"].get("difficulty", ""),
                "job_title": s.get("jd_parsed", {}).get("job_title", ""),
                "turn_count": len(s.get("turns", [])),
                "score": s.get("review", {}).get("score", {}).get("total") if s.get("review") else None,
            })
        except Exception:
            continue
    return sorted(sessions, key=lambda item: item["created_at"], reverse=True)


def _save(session: dict):
    """保存 session 到文件"""
    _session_dir().mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_session_dir(), 0o700)
    except OSError:
        pass
    path = _session_dir() / f"{session['id']}.json"
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temp_path, 0o600)
    temp_path.replace(path)
    os.chmod(path, 0o600)
