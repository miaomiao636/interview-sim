"""Single-process, explicitly started preparation tasks with immutable inputs.

The provider key exists only in the runner's memory. JSON progress is atomic under
the same dossier lock as resume/fact changes; no lock is held across an await.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import uuid
from contextlib import contextmanager, ExitStack
from urllib.parse import urlsplit

from . import config, preparation_store as store, store as sessions
from .preparation_models import PROMPT_VERSION, RESUME_PROMPT_VERSION, RUBRIC_VERSION, SCHEMAS, build_messages, validate_result
from .structured import generate_object, public_model_error
from .xiaomi_client import chat_once

_PROCESS_ID = uuid.uuid4().hex
_running = {}
MAX_INPUT_CHARS = 180000
MAX_ACTIVE_TASKS = 4


def _runtime_key(preset_id, task_id, generation):
    return (str(config.DATA_DIR.resolve()), preset_id, task_id, generation)


def _connection_metadata(connection, kind=None):
    address = connection.get("base_url", "")
    parsed = urlsplit(address)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise store.PreparationError("分析 API 地址不能包含账户密码、查询凭据或片段，请在设置中修正。")
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise store.PreparationError("请先配置有效的分析 API 地址。")
    return {"base_url": address, "protocol": connection.get("protocol", "openai"), "model": config.LLM_MODEL_PRO,
            "prompt_version": RESUME_PROMPT_VERSION if kind == 'resume' else PROMPT_VERSION, "rubric_version": RUBRIC_VERSION}


def _version(data, preset, version_id):
    store.ensure_internal_id(version_id, field="version_id")
    version = data.get("versions", {}).get(version_id)
    if not version:
        baseline = store._snapshot_preparation(preset["id"], preset, store._preset_signature(preset))
        if baseline["id"] == version_id:
            version = baseline
    if not version:
        raise store.PreparationNotFound("所选简历版本不属于该岗位或已不存在。")
    return version


def _inputs(kind, data, preset, version_id, connection):
    version = _version(data, preset, version_id)
    facts = sorted(store._evidence_facts(data.get("facts", {})), key=lambda item: item["id"])
    sources = {"resume": version["resume"], **{"fact:" + item["id"]: item["text"] for item in facts}}
    job = {key: preset.get(key, "") for key in ("id", "company", "target_role", "jd", "company_context", "coaching_goal")}
    material = {"kind": kind, "version_id": version_id, "job": job, "resume_version": copy.deepcopy(version),
                "sources": sources, "confirmed_facts": [{"id": f["id"], "text": f["text"], "confirmed_at": f.get("confirmed_at", "")} for f in facts],
                "preset_signature": store._preset_signature(preset), "provider": _connection_metadata(connection, kind)}
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True)
    if len(encoded) > MAX_INPUT_CHARS:
        raise store.PreparationError("当前岗位材料过长，请精简 JD、简历或已确认素材后再运行准备任务。")
    material["input_fingerprint"] = hashlib.sha256(encoded.encode()).hexdigest()
    material["current_version_id"] = data.get("current_version_id")
    return material


def _session_inputs(data, preset, session, connection):
    """Pure validation on snapshots held under session -> preparation locks."""
    if session is None:
        raise store.PreparationNotFound("面试记录不存在。")
    cfg = session.get("config", {})
    if cfg.get("preset_id") != preset["id"]:
        raise store.PreparationConflict("该面试未绑定当前岗位，旧的未关联面试不能自动提取素材。")
    if (session.get("status") not in {"ended", "completed", "interview_finished", "review_failed"}
            or session.get("active_question") or (session.get("report_job") or {}).get("status") == "running"):
        raise store.PreparationConflict("请先结束面试或重答，并等待正在生成的报告完成后再提取素材。")
    version_id = cfg.get("resume_version_id")
    if not version_id:
        raise store.PreparationConflict("本场面试缺少简历版本来源，不能自动提取素材。")
    _version(data, preset, version_id)
    if data.get("preset_signature") != store._preset_signature(preset):
        raise store.PreparationConflict("岗位输入已更新，请先保存当前岗位输入快照的新版本再提取素材。")
    turns = []
    seen = set()
    for turn in session.get("turns", []):
        answer = turn.get("answer", "")
        if not isinstance(answer, str) or not answer.strip() or turn.get("status", "answered") != "answered":
            continue
        qid, attempt = turn.get("question_id"), turn.get("attempt", 1)
        if not isinstance(qid, str) or not qid.strip() or len(qid) > 240 or type(attempt) is not int or attempt < 1 or (qid, attempt) in seen:
            raise store.PreparationError("原始作答的题号或作答次数不完整或重复，不能提取素材。")
        seen.add((qid, attempt))
        turns.append({"question_id": qid, "attempt": attempt, "question": turn.get("question", ""), "answer": answer})
    if not turns:
        raise store.PreparationError("本场没有非空的已回答内容，暂不能提取经历素材。")
    source = {"materials": store.launch_material_fingerprint(cfg), "turns": session.get("turns", [])}
    source_fingerprint = hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    material = {"kind": "session_materials", "session_id": session["id"], "version_id": version_id,
                "resume_version_id": version_id, "turns": turns, "source_fingerprint": source_fingerprint,
                "preset_signature": store._preset_signature(preset), "provider": _connection_metadata(connection)}
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True)
    if len(encoded) > MAX_INPUT_CHARS:
        raise store.PreparationError("本场回答内容过长，暂不能整体提取；请手动整理并确认经历素材。")
    material["input_fingerprint"] = hashlib.sha256(encoded.encode()).hexdigest()
    return material


@contextmanager
def _locked_sources(source_ids):
    """Never entered with a preparation lock held. Sorted for dossier reads."""
    from .routers.review import recover_report_job
    with ExitStack() as stack:
        source_sessions = {}
        for sid in sorted(set(source_ids)):
            sessions.ensure_session_id(sid)
            stack.enter_context(sessions.locked_session(sid))
        for sid in sorted(set(source_ids)):
            source_sessions[sid] = recover_report_job(sid)
        yield source_sessions


def _source_ids(preset_id, task_id=None):
    # Atomic JSON read is only discovery; state is re-read under locks below.
    discovered = (store.read_preparation(preset_id) or {}).get("tasks", {})
    selected = [discovered[task_id]] if task_id in discovered else ([] if task_id else discovered.values())
    return [task["inputs"]["session_id"] for task in selected if task.get("kind") == "session_materials"]


def _stale(task, data, preset, source_sessions=None):
    try:
        if task["kind"] == "session_materials":
            current = _session_inputs(data, preset, (source_sessions or {}).get(task["session_id"]), config.get_connection("analysis"))
        else:
            current = _inputs(task["kind"], data, preset, task["version_id"], config.get_connection("analysis"))
        return current["input_fingerprint"] != task["input_fingerprint"]
    except (store.PreparationError, ValueError, KeyError):
        return True


def _recover_orphans(preset_id, data):
    changed = False
    for task in data.get("tasks", {}).values():
        key = _runtime_key(preset_id, task["id"], task["generation"])
        runner = _running.get(key)
        if task["status"] == "running" and (task.get("runner_id") != _PROCESS_ID or runner is None or runner.done()):
            task.update(status="interrupted", stage="服务已重启或任务中断", error="任务已中断，请明确重试；已发送的请求可能已产生用量。", updated_at=store.now_iso())
            changed = True
    if changed:
        store.write_preparation(preset_id, data)


def _public(task, data, preset, source_sessions=None):
    fields = ("id", "kind", "session_id", "version_id", "generation", "status", "stage", "started_at", "updated_at", "error", "result", "input_fingerprint", "preset_signature", "proposals_published")
    public = {key: copy.deepcopy(task.get(key)) for key in fields}
    public["stale"] = _stale(task, data, preset, source_sessions)
    return public


def _envelope(task, data, preset, source_sessions=None):
    return {"task": _public(task, data, preset, source_sessions), "revision": int(data.get("revision", 0))}


def get_preparation(preset_id):
    preset = store.get_preset_snapshot(preset_id)
    with _locked_sources(_source_ids(preset_id)) as source_sessions, store.locked_preparation(preset_id):
        data = store.read_preparation(preset_id)
        if data:
            _recover_orphans(preset_id, data)
            public_tasks = [_public(task, data, preset, source_sessions) for task in data.get("tasks", {}).values()]
        else:
            public_tasks = []
        view = store.get_preparation_view(preset_id)
        view["tasks"] = public_tasks
        return view


def get_task(preset_id, task_id):
    store.ensure_internal_id(task_id, field="task_id")
    preset = store.get_preset_snapshot(preset_id)
    with _locked_sources(_source_ids(preset_id, task_id)) as source_sessions, store.locked_preparation(preset_id):
        data = store.read_preparation(preset_id)
        if not data or task_id not in data.get("tasks", {}):
            raise store.PreparationNotFound("准备任务不存在。")
        _recover_orphans(preset_id, data)
        return _envelope(data["tasks"][task_id], data, preset, source_sessions)


def _schedule(preset_id, task, connection):
    if sum(not item.done() for item in _running.values()) >= MAX_ACTIVE_TASKS:
        raise store.PreparationConflict("已有多个准备任务执行中，请等待完成或取消后再试。")
    key = _runtime_key(preset_id, task["id"], task["generation"])
    runner = asyncio.create_task(_execute(preset_id, task["id"], task["generation"], copy.deepcopy(task["inputs"]), dict(connection)))
    _running[key] = runner
    def finished(completed):
        if _running.get(key) is completed:
            _running.pop(key, None)
    runner.add_done_callback(finished)


def create_task(preset_id, *, kind, version_id, revision):
    if kind not in {"resume", "recruitment", "materials"}:
        raise store.PreparationError("未知准备任务类型。")
    preset = store.get_preset_snapshot(preset_id)
    connection = dict(config.get_connection("analysis"))
    if not connection.get("api_key"):
        raise store.PreparationError("请先在系统设置配置分析模型 API Key。")
    with store.locked_preparation(preset_id):
        data = store._ensure_preparation(preset_id, preset)
        _recover_orphans(preset_id, data)
        store._check_revision(data, revision)
        inputs = _inputs(kind, data, preset, version_id, connection)
        for previous in reversed(list(data["tasks"].values())):
            if previous["input_fingerprint"] == inputs["input_fingerprint"] and previous["status"] in {"running", "completed"}:
                if previous["status"] == "running" or kind != "resume" or previous.get("proposals_published"):
                    return _envelope(previous, data, preset)
        task_id = store.new_id()
        task = {"id": task_id, "kind": kind, "version_id": version_id, "generation": 1, "status": "running", "stage": "正在分析已选材料", "started_at": store.now_iso(),
                "updated_at": store.now_iso(), "error": "", "result": None, "inputs": inputs, "input_fingerprint": inputs["input_fingerprint"],
                "preset_signature": inputs["preset_signature"], "runner_id": _PROCESS_ID, "proposals_published": False}
        # Fail before persisting an in-flight task if the local capacity is full.
        _schedule(preset_id, task, connection)
        data["versions"].setdefault(version_id, copy.deepcopy(inputs["resume_version"]))
        data["tasks"][task_id] = task
        store.write_preparation(preset_id, data)
        return _envelope(task, data, preset)


def create_session_material_task(preset_id, *, session_id, revision):
    sessions.ensure_session_id(session_id)
    store.ensure_internal_id(preset_id, field="preset_id")
    connection = dict(config.get_connection("analysis"))
    with _locked_sources([session_id]) as source_sessions, store.locked_preparation(preset_id):
        preset = store.get_preset_snapshot(preset_id)
        data = store._ensure_preparation(preset_id, preset)
        _recover_orphans(preset_id, data)
        store._check_revision(data, revision)
        inputs = _session_inputs(data, preset, source_sessions[session_id], connection)
        if not connection.get("api_key"):
            raise store.PreparationError("请先在系统设置配置分析模型 API Key。")
        for previous in reversed(list(data["tasks"].values())):
            if previous["input_fingerprint"] == inputs["input_fingerprint"] and previous["status"] in {"running", "completed"}:
                return _envelope(previous, data, preset, source_sessions)
        task_id = store.new_id()
        task = {"id": task_id, "kind": "session_materials", "session_id": session_id, "version_id": inputs["version_id"],
                "generation": 1, "status": "running", "stage": "正在从原回答提取待确认经历", "started_at": store.now_iso(),
                "updated_at": store.now_iso(), "error": "", "result": None, "inputs": inputs, "input_fingerprint": inputs["input_fingerprint"],
                "preset_signature": inputs["preset_signature"], "runner_id": _PROCESS_ID, "proposals_published": False}
        _schedule(preset_id, task, connection)
        data["versions"].setdefault(inputs["version_id"], copy.deepcopy(_version(data, preset, inputs["version_id"])))
        data["tasks"][task_id] = task
        store.write_preparation(preset_id, data)
        return _envelope(task, data, preset, source_sessions)


def cancel_task(preset_id, task_id):
    store.ensure_internal_id(task_id, field="task_id")
    preset = store.get_preset_snapshot(preset_id)
    with _locked_sources(_source_ids(preset_id, task_id)) as source_sessions, store.locked_preparation(preset_id):
        data = store.read_preparation(preset_id)
        if not data or task_id not in data.get("tasks", {}):
            raise store.PreparationNotFound("准备任务不存在。")
        task = data["tasks"][task_id]
        if task["status"] == "running":
            key = _runtime_key(preset_id, task_id, task["generation"])
            task.update(status="cancelled", stage="已取消本地后续处理", updated_at=store.now_iso(), error="已发送的远程请求仍可能产生用量。")
            store.write_preparation(preset_id, data)
            runner = _running.get(key)
            if runner:
                runner.cancel()
        return _envelope(task, data, preset, source_sessions)


def retry_task(preset_id, task_id):
    store.ensure_internal_id(task_id, field="task_id")
    preset = store.get_preset_snapshot(preset_id)
    connection = dict(config.get_connection("analysis"))
    if not connection.get("api_key"):
        raise store.PreparationError("请先在系统设置配置分析模型 API Key。")
    with _locked_sources(_source_ids(preset_id, task_id)) as source_sessions, store.locked_preparation(preset_id):
        data = store.read_preparation(preset_id)
        if not data or task_id not in data.get("tasks", {}):
            raise store.PreparationNotFound("准备任务不存在。")
        _recover_orphans(preset_id, data)
        task = data["tasks"][task_id]
        if task["status"] not in {"failed", "cancelled", "interrupted"}:
            raise store.PreparationConflict("仅失败、中断或取消的任务可以重试。")
        if _stale(task, data, preset, source_sessions):
            raise store.PreparationConflict("任务输入已变化，请基于当前材料创建新任务。")
        task.update(generation=task["generation"] + 1, status="running", stage="正在重新分析原始材料", error="", updated_at=store.now_iso(), started_at=store.now_iso(), runner_id=_PROCESS_ID)
        if task["kind"] != "session_materials":
            task["inputs"]["current_version_id"] = data.get("current_version_id")
        _schedule(preset_id, task, connection)
        store.write_preparation(preset_id, data)
        return _envelope(task, data, preset, source_sessions)


def _progress(preset_id, task_id, generation, **changes):
    with store.locked_preparation(preset_id):
        data = store.read_preparation(preset_id)
        task = (data or {}).get("tasks", {}).get(task_id)
        if not task or task["generation"] != generation or task["status"] != "running":
            return
        task.update(**changes, updated_at=store.now_iso())
        store.write_preparation(preset_id, data)


async def _execute(preset_id, task_id, generation, inputs, connection):
    source_ids = [inputs["session_id"]] if inputs["kind"] == "session_materials" else []
    async def call(**kwargs):
        # generate_object may request one correction after a provider swallowed
        # cancellation. Guard every billable request, not only final writes.
        with _locked_sources(source_ids) as source_sessions, store.locked_preparation(preset_id):
            data = store.read_preparation(preset_id)
            task = (data or {}).get("tasks", {}).get(task_id)
            runner = _running.get(_runtime_key(preset_id, task_id, generation))
            if (not task or task["status"] != "running" or task["generation"] != generation
                    or task.get("runner_id") != _PROCESS_ID or runner is None or runner.done()):
                raise asyncio.CancelledError
            if source_ids and _stale(task, data, store.get_preset_snapshot(preset_id), source_sessions):
                raise store.PreparationConflict("本场面试、岗位或分析配置已变化，已停止后续提取。请核对后重新创建任务。")
        return await chat_once(**kwargs, connection_snapshot=connection)
    try:
        result = await generate_object(call, build_messages(inputs["kind"], inputs), model=inputs["provider"]["model"],
                                       validate=lambda raw: validate_result(inputs["kind"], raw, inputs), max_tokens=5000,
                                       on_retry=lambda: _progress(preset_id, task_id, generation, stage="正在修正输出结构（最多一次）"))
        with _locked_sources(source_ids) as source_sessions, store.locked_preparation(preset_id):
            data = store.read_preparation(preset_id)
            task = (data or {}).get("tasks", {}).get(task_id)
            if not task or task["generation"] != generation or task["status"] != "running":
                return
            preset = store.get_preset_snapshot(preset_id)
            publish = not _stale(task, data, preset, source_sessions)
            if source_ids:
                if not publish:
                    raise store.PreparationConflict("原面试已改变或开始重答，未发布旧的提取结果。请结束后重新创建任务。")
                result = store._publish_session_facts(data, session_id=inputs["session_id"], resume_version_id=inputs["version_id"], proposals=result["facts"])
            else:
                publish = publish and data.get("current_version_id") == inputs["current_version_id"]
            if inputs["kind"] == "resume":
                if publish:
                    for suggestion in result["suggestions"]:
                        suggestion["source_task_id"] = task_id
                        data["suggestions"][suggestion["id"]] = copy.deepcopy(suggestion)
                    if result["suggestions"]:
                        data["revision"] += 1
                else:
                    for suggestion in result["suggestions"]:
                        suggestion.update(applicable=False, warning="运行期间材料、模型或当前简历版本已改变。请基于所选材料重新诊断。")
            task.update(status="completed", stage="准备结果已保存" if publish else "已保存旧输入结果，未发布修改建议", result=result, error="", proposals_published=publish, updated_at=store.now_iso())
            store.write_preparation(preset_id, data)
    except asyncio.CancelledError:
        _progress(preset_id, task_id, generation, status="interrupted", stage="任务已中断", error="任务已中断，请明确重试。")
    except store.PreparationError as exc:
        _progress(preset_id, task_id, generation, status="failed", stage="材料已变化或当前任务不可继续", error=str(exc))
    except Exception as exc:
        _progress(preset_id, task_id, generation, status="failed", stage="当前任务失败，可重试", error=public_model_error(exc))


def validate_suggestion_context(preset_id, suggestion, *, data, preset):
    """Called by accept_suggestion while its dossier lock is already held."""
    if suggestion.get("status") == "accepted" or not suggestion.get("source_task_id"):
        return
    task = data.get("tasks", {}).get(suggestion["source_task_id"])
    if not task or _stale(task, data, preset):
        raise store.PreparationConflict("建议依赖的岗位、确认素材或分析配置已变化，请重新诊断。")


def resolve_launch(preset_id, version_id):
    preset = store.get_preset_snapshot(preset_id)
    with store.locked_preparation(preset_id):
        data = store._ensure_preparation(preset_id, preset)
        version = _version(data, preset, version_id)
        new_baseline = version_id not in data["versions"]
        if not store.read_preparation(preset_id) or new_baseline:
            data["versions"].setdefault(version_id, copy.deepcopy(version))
            store.write_preparation(preset_id, data)
        snapshot = {"schema_version": 1, "preset_id": preset_id, "resume_version_id": version_id, "preset_signature": store._preset_signature(preset),
                    "resume_assessment": None, "requirements": [], "provenance": {},
                    "material_fingerprint": store.launch_material_fingerprint({**preset, "preset_id": preset_id,
                                                                                "resume_version_id": version_id, "resume": version["resume"]})}
        for task in data.get("tasks", {}).values():
            if task["status"] != "completed" or task["version_id"] != version_id or _stale(task, data, preset):
                continue
            if task["kind"] == "resume":
                snapshot["resume_assessment"] = copy.deepcopy(task["result"]["assessment"])
            elif task["kind"] == "recruitment":
                snapshot["requirements"] = copy.deepcopy(task["result"]["requirements"])
            snapshot["provenance"][task["kind"]] = {"task_id": task["id"], "input_fingerprint": task["input_fingerprint"], **task["inputs"]["provider"]}
        return {"job": copy.deepcopy(preset), "version": copy.deepcopy(version), "preparation_snapshot": snapshot}


async def shutdown_tasks():
    """Cancel local runners on shutdown; do not restart persisted work implicitly."""
    active = list(_running.items())
    # Persist invalidation first. A remote transport may suppress cancellation;
    # such a response must not publish or launch a structural corrective retry.
    for key, runner in active:
        root, preset_id, task_id, generation = key
        if root == str(config.DATA_DIR.resolve()) and not runner.done():
            _progress(preset_id, task_id, generation, status="interrupted", stage="服务正在关闭，任务已中断",
                      error="服务已关闭，请重启后明确重试；已发送请求仍可能产生用量。")
    runners = [runner for _, runner in active]
    for runner in runners:
        runner.cancel()
    if runners:
        await asyncio.gather(*runners, return_exceptions=True)
    _running.clear()
