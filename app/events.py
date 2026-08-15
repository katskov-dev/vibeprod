"""Мини-шина событий брокера.

emit() — синхронная функция: её можно звать из любого потока (вебхук-эндпоинты,
поток APScheduler, event loop). Она шедулит async-задачу доставки на главный
event loop брокера (MAIN_LOOP), как это делает spawn_start в main.py.

Потребители: исходящие вебхуки (outwebhooks.py) и всё, что захочется добавить
потом. Никакой очереди — события теряются при падении процесса, это осознанный
компромисс в духе проекта.
"""

import logging

from . import db
from . import outwebhooks

log = logging.getLogger("vibeprod.events")

EVENT_TYPES = (
    "session.created",
    "session.started",
    "session.completed",
    "session.failed",
    "session.expired",
    "schedule.fired",
    "webhook.received",
    "webhook.test",
)


def emit(event, data=None, main_loop=None):
    """Отправить событие потребителям. Потокобезопасно: если main_loop не
    передан, задача шедулится через outwebhooks на главный loop брокера."""
    if event not in EVENT_TYPES:
        log.warning("emit: неизвестное событие %r", event)
        return
    outwebhooks.dispatch(event, data or {}, main_loop=main_loop)


def session_event_data(sid, **extra):
    """Стандартный пейлоад для событий session.*: сводка сессии + ссылка на UI."""
    row = db.query_one(
        "SELECT s.*, a.name AS agent_name, p.name AS project_name FROM sessions s "
        "LEFT JOIN agents a ON a.id=s.agent_id LEFT JOIN projects p ON p.id=s.project_id "
        "WHERE s.id=?",
        (sid,),
    )
    if not row:
        return {"id": sid, **extra}
    data = {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "source": row["source"],
        "prompt": row["prompt"],
        "agent_name": row["agent_name"],
        "project_name": row["project_name"],
        "error": row["error"],
    }
    cfg = db.query_one(
        "SELECT web_url FROM telegram_config WHERE project_id=? AND enabled=1 AND token<>''",
        (row["project_id"],),
    )
    url = ((cfg or {}).get("web_url") or "").strip()
    if url:
        data["url"] = url.rstrip("/") + "/#sessions/" + sid
    data.update(extra)
    return data
