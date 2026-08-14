import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from . import scheduler
from . import session_manager
from . import telegram
from .api import agents, catalog, channels, guardian, projects, providers, schedules, sessions, telegram as telegram_api, webhooks, ws
from .streamer import streams

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("harness")

STATIC_DIR = Path(__file__).parent / "static"

MAIN_LOOP = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    db.init_db()
    if not db.query_one("SELECT id FROM agents WHERE is_guardian=0 LIMIT 1"):
        default_project = db.query_one("SELECT id FROM projects ORDER BY id LIMIT 1")
        db.execute(
            "INSERT INTO agents(name, description, mode, model, system_prompt, permission, is_default, project_id) "
            "VALUES('general', 'Универсальный агент по умолчанию', 'primary', ?, ?, '\"allow\"', 1, ?)",
            (os.environ.get("HARNESS_DEFAULT_MODEL", "deepseek/deepseek-chat"),
             "Ты полезный ассистент. Отвечай по-русски, кратко и по делу.",
             default_project["id"] if default_project else None),
        )
    await asyncio.to_thread(session_manager.reconcile)
    await session_manager.reattach_streamers()
    scheduler.init_scheduler(asyncio.get_running_loop())
    cleanup_task = asyncio.create_task(session_manager.cleanup_loop())
    await telegram.start()
    log.info("harness broker up")
    yield
    await telegram.stop()
    cleanup_task.cancel()
    for sid in list(streams.tasks):
        await streams.stop(sid)
    scheduler.stop_scheduler()


app = FastAPI(title="opencode harness", lifespan=lifespan)
app.include_router(agents.router)
app.include_router(catalog.router)
app.include_router(channels.router)
app.include_router(guardian.router)
app.include_router(projects.router)
app.include_router(providers.router)
app.include_router(sessions.router)
app.include_router(schedules.router)
app.include_router(telegram_api.router)
app.include_router(webhooks.router)
app.include_router(ws.router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


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
