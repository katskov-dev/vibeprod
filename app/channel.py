"""Отправка сообщений в каналы из любого места брокера.

Telegram-боты живут в telegram.py и слушают входящие; здесь — общая исходящая
часть: разбивка длинного текста, retry по 429 и send(), которую можно звать
откуда угодно (уведомления о завершении фоновых сессий и т.п.).
"""
import asyncio
import logging

import httpx

from . import db

log = logging.getLogger("vibeprod.channel")

API = "https://api.telegram.org"
MSG_LIMIT = 4000


def split_text(text, limit=MSG_LIMIT):
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


async def api_call(client, token, method, **params):
    for _ in range(4):
        r = await client.post(f"/bot{token}/{method}", json=params)
        if r.status_code == 429:
            retry = int((r.json().get("parameters") or {}).get("retry_after", 2)) + 1
            await asyncio.sleep(retry)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


async def send(project_id, chat_id, text, reply_to=None):
    """Шлёт текст в Telegram-чат проекта. Клиент на каждый вызов — можно звать из любого модуля."""
    row = db.query_one(
        "SELECT token FROM telegram_config WHERE project_id=? AND enabled=1 AND token<>''",
        (project_id,),
    )
    if not row:
        log.warning("channel send: у проекта %s нет активного telegram-конфига", project_id)
        return None
    last = None
    async with httpx.AsyncClient(base_url=API, timeout=httpx.Timeout(60.0, connect=10.0), trust_env=False) as client:
        for chunk in split_text(text):
            try:
                r = await api_call(
                    client,
                    row["token"],
                    "sendMessage",
                    chat_id=chat_id,
                    text=chunk,
                    disable_web_page_preview=True,
                    reply_to_message_id=reply_to,
                )
            except Exception as exc:
                log.warning("channel send (project %s, chat %s): %s", project_id, chat_id, exc)
                break
            last = (r.get("result") or {}).get("message_id")
    return last
