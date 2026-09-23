"""FastAPI 入口"""
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import config
from . import voice_cleanup
from .routers import chat, documents, plan, presets, preparation, review, settings

@asynccontextmanager
async def lifespan(_app):
    try:
        yield
    finally:
        from .preparation_tasks import shutdown_tasks
        await shutdown_tasks()
        await chat.shutdown_question_jobs()
        await review.shutdown_report_jobs()


app = FastAPI(title="interview-sim", version="1.5.0", lifespan=lifespan)
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
app.include_router(preparation.router)
app.include_router(voice_cleanup.router)


@app.middleware('http')
async def revalidate_workbench(request, call_next):
    response = await call_next(request)
    if request.url.path == '/' or request.url.path.startswith('/assets/'):
        # Local upgrades must not combine new markup with a cached old runtime.
        response.headers['Cache-Control'] = 'no-cache'
    return response

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
