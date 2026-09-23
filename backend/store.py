"""面试 Session 持久化（本地 JSON 文件）"""
from __future__ import annotations
import json
import os
import uuid
import re
import copy
import hashlib
import threading
from contextlib import contextmanager
from functools import wraps
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config
from .question_policy import next_uncovered_question

SESSION_ID_PATTERN = r"^[0-9a-f]{8}$"
TERMINAL_STATES = {"ended", "completed", "reviewing", "review_failed", "interview_finished"}
_locks = {}
_lock_guard = threading.Lock()


class SessionConflict(ValueError):
    pass


def ensure_session_id(session_id):
    if not isinstance(session_id, str) or not re.fullmatch(SESSION_ID_PATTERN, session_id):
        raise ValueError("面试记录编号无效，请刷新页面。")


def session_key(session_id):
    ensure_session_id(session_id)
    return (str(_session_dir().resolve()), session_id)


@contextmanager
def locked_session(session_id):
    key = session_key(session_id)
    with _lock_guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        yield


def _session_locked(function):
    @wraps(function)
    def wrapped(session_id, *args, **kwargs):
        with locked_session(session_id):
            return function(session_id, *args, **kwargs)
    return wrapped


def question_input_signature(session):
    value = [session.get('config'), session.get('turns'), session.get('blueprint')]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def invalidate_question_generation(session, reason="面试状态已改变"):
    """Mutate a locked session; callers persist the whole transition atomically."""
    session['question_generation'] = int(session.get('question_generation', 0)) + 1
    job = session.get('next_question_job')
    if job and job.get('status') == 'running':
        job.update(status='cancelled', error=reason)


def _archive_review(session):
    if session.get('review') and not session.get('review_is_stale'):
        previous = session.setdefault('previous_reviews', [])
        previous.append({'report': copy.deepcopy(session['review']), 'job': copy.deepcopy(session.get('report_job')), 'archived_at': datetime.now().isoformat()})
        session['review_is_stale'] = True


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


@_session_locked
def get_session(session_id: str) -> Optional[dict]:
    """读取 session"""
    path = _session_dir() / f"{session_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


@_session_locked
def update_session(session_id: str, updates: dict) -> Optional[dict]:
    """更新 session 字段"""
    session = get_session(session_id)
    if session is None:
        return None
    if session.get('status') in TERMINAL_STATES and updates.get('status') in {'created', 'planning', 'planned', 'interviewing'}:
        raise SessionConflict('已结束的面试不能由通用更新重新开启，请使用明确的单题重答操作。')
    session.update(updates)
    _save(session)
    return session


@_session_locked
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


@_session_locked
def set_active_question(
    session_id: str,
    question_id: str,
    question: str,
    blueprint_id=None,
    *,
    attempt: int = 1,
    is_retry: bool = False,
    focus: str = "",
    question_kind: str = "",
    parent_question_id=None,
) -> Optional[dict]:
    """Set the visible question and persist it before the candidate can answer."""
    session = get_session(session_id)
    if session is None:
        return None
    if session.get("status") in TERMINAL_STATES:
        return None
    if session.get('active_question'):
        active = session['active_question']
        return active if active['question_id'] == question_id and active.get('attempt', 1) == attempt else None
    return _activate(session, question_id, question, blueprint_id, attempt=attempt, is_retry=is_retry,
                     question_kind=question_kind, parent_question_id=parent_question_id)


def _activate(session, question_id, question, blueprint_id=None, *, attempt=1, is_retry=False, question_kind='', parent_question_id=None, followup_focus=None):
    """Locked transition; explicit retry validates its terminal source first."""

    active_question = {
        "question_id": question_id,
        "question": question.strip(),
        "blueprint_id": blueprint_id,
        "attempt": max(1, attempt),
        "is_retry": is_retry,
        "question_kind": question_kind or ('retry' if is_retry else 'blueprint' if blueprint_id is not None else 'adaptive'),
        "parent_question_id": parent_question_id,
        "followup_focus": followup_focus,
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
        "blueprint_id": blueprint_id,
        "question_kind": active_question['question_kind'],
        "parent_question_id": parent_question_id,
        "followup_focus": followup_focus,
        "time": active_question["asked_at"],
    })
    session["status"] = "interviewing"
    _save(session)
    return active_question


@_session_locked
def record_answer(session_id: str, answer: str, *, skipped: bool = False, reason: str = "", voice_input=None) -> Optional[dict]:
    """Attach one candidate answer to the question currently visible to them."""
    session = get_session(session_id)
    if session is None:
        return None
    active = session.get("active_question")
    if not active or session.get('status') in TERMINAL_STATES or (session.get('report_job') or {}).get('status') == 'running':
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
        "question_kind": active.get('question_kind', 'retry' if active.get('is_retry') else 'blueprint' if active.get('blueprint_id') is not None else 'adaptive'),
        "parent_question_id": active.get('parent_question_id'),
        "followup_focus": active.get('followup_focus'),
        "asked_at": active.get("asked_at"),
        "answered_at": answered_at,
    }
    if voice_input is not None and not skipped:
        turn['voice_input'] = copy.deepcopy(voice_input)
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
    if active.get('is_retry'):
        _archive_review(session)
        session['status'] = 'interview_finished'
    _save(session)
    return turn


@_session_locked
def submit_answer(session_id, question_id, attempt, operation_id, answer, *, skipped=False, reason='', voice_input=None):
    session = get_session(session_id)
    if session is None:
        return None
    intent = {'kind': 'skip' if skipped else 'answer', 'question_id': question_id, 'attempt': attempt, 'answer': answer.strip(), 'reason': reason if skipped else ''}
    # Omit absent metadata to preserve idempotent replays from old clients.
    if voice_input is not None and not skipped:
        intent['voice_input'] = copy.deepcopy(voice_input)
    operations = session.setdefault('answer_operations', {})
    if operation_id in session.get('recovery_operations', {}):
        raise SessionConflict('该操作编号已用于恢复下一题，请使用新的提交编号。')
    previous = operations.get(operation_id)
    if previous:
        if previous['intent'] != intent:
            raise SessionConflict('同一提交编号对应不同内容，请刷新后重新确认。')
        return {**copy.deepcopy(previous['result']), 'reused': True}
    active = session.get('active_question')
    if (not active or active['question_id'] != question_id or active.get('attempt', 1) != attempt
            or session.get('status') in TERMINAL_STATES or (session.get('report_job') or {}).get('status') == 'running'):
        raise SessionConflict('当前题目或作答轮次已变化，请重新载入面试记录。')
    result = skip_question(session_id, question_id, reason, attempt=attempt) if skipped else {'turn': record_answer(session_id, answer, voice_input=voice_input), 'active_question': None, 'finished': bool(active.get('is_retry'))}
    session = get_session(session_id)
    session.setdefault('answer_operations', {})[operation_id] = {'intent': intent, 'result': result}
    _save(session)
    return {**result, 'reused': False}


@_session_locked
def skip_question(session_id: str, question_id: str, reason: str = "", *, attempt=None) -> Optional[dict]:
    session = get_session(session_id)
    active = (session or {}).get("active_question")
    if not active or active["question_id"] != question_id or (attempt is not None and active.get('attempt', 1) != attempt):
        return None
    turn = record_answer(session_id, "", skipped=True, reason=reason)
    if turn is None:
        return None
    session = get_session(session_id)
    cursor = session.get("question_cursor", 0)
    item = next_uncovered_question(session)
    if item and not active.get("is_retry"):
        next_question = set_active_question(session_id, f"q-{cursor + 1}", item["question"], item.get("id"))
    else:
        next_question = None
        update_session(session_id, {"status": "interview_finished"})
    return {"turn": turn, "active_question": next_question, "finished": next_question is None}


@_session_locked
def end_session(session_id: str) -> Optional[dict]:
    """Finish locally without analysis. Repeated calls do not duplicate turns."""
    session = get_session(session_id)
    if session is None or session.get("status") in {"ended", "completed"}:
        return session
    if (session.get('report_job') or {}).get('status') == 'running':
        raise SessionConflict('报告正在生成，不能在此时结束或改写会话。')
    if session.get("active_question"):
        record_answer(session_id, "", skipped=True, reason="结束面试时本题尚未回答")
    session = get_session(session_id)
    invalidate_question_generation(session)
    session.update(status='ended', ended_at=datetime.now().isoformat())
    _save(session)
    return session


@_session_locked
def prepare_retry(session_id: str, question_id: str) -> Optional[dict]:
    """Re-open a previous question as a deliberate-practice attempt."""
    session = get_session(session_id)
    if session is None:
        return None
    active = session.get('active_question')
    if active:
        if active.get('is_retry') and active.get('question_id') == question_id:
            return active
        raise SessionConflict('还有活动中的问题，请先完成或结束本场面试。')
    if session.get('status') not in TERMINAL_STATES - {'reviewing'} or (session.get('report_job') or {}).get('status') == 'running':
        raise SessionConflict('请先结束当前面试，再开始单题重答。')

    attempts = [
        turn for turn in session.get("turns", [])
        if turn.get("question_id") == question_id
    ]
    if not attempts:
        return None
    previous = attempts[-1]
    invalidate_question_generation(session)
    return _activate(
        session,
        question_id=question_id,
        question=previous["question"],
        blueprint_id=previous.get("blueprint_id"),
        attempt=max(turn.get("attempt", 1) for turn in attempts) + 1,
        is_retry=True,
        question_kind='retry',
        parent_question_id=question_id,
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
                "score": (s['review'].get('interview_performance', {}).get('first_attempt', {}).get('total')
                          if (s.get('review') or {}).get('schema_version') == 2 else
                          (s.get('review') or {}).get('score', {}).get('total')),
                "score_kind": ('interview_first_attempt' if (s.get('review') or {}).get('schema_version') == 2
                               else 'legacy_five_dimensions' if s.get('review') else 'not_assessed'),
            })
        except Exception:
            continue
    return sorted(sessions, key=lambda item: item["created_at"], reverse=True)


def _save(session: dict):
    """保存 session 到文件"""
    with locked_session(session['id']):
        _session_dir().mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(_session_dir(), 0o700)
        except OSError:
            pass
        path = _session_dir() / f"{session['id']}.json"
        temp_path = path.with_suffix(".tmp")
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(session, handle, ensure_ascii=False, indent=2)
        temp_path.replace(path)
        os.chmod(path, 0o600)
