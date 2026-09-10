"""FastAPI 入口"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import config
from .routers import chat, documents, plan, presets, review, settings

app = FastAPI(title="interview-sim", version="1.5.0")
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "testserver"],
)

# 注册 API 路由（先注册，优先匹配）
app.include_router(plan.router)
app.include_router(chat.router)
app.include_router(review.router)
app.include_router(documents.router)
app.include_router(settings.router)
app.include_router(presets.router)

# 前端静态文件目录
frontend_dir = Path(__file__).parent.parent / "frontend"


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "provider": "MiMo",
        "api_key_configured": bool(config.XIAOMI_API_KEY.strip()),
        "local_only": True,
        "capabilities": {role: bool(config.get_connection(role)["api_key"]) for role in ("chat", "analysis", "asr", "tts")},
    }


@app.get("/")
async def root():
    """返回前端首页"""
    index = frontend_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"error": "frontend not found"}


# 静态资源（CSS/JS）挂载在 /assets 下
if frontend_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dir)), name="static")
