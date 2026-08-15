"""Guardian MCP: служебный MCP-сервер агента-оператора Vibeprod.

Streamable HTTP поверх JSON-RPC, без внешних зависимостей: инструменты
работают напрямую с sqlite (db) и docker (docker_runner). Доступен только
воркерам guardian-агента: URL и токен вставляются в opencode.json при
рендере workspace, порт наружу не публикуется, каждый запрос требует
Authorization: Bearer <guardian_secret>.
"""
import asyncio
import contextvars
import json
import mimetypes
import os
import re
import secrets

from fastapi import HTTPException

from . import db
from . import files_store
from . import scheduler
from .api.agents import NAME_RE, _check_project, _validate_agent
from .api.webhooks import _validate as _validate_webhook
from .provider_check import check_provider, env_var_for


class ToolError(Exception):
    pass


def get_secret():
    row = db.query_one("SELECT value FROM settings WHERE key='guardian_secret'")
    if row and row["value"]:
        return row["value"]
    secret = secrets.token_urlsafe(32)
    db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('guardian_secret', ?)", (secret,))
    return secret


def guardian_url():
    env = os.environ.get("VIBEPROD_GUARDIAN_URL")
    if env:
        return env.rstrip("/")
    port = os.environ.get("VIBEPROD_PORT", "8000")
    return f"http://host.docker.internal:{port}/guardian/mcp"


def guardian_mcp_entry(session_id=None, project_id=None):
    """Синтетическая запись MCP для opencode.json guardian-агента."""
    headers = {"Authorization": f"Bearer {get_secret()}"}
    if session_id:
        headers["X-Vibeprod-Session"] = session_id
    if project_id is not None:
        headers["X-Vibeprod-Project"] = str(project_id)
    return {
        "name": "guardian",
        "type": "remote",
        "url": guardian_url(),
        "headers": json.dumps(headers),
        "enabled": 1,
    }


# Контекст вызова: сессия и проект воркера, передаваемые заголовками из opencode.json
CALL_CTX = contextvars.ContextVar("guardian_call_ctx", default=None)


def _session_ctx():
    return CALL_CTX.get() or {}


def _int(value, field):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ToolError(f"{field}: целое число")


def _agent_row(agent_id):
    row = db.query_one("SELECT * FROM agents WHERE id=?", (agent_id,))
    if not row:
        raise ToolError(f"агент {agent_id} не найден")
    if row["is_guardian"]:
        raise ToolError("guardian — системный агент, его нельзя менять")
    return row


def _json_field(raw, field):
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            json.loads(raw)
            return raw
        except ValueError:
            raise ToolError(f"{field}: корректный JSON или пусто")
    return json.dumps(raw, ensure_ascii=False)


def _tool_result(text, is_error=False):
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _pretty(data):
    return json.dumps(data, ensure_ascii=False, default=str)


# ---------- проекты ----------

def h_project_list(args):
    return db.query(
        "SELECT p.id, p.name, p.description, "
        "(SELECT COUNT(*) FROM agents a WHERE a.project_id=p.id AND a.is_guardian=0) AS agent_count "
        "FROM projects p ORDER BY p.id"
    )


def h_project_create(args):
    name = (args.get("name") or "").strip()
    if not name:
        raise ToolError("name обязателен")
    pid = db.execute(
        "INSERT INTO projects(name, description) VALUES(?,?)",
        (name, args.get("description") or ""),
    )
    return db.query_one("SELECT * FROM projects WHERE id=?", (pid,))


def h_project_update(args):
    pid = _int(args.get("id"), "id")
    row = db.query_one("SELECT * FROM projects WHERE id=?", (pid,))
    if not row:
        raise ToolError(f"проект {pid} не найден")
    name = (args.get("name") or row["name"]).strip()
    if not name:
        raise ToolError("name не может быть пустым")
    db.execute(
        "UPDATE projects SET name=?, description=? WHERE id=?",
        (name, args.get("description", row["description"]), pid),
    )
    return db.query_one("SELECT * FROM projects WHERE id=?", (pid,))


async def h_project_delete(args):
    pid = _int(args.get("id"), "id")
    row = db.query_one("SELECT * FROM projects WHERE id=?", (pid,))
    if not row:
        raise ToolError(f"проект {pid} не найден")
    if db.query_one("SELECT COUNT(*) AS n FROM projects")["n"] <= 1:
        raise ToolError("нельзя удалить последний проект")
    from . import session_manager

    for s in db.query("SELECT id FROM sessions WHERE project_id=?", (pid,)):
        await session_manager.delete_session(s["id"])
    schedule_ids = [r["id"] for r in db.query("SELECT id FROM schedules WHERE project_id=?", (pid,))]
    db.execute("DELETE FROM schedules WHERE project_id=?", (pid,))
    for sid in schedule_ids:
        scheduler.apply_schedule(sid)
    db.execute("DELETE FROM agents WHERE project_id=?", (pid,))
    db.execute("DELETE FROM providers WHERE project_id=?", (pid,))
    db.execute("DELETE FROM projects WHERE id=?", (pid,))
    return {"ok": True, "deleted_project_id": pid}


# ---------- агенты ----------

def h_agent_list(args):
    sql = (
        "SELECT id, name, description, mode, model, temperature, system_prompt, permission, "
        "is_default, project_id FROM agents WHERE is_guardian=0"
    )
    params = ()
    if args.get("project_id") is not None:
        sql += " AND project_id=?"
        params = (_int(args.get("project_id"), "project_id"),)
    sql += " ORDER BY name"
    return db.query(sql, params)


def _agent_payload(args):
    keys = ("name", "description", "mode", "model", "temperature", "variant",
            "system_prompt", "permission", "is_default", "project_id")
    return {k: args[k] for k in keys if k in args and args[k] is not None}


def h_agent_get(args):
    return _agent_row(_int(args.get("id"), "id"))


def h_agent_create(args):
    payload = _agent_payload(args)
    name, mode, model, temperature = _validate_agent(payload)
    if db.query_one("SELECT id FROM agents WHERE name=?", (name,)):
        raise ToolError(f"агент с именем {name} уже есть")
    aid = db.execute(
        "INSERT INTO agents(name, description, mode, model, temperature, variant, system_prompt, "
        "permission, is_default, project_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            name,
            payload.get("description") or "",
            mode,
            model or "deepseek/deepseek-chat",
            temperature,
            payload.get("variant") or None,
            payload.get("system_prompt") or "",
            payload.get("permission") or '"allow"',
            1 if payload.get("is_default") else 0,
            _check_project(payload.get("project_id")),
        ),
    )
    if payload.get("is_default"):
        db.execute("UPDATE agents SET is_default=0 WHERE id<>?", (aid,))
    return db.query_one("SELECT * FROM agents WHERE id=?", (aid,))


def h_agent_update(args):
    aid = _int(args.get("id"), "id")
    row = _agent_row(aid)
    payload = _agent_payload(args)
    name, mode, model, temperature = _validate_agent({**row, **payload})
    other = db.query_one("SELECT id FROM agents WHERE name=? AND id<>?", (name, aid))
    if other:
        raise ToolError(f"агент с именем {name} уже есть")
    db.execute(
        "UPDATE agents SET name=?, description=?, mode=?, model=?, temperature=?, variant=?, "
        "system_prompt=?, permission=?, is_default=?, project_id=?, updated_at=datetime('now') WHERE id=?",
        (
            name,
            payload.get("description", row["description"]),
            mode,
            model or row["model"],
            temperature,
            payload.get("variant", row["variant"]) or None,
            payload.get("system_prompt", row["system_prompt"]),
            payload.get("permission", row["permission"]),
            1 if payload.get("is_default") else 0,
            _check_project(payload.get("project_id", row["project_id"])),
            aid,
        ),
    )
    if payload.get("is_default"):
        db.execute("UPDATE agents SET is_default=0 WHERE id<>?", (aid,))
    return db.query_one("SELECT * FROM agents WHERE id=?", (aid,))


def h_agent_delete(args):
    aid = _int(args.get("id"), "id")
    _agent_row(aid)
    db.execute("DELETE FROM agents WHERE id=?", (aid,))
    return {"ok": True}


# ---------- MCP: каталог и агенты ----------

def h_mcp_catalog_list(args):
    return db.query(
        "SELECT id, name, description, kind, type, url, command, service_container, builtin "
        "FROM mcp_catalog ORDER BY builtin DESC, name"
    )


def h_agent_mcp_list(args):
    aid = _int(args.get("agent_id"), "agent_id")
    _agent_row(aid)
    return db.query(
        "SELECT id, name, type, command, url, enabled FROM agent_mcp WHERE agent_id=? ORDER BY name",
        (aid,),
    )


def h_agent_mcp_attach(args):
    aid = _int(args.get("agent_id"), "agent_id")
    _agent_row(aid)
    cid = _int(args.get("catalog_id"), "catalog_id")
    entry = db.query_one("SELECT * FROM mcp_catalog WHERE id=?", (cid,))
    if not entry:
        raise ToolError(f"запись каталога {cid} не найдена")
    db.execute("DELETE FROM agent_mcp WHERE agent_id=? AND name=?", (aid, entry["name"]))
    mid = db.execute(
        "INSERT INTO agent_mcp(agent_id, name, type, command, url, headers, environment, enabled) "
        "VALUES(?,?,?,?,?,?,?,1)",
        (aid, entry["name"], entry["type"], entry["command"], entry["url"],
         entry["headers"], entry["environment"]),
    )
    return db.query_one("SELECT * FROM agent_mcp WHERE id=?", (mid,))


def h_agent_mcp_add(args):
    aid = _int(args.get("agent_id"), "agent_id")
    _agent_row(aid)
    name = (args.get("name") or "").strip()
    if not name:
        raise ToolError("name обязателен")
    mtype = args.get("type") or "local"
    if mtype not in ("local", "remote"):
        raise ToolError("type: local | remote")
    if mtype == "remote" and not args.get("url"):
        raise ToolError("url обязателен для remote")
    if db.query_one("SELECT id FROM agent_mcp WHERE agent_id=? AND name=?", (aid, name)):
        raise ToolError(f"mcp с именем {name} уже есть у агента")
    mid = db.execute(
        "INSERT INTO agent_mcp(agent_id, name, type, command, url, headers, environment, enabled) "
        "VALUES(?,?,?,?,?,?,?,1)",
        (
            aid, name, mtype,
            _json_field(args.get("command"), "command"),
            args.get("url"),
            _json_field(args.get("headers"), "headers"),
            _json_field(args.get("environment"), "environment"),
        ),
    )
    return db.query_one("SELECT * FROM agent_mcp WHERE id=?", (mid,))


def h_agent_mcp_detach(args):
    aid = _int(args.get("agent_id"), "agent_id")
    _agent_row(aid)
    name = (args.get("name") or "").strip()
    if not name:
        raise ToolError("name обязателен")
    db.execute("DELETE FROM agent_mcp WHERE agent_id=? AND name=?", (aid, name))
    return {"ok": True}


def h_agent_mcp_update(args):
    aid = _int(args.get("agent_id"), "agent_id")
    _agent_row(aid)
    name = (args.get("name") or "").strip()
    row = db.query_one("SELECT * FROM agent_mcp WHERE agent_id=? AND name=?", (aid, name))
    if not row:
        raise ToolError(f"mcp {name} не найден у агента")
    enabled = 1 if args.get("enabled", True) else 0
    db.execute("UPDATE agent_mcp SET enabled=? WHERE id=?", (enabled, row["id"]))
    return db.query_one("SELECT * FROM agent_mcp WHERE id=?", (row["id"],))


# ---------- скиллы ----------

def h_skill_list(args):
    return db.query("SELECT id, name, description, body FROM skills ORDER BY name")


def h_skill_create(args):
    name = (args.get("name") or "").strip().lower()
    if not NAME_RE.match(name):
        raise ToolError("имя скилла: строчные латинские буквы, цифры, дефис")
    if db.query_one("SELECT id FROM skills WHERE name=?", (name,)):
        raise ToolError(f"скилл с именем {name} уже есть")
    sid = db.execute(
        "INSERT INTO skills(name, description, body) VALUES(?,?,?)",
        (name, args.get("description") or "", args.get("body") or ""),
    )
    return db.query_one("SELECT * FROM skills WHERE id=?", (sid,))


def h_skill_update(args):
    sid = _int(args.get("id"), "id")
    row = db.query_one("SELECT * FROM skills WHERE id=?", (sid,))
    if not row:
        raise ToolError(f"скилл {sid} не найден")
    name = (args.get("name") or row["name"]).strip().lower()
    if not NAME_RE.match(name):
        raise ToolError("имя скилла: строчные латинские буквы, цифры, дефис")
    other = db.query_one("SELECT id FROM skills WHERE name=? AND id<>?", (name, sid))
    if other:
        raise ToolError(f"скилл с именем {name} уже есть")
    db.execute(
        "UPDATE skills SET name=?, description=?, body=? WHERE id=?",
        (name, args.get("description", row["description"]), args.get("body", row["body"]), sid),
    )
    return db.query_one("SELECT * FROM skills WHERE id=?", (sid,))


def h_skill_delete(args):
    sid = _int(args.get("id"), "id")
    db.execute("DELETE FROM skills WHERE id=?", (sid,))
    return {"ok": True}


def h_agent_skills_set(args):
    aid = _int(args.get("agent_id"), "agent_id")
    _agent_row(aid)
    skill_ids = args.get("skill_ids") or []
    try:
        skill_ids = [int(s) for s in skill_ids]
    except (TypeError, ValueError):
        raise ToolError("skill_ids: список id")
    db.execute("DELETE FROM agent_skills WHERE agent_id=?", (aid,))
    db.exec_many(
        "INSERT INTO agent_skills(agent_id, skill_id) VALUES(?,?)",
        [(aid, s) for s in skill_ids],
    )
    return {"ok": True, "skill_ids": skill_ids}


def h_agent_skills_list(args):
    aid = _int(args.get("agent_id"), "agent_id")
    _agent_row(aid)
    return db.query(
        "SELECT s.id, s.name, s.description FROM skills s "
        "JOIN agent_skills x ON x.skill_id=s.id WHERE x.agent_id=? ORDER BY s.name",
        (aid,),
    )


# ---------- провайдеры ----------

PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def h_provider_list(args):
    return db.query(
        "SELECT id, label, env_var, enabled, project_id, models, last_check_ok, last_check_error, "
        "last_check_at, (api_key IS NOT NULL AND api_key <> '') AS has_key FROM providers ORDER BY id"
    )


def h_provider_add(args):
    pid = (args.get("id") or "").strip().lower()
    if not PROVIDER_ID_RE.match(pid):
        raise ToolError("id провайдера: строчные латинские буквы, цифры, дефис")
    if db.query_one("SELECT id FROM providers WHERE id=?", (pid,)):
        raise ToolError(f"провайдер {pid} уже добавлен")
    db.execute(
        "INSERT INTO providers(id, label, env_var, api_key, enabled, project_id) VALUES(?,?,?,?,?,?)",
        (
            pid,
            args.get("label") or "",
            env_var_for(pid),
            args.get("api_key") or "",
            1 if args.get("enabled", True) else 0,
            _check_project(args.get("project_id")),
        ),
    )
    return {"ok": True, "id": pid, "env_var": env_var_for(pid)}


def h_provider_update(args):
    pid = (args.get("id") or "").strip().lower()
    row = db.query_one("SELECT * FROM providers WHERE id=?", (pid,))
    if not row:
        raise ToolError(f"провайдер {pid} не найден")
    project_id = _check_project(args.get("project_id", row["project_id"]))
    if args.get("api_key"):
        db.execute(
            "UPDATE providers SET label=?, api_key=?, enabled=?, project_id=?, updated_at=datetime('now') WHERE id=?",
            (args.get("label", row["label"]), args.get("api_key"),
             1 if args.get("enabled", row["enabled"]) else 0, project_id, pid),
        )
    else:
        db.execute(
            "UPDATE providers SET label=?, enabled=?, project_id=?, updated_at=datetime('now') WHERE id=?",
            (args.get("label", row["label"]),
             1 if args.get("enabled", row["enabled"]) else 0, project_id, pid),
        )
    return {"ok": True, "id": pid}


def h_provider_delete(args):
    pid = (args.get("id") or "").strip().lower()
    if not db.query_one("SELECT id FROM providers WHERE id=?", (pid,)):
        raise ToolError(f"провайдер {pid} не найден")
    db.execute("DELETE FROM providers WHERE id=?", (pid,))
    return {"ok": True}


async def h_provider_check(args):
    pid = (args.get("id") or "").strip().lower()
    row = db.query_one("SELECT * FROM providers WHERE id=?", (pid,))
    if not row:
        raise ToolError(f"провайдер {pid} не найден")
    deep = bool(args.get("deep", True))
    result = await asyncio.to_thread(check_provider, pid, row["api_key"], deep=deep)
    db.execute(
        "UPDATE providers SET models=?, models_full=?, last_check_ok=?, last_check_error=?, "
        "last_gen=?, last_check_at=datetime('now') WHERE id=?",
        (
            json.dumps(result.get("models") or [], ensure_ascii=False),
            json.dumps(result.get("model_details") or {}, ensure_ascii=False),
            1 if result.get("ok") else 0,
            result.get("error") or "",
            json.dumps(result.get("gen"), ensure_ascii=False) if result.get("gen") else None,
            pid,
        ),
    )
    return {"provider": pid, **result}


# ---------- вебхуки ----------

def _webhook_out(row):
    out = dict(row)
    out.pop("secret", None)
    out["has_secret"] = bool(row.get("secret"))
    return out


def _guardian_agent_check(agent_row):
    if agent_row.get("is_guardian"):
        raise ToolError("guardian нельзя запускать по вебхуку")


def h_webhook_list(args):
    return [
        _webhook_out(r)
        for r in db.query(
            "SELECT w.*, a.name AS agent_name FROM webhooks w "
            "LEFT JOIN agents a ON a.id=w.agent_id ORDER BY w.slug"
        )
    ]


def h_webhook_create(args):
    payload = {k: args[k] for k in ("slug", "agent_id", "project_id", "title", "prompt", "secret", "enabled")
               if k in args and args[k] is not None}
    slug, agent, project_id = _validate_webhook(payload)
    _guardian_agent_check(agent)
    if db.query_one("SELECT id FROM webhooks WHERE slug=?", (slug,)):
        raise ToolError(f"webhook с slug {slug} уже есть")
    wid = db.execute(
        "INSERT INTO webhooks(slug, agent_id, project_id, title, prompt, secret, enabled) VALUES(?,?,?,?,?,?,?)",
        (
            slug,
            agent["id"],
            project_id,
            payload.get("title") or "",
            payload.get("prompt") or "",
            payload.get("secret") or "",
            1 if payload.get("enabled", True) else 0,
        ),
    )
    return _webhook_out(db.query_one("SELECT * FROM webhooks WHERE id=?", (wid,)))


def h_webhook_update(args):
    wid = _int(args.get("id"), "id")
    row = db.query_one("SELECT * FROM webhooks WHERE id=?", (wid,))
    if not row:
        raise ToolError(f"webhook {wid} не найден")
    payload = {k: args[k] for k in ("slug", "agent_id", "project_id", "title", "prompt", "secret", "enabled")
               if k in args and args[k] is not None}
    slug, agent, project_id = _validate_webhook({**row, **payload})
    _guardian_agent_check(agent)
    other = db.query_one("SELECT id FROM webhooks WHERE slug=? AND id<>?", (slug, wid))
    if other:
        raise ToolError(f"webhook с slug {slug} уже есть")
    db.execute(
        "UPDATE webhooks SET slug=?, agent_id=?, project_id=?, title=?, prompt=?, secret=?, enabled=? WHERE id=?",
        (
            slug,
            agent["id"],
            project_id,
            payload.get("title", row["title"]) or "",
            payload.get("prompt", row["prompt"]) or "",
            payload.get("secret", row["secret"]),
            1 if payload.get("enabled", row["enabled"]) else 0,
            wid,
        ),
    )
    return _webhook_out(db.query_one("SELECT * FROM webhooks WHERE id=?", (wid,)))


def h_webhook_delete(args):
    wid = _int(args.get("id"), "id")
    db.execute("DELETE FROM webhooks WHERE id=?", (wid,))
    return {"ok": True}


# ---------- расписания ----------

def h_schedule_list(args):
    rows = db.query(
        "SELECT s.*, a.name AS agent_name FROM schedules s "
        "LEFT JOIN agents a ON a.id=s.agent_id ORDER BY s.id"
    )
    for r in rows:
        r["next_run"] = scheduler.job_next_run(r["id"])
        last = db.query_one("SELECT status FROM schedule_runs WHERE schedule_id=? ORDER BY id DESC LIMIT 1", (r["id"],))
        r["last_run_status"] = last["status"] if last else None
    return rows


def h_schedule_create(args):
    agent_id = _int(args.get("agent_id"), "agent_id")
    _agent_row(agent_id)
    agent = db.query_one("SELECT project_id FROM agents WHERE id=?", (agent_id,))
    project_id = _check_project(args.get("project_id", agent["project_id"]))
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        raise ToolError("prompt обязателен")
    cron = (args.get("cron") or "").strip()
    if not cron:
        raise ToolError("cron обязателен")
    tz = args.get("timezone") or "Europe/Moscow"
    try:
        scheduler.validate_cron(cron, tz)
    except ValueError as exc:
        raise ToolError(f"некорректный cron: {exc}")
    sid = db.execute(
        "INSERT INTO schedules(agent_id, project_id, title, prompt, cron, timezone, enabled) VALUES(?,?,?,?,?,?,?)",
        (agent_id, project_id, args.get("title") or "", prompt, cron, tz,
         1 if args.get("enabled", True) else 0),
    )
    scheduler.apply_schedule(sid)
    return db.query_one("SELECT * FROM schedules WHERE id=?", (sid,))


def h_schedule_update(args):
    sid = _int(args.get("id"), "id")
    row = db.query_one("SELECT * FROM schedules WHERE id=?", (sid,))
    if not row:
        raise ToolError(f"расписание {sid} не найдено")
    keys = ("agent_id", "project_id", "title", "prompt", "cron", "timezone", "enabled")
    merged = {**row, **{k: args[k] for k in keys if k in args and args[k] is not None}}
    if int(merged["agent_id"]) != row["agent_id"]:
        _agent_row(int(merged["agent_id"]))
    cron = merged["cron"]
    tz = merged["timezone"] or "Europe/Moscow"
    try:
        scheduler.validate_cron(cron, tz)
    except ValueError as exc:
        raise ToolError(f"некорректный cron: {exc}")
    project_id = _check_project(merged.get("project_id", row["project_id"]))
    db.execute(
        "UPDATE schedules SET agent_id=?, project_id=?, title=?, prompt=?, cron=?, timezone=?, enabled=? WHERE id=?",
        (
            int(merged["agent_id"]),
            project_id,
            merged["title"] or "",
            merged["prompt"],
            cron,
            tz,
            1 if merged["enabled"] else 0,
            sid,
        ),
    )
    scheduler.apply_schedule(sid)
    return db.query_one("SELECT * FROM schedules WHERE id=?", (sid,))


def h_schedule_delete(args):
    sid = _int(args.get("id"), "id")
    db.execute("DELETE FROM schedules WHERE id=?", (sid,))
    scheduler.apply_schedule(sid)
    return {"ok": True}


async def h_schedule_run_now(args):
    sid = _int(args.get("id"), "id")
    row = db.query_one("SELECT * FROM schedules WHERE id=?", (sid,))
    if not row:
        raise ToolError(f"расписание {sid} не найдено")
    await asyncio.to_thread(scheduler._fire, sid)
    return {"ok": True}


# ---------- файлы проекта ----------

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 МБ на файл


def _file_ctx(args):
    """project_id из аргументов или из контекста сессии (заголовок X-Vibeprod-Project)."""
    ctx = _session_ctx()
    pid = args.get("project_id")
    if pid is None and ctx.get("project_id"):
        pid = int(ctx["project_id"])
    if pid is None:
        raise ToolError("project_id обязателен")
    pid = _int(pid, "project_id")
    if not db.query_one("SELECT id FROM projects WHERE id=?", (pid,)):
        raise ToolError(f"проект {pid} не найден")
    return pid


def _workspace_path(args):
    """Путь к файлу в workspace воркера текущей сессии (если передан workspace_path)."""
    raw = args.get("workspace_path")
    if not raw:
        return None
    ctx = _session_ctx()
    sid = ctx.get("session_id")
    if not sid:
        raise ToolError("workspace_path доступен только в сессии воркера (нет X-Vibeprod-Session)")
    from . import session_manager

    root = session_manager.host_ws_dir(sid).resolve()
    p = (root / str(raw).lstrip("/")).resolve()
    if not str(p).startswith(str(root) + os.sep):
        raise ToolError("workspace_path: выход за пределы workspace запрещён")
    if not p.is_file():
        raise ToolError(f"файл {raw} не найден в workspace сессии")
    return p


def h_file_list(args):
    pid = _file_ctx(args)
    return files_store.list_objects(pid, (args.get("prefix") or "").lstrip("/"))


def h_file_put(args):
    pid = _file_ctx(args)
    target = (args.get("path") or "").strip().lstrip("/")
    if not target:
        raise ToolError("path обязателен")
    ws = _workspace_path(args)
    if ws is not None:
        data = ws.read_bytes()
        if len(data) > MAX_FILE_SIZE:
            raise ToolError(f"файл больше {MAX_FILE_SIZE // (1024 * 1024)} МБ")
        content_type = args.get("content_type") or mimetypes.guess_type(ws.name)[0] or "application/octet-stream"
    else:
        content = args.get("content")
        if content is None:
            raise ToolError("content или workspace_path обязателен")
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False, indent=2)
        else:
            content = str(content)
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_SIZE:
            raise ToolError(f"файл больше {MAX_FILE_SIZE // (1024 * 1024)} МБ")
        content_type = args.get("content_type") or mimetypes.guess_type(target)[0] or "text/plain"
    files_store.upload(pid, target, data, content_type)
    return {
        "ok": True,
        "path": target,
        "size": len(data),
        "url": files_store.content_url(pid, target),
    }


def h_file_delete(args):
    pid = _file_ctx(args)
    target = (args.get("path") or "").strip().lstrip("/")
    if not target:
        raise ToolError("path обязателен")
    try:
        files_store.stat(pid, target)
    except Exception:
        raise ToolError(f"файл {target} не найден в файлах проекта {pid}")
    files_store.delete(pid, target)
    return {"ok": True, "deleted": target}


# ---------- сессии ----------

def h_session_list(args):
    try:
        limit = min(int(args.get("limit") or 20), 100)
    except (TypeError, ValueError):
        limit = 20
    return db.query(
        "SELECT id, agent_name, title, source, status, model, created_at, started_at, finished_at, error "
        "FROM sessions ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )


def h_session_run(args):
    agent_id = _int(args.get("agent_id"), "agent_id")
    _agent_row(agent_id)
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        raise ToolError("prompt обязателен")
    from . import session_manager

    sid = session_manager.create_session(agent_id, args.get("title") or prompt[:60], prompt, source="guardian")
    from .main import spawn_start

    spawn_start(sid, prompt)
    return {"ok": True, "session_id": sid, "status": "queued"}


async def h_session_abort(args):
    sid = args.get("session_id") or ""
    if not sid:
        raise ToolError("session_id обязателен")
    from . import session_manager

    await session_manager.abort_session(sid)
    return {"ok": True}


async def h_session_delete(args):
    sid = args.get("session_id") or ""
    if not sid:
        raise ToolError("session_id обязателен")
    from . import session_manager

    await session_manager.delete_session(sid)
    return {"ok": True}


HANDLERS = {
    "project_list": h_project_list,
    "project_create": h_project_create,
    "project_update": h_project_update,
    "project_delete": h_project_delete,
    "agent_list": h_agent_list,
    "agent_get": h_agent_get,
    "agent_create": h_agent_create,
    "agent_update": h_agent_update,
    "agent_delete": h_agent_delete,
    "mcp_catalog_list": h_mcp_catalog_list,
    "agent_mcp_list": h_agent_mcp_list,
    "agent_mcp_attach": h_agent_mcp_attach,
    "agent_mcp_add": h_agent_mcp_add,
    "agent_mcp_detach": h_agent_mcp_detach,
    "agent_mcp_update": h_agent_mcp_update,
    "skill_list": h_skill_list,
    "skill_create": h_skill_create,
    "skill_update": h_skill_update,
    "skill_delete": h_skill_delete,
    "agent_skills_set": h_agent_skills_set,
    "agent_skills_list": h_agent_skills_list,
    "provider_list": h_provider_list,
    "provider_add": h_provider_add,
    "provider_update": h_provider_update,
    "provider_delete": h_provider_delete,
    "provider_check": h_provider_check,
    "webhook_list": h_webhook_list,
    "webhook_create": h_webhook_create,
    "webhook_update": h_webhook_update,
    "webhook_delete": h_webhook_delete,
    "schedule_list": h_schedule_list,
    "schedule_create": h_schedule_create,
    "schedule_update": h_schedule_update,
    "schedule_delete": h_schedule_delete,
    "schedule_run_now": h_schedule_run_now,
    "file_list": h_file_list,
    "file_put": h_file_put,
    "file_delete": h_file_delete,
    "session_list": h_session_list,
    "session_run": h_session_run,
    "session_abort": h_session_abort,
    "session_delete": h_session_delete,
}


async def call_tool(name, args, ctx=None):
    fn = HANDLERS.get(name)
    if fn is None:
        return _tool_result(f"неизвестный инструмент: {name}", is_error=True)
    token = CALL_CTX.set(ctx or {})
    try:
        try:
            result = fn(args or {})
            if asyncio.iscoroutine(result):
                result = await result
        except HTTPException as exc:
            return _tool_result(str(exc.detail), is_error=True)
        except ToolError as exc:
            return _tool_result(str(exc), is_error=True)
        except Exception as exc:
            return _tool_result(f"{type(exc).__name__}: {exc}", is_error=True)
        if isinstance(result, dict) and set(result.keys()) == {"content", "isError"}:
            return result
        return _tool_result(_pretty(result))
    finally:
        CALL_CTX.reset(token)


def _prop(ptype, desc, **extra):
    d = {"type": ptype, "description": desc}
    d.update(extra)
    return d


def _tool(name, desc, props, required):
    return {
        "name": name,
        "description": desc,
        "inputSchema": {"type": "object", "properties": props, "required": required},
    }


AGENT_PROPS = {
    "id": _prop("integer", "id агента"),
    "name": _prop("string", "имя: строчные латинские буквы, цифры, дефис"),
    "description": _prop("string", "описание"),
    "mode": _prop("string", "primary | subagent | all (по умолчанию primary)"),
    "model": _prop("string", "модель в формате provider/model, например deepseek/deepseek-chat"),
    "temperature": _prop("number", "температура (необязательно)"),
    "system_prompt": _prop("string", "system-промпт агента"),
    "permission": _prop("string", "permission-конфиг JSON (необязательно)"),
    "is_default": _prop("boolean", "сделать агентом по умолчанию"),
    "project_id": _prop("integer", "id проекта"),
}

TOOLS = [
    _tool("project_list", "Список проектов (id, name, agent_count).", {}, []),
    _tool("project_create", "Создать проект.", {"name": _prop("string", "название проекта"), "description": _prop("string", "описание")}, ["name"]),
    _tool("project_update", "Переименовать проект / сменить описание.", {"id": _prop("integer", "id проекта"), "name": _prop("string", "новое название"), "description": _prop("string", "новое описание")}, ["id"]),
    _tool("project_delete", "Удалить проект ВМЕСТЕ со всеми его агентами, сессиями, расписаниями, провайдерами. Требует подтверждения пользователя.", {"id": _prop("integer", "id проекта")}, ["id"]),
    _tool("agent_list", "Список агентов (кроме системного guardian).", {"project_id": _prop("integer", "фильтр по проекту")}, []),
    _tool("agent_get", "Агент по id.", {"id": _prop("integer", "id агента")}, ["id"]),
    _tool("agent_create", "Создать агента. Модель — provider/model.", AGENT_PROPS, ["name"]),
    _tool("agent_update", "Изменить агента (поля необязательны, меняются только переданные).", AGENT_PROPS, ["id"]),
    _tool("agent_delete", "Удалить агента. Каскадно удаляет его MCP, скиллы, вебхуки и расписания. Требует подтверждения.", {"id": _prop("integer", "id агента")}, ["id"]),
    _tool("mcp_catalog_list", "Каталог переиспользуемых MCP-серверов (generic и docker-сервисы).", {}, []),
    _tool("agent_mcp_list", "MCP-серверы, подключённые к агенту.", {"agent_id": _prop("integer", "id агента")}, ["agent_id"]),
    _tool("agent_mcp_attach", "Подключить агенту MCP из каталога (catalog_id из mcp_catalog_list).", {"agent_id": _prop("integer", "id агента"), "catalog_id": _prop("integer", "id записи каталога")}, ["agent_id", "catalog_id"]),
    _tool("agent_mcp_add", "Добавить агенту произвольный MCP (не из каталога).", {
        "agent_id": _prop("integer", "id агента"),
        "name": _prop("string", "имя MCP"),
        "type": _prop("string", "local (команда) | remote (URL)"),
        "command": _prop("string", "для local: JSON-список команды"),
        "url": _prop("string", "для remote: URL MCP-сервера"),
        "headers": _prop("string", "для remote: JSON-объект заголовков"),
        "environment": _prop("string", "JSON-объект переменных окружения"),
    }, ["agent_id", "name", "type"]),
    _tool("agent_mcp_detach", "Отключить MCP от агента по имени.", {"agent_id": _prop("integer", "id агента"), "name": _prop("string", "имя MCP")}, ["agent_id", "name"]),
    _tool("agent_mcp_update", "Включить/выключить MCP у агента.", {"agent_id": _prop("integer", "id агента"), "name": _prop("string", "имя MCP"), "enabled": _prop("boolean", "включён")}, ["agent_id", "name"]),
    _tool("skill_list", "Список скиллов.", {}, []),
    _tool("skill_create", "Создать скилл (навык агента, body — инструкция в markdown).", {"name": _prop("string", "имя: строчные латинские буквы, цифры, дефис"), "description": _prop("string", "описание"), "body": _prop("string", "тело скилла")}, ["name"]),
    _tool("skill_update", "Изменить скилл.", {"id": _prop("integer", "id скилла"), "name": _prop("string", "имя"), "description": _prop("string", "описание"), "body": _prop("string", "тело")}, ["id"]),
    _tool("skill_delete", "Удалить скилл.", {"id": _prop("integer", "id скилла")}, ["id"]),
    _tool("agent_skills_set", "Задать список скиллов агента целиком (заменяет текущий).", {"agent_id": _prop("integer", "id агента"), "skill_ids": _prop("array", "список id скиллов", items={"type": "integer"})}, ["agent_id", "skill_ids"]),
    _tool("agent_skills_list", "Скиллы, подключённые к агенту.", {"agent_id": _prop("integer", "id агента")}, ["agent_id"]),
    _tool("provider_list", "Список провайдеров (ключи скрыты, has_key показывает наличие).", {}, []),
    _tool("provider_add", "Добавить провайдера (API-ключ спрашивай у пользователя, не выдумывай).", {
        "id": _prop("string", "id провайдера: deepseek, openai, anthropic, google, groq, xai, openrouter, …"),
        "label": _prop("string", "подпись"),
        "api_key": _prop("string", "API-ключ"),
        "enabled": _prop("boolean", "включён"),
        "project_id": _prop("integer", "id проекта"),
    }, ["id"]),
    _tool("provider_update", "Изменить провайдера (label, ключ, enabled, проект).", {
        "id": _prop("string", "id провайдера"),
        "label": _prop("string", "подпись"),
        "api_key": _prop("string", "новый API-ключ (пусто — не менять)"),
        "enabled": _prop("boolean", "включён"),
        "project_id": _prop("integer", "id проекта"),
    }, ["id"]),
    _tool("provider_delete", "Удалить провайдера.", {"id": _prop("string", "id провайдера")}, ["id"]),
    _tool("provider_check", "Проверить провайдера: поднимает probe-контейнер opencode, проверяет регистрацию, список моделей и (deep=true) делает тест-запрос.", {"id": _prop("string", "id провайдера"), "deep": _prop("boolean", "тест-запрос к модели (по умолчанию true)")}, ["id"]),
    _tool("webhook_list", "Список вебхуков.", {}, []),
    _tool("webhook_create", "Создать вебхук (POST /api/webhooks/{slug}/run запускает агента).", {
        "slug": _prop("string", "slug: строчные латинские буквы, цифры, дефис"),
        "agent_id": _prop("integer", "id агента"),
        "title": _prop("string", "заголовок"),
        "prompt": _prop("string", "промпт по умолчанию"),
        "secret": _prop("string", "секрет (проверяется по X-Webhook-Secret)"),
        "enabled": _prop("boolean", "включён"),
        "project_id": _prop("integer", "id проекта"),
    }, ["slug", "agent_id"]),
    _tool("webhook_update", "Изменить вебхук.", {
        "id": _prop("integer", "id вебхука"),
        "slug": _prop("string", "slug"),
        "agent_id": _prop("integer", "id агента"),
        "title": _prop("string", "заголовок"),
        "prompt": _prop("string", "промпт по умолчанию"),
        "secret": _prop("string", "секрет"),
        "enabled": _prop("boolean", "включён"),
        "project_id": _prop("integer", "id проекта"),
    }, ["id"]),
    _tool("webhook_delete", "Удалить вебхук.", {"id": _prop("integer", "id вебхука")}, ["id"]),
    _tool("schedule_list", "Список расписаний (cron).", {}, []),
    _tool("schedule_create", "Создать cron-расписание запуска агента.", {
        "agent_id": _prop("integer", "id агента"),
        "cron": _prop("string", "cron-выражение, например '0 9 * * 1-5'"),
        "prompt": _prop("string", "промпт запуска"),
        "title": _prop("string", "заголовок"),
        "timezone": _prop("string", "таймзона (по умолчанию Europe/Moscow)"),
        "enabled": _prop("boolean", "включено"),
        "project_id": _prop("integer", "id проекта"),
    }, ["agent_id", "cron", "prompt"]),
    _tool("schedule_update", "Изменить расписание.", {
        "id": _prop("integer", "id расписания"),
        "agent_id": _prop("integer", "id агента"),
        "cron": _prop("string", "cron-выражение"),
        "prompt": _prop("string", "промпт"),
        "title": _prop("string", "заголовок"),
        "timezone": _prop("string", "таймзона"),
        "enabled": _prop("boolean", "включено"),
        "project_id": _prop("integer", "id проекта"),
    }, ["id"]),
    _tool("schedule_delete", "Удалить расписание.", {"id": _prop("integer", "id расписания")}, ["id"]),
    _tool("schedule_run_now", "Запустить расписание немедленно.", {"id": _prop("integer", "id расписания")}, ["id"]),
    _tool("file_list", "Список файлов проекта (MinIO).", {"project_id": _prop("integer", "id проекта (необязателен — берётся из контекста сессии)"), "prefix": _prop("string", "подпапка, например 'shots'")}, []),
    _tool("file_put", "Сохранить файл в файлы проекта и получить публичную ссылку (аналог скриншотов). content — содержимое (текст/JSON), workspace_path — путь к файлу в workspace воркера, например 'index.html' или 'dist/app.js' (читается с диска воркера).", {
        "project_id": _prop("integer", "id проекта (необязателен — берётся из контекста сессии)"),
        "path": _prop("string", "путь в файлах проекта, например 'reports/отчёт.md'"),
        "content": _prop("string", "содержимое файла (альтернатива workspace_path)"),
        "workspace_path": _prop("string", "путь к файлу в workspace воркера (альтернатива content)"),
        "content_type": _prop("string", "MIME-тип (по умолчанию — по расширению)"),
    }, ["path"]),
    _tool("file_delete", "Удалить файл из файлов проекта. Требует подтверждения.", {"project_id": _prop("integer", "id проекта (необязателен — из контекста сессии)"), "path": _prop("string", "путь файла в файлах проекта")}, ["path"]),
    _tool("session_list", "Последние сессии со статусами.", {"limit": _prop("integer", "сколько вернуть (по умолчанию 20)")}, []),
    _tool("session_run", "Запустить агента с промптом в новой сессии (проверка настройки).", {
        "agent_id": _prop("integer", "id агента"),
        "prompt": _prop("string", "промпт"),
        "title": _prop("string", "заголовок сессии"),
    }, ["agent_id", "prompt"]),
    _tool("session_abort", "Прервать выполняющуюся сессию.", {"session_id": _prop("string", "id сессии")}, ["session_id"]),
    _tool("session_delete", "Удалить сессию (контейнер и историю).", {"session_id": _prop("string", "id сессии")}, ["session_id"]),
]
