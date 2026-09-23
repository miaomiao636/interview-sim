"""Compatibility boundary for retired voice cleanup; no model processing.

The historical voice_input shape remains readable. A stale page receives an
explicit 410 instead of silently invoking the removed rewrite feature.
"""
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import store

router = APIRouter()
Status = Literal['not_requested', 'cleaned', 'unchanged', 'failed', 'rejected']


class VoiceInput(BaseModel):
    """Read/submit compatibility for previously captured evidence, not cleanup."""
    model_config = ConfigDict(extra='forbid', strict=True)
    raw_transcript: str = Field(min_length=1, max_length=50000)
    cleaned_transcript: str | None = Field(default=None, max_length=50000)
    cleanup_status: Status = 'not_requested'

    @model_validator(mode='after')
    def meaningful(self):
        if not self.raw_transcript.strip():
            raise ValueError('原始转写不能为空。')
        if self.cleanup_status in {'cleaned', 'unchanged'} and not (self.cleaned_transcript or '').strip():
            raise ValueError('历史整理记录必须带有对应文字。')
        return self


@router.post('/api/sessions/{session_id}/voice-cleanup', include_in_schema=False)
async def retired_cleanup(session_id: Annotated[str, Path(pattern=store.SESSION_ID_PATTERN)]):
    raise HTTPException(410, '语音整理功能已取消，不会调用模型。请刷新页面，核对原始转写后确认发送。')
