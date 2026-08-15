"""Broker MCP: встроенные инструменты Vibeprod для воркеров всех сессий.

Воркер каждой сессии получает в opencode.json remote-MCP «vibeprod» с URL
брокера и Bearer-секретом (тот же, что у guardian MCP). Сейчас это инструменты
Telegram: агент может написать пользователю и прислать файл (файлы проекта,
отчёты, скриншоты) — канал настраивается в «Автоматизация → Каналы».

Протокол — streamable HTTP поверх JSON-RPC, как у guardian. Доступ к файлам
воркера: broker читает workspace сессии на хосте (bind-mount), путь берётся из
X-Vibeprod-Session заголовка.
"""
import asyncio
import json
import os

from . import channel
from . import db
from . import session_manager


def get_secret():
    from .guardian_mcp import get_secret as _get

    return _get()


def broker_mcp_url():
    env = os.environ.get("VIBEPROD_BROKER_MCP_URL", "").strip()
    if env:
        return env.rstrip("/")
    port = os.environ.get("VIBEPROD_PORT", "8000")
    return f"http://host.docker.internal:{port}/broker/mcp"


def broker_mcp_entry(session_id=None, project_id=None):
    """Синтетическая запись MCP для opencode.json воркера (любая сессия)."""
    headers = {"Authorization": f"Bearer {get_secret()}"}
    if session_id:
        headers["X-Vibeprod-Session"] = session_id
    if project_id is not None:
        headers["X-Vibeprod-Project"] = str(project_id)
    return {
        "name": "vibeprod",
        "type": "remote",
        "url": broker_mcp_url(),
        "headers": json.dumps(headers),
        "enabled": 1,
    }


class ToolError(Exception):
    pass


def _tool_result(text, is_error=False):
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _prop(ptype, desc):
    return {"type": ptype, "description": desc}


def _tool(name, desc, props, required):
    return {
        "name": name,
        "description": desc,
        "inputSchema": {"type": "object", "properties": props, "required": required},
    }


def _channel_cfg(ctx, chat_id):
    """Конфиг Telegram проекта и chat назначения (из аргумента или notify_chat_id)."""
    pid = ctx.get("project_id")
    if pid is None:
        raise ToolError(
            "сессия не привязана к проекту — канал не определить. Передайте chat_id явно."
        )
    cfg = db.query_one(
        "SELECT * FROM telegram_config WHERE project_id=? AND enabled=1 AND token<>''",
        (pid,),
    )
    if not cfg:
        raise ToolError(
            "в проекте не настроен Telegram-канал. Настройте: Автоматизация → Каналы → Telegram."
        )
    chat = str(chat_id or "").strip() or (cfg.get("notify_chat_id") or "").strip()
    if not chat:
        raise ToolError(
            "в настройках канала не задан чат для уведомлений "
            "(пришлите боту /chatid и сохраните его в «Автоматизация → Каналы»), "
            "а chat_id в вызове не передан."
        )
    return pid, chat


def h_telegram_info(args, ctx):
    pid = ctx.get("project_id")
    if pid is None:
        raise ToolError("сессия не привязана к проекту")
    cfg = db.query_one(
        "SELECT bot_username, notify_chat_id, enabled, connected, last_error "
        "FROM telegram_config WHERE project_id=?",
        (pid,),
    )
    if not cfg:
        return {"configured": False}
    return {
        "configured": bool(cfg["enabled"]),
        "connected": bool(cfg["connected"]),
        "bot_username": cfg["bot_username"],
        "notify_chat_id": cfg["notify_chat_id"],
        "last_error": cfg["last_error"],
    }


async def h_telegram_send(args, ctx):
    text = (args.get("text") or "").strip()
    if not text:
        raise ToolError("text обязателен")
    pid, chat = _channel_cfg(ctx, args.get("chat_id"))
    reply_to = args.get("reply_to")
    try:
        reply_to = int(reply_to) if reply_to is not None else None
    except (TypeError, ValueError):
        reply_to = None
    message_id = await channel.send(pid, chat, text, reply_to=reply_to)
    if message_id is None:
        raise ToolError("Telegram не принял сообщение (см. логи брокера)")
    return {"ok": True, "chat_id": chat, "message_id": message_id}


async def h_telegram_send_file(args, ctx):
    """Файл из workspace воркера (path) или из текста (content)."""
    path = (args.get("path") or "").strip()
    filename = (args.get("filename") or "").strip()
    if path:
        sid = ctx.get("session_id")
        if not sid:
            raise ToolError("path доступен только из сессии воркера")
        ws = session_manager.host_ws_dir(sid).resolve()
        target = (ws / path).resolve()
        if not (target == ws or str(target).startswith(str(ws) + os.sep)):
            raise ToolError("path выходит за пределы workspace")
        if not target.is_file():
            raise ToolError(f"файл не найден в workspace: {path}")
        if target.stat().st_size > channel.FILE_LIMIT:
            raise ToolError(f"файл больше {channel.FILE_LIMIT // (1024 * 1024)} МБ — Telegram не примет")
        content = target.read_bytes()
        filename = filename or target.name
    else:
        content = (args.get("content") or "").encode("utf-8")
        filename = filename or "message.txt"
        if len(content) > channel.FILE_LIMIT:
            raise ToolError(f"контент больше {channel.FILE_LIMIT // (1024 * 1024)} МБ — Telegram не примет")
    if not filename:
        filename = "file"
    pid, chat = _channel_cfg(ctx, args.get("chat_id"))
    caption = (args.get("caption") or "").strip() or None
    message_id = await channel.send_file(pid, chat, content, filename, caption=caption)
    return {"ok": True, "chat_id": chat, "message_id": message_id, "filename": filename}


HANDLERS = {
    "telegram_info": h_telegram_info,
    "telegram_send": h_telegram_send,
    "telegram_send_file": h_telegram_send_file,
}

BROKER_TOOLS = [
    _tool(
        "telegram_info",
        "Статус Telegram-канала проекта: настроен ли бот, id чата для уведомлений, ошибки подключения.",
        {},
        [],
    ),
    _tool(
        "telegram_send",
        "Отправить текстовое сообщение пользователю в Telegram (канал из «Автоматизация → Каналы»). "
        "Для уведомлений по результатам работы — это основной способ сообщить результат.",
        {
            "text": _prop("string", "текст сообщения (поддерживает Markdown-разметку Telegram)"),
            "chat_id": _prop("string", "id чата (необязателен — по умолчанию чат уведомлений проекта)"),
            "reply_to": _prop("integer", "message_id сообщения, на которое ответить (необязательно)"),
        },
        ["text"],
    ),
    _tool(
        "telegram_send_file",
        "Отправить файл пользователю в Telegram: фото (.png/.jpg/...) придёт картинкой, "
        "остальное — документом. Источник: path — файл в workspace воркера (например 'report.html' "
        "или 'shots/screen.png'), либо content + filename — текстовое содержимое.",
        {
            "path": _prop("string", "путь к файлу относительно workspace воркера"),
            "content": _prop("string", "содержимое файла текстом (если path не задан)"),
            "filename": _prop("string", "имя файла (для content; по умолчанию message.txt)"),
            "caption": _prop("string", "подпись к файлу (необязательно)"),
            "chat_id": _prop("string", "id чата (необязателен — по умолчанию чат уведомлений проекта)"),
        },
        [],
    ),
]


async def call_tool(name, args, ctx=None):
    fn = HANDLERS.get(name)
    if fn is None:
        return _tool_result(f"неизвестный инструмент: {name}", is_error=True)
    try:
        result = fn(args or {}, ctx or {})
        if asyncio.iscoroutine(result):
            result = await result
    except ToolError as exc:
        return _tool_result(str(exc), is_error=True)
    except Exception as exc:
        return _tool_result(f"{type(exc).__name__}: {exc}", is_error=True)
    if isinstance(result, dict) and set(result.keys()) == {"content", "isError"}:
        return result
    return _tool_result(json.dumps(result, ensure_ascii=False, default=str))
