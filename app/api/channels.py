"""Каталог каналов запуска (Автоматизация → Каналы)."""
from fastapi import APIRouter

from .. import db

router = APIRouter(prefix="/api")

CHANNELS = [
    {
        "id": "telegram",
        "name": "Telegram",
        "description": "Запуск агентов и диалог с ними из Telegram. Первое сообщение создаёт сессию, "
        "дальше переписка продолжает её. Может присылать сводки о завершении запусков по расписанию и вебхукам.",
    },
]


def _resolve_project(project_id):
    if project_id is not None:
        try:
            pid = int(project_id)
        except (TypeError, ValueError):
            pid = None
        if pid is not None and db.query_one("SELECT id FROM projects WHERE id=?", (pid,)):
            return pid
    first = db.query_one("SELECT id FROM projects ORDER BY id LIMIT 1")
    return first["id"] if first else None


def _telegram_row(pid):
    row = db.query_one("SELECT token, allowed_users, enabled, bot_username, connected, last_error FROM telegram_config WHERE project_id=?", (pid,))
    d = dict(row) if row else {}
    return {
        **CHANNELS[0],
        "configured": bool(d.get("token")),
        "enabled": bool(d.get("enabled", 1)),
        "connected": bool(d.get("connected")),
        "bot_username": d.get("bot_username"),
        "last_error": d.get("last_error"),
    }


@router.get("/channels")
def list_channels(project_id: int = None):
    pid = _resolve_project(project_id)
    if pid is None:
        return []
    return [_telegram_row(pid)]
