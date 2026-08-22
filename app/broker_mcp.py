"""Broker MCP: встроенные инструменты Vibeprod для воркеров всех сессий.

Воркер каждой сессии получает в opencode.json remote-MCP «vibeprod» с URL
брокера и Bearer-секретом (тот же, что у guardian MCP). Инструменты:
- память агента (memory_get/memory_set) — долговременный текст, доступный
  между сессиями; включается/выключается в настройках агента;
- Telegram: агент может написать пользователю и прислать файл (файлы проекта,
  отчёты, скриншоты) — канал настраивается в «Автоматизация → Каналы»;
- issues проекта (create/list/get/update/comment/comment_delete/delete),
  вызовы других агентов (agent_call_list/agent_run/agent_status) и обмен
  файлами с хранилищем проекта (file_download/file_upload).

Протокол — streamable HTTP поверх JSON-RPC, как у guardian. Доступ к файлам
воркера: broker читает workspace сессии на хосте (bind-mount), путь берётся из
X-Vibeprod-Session заголовка.
"""
import asyncio
import json
import mimetypes
import os
import urllib.request
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


def _workspace_file_path(sid, raw, must_exist=False):
    """Путь к файлу в workspace воркера (видимый брокеру) с проверкой границ.

    Брокер ходит в workspace по внутриконтейнерному пути ws_dir (в compose это
    /app/data/... — bind-mount данных; host_ws_dir — только для docker-демона).
    """
    ws = session_manager.ws_dir(sid).resolve()
    target = (ws / str(raw).lstrip("/")).resolve()
    if not (target == ws or str(target).startswith(str(ws) + os.sep)):
        raise ToolError("путь выходит за пределы workspace")
    if must_exist and not target.is_file():
        raise ToolError(f"файл не найден в workspace: {raw}")
    return ws, target


async def h_telegram_send_file(args, ctx):
    """Файл из workspace воркера (path) или из текста (content)."""
    path = (args.get("path") or "").strip()
    filename = (args.get("filename") or "").strip()
    if path:
        sid = ctx.get("session_id")
        if not sid:
            raise ToolError("path доступен только из сессии воркера")
        _, target = _workspace_file_path(sid, path, must_exist=True)
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


# ---------- память агента ----------

MEMORY_LIMIT = 100_000


def _memory_agent(ctx):
    """Агент текущей сессии, если у него включена память."""
    sid = ctx.get("session_id")
    if not sid:
        raise ToolError("память доступна только из сессии воркера")
    row = db.query_one(
        "SELECT a.id, a.name, a.memory, a.memory_enabled FROM sessions s "
        "JOIN agents a ON a.id=s.agent_id WHERE s.id=?",
        (sid,),
    )
    if not row:
        raise ToolError("сессия не привязана к агенту")
    if not row["memory_enabled"]:
        raise ToolError("память у агента выключена — включите её в настройках агента")
    return row


def h_memory_get(args, ctx):
    row = _memory_agent(ctx)
    return {"agent": row["name"], "memory": row["memory"] or ""}


def h_memory_set(args, ctx):
    row = _memory_agent(ctx)
    text = args.get("memory")
    if text is None:
        raise ToolError("memory обязателен")
    text = str(text)
    if len(text) > MEMORY_LIMIT:
        raise ToolError(f"память больше {MEMORY_LIMIT // 1000}К символов — сократите")
    db.execute(
        "UPDATE agents SET memory=?, updated_at=datetime('now') WHERE id=?",
        (text, row["id"]),
    )
    return {"ok": True, "agent": row["name"], "length": len(text)}


# ---------- issues ----------

ISSUE_STATUSES = ("open", "in_progress", "review", "done", "cancelled")
ISSUE_PRIORITIES = ("low", "medium", "high", "critical")


def _issue_project(ctx):
    pid = ctx.get("project_id")
    if pid is None:
        raise ToolError("сессия не привязана к проекту — issue некуда записать")
    return pid


def _issue_agent(ctx):
    """Агент текущей сессии (id, имя, видит ли только свои issues)."""
    sid = ctx.get("session_id")
    if not sid:
        return None
    return db.query_one(
        "SELECT s.agent_id, a.name, a.issues_own_only FROM sessions s "
        "JOIN agents a ON a.id=s.agent_id WHERE s.id=?",
        (sid,),
    )


def _tags_in(raw):
    if isinstance(raw, list):
        parts = [str(t).strip() for t in raw]
    else:
        parts = [t.strip() for t in str(raw or "").split(",")]
    return [t for t in parts if t][:10]


def _issue_assignee(raw):
    """Исполнитель по id или имени агента; None — без исполнителя."""
    if raw is None:
        return None
    if isinstance(raw, int) or (isinstance(raw, str) and raw.strip().isdigit()):
        row = db.query_one("SELECT id FROM agents WHERE id=? AND is_guardian=0", (int(raw),))
    else:
        row = db.query_one("SELECT id FROM agents WHERE name=? AND is_guardian=0", (str(raw).strip(),))
    if not row:
        raise ToolError(f"исполнитель не найден (id или имя агента): {raw}")
    return row["id"]


def _issue_own_check(me, issue):
    """При включённой настройке «видит только свои issues» — доступ только к своим."""
    if not me or not me["issues_own_only"]:
        return
    if not issue.get("assignee_id") or int(issue["assignee_id"]) != int(me["agent_id"]):
        raise ToolError(
            "настройка «видит только свои issues»: этот issue назначен другому исполнителю"
        )


def _issue_row(iid):
    return db.query_one(
        "SELECT i.*, a.name AS assignee_name FROM issues i "
        "LEFT JOIN agents a ON a.id=i.assignee_id WHERE i.id=?",
        (iid,),
    )


def _issue_out(row):
    d = dict(row)
    d["tags"] = [t for t in str(d.get("tags") or "").split(",") if t]
    d["comments"] = db.query(
        "SELECT id, agent_name, text, created_at FROM issue_comments WHERE issue_id=? ORDER BY id",
        (d["id"],),
    )
    return d


def h_issue_create(args, ctx):
    title = (args.get("title") or "").strip()
    if not title:
        raise ToolError("title обязателен")
    status = args.get("status") or "open"
    if status not in ISSUE_STATUSES:
        raise ToolError(f"status: один из {', '.join(ISSUE_STATUSES)}")
    priority = args.get("priority") or "medium"
    if priority not in ISSUE_PRIORITIES:
        raise ToolError(f"priority: один из {', '.join(ISSUE_PRIORITIES)}")
    tags = ",".join(_tags_in(args.get("tags")))
    me = _issue_agent(ctx)
    assignee_id = _issue_assignee(args.get("assignee"))
    if me and me["issues_own_only"]:
        if assignee_id is not None and int(assignee_id) != int(me["agent_id"]):
            raise ToolError("настройка «видит только свои issues»: исполнителем можно назначить только себя")
        assignee_id = me["agent_id"]
    created_by = me["name"] if me else "agent"
    iid = db.execute(
        "INSERT INTO issues(project_id, title, description, status, priority, assignee_id, tags, created_by) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (_issue_project(ctx), title, args.get("description") or "", status, priority, assignee_id, tags, created_by),
    )
    return _issue_out(_issue_row(iid))


def h_issue_list(args, ctx):
    pid = _issue_project(ctx)
    sql = ("SELECT i.*, a.name AS assignee_name FROM issues i "
           "LEFT JOIN agents a ON a.id=i.assignee_id WHERE i.project_id=? AND 1=1")
    params = [pid]
    me = _issue_agent(ctx)
    if me and me["issues_own_only"]:
        sql += " AND i.assignee_id=?"
        params.append(me["agent_id"])
    if args.get("status") in ISSUE_STATUSES:
        sql += " AND i.status=?"
        params.append(args["status"])
    if args.get("priority") in ISSUE_PRIORITIES:
        sql += " AND i.priority=?"
        params.append(args["priority"])
    if args.get("assignee"):
        ref = args["assignee"]
        if str(ref).strip() == "me" and me:
            sql += " AND i.assignee_id=?"
            params.append(me["agent_id"])
        else:
            sql += " AND i.assignee_id=?"
            params.append(_issue_assignee(ref))
    if args.get("tag"):
        sql += " AND (',' || i.tags || ',') LIKE ?"
        params.append(f"%,{str(args['tag']).strip()},%")
    if args.get("q"):
        like = f"%{str(args['q']).strip()}%"
        sql += " AND (i.title LIKE ? OR i.description LIKE ?)"
        params.extend((like, like))
    order = " ".join(f"WHEN '{s}' THEN {i}" for i, s in enumerate(ISSUE_STATUSES))
    sql += f" ORDER BY CASE i.status {order} ELSE 9 END, i.id DESC"
    rows = db.query(sql, params)
    limit = args.get("limit")
    if limit is not None:
        try:
            rows = rows[: max(1, min(int(limit), 100))]
        except (TypeError, ValueError):
            pass
    return [_issue_out(r) for r in rows]


def h_issue_get(args, ctx):
    iid = args.get("id")
    try:
        iid = int(iid)
    except (TypeError, ValueError):
        raise ToolError("id: целое число")
    row = _issue_row(iid)
    if not row:
        raise ToolError(f"issue {iid} не найден")
    me = _issue_agent(ctx)
    _issue_own_check(me, row)
    return _issue_out(row)


def h_issue_update(args, ctx):
    iid = args.get("id")
    try:
        iid = int(iid)
    except (TypeError, ValueError):
        raise ToolError("id: целое число")
    row = db.query_one("SELECT * FROM issues WHERE id=?", (iid,))
    if not row:
        raise ToolError(f"issue {iid} не найден")
    me = _issue_agent(ctx)
    _issue_own_check(me, row)
    merged = {**row, **args}
    status = merged.get("status") or row["status"]
    if status not in ISSUE_STATUSES:
        raise ToolError(f"status: один из {', '.join(ISSUE_STATUSES)}")
    priority = merged.get("priority") or row["priority"]
    if priority not in ISSUE_PRIORITIES:
        raise ToolError(f"priority: один из {', '.join(ISSUE_PRIORITIES)}")
    tags = ",".join(_tags_in(args.get("tags"))) if args.get("tags") is not None else row["tags"]
    if "assignee" in args:
        assignee_id = _issue_assignee(args.get("assignee"))
        if me and me["issues_own_only"] and (assignee_id is None or int(assignee_id) != int(me["agent_id"])):
            raise ToolError("настройка «видит только свои issues»: исполнителем можно назначить только себя")
    else:
        assignee_id = row["assignee_id"]
    db.execute(
        "UPDATE issues SET title=?, description=?, status=?, priority=?, assignee_id=?, tags=?, "
        "updated_at=datetime('now') WHERE id=?",
        (
            (merged.get("title") or "").strip() or row["title"],
            merged.get("description") or row["description"],
            status,
            priority,
            assignee_id,
            tags,
            iid,
        ),
    )
    return _issue_out(_issue_row(iid))


def h_issue_comment(args, ctx):
    iid = args.get("id")
    try:
        iid = int(iid)
    except (TypeError, ValueError):
        raise ToolError("id: целое число")
    row = db.query_one("SELECT * FROM issues WHERE id=?", (iid,))
    if not row:
        raise ToolError(f"issue {iid} не найден")
    me = _issue_agent(ctx)
    _issue_own_check(me, row)
    text = (args.get("text") or "").strip()
    if not text:
        raise ToolError("text обязателен")
    cid = db.execute(
        "INSERT INTO issue_comments(issue_id, agent_id, agent_name, text) VALUES(?,?,?,?)",
        (iid, me["agent_id"] if me else None, me["name"] if me else "", text),
    )
    db.execute("UPDATE issues SET updated_at=datetime('now') WHERE id=?", (iid,))
    return db.query_one("SELECT * FROM issue_comments WHERE id=?", (cid,))


def h_issue_comment_delete(args, ctx):
    iid = args.get("issue_id")
    cid = args.get("comment_id")
    try:
        iid = int(iid)
        cid = int(cid)
    except (TypeError, ValueError):
        raise ToolError("issue_id и comment_id: целые числа")
    row = db.query_one("SELECT * FROM issues WHERE id=?", (iid,))
    if not row:
        raise ToolError(f"issue {iid} не найден")
    me = _issue_agent(ctx)
    _issue_own_check(me, row)
    c = db.query_one("SELECT * FROM issue_comments WHERE id=? AND issue_id=?", (cid, iid))
    if not c:
        raise ToolError(f"комментарий {cid} не найден")
    if me and c["agent_id"] is not None and int(c["agent_id"]) != int(me["agent_id"]):
        raise ToolError("можно удалять только свои комментарии")
    db.execute("DELETE FROM issue_comments WHERE id=?", (cid,))
    db.execute("UPDATE issues SET updated_at=datetime('now') WHERE id=?", (iid,))
    return {"ok": True}


def h_issue_delete(args, ctx):
    iid = args.get("id")
    try:
        iid = int(iid)
    except (TypeError, ValueError):
        raise ToolError("id: целое число")
    row = db.query_one("SELECT * FROM issues WHERE id=?", (iid,))
    if not row:
        raise ToolError(f"issue {iid} не найден")
    me = _issue_agent(ctx)
    _issue_own_check(me, row)
    db.execute("DELETE FROM issues WHERE id=?", (iid,))
    return {"ok": True}


def _dest_relative(dest, path):
    """dest → путь относительно workspace воркера.

    Агент передаёт абсолютный путь внутри воркера (/workspace/...): приводим
    к относительному. Относительный — как есть (папка с "/" на конце —
    дополняется именем файла из path).
    """
    dest = str(dest or "").strip()
    if dest.startswith("/"):
        parts = [x for x in PurePosixPath(dest).parts if x not in ("", "/")]
        if not parts or parts[0] != "workspace":
            raise ToolError(
                "dest: абсолютный путь должен быть внутри /workspace воркера "
                "(например /workspace/reports/отчёт.pdf) — брокер пишет только в workspace"
            )
        parts = parts[1:]
        if dest.endswith("/"):
            parts.append(PurePosixPath(path).name)
        if not parts:
            raise ToolError("dest указывает на корень /workspace — укажите путь к файлу")
        return str(PurePosixPath(*parts))
    if not dest:
        return PurePosixPath(path).name
    if dest.endswith("/"):
        dest += PurePosixPath(path).name
    return dest


def h_file_download(args, ctx):
    """Скачивает файл из файлов проекта (MinIO) в workspace воркера.

    Исполняется на брокере: только он видит workspace сессии (files MCP живёт
    в контейнере playwright и до воркера не дотягивается).
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
    rel = _dest_relative(args.get("dest"), path)
    ws, target = _workspace_file_path(sid, rel)
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


def h_file_upload(args, ctx):
    """Загружает файл из workspace воркера (или текст) в файлы проекта (MinIO)."""
    pid = ctx.get("project_id")
    if pid is None:
        raise ToolError("сессия не привязана к проекту — файлы проекта не определить")
    sid = ctx.get("session_id")
    if not sid:
        raise ToolError("загрузка доступна только из сессии воркера")
    path = str(args.get("path") or "").strip()
    filename = (args.get("filename") or "").strip()
    if path:
        _, target = _workspace_file_path(sid, path, must_exist=True)
        if target.stat().st_size > DOWNLOAD_LIMIT:
            raise ToolError(f"файл больше {DOWNLOAD_LIMIT // (1024 * 1024)} МБ")
        data = target.read_bytes()
        filename = filename or target.name
    else:
        data = (args.get("content") or "").encode("utf-8")
        filename = filename or "message.txt"
        if len(data) > DOWNLOAD_LIMIT:
            raise ToolError(f"контент больше {DOWNLOAD_LIMIT // (1024 * 1024)} МБ")
    if not filename:
        raise ToolError("имя файла не определено — укажите path или filename")
    dest = str(args.get("dest") or "").strip().lstrip("/")
    if not dest:
        dest = PurePosixPath(filename).name
    else:
        if ".." in PurePosixPath(dest).parts:
            raise ToolError("dest: недопустимый путь")
        if dest.endswith("/"):
            dest += PurePosixPath(filename).name
        dest = str(PurePosixPath(dest))
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    try:
        files_store.upload(pid, dest, data, content_type, size=len(data))
    except Exception as exc:
        raise ToolError(f"не удалось сохранить {dest}: {exc}")
    return {
        "ok": True,
        "path": dest,
        "name": PurePosixPath(dest).name,
        "size": len(data),
        "url": files_store.content_url(pid, dest),
    }


# ---------- exa web-поиск ----------

EXA_API = "https://api.exa.ai/search"
EXA_TOOLS = {"exa_search"}


def _exa_enabled(ctx):
    """exa_search доступен только агентам с включённой настройкой exa_enabled."""
    sid = ctx.get("session_id")
    if not sid:
        raise ToolError("exa-поиск доступен только из сессии воркера")
    row = db.query_one(
        "SELECT a.exa_enabled FROM sessions s JOIN agents a ON a.id=s.agent_id WHERE s.id=?",
        (sid,),
    )
    if not row:
        raise ToolError("сессия не привязана к агенту")
    if not row["exa_enabled"]:
        raise ToolError("exa-поиск у агента выключен — включите его в настройках агента")
    return row


def _exa_request(payload):
    key = os.environ.get("EXA_API_KEY", "").strip()
    if not key:
        raise ToolError("EXA_API_KEY не задан на брокере (env брокера)")
    req = urllib.request.Request(
        EXA_API,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def h_exa_search(args, ctx):
    _exa_enabled(ctx)
    query = (args.get("query") or "").strip()
    if not query:
        raise ToolError("query обязателен")
    try:
        num = int(args.get("num_results") or 5)
    except (TypeError, ValueError):
        num = 5
    num = max(1, min(num, 10))
    payload = {
        "query": query,
        "numResults": num,
        "type": "auto",
        "contents": {"text": {"maxCharacters": 2000}},
    }
    data = await asyncio.to_thread(_exa_request, payload)
    results = []
    for r in data.get("results") or []:
        results.append({
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "published": r.get("publishedDate") or "",
            "author": r.get("author") or "",
            "text": (r.get("text") or "")[:2000],
        })
    return {"query": query, "results": results}


# ---------- вызовы других агентов ----------

TEAM_TOOLS = {"agent_call_list", "agent_run", "agent_status"}

CALL_WAIT_DEFAULT = 900
CALL_WAIT_MIN = 5
CALL_WAIT_MAX = 3600
CALL_POLL = 3


def _caller_agent(ctx):
    """Агент текущей сессии (кто вызывает)."""
    sid = ctx.get("session_id")
    if not sid:
        raise ToolError("вызов других агентов доступен только из сессии воркера")
    row = db.query_one(
        "SELECT s.agent_id, s.project_id, a.name FROM sessions s "
        "JOIN agents a ON a.id=s.agent_id WHERE s.id=?",
        (sid,),
    )
    if not row:
        raise ToolError("сессия не привязана к агенту")
    return row


def _target_agent(args):
    """Целевой агент по id или имени (не guardian)."""
    raw = str(args.get("agent") or "").strip()
    if not raw:
        raise ToolError("agent обязателен: id или имя агента")
    try:
        row = db.query_one("SELECT * FROM agents WHERE id=? AND is_guardian=0", (int(raw),))
    except (TypeError, ValueError):
        row = db.query_one("SELECT * FROM agents WHERE name=? AND is_guardian=0", (raw,))
    if not row:
        raise ToolError(f"агент {raw} не найден")
    return row


def _call_allowed(caller_id, target_id):
    return bool(
        db.query_one("SELECT 1 FROM agent_calls WHERE caller_id=? AND target_id=?", (caller_id, target_id))
    )


def _session_result_out(row):
    out = {
        "session_id": row["id"],
        "agent_name": row["agent_name"],
        "title": row["title"],
        "status": row["status"],
        "error": row["error"],
    }
    raw = row["result_json"]
    if raw:
        try:
            out["result"] = json.loads(raw)
        except (ValueError, TypeError):
            out["result"] = raw
    return out


def h_agent_call_list(args, ctx):
    caller = _caller_agent(ctx)
    rows = db.query(
        "SELECT a.id, a.name, a.description, a.mode FROM agent_calls c "
        "JOIN agents a ON a.id=c.target_id WHERE c.caller_id=? ORDER BY a.name",
        (caller["agent_id"],),
    )
    return [dict(r) for r in rows]


async def h_agent_run(args, ctx):
    caller = _caller_agent(ctx)
    target = _target_agent(args)
    if int(target["id"]) == int(caller["agent_id"]):
        raise ToolError("агент не может вызвать сам себя")
    if not _call_allowed(caller["agent_id"], target["id"]):
        raise ToolError(
            f"агент «{caller['name']}» не может вызывать агента «{target['name']}» — "
            "добавьте его в «Может вызывать» в настройках агента"
        )
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        raise ToolError("prompt обязателен")
    sid = session_manager.create_session(
        target["id"],
        args.get("title") or prompt[:60],
        prompt,
        source="agent",
        project_id=caller["project_id"],
    )
    from .main import spawn_start

    spawn_start(sid, prompt)
    if not args.get("wait", True):
        return {"ok": True, "session_id": sid, "status": "queued"}
    try:
        timeout = int(args.get("timeout") or CALL_WAIT_DEFAULT)
    except (TypeError, ValueError):
        timeout = CALL_WAIT_DEFAULT
    timeout = max(CALL_WAIT_MIN, min(timeout, CALL_WAIT_MAX))
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        row = db.query_one("SELECT * FROM sessions WHERE id=?", (sid,))
        if row and row["status"] in ("completed", "failed"):
            return {"ok": True, **_session_result_out(row)}
        if asyncio.get_event_loop().time() >= deadline:
            return {
                "ok": True,
                "session_id": sid,
                "status": (row or {}).get("status") or "queued",
                "note": f"сессия ещё не завершилась (лимит {timeout}с) — проверьте результат через agent_status",
            }
        await asyncio.sleep(CALL_POLL)


def h_agent_status(args, ctx):
    sid = (args.get("session_id") or "").strip()
    if not sid:
        raise ToolError("session_id обязателен")
    pid = ctx.get("project_id")
    if pid is None:
        raise ToolError("сессия не привязана к проекту")
    row = db.query_one("SELECT * FROM sessions WHERE id=?", (sid,))
    if not row:
        raise ToolError(f"сессия {sid} не найдена")
    if row.get("project_id") is not None and int(row["project_id"]) != int(pid):
        raise ToolError("нет доступа к сессии другого проекта")
    return _session_result_out(row)


HANDLERS = {
    "telegram_info": h_telegram_info,
    "telegram_send": h_telegram_send,
    "telegram_send_file": h_telegram_send_file,
    "memory_get": h_memory_get,
    "memory_set": h_memory_set,
    "issue_create": h_issue_create,
    "issue_list": h_issue_list,
    "issue_get": h_issue_get,
    "issue_update": h_issue_update,
    "issue_comment": h_issue_comment,
    "issue_comment_delete": h_issue_comment_delete,
    "issue_delete": h_issue_delete,
    "file_download": h_file_download,
    "file_upload": h_file_upload,
    "exa_search": h_exa_search,
    "agent_call_list": h_agent_call_list,
    "agent_run": h_agent_run,
    "agent_status": h_agent_status,
}

BROKER_TOOLS = [
    _tool(
        "memory_get",
        "Прочитать долговременную память агента — текст, который сохраняется между сессиями "
        "и помогает переключаться между задачами. Вызывай в начале новой задачи.",
        {},
        [],
    ),
    _tool(
        "memory_set",
        "Обновить долговременную память агента (заменяет текст целиком). Сохраняй сюда то, что "
        "пригодится в следующих сессиях: контекст проектов, договорённости с пользователем, "
        "прогресс по задачам. Сначала прочитай текущую память через memory_get и запиши новую "
        "версию целиком.",
        {"memory": _prop("string", "полный новый текст памяти (заменяет старый)")},
        ["memory"],
    ),
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
        "Завести issue в трекере задач проекта (бэклог Vibeprod): заголовок, описание, "
        "статус, приоритет, исполнитель, теги. Пишите сюда задачи, которые вы нашли или "
        "сделали — пользователь увидит их в интерфейсе.",
        {
            "title": _prop("string", "название issue (кратко и по делу)"),
            "description": _prop("string", "детальное описание (контекст, шаги, что сделано/нужно сделать)"),
            "status": _prop("string", "open | in_progress | review | done | cancelled (по умолчанию open)"),
            "priority": _prop("string", "low | medium | high | critical (по умолчанию medium)"),
            "assignee": _prop("string", "исполнитель: id или имя агента (необязательно)"),
            "tags": _prop("array", "список тегов, например [\"баг\", \"рефакторинг\"]"),
        },
        ["title"],
    ),
    _tool(
        "issue_list",
        "Список issues проекта с фильтрами: status (open/in_progress/review/done/cancelled), "
        "priority (low/medium/high/critical), assignee (id, имя агента или 'me'), tag, "
        "q (поиск по названию и описанию). У каждого issue есть комментарии.",
        {
            "status": _prop("string", "фильтр по статусу (необязательно)"),
            "priority": _prop("string", "фильтр по приоритету (необязательно)"),
            "assignee": _prop("string", "фильтр по исполнителю: id, имя агента или 'me' (необязательно)"),
            "tag": _prop("string", "фильтр по тегу (необязательно)"),
            "q": _prop("string", "поиск по названию и описанию (необязательно)"),
            "limit": _prop("integer", "сколько вернуть (по умолчанию все, максимум 100)"),
        },
        [],
    ),
    _tool(
        "issue_update",
        "Обновить issue: заголовок, описание, статус, приоритет, исполнитель, теги "
        "(меняются только переданные поля).",
        {
            "id": _prop("integer", "id issue"),
            "title": _prop("string", "новое название"),
            "description": _prop("string", "новое описание"),
            "status": _prop("string", "open | in_progress | review | done | cancelled"),
            "priority": _prop("string", "low | medium | high | critical"),
            "assignee": _prop("string", "исполнитель: id или имя агента (null/пусто — снять исполнителя)"),
            "tags": _prop("array", "новый список тегов (заменяет старые)"),
        },
        ["id"],
    ),
    _tool(
        "issue_comment",
        "Добавить комментарий к issue (текст и имя агента, который добавил, сохраняются "
        "и видны в интерфейсе). Используйте, чтобы сообщить прогресс или вопросы по задаче.",
        {
            "id": _prop("integer", "id issue"),
            "text": _prop("string", "текст комментария"),
        },
        ["id", "text"],
    ),
    _tool(
        "issue_get",
        "Получить один issue по id со всеми комментариями (например, чтобы "
        "посмотреть свежие обсуждения конкретной задачи).",
        {"id": _prop("integer", "id issue")},
        ["id"],
    ),
    _tool(
        "issue_comment_delete",
        "Удалить свой комментарий к issue (только свои комментарии; чужие удалить нельзя).",
        {
            "issue_id": _prop("integer", "id issue"),
            "comment_id": _prop("integer", "id комментария"),
        },
        ["issue_id", "comment_id"],
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
        "path — относительный путь к файлу в файлах проекта (см. list_files инструмента files MCP). "
        "dest — АБСОЛЮТНЫЙ путь назначения в workspace воркера, например /workspace/reports/отчёт.pdf "
        "(папки создаются автоматически; допустим и относительный путь от корня workspace, "
        "dest с '/' на конце означает «в эту папку под именем из path»). "
        "По умолчанию файл кладётся в корень workspace под именем из path. Возвращает относительный путь в workspace.",
        {
            "path": _prop("string", "относительный путь к файлу в файлах проекта, например shots/отчёт.png"),
            "dest": _prop("string", "абсолютный путь назначения в workspace воркера (/workspace/...), необязательно"),
        },
        ["path"],
    ),
    _tool(
        "file_upload",
        "Сохранить файл из workspace воркера в файлы проекта (появится в разделе «Файлы»). "
        "Источник: path — путь в workspace воркера (например 'shots/скрин.png'), либо content + filename "
        "для текста. dest — относительный путь назначения в файлах проекта (например 'отчёты/итог.md'), "
        "по умолчанию — корень под именем файла.",
        {
            "path": _prop("string", "путь к файлу относительно workspace воркера (если content не задан)"),
            "content": _prop("string", "содержимое файла текстом (если path не задан)"),
            "filename": _prop("string", "имя файла (для content; по умолчанию из path)"),
            "dest": _prop("string", "путь назначения в файлах проекта, необязательно"),
        },
        [],
    ),
    _tool(
        "exa_search",
        "Живой поиск в интернете через Exa: свежие статьи, документация, новости. "
        "Используй, когда нужна актуальная информация из веба. Возвращает заголовки, "
        "ссылки, авторов и текст результатов.",
        {
            "query": _prop("string", "поисковый запрос (на естественном языке, можно по-русски или по-английски)"),
            "num_results": _prop("integer", "сколько результатов вернуть (по умолчанию 5, максимум 10)"),
        },
        ["query"],
    ),
    _tool(
        "agent_call_list",
        "Список агентов, которых ты можешь вызвать (настроено в «Может вызывать» в настройках твоего агента). "
        "Вызывай в начале, чтобы узнать доступных агентов, их роли и описания.",
        {},
        [],
    ),
    _tool(
        "agent_run",
        "Вызвать другого агента как отдельную сессию со своим workspace, инструментами и памятью. "
        "agent — id или имя агента из agent_call_list. Сессия получит prompt как первое сообщение. "
        "По умолчанию ждёт завершения (wait=true) и возвращает статус и итоговый результат; "
        "если задача долгая, передай wait=false и проверяй результат позже через agent_status.",
        {
            "agent": _prop("string", "id или имя агента из agent_call_list"),
            "prompt": _prop("string", "задача для агента (подробно: контекст, цель, критерии готовности)"),
            "title": _prop("string", "заголовок сессии (необязательно — по умолчанию начало prompt)"),
            "wait": _prop("boolean", "ждать завершения сессии (по умолчанию true)"),
            "timeout": _prop("integer", f"максимум ожидания в секундах при wait=true (по умолчанию {CALL_WAIT_DEFAULT}, максимум {CALL_WAIT_MAX})"),
        },
        ["agent", "prompt"],
    ),
    _tool(
        "agent_status",
        "Статус и результат ранее запущенной сессии (session_id из agent_run или из ответа без ожидания). "
        "Возвращает status (queued/starting/running/completed/failed), error и result (итоговый результат агента).",
        {"session_id": _prop("string", "id сессии из ответа agent_run")},
        ["session_id"],
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


def tools_for(ctx):
    """Инструменты для воркера: memory_* — только при включённой памяти агента,
    agent_* (вызовы агентов) — только если у агента настроен список вызовов."""
    tools = BROKER_TOOLS
    sid = (ctx or {}).get("session_id")
    if sid:
        row = db.query_one(
            "SELECT a.memory_enabled, a.exa_enabled, "
            "(SELECT COUNT(*) FROM agent_calls c WHERE c.caller_id=a.id) AS calls "
            "FROM sessions s JOIN agents a ON a.id=s.agent_id WHERE s.id=?",
            (sid,),
        )
        if row:
            if not row["memory_enabled"]:
                tools = [t for t in tools if not t["name"].startswith("memory_")]
            if not row["calls"]:
                tools = [t for t in tools if t["name"] not in TEAM_TOOLS]
            if not row["exa_enabled"]:
                tools = [t for t in tools if t["name"] not in EXA_TOOLS]
    return tools
