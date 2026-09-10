"""本地运行配置。

环境变量仍可作为默认值；网页保存的本地配置优先级更高。包含 API Key 的
``~/.interview-sim/config.json`` 只保存在当前电脑，并以 0600 权限写入。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parent.parent
_env_path = ROOT_DIR / "config" / ".env"
load_dotenv(_env_path)

DATA_DIR = Path(os.getenv("INTERVIEW_SIM_HOME", str(Path.home() / ".interview-sim"))).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)
try:
    os.chmod(DATA_DIR, 0o700)
except OSError:
    pass
LOCAL_SETTINGS_PATH = DATA_DIR / "config.json"

DEFAULTS = {
    "profile": {
        "display_name": "",
        "target_role": "",
        "coaching_goal": "",
    },
    "ai": {
        "base_url": "https://api.xiaomimimo.com/v1",
        "llm_model": "mimo-v2.5",
        "llm_model_pro": "mimo-v2.5-pro",
        "asr_model": "mimo-v2.5-asr",
        "tts_model": "mimo-v2.5-tts",
    },
    "defaults": {
        "persona": "技术负责人",
        "difficulty": "标准",
        "voice": "白桦",
    },
}


def _read_local_settings() -> dict[str, Any]:
    try:
        data = json.loads(LOCAL_SETTINGS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _nested(data: dict[str, Any], group: str, key: str, fallback: str) -> str:
    group_data = data.get(group)
    if not isinstance(group_data, dict):
        return fallback
    value = group_data.get(key)
    return str(value).strip() if value is not None else fallback


def reload_runtime_settings() -> None:
    """重新加载可在运行时修改的模型配置。"""
    global XIAOMI_BASE_URL, XIAOMI_API_KEY
    global LLM_MODEL, LLM_MODEL_PRO, ASR_MODEL, TTS_MODEL, TTS_VOICE

    local = _read_local_settings()
    XIAOMI_BASE_URL = _nested(
        local, "ai", "base_url", os.getenv("XIAOMI_BASE_URL", DEFAULTS["ai"]["base_url"])
    )
    XIAOMI_API_KEY = _nested(local, "ai", "api_key", os.getenv("XIAOMI_API_KEY", ""))
    LLM_MODEL = _nested(local, "ai", "llm_model", os.getenv("LLM_MODEL", DEFAULTS["ai"]["llm_model"]))
    LLM_MODEL_PRO = _nested(
        local, "ai", "llm_model_pro", os.getenv("LLM_MODEL_PRO", DEFAULTS["ai"]["llm_model_pro"])
    )
    ASR_MODEL = _nested(local, "ai", "asr_model", os.getenv("ASR_MODEL", DEFAULTS["ai"]["asr_model"]))
    TTS_MODEL = _nested(local, "ai", "tts_model", os.getenv("TTS_MODEL", DEFAULTS["ai"]["tts_model"]))
    TTS_VOICE = _nested(local, "defaults", "voice", os.getenv("TTS_VOICE", DEFAULTS["defaults"]["voice"]))


def get_public_settings() -> dict[str, Any]:
    """返回可在页面显示的配置，永不返回 API Key 明文。"""
    local = _read_local_settings()
    profile = {
        key: _nested(local, "profile", key, value)
        for key, value in DEFAULTS["profile"].items()
    }
    ai = {
        "base_url": _nested(local, "ai", "base_url", XIAOMI_BASE_URL),
        "llm_model": _nested(local, "ai", "llm_model", LLM_MODEL),
        "llm_model_pro": _nested(local, "ai", "llm_model_pro", LLM_MODEL_PRO),
        "asr_model": _nested(local, "ai", "asr_model", ASR_MODEL),
        "tts_model": _nested(local, "ai", "tts_model", TTS_MODEL),
        "has_api_key": bool(_nested(local, "ai", "api_key", XIAOMI_API_KEY)),
    }
    defaults = {
        "persona": _nested(local, "defaults", "persona", DEFAULTS["defaults"]["persona"]),
        "difficulty": _nested(local, "defaults", "difficulty", DEFAULTS["defaults"]["difficulty"]),
        "voice": _nested(local, "defaults", "voice", TTS_VOICE),
    }
    return {
        "profile": profile,
        "ai": ai,
        "connections": public_connections(),
        "defaults": defaults,
        "is_first_run": not all(get_connection(role)["api_key"] for role in ("chat", "analysis")) or not profile["target_role"],
    }


def get_connection(role: str) -> dict[str, Any]:
    """Resolve each capability independently; old installations inherit the shared connection."""
    local = _read_local_settings()
    entry = (local.get("connections") or {}).get(role) or {}
    inherited = entry.get("inherit", True)
    shared = local.get("ai") or {}
    return {
        "inherit": inherited,
        "base_url": (shared.get("base_url", XIAOMI_BASE_URL) if inherited else entry.get("base_url", "")),
        "api_key": (shared.get("api_key", XIAOMI_API_KEY) if inherited else entry.get("api_key", "")),
        "protocol": entry.get("protocol", "mimo" if role in {"asr", "tts"} else "openai"),
        "voice": entry.get("voice", ""),
        "sample_rate": entry.get("sample_rate", 24000),
    }


def public_connections() -> dict[str, Any]:
    result = {}
    for role in ("chat", "analysis", "asr", "tts"):
        entry = get_connection(role)
        entry["has_api_key"] = bool(entry.pop("api_key"))
        result[role] = entry
    return result


def save_local_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """原子写入白名单配置；空密钥表示保留已有密钥。"""
    current = _read_local_settings()
    current_ai = current.get("ai") if isinstance(current.get("ai"), dict) else {}
    incoming_ai = payload.get("ai") if isinstance(payload.get("ai"), dict) else {}
    incoming_key = str(incoming_ai.get("api_key", "")).strip()
    existing_key = str(current_ai.get("api_key", "")).strip()

    clean = {
        "profile": {
            key: str((payload.get("profile") or {}).get(key, "")).strip()
            for key in DEFAULTS["profile"]
        },
        "ai": {
            key: str(incoming_ai.get(key, default)).strip()
            for key, default in DEFAULTS["ai"].items()
        },
        "defaults": {
            key: str((payload.get("defaults") or {}).get(key, default)).strip()
            for key, default in DEFAULTS["defaults"].items()
        },
    }
    if incoming_key or existing_key:
        clean["ai"]["api_key"] = incoming_key or existing_key

    # An old client must not erase newer per-capability connection settings.
    clean["connections"] = dict(current.get("connections") or {})
    for role, incoming in (payload.get("connections") or {}).items():
        if role not in {"chat", "analysis", "asr", "tts"}:
            continue
        previous = clean["connections"].get(role) or {}
        entry = {key: incoming[key] for key in ("inherit", "base_url", "protocol", "voice", "sample_rate") if key in incoming}
        new_key = str(incoming.get("api_key", "")).strip()
        # Never silently forward an old provider's secret to a new address.
        if not entry.get("inherit", True) and entry.get("base_url") != previous.get("base_url") and not new_key:
            raise ValueError("更改独立服务地址时，请重新填写该服务的 API Key。")
        entry["api_key"] = new_key or previous.get("api_key", "")
        clean["connections"][role] = entry

    if (existing_key or XIAOMI_API_KEY) and clean["ai"]["base_url"] != current_ai.get("base_url", XIAOMI_BASE_URL) and not incoming_key:
        raise ValueError("更改默认服务地址时，请重新填写 API Key，避免把旧密钥发往新服务。")

    LOCAL_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(LOCAL_SETTINGS_PATH.parent, 0o700)
    except OSError:
        pass
    temp_path = LOCAL_SETTINGS_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temp_path, 0o600)
    temp_path.replace(LOCAL_SETTINGS_PATH)
    os.chmod(LOCAL_SETTINGS_PATH, 0o600)
    return get_public_settings()


reload_runtime_settings()

# 面试记录存储
SESSION_DIR = Path(os.getenv("SESSION_DIR", str(DATA_DIR / "sessions"))).expanduser()
SESSION_DIR.mkdir(parents=True, exist_ok=True)
try:
    os.chmod(SESSION_DIR, 0o700)
except OSError:
    pass

# 服务端口
PORT = int(os.getenv("PORT", "8800"))
