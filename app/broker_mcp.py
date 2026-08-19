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
from pathlib import PurePosixPath

from . import channel
from . import db
from . import files_store
from . import session_manager

DOWNLOAD_LIMIT = 512 * 1024 * 1024


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


# ---------- issues ----------

ISSUE_STATUSES = ("open", "in_progress", "done")


def _issue_project(ctx):
    pid = ctx.get("project_id")
    if pid is None:
        raise ToolError("сессия не привязана к проекту — issue некуда записать")
    return pid


def _tags_in(raw):
    if isinstance(raw, list):
        parts = [str(t).strip() for t in raw]
    else:
        parts = [t.strip() for t in str(raw or "").split(",")]
    return [t for t in parts if t][:10]


def _issue_out(row):
    d = dict(row)
    d["tags"] = [t for t in str(d.get("tags") or "").split(",") if t]
    return d


def h_issue_create(args, ctx):
    title = (args.get("title") or "").strip()
    if not title:
        raise ToolError("title обязателен")
    status = args.get("status") or "open"
    if status not in ISSUE_STATUSES:
        raise ToolError(f"status: один из {', '.join(ISSUE_STATUSES)}")
    tags = ",".join(_tags_in(args.get("tags")))
    iid = db.execute(
        "INSERT INTO issues(project_id, title, description, status, tags, created_by) VALUES(?,?,?,?,?, 'agent')",
        (_issue_project(ctx), title, args.get("description") or "", status, tags),
    )
    return _issue_out(db.query_one("SELECT * FROM issues WHERE id=?", (iid,)))


def h_issue_list(args, ctx):
    pid = _issue_project(ctx)
    sql = "SELECT * FROM issues WHERE project_id=? AND 1=1"
    params = [pid]
    if args.get("status") in ISSUE_STATUSES:
        sql += " AND status=?"
        params.append(args["status"])
    if args.get("tag"):
        sql += " AND (',' || tags || ',') LIKE ?"
        params.append(f"%,{str(args['tag']).strip()},%")
    if args.get("q"):
        like = f"%{str(args['q']).strip()}%"
        sql += " AND (title LIKE ? OR description LIKE ?)"
        params.extend((like, like))
    sql += " ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END, id DESC"
    rows = db.query(sql, params)
    limit = args.get("limit")
    if limit is not None:
        try:
            rows = rows[: max(1, min(int(limit), 100))]
        except (TypeError, ValueError):
            pass
    return [_issue_out(r) for r in rows]


def h_issue_update(args, ctx):
    iid = args.get("id")
    try:
        iid = int(iid)
    except (TypeError, ValueError):
        raise ToolError("id: целое число")
    row = db.query_one("SELECT * FROM issues WHERE id=?", (iid,))
    if not row:
        raise ToolError(f"issue {iid} не найден")
    merged = {**row, **args}
    if str(merged.get("status") or "open") not in ISSUE_STATUSES:
        raise ToolError(f"status: один из {', '.join(ISSUE_STATUSES)}")
    tags = ",".join(_tags_in(args.get("tags"))) if args.get("tags") is not None else row["tags"]
    db.execute(
        "UPDATE issues SET title=?, description=?, status=?, tags=?, updated_at=datetime('now') WHERE id=?",
        (
            (merged.get("title") or "").strip() or row["title"],
            merged.get("description") or row["description"],
            merged.get("status") or row["status"],
            tags,
            iid,
        ),
    )
    return _issue_out(db.query_one("SELECT * FROM issues WHERE id=?", (iid,)))


def h_issue_delete(args, ctx):
    iid = args.get("id")
    try:
        iid = int(iid)
    except (TypeError, ValueError):
        raise ToolError("id: целое число")
    if not db.query_one("SELECT id FROM issues WHERE id=?", (iid,)):
        raise ToolError(f"issue {iid} не найден")
    db.execute("DELETE FROM issues WHERE id=?", (iid,))
    return {"ok": True}


def h_file_download(args, ctx):
    """Скачивает файл из файлов проекта (MinIO) в workspace воркера.

    Исполняется на брокере: только он видит workspace сессии на хосте
    (files MCP живёт в контейнере playwright и до воркера не дотягивается).
    """
    path = str(args.get("path") or "").strip().lstrip("/")
    if not path or ".." in PurePosixPath(path).parts:
        raise ToolError("path: путь к файлу в файлах проекта (например shots/отчёт.png)")
    pid = ctx.get("project_id")
    if pid is None:
        raise ToolError("сессия не привязана к проекту — файлы проекта не определить")
    sid = ctx.get("session_id")
    if not sid:
        raise ToolError("скачивание доступно только из сессии воркера")
    dest = str(args.get("dest") or "").strip()
    if dest.endswith("/"):
        dest += PurePosixPath(path).name
    if not dest:
        dest = PurePosixPath(path).name
    ws = session_manager.host_ws_dir(sid).resolve()
    target = (ws / dest).resolve()
    if not (target == ws or str(target).startswith(str(ws) + os.sep)):
        raise ToolError("dest выходит за пределы workspace")
    try:
        obj = files_store.get_object(pid, path)
    except Exception as exc:
        raise ToolError(f"не удалось прочитать {path}: {exc}")
    try:
        size_hdr = int(obj.headers.get("Content-Length") or 0)
    except (ValueError, TypeError):
        size_hdr = 0
    if size_hdr > DOWNLOAD_LIMIT:
        raise ToolError(f"файл больше {DOWNLOAD_LIMIT // (1024 * 1024)} МБ — не скачиваем")
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with open(target, "wb") as fh:
            for chunk in obj.stream(1024 * 1024):
                written += len(chunk)
                if written > DOWNLOAD_LIMIT:
                    raise ToolError(f"файл больше {DOWNLOAD_LIMIT // (1024 * 1024)} МБ — не скачиваем")
                fh.write(chunk)
    except ToolError:
        target.unlink(missing_ok=True)
        raise
    return {"ok": True, "path": str(target.relative_to(ws)), "name": target.name, "size": written}


HANDLERS = {
    "telegram_info": h_telegram_info,
    "telegram_send": h_telegram_send,
    "telegram_send_file": h_telegram_send_file,
    "issue_create": h_issue_create,
    "issue_list": h_issue_list,
    "issue_update": h_issue_update,
    "issue_delete": h_issue_delete,
    "file_download": h_file_download,
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
    _tool(
        "issue_create",
        "Завести issue в трекере задач проекта (бэклог Vibeprod): заголовок, описание, статус, теги. "
        "Пишите сюда задачи, которые вы нашли или сделали — пользователь увидит их в интерфейсе.",
        {
            "title": _prop("string", "название issue (кратко и по делу)"),
            "description": _prop("string", "детальное описание (контекст, шаги, что сделано/нужно сделать)"),
            "status": _prop("string", "open | in_progress | done (по умолчанию open)"),
            "tags": _prop("array", "список тегов, например [\"баг\", \"рефакторинг\"]"),
        },
        ["title"],
    ),
    _tool(
        "issue_list",
        "Список issues проекта с фильтрами: status (open/in_progress/done), tag, q (поиск по названию и описанию).",
        {
            "status": _prop("string", "фильтр по статусу (необязательно)"),
            "tag": _prop("string", "фильтр по тегу (необязательно)"),
            "q": _prop("string", "поиск по названию и описанию (необязательно)"),
            "limit": _prop("integer", "сколько вернуть (по умолчанию все, максимум 100)"),
        },
        [],
    ),
    _tool(
        "issue_update",
        "Обновить issue: заголовок, описание, статус, теги (меняются только переданные поля).",
        {
            "id": _prop("integer", "id issue"),
            "title": _prop("string", "новое название"),
            "description": _prop("string", "новое описание"),
            "status": _prop("string", "open | in_progress | done"),
            "tags": _prop("array", "новый список тегов (заменяет старые)"),
        },
        ["id"],
    ),
    _tool(
        "issue_delete",
        "Удалить issue. Требует подтверждения пользователя.",
        {"id": _prop("integer", "id issue")},
        ["id"],
    ),
    _tool(
        "file_download",
        "Скачать файл из файлов проекта (хранилище MinIO) в workspace воркера — в нужную папку. "
        "path — путь в файлах проекта (см. list_files инструмента files MCP), dest — путь назначения "
        "относительно workspace, папки создаются автоматически (например 'reports/отчёт.pdf'). "
        "По умолчанию файл кладётся в корень workspace под именем из path. Возвращает путь в workspace.",
        {
            "path": _prop("string", "путь к файлу в файлах проекта, например shots/отчёт.png"),
            "dest": _prop("string", "путь назначения относительно workspace (необязательно)"),
        },
        ["path"],
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
