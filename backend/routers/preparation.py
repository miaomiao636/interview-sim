"""P1 API for job-preparation dossiers and immutable resume revisions."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .. import preparation_tasks

from ..preparation_store import (
    PreparationConflict,
    PreparationError,
    PreparationNotFound,
    accept_suggestion,
    create_version,
    get_preparation_view,
    ensure_internal_id,
    set_fact_decision,
)


router = APIRouter()


class VersionCreateRequest(BaseModel):
    revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=120)
    resume: str = Field(min_length=1, max_length=50000)
    parent_id: str = ""


class SuggestionAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=0)
    truth_confirmed: bool = False


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["resume", "recruitment", "materials"]
    version_id: str = Field(pattern=r"^[0-9a-f]{16,64}$")
    revision: int = Field(ge=0)


class EmptyTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FactDecisionRequest(BaseModel):
    revision: int = Field(ge=0)
    decision: Literal["confirm", "reject"]
    text: str = Field(default="", max_length=12000)


class SessionMaterialsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(pattern=r"^[0-9a-f]{8}$")
    revision: int = Field(ge=0)


def _bad_request(message: str):
    raise HTTPException(status_code=422, detail=message)


def _conflict(message: str):
    raise HTTPException(status_code=409, detail=message)


def _not_found(message: str):
    raise HTTPException(status_code=404, detail=message)


def _map_http_error(exc: Exception):
    if isinstance(exc, PreparationNotFound):
        _not_found(str(exc))
    if isinstance(exc, PreparationConflict):
        _conflict(str(exc))
    if isinstance(exc, (ValueError, PreparationError)):
        _bad_request(str(exc))
    raise exc


@router.get("/api/preparation/{preset_id}")
async def get_preparation(preset_id: str):
    try:
        ensure_internal_id(preset_id, field="preset_id")
        return preparation_tasks.get_preparation(preset_id)
    except Exception as exc:
        _map_http_error(exc)


@router.post("/api/preparation/{preset_id}/versions", status_code=201)
async def create_preparation_version(preset_id: str, req: VersionCreateRequest):
    try:
        ensure_internal_id(preset_id, field="preset_id")
        if req.parent_id:
            ensure_internal_id(req.parent_id, field="parent_id")
        return create_version(
            preset_id=preset_id,
            revision=req.revision,
            name=req.name,
            resume=req.resume,
            parent_id=req.parent_id or None,
        )
    except Exception as exc:
        _map_http_error(exc)


@router.post("/api/preparation/{preset_id}/suggestions/{suggestion_id}/accept")
async def accept_preparation_suggestion(preset_id: str, suggestion_id: str, req: SuggestionAcceptRequest):
    try:
        ensure_internal_id(preset_id, field="preset_id")
        ensure_internal_id(suggestion_id, field="suggestion_id")
        return accept_suggestion(preset_id=preset_id, suggestion_id=suggestion_id, revision=req.revision, truth_confirmed=req.truth_confirmed)
    except Exception as exc:
        _map_http_error(exc)


@router.post("/api/preparation/{preset_id}/facts/{fact_id}/decision")
async def decision_preparation_fact(preset_id: str, fact_id: str, req: FactDecisionRequest):
    try:
        ensure_internal_id(preset_id, field="preset_id")
        ensure_internal_id(fact_id, field="fact_id")
        return set_fact_decision(
            preset_id=preset_id,
            fact_id=fact_id,
            revision=req.revision,
            decision=req.decision,
            text=req.text,
        )
    except Exception as exc:
        _map_http_error(exc)


@router.post("/api/preparation/{preset_id}/session-materials", status_code=202)
async def ingest_session_materials(preset_id: str, req: SessionMaterialsRequest):
    try:
        ensure_internal_id(preset_id, field="preset_id")
        return preparation_tasks.create_session_material_task(preset_id, session_id=req.session_id, revision=req.revision)
    except Exception as exc:
        _map_http_error(exc)


@router.post("/api/preparation/{preset_id}/tasks", status_code=202)
async def create_preparation_task(preset_id: str, req: TaskCreateRequest):
    try:
        return preparation_tasks.create_task(preset_id, kind=req.kind, version_id=req.version_id, revision=req.revision)
    except Exception as exc:
        _map_http_error(exc)


@router.get("/api/preparation/{preset_id}/tasks/{task_id}")
async def get_preparation_task(preset_id: str, task_id: str):
    try:
        return preparation_tasks.get_task(preset_id, task_id)
    except Exception as exc:
        _map_http_error(exc)


@router.post("/api/preparation/{preset_id}/tasks/{task_id}/retry", status_code=202)
async def retry_preparation_task(preset_id: str, task_id: str, req: EmptyTaskRequest = EmptyTaskRequest()):
    try:
        return preparation_tasks.retry_task(preset_id, task_id)
    except Exception as exc:
        _map_http_error(exc)


@router.post("/api/preparation/{preset_id}/tasks/{task_id}/cancel")
async def cancel_preparation_task(preset_id: str, task_id: str, req: EmptyTaskRequest = EmptyTaskRequest()):
    try:
        return preparation_tasks.cancel_task(preset_id, task_id)
    except Exception as exc:
        _map_http_error(exc)
