import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from . import db
from . import docker_runner
from . import files_store
from . import notify
from . import outwebhooks
from . import scheduler
from . import session_manager
from . import telegram
from .api import (
    agents,
    auth as auth_api,
    catalog,
    channels,
    files,
    guardian,
    outwebhooks as outwebhooks_api,
    projects,
    providers,
    schedules,
    sessions,
    telegram as telegram_api,
    webhooks,
    ws,
)
from .streamer import streams

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("vibeprod")

STATIC_DIR = Path(__file__).parent / "static"

MAIN_LOOP = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    if not docker_runner.daemon_ok():
        log.error(
            "Docker-демон недоступен — сессии и проверки провайдеров не запустятся. "
            "Проверьте: docker запущен на хосте; в compose брокеру примонтирован "
            "/var/run/docker.sock; у процесса есть права на него "
            "(sudo usermod -aG docker <user>)."
        )
    db.init_db()
    if not db.query_one("SELECT id FROM agents WHERE is_guardian=0 LIMIT 1"):
        default_project = db.query_one("SELECT id FROM projects ORDER BY id LIMIT 1")
        db.execute(
            "INSERT INTO agents(name, description, mode, model, system_prompt, permission, is_default, project_id) "
            "VALUES('general', 'Универсальный агент по умолчанию', 'primary', ?, ?, ?, 1, ?)",
            (
                os.environ.get("VIBEPROD_DEFAULT_MODEL", "deepseek/deepseek-chat"),
                "Ты полезный ассистент. Отвечай по-русски, кратко и по делу.",
                '{"edit": "allow", "bash": "allow"}',
                default_project["id"] if default_project else None,
            ),
        )
    try:
        from . import mcp_services

        pw = db.query_one("SELECT * FROM mcp_catalog WHERE name='playwright' AND service_container IS NOT NULL")
        if pw:
            await asyncio.to_thread(mcp_services.ensure_running, pw)
    except Exception:
        log.warning("playwright service not started", exc_info=True)
    await asyncio.to_thread(session_manager.reconcile)
    await session_manager.reattach_streamers()
    scheduler.init_scheduler(asyncio.get_running_loop())
    cleanup_task = asyncio.create_task(session_manager.cleanup_loop())
    streams.hooks.append(notify.on_session_done)
    await outwebhooks.requeue_pending()
    await telegram.start()
    log.info("vibeprod broker up")
    yield
    await telegram.stop()
    cleanup_task.cancel()
    for sid in list(streams.tasks):
        await streams.stop(sid)
    scheduler.stop_scheduler()


app = FastAPI(title="opencode Vibeprod", lifespan=lifespan)
app.include_router(auth_api.router)
app.include_router(agents.router)
app.include_router(broker_mcp_api.router)
app.include_router(catalog.router)
app.include_router(channels.router)
app.include_router(files.router)
app.include_router(guardian.router)
app.include_router(outwebhooks_api.router)
app.include_router(projects.router)
app.include_router(providers.router)
app.include_router(sessions.router)
app.include_router(schedules.router)
app.include_router(telegram_api.router)
app.include_router(webhooks.router)
app.include_router(ws.router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

PUBLIC_PATHS = {"/login", "/api/login", "/api/logout", "/api/auth"}


@app.middleware("http")
async def require_auth(request: Request, call_next):
    if not auth.ENABLED or request.scope["type"] != "http":
        return await call_next(request)
    path = request.url.path
    if (
        path in PUBLIC_PATHS
        or path.startswith("/static/")
        or path.startswith("/guardian/mcp")
        or path.startswith("/broker/mcp")
        or (path.startswith("/api/webhooks/") and path.endswith("/run"))
    ):
        return await call_next(request)
    if auth.check_request(request):
        return await call_next(request)
    if path in ("/api/files/content", "/api/files/stat") and _file_token_ok(request):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return RedirectResponse("/login")


def _file_token_ok(request: Request) -> bool:
    """Контент файлов доступен по токену проекта (ссылки для агентов)."""
    try:
        project_id = int(request.query_params.get("project_id") or "")
        return files_store.check_file_token(project_id, request.query_params.get("token") or "")
    except (TypeError, ValueError):
        return False


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health")
def health():
    """Статус деплоя: docker-демон, MinIO, число живых воркеров.

    Используется healthcheck'ом compose (curl /api/health) и smoke-тестом.
    """
    docker_ok = docker_runner.daemon_ok()
    s3_ok = files_store.healthy()
    workers = 0
    try:
        workers = len(docker_runner.list_vibeprod_containers())
    except Exception:
        pass
    return {
        "ok": docker_ok and s3_ok,
        "docker": docker_ok,
        "s3": s3_ok,
        "workers": workers,
    }


@app.get("/login")
def login_page(request: Request):
    if not auth.ENABLED or auth.check_request(request):
        return RedirectResponse("/")
    return FileResponse(str(STATIC_DIR / "login.html"))


def spawn_start(session_id, prompt, restart=False):
    async def _task():
        try:
            if restart:
                await session_manager.restart_session(session_id)
            else:
                await session_manager.start_session(session_id, initial_prompt=prompt)
        except Exception:
            log.exception("start %s", session_id)

    if MAIN_LOOP is None:
        log.error("no main loop, cannot start %s", session_id)
        return
    asyncio.run_coroutine_threadsafe(_task(), MAIN_LOOP)
