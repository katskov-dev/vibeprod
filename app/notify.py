"""Уведомления в каналы: сводка результата фоновых запусков (расписания,
вебхуки, автоматизации).

Хук on_session_done() вызывается стримером при финализации сессии. Если у
проекта в telegram_config задан notify_chat_id, туда уходит сводка: источник,
статус, ошибка (если была), краткий текст ответа агента и ссылка на сессию.
"""
import logging

from . import channel
from . import db

log = logging.getLogger("vibeprod.notify")

NOTIFY_SOURCES = ("schedule", "webhook", "automation")
RESULT_LIMIT = 1500

SOURCE_LABEL = {"schedule": "Расписание", "webhook": "Вебхук", "automation": "Автоматизация"}


def _result_text(result):
    """Текст последнего сообщения ассистента из результата сессии."""
    for m in reversed(result or []):
        info = m.get("info") or {}
        if info.get("role") != "assistant":
            continue
        texts = [
            (p.get("text") or "").strip()
            for p in info.get("parts") or []
            if p.get("type") == "text" and (p.get("text") or "").strip()
        ]
        if texts:
            return "\n\n".join(texts)
    return None


def _summary(text):
    text = (text or "").strip()
    if len(text) <= RESULT_LIMIT:
        return text
    return text[:RESULT_LIMIT].rstrip() + "\n…"


async def on_session_done(sid, status, error, result):
    row = db.query_one("SELECT * FROM sessions WHERE id=?", (sid,))
    if not row or row["source"] not in NOTIFY_SOURCES:
        return
    cfg = db.query_one(
        "SELECT * FROM telegram_config WHERE project_id=? AND enabled=1 AND token<>''",
        (row["project_id"],),
    )
    if not cfg:
        return
    chat_id = (cfg.get("notify_chat_id") or "").strip()
    if not chat_id:
        return
    if status == "completed" and (cfg.get("notify_mode") or "all") == "errors":
        return
    lines = [f"{SOURCE_LABEL.get(row['source'], row['source'])}: {row['title'] or '—'}"]
    if status == "failed":
        lines.append("Статус: ошибка")
        if error:
            lines.append(f"Ошибка: {str(error)[:500]}")
    else:
        lines.append("Статус: готово")
    text = _result_text(result)
    if text:
        lines.append("")
        lines.append(_summary(text))
    web_url = (cfg.get("web_url") or "").strip()
    if web_url:
        lines.append(f"\n{web_url.rstrip('/')}/#sessions/{sid}")
    try:
        await channel.send(row["project_id"], chat_id, "\n".join(lines))
    except Exception:
        log.exception("notify session %s", sid)
