"""首次启动与本地配置 API。"""
from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from .. import config
from ..xiaomi_client import reset_client


router = APIRouter()


class ProfileSettings(BaseModel):
    display_name: str = Field(default="", max_length=80)
    target_role: str = Field(default="", max_length=160)
    coaching_goal: str = Field(default="", max_length=500)


class AISettings(BaseModel):
    base_url: str = Field(min_length=8, max_length=500)
    api_key: str = Field(default="", max_length=4096)
    llm_model: str = Field(min_length=1, max_length=160)
    llm_model_pro: str = Field(min_length=1, max_length=160)
    asr_model: str = Field(min_length=1, max_length=160)
    tts_model: str = Field(min_length=1, max_length=160)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parsed = urlparse(value)
        is_loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("请输入有效的 HTTP(S) API 地址")
        if parsed.scheme != "https" and not is_loopback:
            raise ValueError("远程 API 必须使用 HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("API 地址不能包含用户名、密码、查询参数或片段；密钥请填入 Key 栏")
        return value


class InterviewDefaults(BaseModel):
    persona: Literal["技术负责人", "HR", "业务总监", "压力面", "英文面"] = "技术负责人"
    difficulty: Literal["初级", "标准", "进阶", "压力"] = "标准"
    voice: Literal["白桦", "苏打", "冰糖", "茉莉"] = "白桦"


class ConnectionSettings(BaseModel):
    inherit: bool = True
    base_url: str = Field(default="", max_length=500)
    api_key: str = Field(default="", max_length=4096)
    protocol: Literal["mimo", "openai"] = "openai"
    voice: str = Field(default="", max_length=100)
    sample_rate: Literal[16000, 22050, 24000, 44100, 48000] = 24000

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return AISettings.validate_base_url(value) if value.strip() else ""

    @model_validator(mode="after")
    def require_independent_url(self):
        if not self.inherit and not self.base_url:
            raise ValueError("独立连接必须填写 API 地址")
        return self


class SettingsUpdate(BaseModel):
    profile: ProfileSettings
    ai: AISettings
    defaults: InterviewDefaults
    connections: dict[Literal["chat", "analysis", "asr", "tts"], ConnectionSettings] = Field(default_factory=dict)


@router.get("/api/settings")
async def get_settings():
    return config.get_public_settings()


@router.put("/api/settings")
async def update_settings(req: SettingsUpdate):
    try:
        public = config.save_local_settings(req.model_dump())
        config.reload_runtime_settings()
        reset_client()
        return public
    except OSError as exc:
        raise HTTPException(status_code=500, detail="本地配置保存失败，请检查 ~/.interview-sim 目录权限。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
