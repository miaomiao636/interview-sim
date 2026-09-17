"""Persistent data helpers for job preparation dossiers (P1).

Data is stored in plain JSON under DATA_DIR/preparation/{preset_id}.json.
All writes use atomic temp-file replacement and per-preset in-process locks.
"""

from __future__ import annotations

import hashlib
import copy
import json
import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal


from . import config


SCHEMA_VERSION = 1
ID_RE = re.compile(r"^[0-9a-f]{16,64}$")
MAX_PRESET_TEXT = 50000
MAX_NAME = 120
MAX_FACT_TEXT = 12000
MAX_TASK_META = 120
MAX_FACT_DECISION_TEXT = 12000


class PreparationError(RuntimeError):
    pass


class PreparationNotFound(PreparationError):
    pass


class PreparationConflict(PreparationError):
    pass


class InternalContractError(PreparationError):
    pass


def _preparation_dir() -> Path:
    return config.DATA_DIR / "preparations"


def _ensure_dirs() -> None:
    _preparation_dir().mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_preparation_dir(), 0o700)
    except OSError:
        pass


_preparation_locks: dict[str, threading.Lock] = {}
_lock_guard = threading.Lock()


def new_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.now().isoformat()


def launch_material_fingerprint(config_fields: dict) -> str:
    """Bind a preparation snapshot to the exact materials used at launch."""
    keys = ("preset_id", "resume_version_id", "jd", "resume", "company", "target_role", "company_context", "coaching_goal")
    material = {key: config_fields.get(key, "") for key in keys}
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def ensure_internal_id(value: str, *, field: str = "id") -> None:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ValueError(f"{field} 必须为 16-64 位小写十六进制字符串。")


def _seed_version_id(preset_id: str, signature: str) -> str:
    return hashlib.sha256(f"{preset_id}:{signature}".encode("utf-8")).hexdigest()[:32]


def preparation_path(preset_id: str) -> Path:
    ensure_internal_id(preset_id, field="preset_id")
    return _preparation_dir() / f"{preset_id}.json"


@contextmanager
def locked_preparation(preset_id: str):
    with _lock_guard:
        lock = _preparation_locks.setdefault(preset_id, threading.Lock())
    with lock:
        yield


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def read_preparation(preset_id: str) -> dict[str, Any] | None:
    ensure_internal_id(preset_id, field="preset_id")
    path = preparation_path(preset_id)
    if not path.exists():
        return None
    data = _read_json(path)
    return data or None


def write_preparation(preset_id: str, data: dict[str, Any]) -> None:
    ensure_internal_id(preset_id, field="preset_id")
    _ensure_dirs()
    path = preparation_path(preset_id)
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    temporary.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _preset_signature(preset: dict[str, Any]) -> str:
    tracked = {
        key: str((preset or {}).get(key, "")).strip()
        for key in (
            "company",
            "target_role",
            "city",
            "salary_min",
            "salary_max",
            "salary_months",
            "education",
            "responsibilities",
            "requirements",
            "company_context",
            "source_jd",
            "coaching_goal",
            "resume",
            "jd",
            "persona",
            "difficulty",
        )
    }
    return hashlib.sha256(
        json.dumps(tracked, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def get_preset_snapshot(preset_id: str) -> dict[str, Any]:
    ensure_internal_id(preset_id, field="preset_id")
    path = config.DATA_DIR / "presets.json"
    if not path.exists():
        raise PreparationNotFound("岗位预设不存在。")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise PreparationNotFound("岗位预设不存在。")
    for item in payload:
        if isinstance(item, dict) and item.get("id") == preset_id:
            return item
    raise PreparationNotFound("岗位预设不存在。")


def _ordered_versions(versions: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(versions.values(), key=lambda item: item.get("created_at", ""))


def _seed_preparation(preset_id: str, preset: dict[str, Any]) -> dict[str, Any]:
    signature = _preset_signature(preset)
    base_version = _snapshot_preparation(
        preset_id,
        preset,
        signature,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "preset_id": preset_id,
        "revision": 0,
        "preset_signature": signature,
        "updated_at": base_version["created_at"],
        "current_version_id": base_version["id"],
        "versions": {base_version["id"]: base_version},
        "facts": {},
        "suggestions": {},
        "tasks": {},
    }


def _snapshot_preparation(
    preset_id: str,
    preset: dict[str, Any],
    signature: str,
    *,
    created_at: str | None = None,
) -> dict[str, Any]:
    return {
        "id": _seed_version_id(preset_id, signature),
        "parent_id": None,
        "name": "岗位当前输入快照",
        "resume": str(preset.get("resume", ""))[:MAX_PRESET_TEXT],
        "source": {
            "kind": "preset",
            "version_signature": signature,
            "preset_id": preset_id,
            "fields": {
                "company": preset.get("company", ""),
                "target_role": preset.get("target_role", ""),
                "jd": preset.get("jd", ""),
            },
        },
        "created_at": created_at or preset.get("updated_at", ""),
        "time_basis": "preset_updated_at",
    }


def _ensure_preparation(preset_id: str, preset: dict[str, Any]) -> dict[str, Any]:
    existing = read_preparation(preset_id)
    if existing:
        existing.setdefault("facts", {})
        existing.setdefault("suggestions", {})
        existing.setdefault("tasks", {})
        existing.setdefault("versions", {})
        return existing
    seeded = _seed_preparation(preset_id, preset)
    return seeded


def _check_revision(data: dict[str, Any], revision: int, *, for_create: bool = False) -> None:
    if for_create and revision < 0:
        raise PreparationConflict("版本号不能为负数。")
    current = data.get("revision", 0)
    if current != revision:
        raise PreparationConflict("结果已更新，请刷新后重试（409）。")


def _is_ancestor(versions: dict[str, Any], ancestor: str, node: str) -> bool:
    visited = set()
    cursor = node
    while cursor and cursor not in visited:
        if cursor == ancestor:
            return True
        visited.add(cursor)
        cursor = versions.get(cursor, {}).get("parent_id")
    return False


def _find_unique_occurrence(content: str, target: str) -> list[int]:
    positions = []
    start = 0
    while True:
        i = content.find(target, start)
        if i == -1:
            break
        positions.append(i)
        start = i + 1
        if len(positions) > 1:
            break
    return positions


def _new_fact_id() -> str:
    return new_id()


def _new_version_id() -> str:
    return new_id()


def _new_suggestion_id() -> str:
    return new_id()


def _new_task_id() -> str:
    return new_id()


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return max(a[0], b[0]) < min(a[1], b[1])


def _normalize_text(value: Any, *, max_length: int, field_name: str) -> str:
    if not isinstance(value, str):
        raise PreparationError(f"{field_name} 必须为字符串。")
    text = value.strip()
    if len(text) > max_length:
        raise PreparationError(f"{field_name} 超出长度限制。")
    return text


def _fact_source_key(fact: dict[str, Any]) -> tuple[str, str]:
    source = fact.get("source") if isinstance(fact.get("source"), dict) else {}
    return (
        str(source.get("session_id", "")),
        str(source.get("question_id", "")),
        str(source.get("attempt", "")),
    )


def _snapshot_versions_for_view(data: dict[str, Any], snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    versions = _ordered_versions(data.get("versions", {}))
    version_ids = {item.get("id") for item in versions if isinstance(item, dict)}
    if snapshot.get("id") not in version_ids:
        versions.append(snapshot)
        versions.sort(key=lambda item: item.get("created_at", ""))
    return versions


def _evidence_facts(facts: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in facts.values()
        if isinstance(item, dict)
        and item.get("status") == "confirmed"
        and not item.get("superseded_by")
    ]


def _fact_lineage(facts: dict[str, Any], fact_id: str) -> list[str]:
    """Return the candidate-to-root chain, rejecting broken stored references."""
    lineage = []
    seen = set()
    cursor = fact_id
    while cursor:
        if cursor in seen or not isinstance(facts.get(cursor), dict):
            raise PreparationConflict("素材修订来源不完整，请刷新后核对。")
        seen.add(cursor)
        lineage.append(cursor)
        cursor = facts[cursor].get("origin_fact_id")
    return lineage


def ingest_fact_proposal(
    preset_id: str,
    *,
    revision: int,
    text: str,
    source: dict[str, Any],
) -> dict[str, Any]:
    ensure_internal_id(preset_id, field="preset_id")
    if not isinstance(source, dict):
        raise PreparationError("素材来源字段必须为对象。")
    normalized_text = _normalize_text(text, max_length=MAX_FACT_DECISION_TEXT, field_name="事实文本")
    if not normalized_text:
        raise PreparationError("事实文本不能为空。")
    if len(json.dumps(source, ensure_ascii=False)) > 24000:
        raise PreparationError("素材来源内容过长。")
    preset = get_preset_snapshot(preset_id)
    with locked_preparation(preset_id):
        data = read_preparation(preset_id)
        if data is None:
            data = _seed_preparation(preset_id, preset)
        _check_revision(data, revision)
        if str(data.get("preset_signature", "")) != _preset_signature(preset):
            raise PreparationConflict("岗位输入已更新，请刷新后重试（409）。")
        for existing in data.get("facts", {}).values():
            if existing.get("text") == normalized_text and existing.get("source") == source:
                return {**existing, "revision": data["revision"]}
        fact_id = _new_fact_id()
        source_session_id = str(source.get("session_id", ""))
        source_question_id = source.get("question_id")
        source_attempt = source.get("attempt", 1)
        source_quote = source.get("quote", "")
        facts = data.setdefault("facts", {})
        fact = {
            "id": fact_id,
            "status": "pending",
            "text": normalized_text,
            "source": dict(source),
            "source_session_id": source_session_id,
            "source_question_id": source_question_id,
            "source_attempt": source_attempt,
            "source_quote": source_quote,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "confirmed_at": "",
            "source_version_id": source.get("source_version_id")
            or data.get("current_version_id"),
            "superseded_by": None,
        }
        facts[fact_id] = fact
        data["revision"] = int(data.get("revision", 0)) + 1
        write_preparation(preset_id, data)
        return {**fact, "revision": data["revision"]}


def get_preparation_view(preset_id: str) -> dict[str, Any]:
    ensure_internal_id(preset_id, field="preset_id")
    preset = get_preset_snapshot(preset_id)
    signature = _preset_signature(preset)
    stored = read_preparation(preset_id)
    if not stored:
        snapshot_version = _snapshot_preparation(preset_id, preset, signature)
        return {
            "schema_version": SCHEMA_VERSION,
            "preset_id": preset_id,
            "revision": 0,
            "preset_signature": signature,
            "stale": False,
            "current_version_id": snapshot_version["id"],
            "versions": [snapshot_version],
            "facts": [],
            "suggestions": [],
            "tasks": [],
            "evidence_facts": [],
        }
    current_signature = str(stored.get("preset_signature", ""))
    stale = current_signature != signature
    snapshot_version = _snapshot_preparation(preset_id, preset, signature)
    versions = _snapshot_versions_for_view(stored, snapshot_version)
    return {
        "schema_version": SCHEMA_VERSION,
        "preset_id": preset_id,
        "revision": int(stored.get("revision", 0)),
        "preset_signature": signature,
        "stale": stale,
        "current_version_id": stored.get("current_version_id"),
        "versions": versions,
        "facts": list(stored.get("facts", {}).values()),
        "suggestions": list(stored.get("suggestions", {}).values()),
        "tasks": list(stored.get("tasks", {}).values()),
        "evidence_facts": _evidence_facts(stored.get("facts", {})),
    }


def create_version(preset_id: str, revision: int, resume: str, name: str, *, parent_id: str | None = None) -> dict[str, Any]:
    preset = get_preset_snapshot(preset_id)
    ensure_internal_id(preset_id, field="preset_id")
    if parent_id:
        ensure_internal_id(parent_id, field="parent_id")

    if not isinstance(name, str) or not name.strip():
        raise PreparationError("版本名称不能为空。")
    if len(name) > MAX_NAME:
        raise PreparationError("版本名称过长。")
    if not isinstance(resume, str) or not resume.strip():
        raise PreparationError("简历正文不能为空。")
    if len(resume) > MAX_PRESET_TEXT:
        raise PreparationError("简历正文过长。")

    with locked_preparation(preset_id):
        signature = _preset_signature(preset)
        data = read_preparation(preset_id)
        if data is None:
            if revision != 0:
                raise PreparationConflict("结果已更新，请刷新后重试（409）。")
            if parent_id and parent_id != _seed_version_id(preset_id, signature):
                raise PreparationConflict("父版本不存在。")
            data = _seed_preparation(preset_id, preset)
            versions = data.setdefault("versions", {})
            base_id = data.get("current_version_id")
        else:
            versions = data.setdefault("versions", {})
            current_signature = str(data.get("preset_signature", ""))
            stale = current_signature != signature
            current_id = data.get("current_version_id")
            snapshot_id = _seed_version_id(preset_id, signature)
            if stale and parent_id != snapshot_id:
                raise PreparationConflict("岗位输入已更新，请先基于当前岗位基线保存。")
            if parent_id == snapshot_id:
                snapshot_version = _snapshot_preparation(
                    preset_id, preset, signature
                )
                versions.setdefault(snapshot_version["id"], snapshot_version)
                base_id = parent_id
                data["preset_signature"] = signature
                data["updated_at"] = now_iso()
            else:
                base_id = parent_id or current_id
                if base_id not in versions:
                    raise PreparationConflict("父版本不存在。")
            if not stale:
                if not _is_ancestor(versions, base_id, current_id or ""):
                    raise PreparationConflict("父版本不在当前版本链上。")
            if base_id is None:
                raise PreparationConflict("岗位记录未能确定当前版本。")
        _check_revision(data, revision)

        version = {
            "id": _new_version_id(),
            "parent_id": base_id,
            "name": name.strip(),
            "resume": resume.strip(),
            "source": {
                "kind": "user_edit",
                "base_id": base_id,
                "source_version_signature": versions.get(base_id, {}).get("source", {}).get("version_signature"),
            },
            "created_at": now_iso(),
        }
        versions[version["id"]] = version
        data["current_version_id"] = version["id"]
        data["revision"] = int(data.get("revision", 0)) + 1
        data["updated_at"] = now_iso()
        write_preparation(preset_id, data)
        return {"version": version, "revision": data["revision"], "current_version_id": version["id"]}


def accept_suggestion(preset_id: str, suggestion_id: str, revision: int, *, truth_confirmed: bool = False) -> dict[str, Any]:
    ensure_internal_id(suggestion_id, field="suggestion_id")
    preset = get_preset_snapshot(preset_id)
    with locked_preparation(preset_id):
        data = _ensure_preparation(preset_id, preset)
        versions = data.setdefault("versions", {})
        suggestions = data.setdefault("suggestions", {})
        suggestion = suggestions.get(suggestion_id)
        if not isinstance(suggestion, dict):
            raise PreparationNotFound("建议不存在。")

        if suggestion.get("status") == "accepted":
            accepted_version = suggestion.get("accepted_version_id")
            if accepted_version and accepted_version in versions:
                return {
                    "version": versions[accepted_version],
                    "revision": data.get("revision", 0),
                    "current_version_id": data.get("current_version_id"),
                    "reused": True,
                }
            raise PreparationConflict("建议已标记为生效但无对应版本。")

        if suggestion.get("status") not in {"pending", None}:
            raise PreparationConflict("建议当前不可采纳。")
        if suggestion.get("applicable") is False:
            raise PreparationConflict("这条建议缺少可信依据，请先补充材料或手动核对后另存版本。")
        if suggestion.get("needs_confirmation") and truth_confirmed is not True:
            raise PreparationConflict("请先核对建议与真实经历一致，再明确确认采纳。")

        _check_revision(data, revision)
        if data.get("preset_signature") != _preset_signature(preset):
            raise PreparationConflict("岗位输入已更新，请刷新并重新诊断后采纳建议。")
        if suggestion.get("source_task_id"):
            from .preparation_tasks import validate_suggestion_context
            # Validate evidence in the same transaction as applying the edit;
            # a prior HTTP check alone races with fact confirmations/rejections.
            validate_suggestion_context(preset_id, suggestion, data=data, preset=preset)

        source_id = suggestion.get("source_version_id")
        current_id = data.get("current_version_id")
        if source_id not in versions or not current_id:
            raise PreparationNotFound("推荐来源版本不存在。")
        if not _is_ancestor(versions, source_id, current_id):
            raise PreparationConflict("建议来源与当前版本不连续，不能直接应用。")

        target = str(suggestion.get("target", "")).strip()
        replacement = str(suggestion.get("replacement", "")).strip()
        if not target:
            raise PreparationError("建议缺少可定位锚点文本。")
        if not replacement:
            raise PreparationError("建议缺少替换文本。")

        current_resume = str(versions[current_id]["resume"]) if isinstance(versions.get(current_id), dict) else ""
        source_resume = str(versions.get(source_id, {}).get("resume", ""))
        source_positions = _find_unique_occurrence(source_resume, target)
        if len(source_positions) == 0:
            raise PreparationConflict("建议来源文本已变更，建议已过期。")
        if len(source_positions) > 1:
            raise PreparationConflict("建议定位不唯一，请先澄清后重试（409）。")
        source_index = source_positions[0]

        positions = _find_unique_occurrence(current_resume, target)
        if len(positions) == 0:
            raise PreparationConflict("目标片段已被改写或移除，建议已过期。")
        if len(positions) > 1:
            raise PreparationConflict("目标片段不唯一，请先澄清后重试（409）。")

        for history_id, history in suggestions.items():
            if not isinstance(history, dict):
                continue
            if history_id == suggestion_id:
                continue
            if history.get("status") != "accepted":
                continue
            if history.get("source_version_id") != source_id:
                continue
            # A suggestion applied on a discarded sibling branch did not edit
            # the current resume, so its source span cannot conflict here.
            if not _is_ancestor(versions, history.get("accepted_version_id"), current_id):
                continue
            source_span = history.get("source_span")
            if not source_span:
                continue
            if _overlaps(
                (source_index, source_index + len(target)),
                (int(source_span.get("start", -1)), int(source_span.get("end", -1))),
            ):
                raise PreparationConflict("建议片段与既有采纳建议重叠。")

        index = positions[0]
        updated = current_resume[:index] + replacement + current_resume[index + len(target):]
        if len(updated) > MAX_PRESET_TEXT:
            raise PreparationError("更新后简历正文过长。")

        version_id = _new_version_id()
        version = {
            "id": version_id,
            "parent_id": current_id,
            "name": f"应用建议 {suggestion_id[:8]}",
            "resume": updated,
            "source": {
                "kind": "suggestion",
                "suggestion_id": suggestion_id,
                "source_version_id": source_id,
                "source_span": {
                    "start": source_index,
                    "end": source_index + len(target),
                },
            },
            "created_at": now_iso(),
        }

        versions[version_id] = version
        data["current_version_id"] = version_id
        suggestion["status"] = "accepted"
        suggestion["accepted_version_id"] = version_id
        suggestion["accepted_at"] = now_iso()
        if suggestion.get("needs_confirmation"):
            suggestion["truth_confirmed"] = True
            suggestion["truth_confirmed_at"] = suggestion["accepted_at"]
        suggestion["source_span"] = {
            "start": source_index,
            "end": source_index + len(target),
        }
        data["revision"] = int(data.get("revision", 0)) + 1
        data["updated_at"] = now_iso()
        write_preparation(preset_id, data)
        return {
            "version": version,
            "revision": data["revision"],
            "current_version_id": version_id,
            "reused": False,
        }


def set_fact_decision(
    preset_id: str,
    fact_id: str,
    revision: int,
    decision: Literal["confirm", "reject"],
    text: str | None = None,
) -> dict[str, Any]:
    ensure_internal_id(fact_id, field="fact_id")
    if decision not in {"confirm", "reject"}:
        raise PreparationError("不支持的决定类型。")
    preset = get_preset_snapshot(preset_id)
    with locked_preparation(preset_id):
        data = _ensure_preparation(preset_id, preset)
        facts = data.setdefault("facts", {})
        fact = facts.get(fact_id)
        if not isinstance(fact, dict):
            raise PreparationNotFound("素材不存在。")

        normalized_text = _normalize_text(text or "", max_length=MAX_FACT_DECISION_TEXT, field_name="事实文本")
        if fact.get("superseded_by"):
            raise PreparationConflict("该素材已被新版本替代，请查看当前有效版本。")
        if decision == "reject" or not normalized_text or normalized_text == fact.get("text"):
            target_status = {"confirm": "confirmed", "reject": "rejected"}.get(decision)
            if target_status == fact.get("status"):
                return {**fact, "revision": data["revision"]}
        else:
            for existing in facts.values():
                if (isinstance(existing, dict)
                        and existing.get("origin_fact_id") == fact_id
                        and existing.get("text") == normalized_text
                        and existing.get("status") == "pending"
                        and not existing.get("superseded_by")):
                    return {**existing, "revision": data["revision"]}
        _check_revision(data, revision)

        source = fact.get("source", {})
        source_session_id = source.get("session_id", "")
        source_question_id = source.get("question_id", "")
        source_attempt = source.get("attempt", 1)
        source_quote = source.get("quote", fact.get("source_quote", ""))

        def _clone_child(fact_id: str, content: str, status: str) -> dict[str, Any]:
            return {
                "id": fact_id,
                "status": status,
                "text": content,
                "source": dict(source),
                "source_session_id": source_session_id,
                "source_question_id": source_question_id,
                "source_attempt": source_attempt,
                "source_quote": source_quote,
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "source_version_id": fact.get("source_version_id") or data.get("current_version_id"),
                "origin_fact_id": fact.get("id"),
                "confirmed_at": "",
                "superseded_by": None,
            }

        if decision == "reject":
            if fact.get("status") == "rejected":
                return {**fact, "revision": int(data.get("revision", 0))}
            fact["status"] = "rejected"
            fact["updated_at"] = now_iso()
            data["revision"] = int(data.get("revision", 0)) + 1
            write_preparation(preset_id, data)
            return {**fact, "revision": data["revision"]}

        current_text = str(fact.get("text", "")).strip()
        if normalized_text and normalized_text != current_text:
            pending_id = _new_fact_id()
            pending = _clone_child(pending_id, normalized_text, "pending")
            pending["created_at"] = now_iso()
            facts[pending_id] = pending
            data["revision"] = int(data.get("revision", 0)) + 1
            write_preparation(preset_id, data)
            return {**pending, "revision": data["revision"]}

        if fact.get("status") == "confirmed":
            return {**fact, "revision": int(data.get("revision", 0))}

        lineage = _fact_lineage(facts, fact_id)
        # Validate the whole family before mutating. Only a confirmed ancestor
        # may be replaced; confirming an old/sibling branch needs a fresh edit
        # from the effective version instead of silently overwriting evidence.
        for effective in _evidence_facts(facts):
            effective_id = effective["id"]
            effective_lineage = _fact_lineage(facts, effective_id)
            if effective_lineage[-1] == lineage[-1] and effective_id not in lineage:
                raise PreparationConflict("已有另一条素材修订被确认，请基于当前有效版本修改。")

        timestamp = now_iso()
        for ancestor_id in lineage[1:]:
            ancestor = facts[ancestor_id]
            ancestor["status"] = "superseded"
            ancestor["superseded_by"] = fact_id
            ancestor["superseded_at"] = timestamp
            ancestor["updated_at"] = timestamp
        fact["status"] = "confirmed"
        fact["confirmed_at"] = timestamp
        fact["updated_at"] = timestamp
        data["revision"] = int(data.get("revision", 0)) + 1
        write_preparation(preset_id, data)
        return {**fact, "revision": data["revision"]}


def _publish_session_facts(data: dict, *, session_id: str, resume_version_id: str, proposals: list[dict]) -> dict:
    """Caller validated model evidence and still holds session -> preparation locks.

    No file/lock acquisition here: validation and publication are one critical
    section in preparation_tasks. Existing decisions and revisions are preserved.
    """
    facts = data.setdefault("facts", {})
    published = []
    created_count = 0
    for proposal in proposals:
        source = {"kind": "session", "session_id": session_id, "question_id": proposal["question_id"],
                  "attempt": proposal["attempt"], "resume_version_id": resume_version_id}
        existing = next((fact for fact in facts.values() if fact.get("source") == source
                         and fact.get("source_quote") == proposal["quote"] and fact.get("text") == proposal["text"]), None)
        if existing is None:
            timestamp = now_iso()
            existing = {"id": _new_fact_id(), "status": "pending", "text": proposal["text"], "source": source,
                        "source_session_id": session_id, "source_question_id": proposal["question_id"], "source_attempt": proposal["attempt"],
                        "source_quote": proposal["quote"], "source_version_id": resume_version_id,
                        "created_at": timestamp, "updated_at": timestamp, "confirmed_at": "", "superseded_by": None}
            facts[existing["id"]] = existing
            created_count += 1
        if not any(fact["id"] == existing["id"] for fact in published):
            published.append(copy.deepcopy(existing))
    if created_count:
        data["revision"] = int(data.get("revision", 0)) + 1
        data["updated_at"] = now_iso()
    return {"facts": published, "created_count": created_count,
            "notice": "新素材仅为原回答片段，尚未确认真实性；请回到岗位准备逐条核对、编辑或拒绝。重复项保留已有确认状态，不会重新采纳。"}
