"""API SSH: серверы, белый список команд, логи запусков.

Админ-эндпоинты (за cookie-авторизацией): CRUD серверов и команд, проверка
подключения, просмотр логов.

Эндпоинты ssh-MCP контейнера (за токеном проекта X-Vibeprod-Token, как
/api/files/content): конфиг (серверы с ключами + команды) и запись логов.
"""
import asyncio
import base64
import hashlib
import re
from typing import Optional

import asyncssh
from fastapi import APIRouter, HTTPException, Query, Request

from .. import auth
from .. import db
from .. import files_store
from ..ssh_config import (
    MAX_OUTPUT,
    SshError,
    arg_regexes,
    key_fingerprint,
    known_hosts_line,
    known_hosts_list,
    known_hosts_obj,
    render_command,
)

router = APIRouter(prefix="/api")

SERVER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$", re.IGNORECASE)
CMD_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _agent_allowed(request: Request, project_id) -> bool:
    """Доступ по токену проекта (контейнер ssh-MCP) или cookie-сессии (UI)."""
    token = request.headers.get("X-Vibeprod-Token") or ""
    if files_store.check_file_token(project_id, token):
        return True
    return not auth.ENABLED or auth.check_request(request)


def _server_row(sid):
    row = db.query_one("SELECT * FROM ssh_servers WHERE id=?", (sid,))
    if not row:
        raise HTTPException(404, "сервер не найден")
    return row


def _server_out(row):
    d = dict(row)
    d["has_key"] = bool(d.pop("private_key", None))
    d["has_password"] = bool(d.pop("password", None))
    d["host_key_fingerprint"] = _known_hosts_fingerprint(d.get("known_hosts"))
    d.pop("known_hosts", None)
    return d


def _known_hosts_fingerprint(raw):
    """SHA256-отпечаток из сохранённой строки known_hosts (для UI)."""
    for line in known_hosts_list(raw):
        parts = line.split()
        if len(parts) >= 3:
            try:
                digest = hashlib.sha256(base64.b64decode(parts[2] + "=" * (-len(parts[2]) % 4))).digest()
                return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")
            except (ValueError, TypeError):
                continue
    return None


def _server_payload(payload, existing=None):
    name = (payload.get("name") or "").strip()
    if not name or not SERVER_NAME_RE.match(name):
        raise HTTPException(400, "имя: буквы/цифры/дефис/подчёркивание, до 80 символов")
    host = (payload.get("host") or "").strip()
    if not host:
        raise HTTPException(400, "host обязателен")
    try:
        port = int(payload.get("port") or (existing["port"] if existing else 22))
    except (TypeError, ValueError):
        raise HTTPException(400, "port: целое число")
    if not 1 <= port <= 65535:
        raise HTTPException(400, "port: 1..65535")
    username = (payload.get("username") or "").strip()
    if not username:
        raise HTTPException(400, "username обязателен")
    auth_type = payload.get("auth_type") or (existing["auth_type"] if existing else "key")
    if auth_type not in ("key", "password"):
        raise HTTPException(400, "auth_type: key | password")
    private_key = payload.get("private_key") or ""
    password = payload.get("password") or ""
    if existing:
        if not private_key.strip():
            private_key = existing["private_key"]
        if not password:
            password = existing["password"]
    if auth_type == "key" and not private_key.strip():
        raise HTTPException(400, "нужен приватный ключ (PEM)")
    if auth_type == "password" and not password:
        raise HTTPException(400, "нужен пароль")
    return name, host, port, username, auth_type, private_key, password


def _resolve_project(project_id):
    if project_id is not None:
        try:
            pid = int(project_id)
        except (TypeError, ValueError):
            raise HTTPException(400, "project_id: целое число")
        if not db.query_one("SELECT id FROM projects WHERE id=?", (pid,)):
            raise HTTPException(404, "проект не найден")
        return pid
    first = db.query_one("SELECT id FROM projects ORDER BY id LIMIT 1")
    return first["id"] if first else None


# ---------- серверы ----------


@router.get("/ssh/servers")
def list_servers(project_id: int = None):
    pid = _resolve_project(project_id)
    if pid is None:
        return []
    return [_server_out(r) for r in db.query("SELECT * FROM ssh_servers WHERE project_id=? ORDER BY id", (pid,))]


@router.post("/ssh/servers")
def create_server(payload: dict):
    pid = _resolve_project(payload.get("project_id"))
    if pid is None:
        raise HTTPException(400, "нет проектов — сначала создайте проект")
    name, host, port, username, auth_type, private_key, password = _server_payload(payload)
    if auth_type == "key":
        try:
            asyncssh.import_private_key(private_key)
        except asyncssh.Error as exc:
            raise HTTPException(400, f"не удалось прочитать приватный ключ: {exc}")
    sid = db.execute(
        "INSERT INTO ssh_servers(project_id, name, host, port, username, auth_type, private_key, password) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (pid, name, host, port, username, auth_type, private_key, password),
    )
    return _server_out(db.query_one("SELECT * FROM ssh_servers WHERE id=?", (sid,)))


@router.put("/ssh/servers/{sid}")
def update_server(sid: int, payload: dict):
    row = _server_row(sid)
    name, host, port, username, auth_type, private_key, password = _server_payload(payload, existing=row)
    if auth_type == "key" and payload.get("private_key"):
        try:
            asyncssh.import_private_key(private_key)
        except asyncssh.Error as exc:
            raise HTTPException(400, f"не удалось прочитать приватный ключ: {exc}")
    if (host, port) != (row["host"], row["port"]):
        # ключ хоста привязан к адресу — при смене адреса проверяем заново
        known_hosts = ""
    else:
        known_hosts = row["known_hosts"]
    db.execute(
        "UPDATE ssh_servers SET name=?, host=?, port=?, username=?, auth_type=?, private_key=?, password=?, "
        "known_hosts=?, enabled=?, last_error=NULL WHERE id=?",
        (name, host, port, username, auth_type, private_key, password, known_hosts, 1 if payload.get("enabled", 1) else 0, sid),
    )
    return _server_out(db.query_one("SELECT * FROM ssh_servers WHERE id=?", (sid,)))


@router.delete("/ssh/servers/{sid}")
def delete_server(sid: int):
    _server_row(sid)
    db.execute("DELETE FROM ssh_servers WHERE id=?", (sid,))
    return {"ok": True}


@router.post("/ssh/servers/{sid}/test")
async def test_server(sid: int, payload: Optional[dict] = None):
    """Проверка подключения с TOFU-сохранением ключа хоста.

    replace_host_key=true — пересохранить ключ после смены ключа хоста.
    """
    row = _server_row(sid)
    replace = bool((payload or {}).get("replace_host_key"))
    if not row["private_key"] and not row["password"]:
        raise HTTPException(400, "не заданы учётные данные (ключ или пароль)")
    known = None if replace or not row["known_hosts"] else known_hosts_obj(row["known_hosts"])
    kwargs = {
        "host": row["host"],
        "port": int(row["port"] or 22),
        "username": row["username"],
        "connect_timeout": 20,
        "known_hosts": known,
    }
    if row["auth_type"] == "password":
        kwargs["password"] = row["password"]
    else:
        try:
            kwargs["client_keys"] = [asyncssh.import_private_key(row["private_key"])]
        except asyncssh.Error as exc:
            raise HTTPException(400, f"не удалось прочитать приватный ключ: {exc}")
    try:
        conn = await asyncssh.connect(**kwargs)
    except asyncssh.HostKeyMismatch:
        raise HTTPException(409, "ключ хоста изменился! Возможно MITM. Подтвердите замену ключа вручную.")
    except asyncssh.PermissionDenied:
        db.execute("UPDATE ssh_servers SET last_error=? WHERE id=?", ("доступ запрещён (проверьте ключ/пароль)", sid))
        raise HTTPException(502, "доступ запрещён (проверьте ключ/пароль)")
    except asyncssh.Error as exc:
        msg = f"SSH: {exc}"
        db.execute("UPDATE ssh_servers SET last_error=? WHERE id=?", (msg[:500], sid))
        raise HTTPException(502, msg)
    except (OSError, TimeoutError, asyncio.TimeoutError) as exc:
        msg = f"соединение: {exc}"
        db.execute("UPDATE ssh_servers SET last_error=? WHERE id=?", (msg[:500], sid))
        raise HTTPException(502, msg)
    try:
        key = conn.get_server_host_key()
        saved = False
        if not row["known_hosts"] or replace:
            db.execute(
                "UPDATE ssh_servers SET known_hosts=?, last_error=NULL WHERE id=?",
                (known_hosts_line(row["host"], row["port"], key), sid),
            )
            saved = True
        fingerprint = key_fingerprint(key)
    finally:
        conn.close()
    return {"ok": True, "fingerprint": fingerprint, "host_key_saved": saved}


# ---------- команды ----------


def _cmd_payload(payload):
    name = (payload.get("name") or "").strip()
    if not CMD_NAME_RE.match(name):
        raise HTTPException(400, "имя команды: строчные буквы/цифры/дефис/подчёркивание")
    command = (payload.get("command") or "").strip()
    if not command or len(command) > 2000:
        raise HTTPException(400, "команда обязательна, до 2000 символов")
    arg_regexes(payload.get("arg_regex"))  # валидация JSON и regex'ов
    try:
        timeout = int(payload.get("timeout") or 60)
    except (TypeError, ValueError):
        raise HTTPException(400, "timeout: целое число секунд")
    if not 1 <= timeout <= 3600:
        raise HTTPException(400, "timeout: 1..3600 секунд")
    return name, command, timeout


@router.get("/ssh/commands")
def list_commands(server_id: int = Query(...)):
    _server_row(server_id)
    return db.query("SELECT * FROM ssh_commands WHERE server_id=? ORDER BY id", (server_id,))


@router.post("/ssh/commands")
def create_command(payload: dict):
    server_id = int(payload.get("server_id") or 0)
    _server_row(server_id)
    name, command, timeout = _cmd_payload(payload)
    if db.query_one("SELECT id FROM ssh_commands WHERE server_id=? AND name=?", (server_id, name)):
        raise HTTPException(409, "команда с таким именем уже есть")
    cid = db.execute(
        "INSERT INTO ssh_commands(server_id, name, description, command, arg_regex, timeout, enabled) "
        "VALUES(?,?,?,?,?,?,?)",
        (
            server_id,
            name,
            payload.get("description") or "",
            command,
            payload.get("arg_regex") or "",
            timeout,
            1 if payload.get("enabled", 1) else 0,
        ),
    )
    return db.query_one("SELECT * FROM ssh_commands WHERE id=?", (cid,))


@router.put("/ssh/commands/{cid}")
def update_command(cid: int, payload: dict):
    row = db.query_one("SELECT * FROM ssh_commands WHERE id=?", (cid,))
    if not row:
        raise HTTPException(404, "команда не найдена")
    name, command, timeout = _cmd_payload({**dict(row), **payload})
    db.execute(
        "UPDATE ssh_commands SET name=?, description=?, command=?, arg_regex=?, timeout=?, enabled=? WHERE id=?",
        (
            name,
            payload.get("description", row["description"]) or "",
            command,
            payload.get("arg_regex", row["arg_regex"]),
            timeout,
            1 if payload.get("enabled", row["enabled"]) else 0,
            cid,
        ),
    )
    return db.query_one("SELECT * FROM ssh_commands WHERE id=?", (cid,))


@router.delete("/ssh/commands/{cid}")
def delete_command(cid: int):
    if not db.query_one("SELECT id FROM ssh_commands WHERE id=?", (cid,)):
        raise HTTPException(404, "команда не найдена")
    db.execute("DELETE FROM ssh_commands WHERE id=?", (cid,))
    return {"ok": True}


# ---------- конфиг для ssh-MCP контейнера ----------


@router.get("/ssh/config")
def agent_config(request: Request, project_id: int = Query(...)):
    if not _agent_allowed(request, project_id):
        raise HTTPException(403, "нет доступа к конфигу SSH")
    from ..ssh_config import agent_config as build

    return build(project_id)


# ---------- логи запусков ----------


@router.get("/ssh/runs")
def list_runs(
    request: Request,
    project_id: int = Query(...),
    server_id: int = None,
    command_name: str = None,
    limit: int = 50,
):
    if not _agent_allowed(request, project_id):
        raise HTTPException(403, "нет доступа к логам SSH")
    limit = max(1, min(int(limit or 50), 200))
    sql = "SELECT * FROM ssh_runs WHERE project_id=? AND 1=1"
    params = [int(project_id)]
    if server_id:
        sql += " AND server_id=?"
        params.append(int(server_id))
    if command_name:
        sql += " AND command_name=?"
        params.append(str(command_name))
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return db.query(sql, params)


@router.post("/ssh/runs")
def create_run(request: Request, payload: dict):
    project_id = int(payload.get("project_id") or 0)
    if not _agent_allowed(request, project_id):
        raise HTTPException(403, "нет доступа к логам SSH")
    server_id = payload.get("server_id")
    if server_id:
        row = db.query_one("SELECT id FROM ssh_servers WHERE id=? AND project_id=?", (int(server_id), project_id))
        if not row:
            raise HTTPException(400, "сервер не принадлежит проекту")
    command_id = payload.get("command_id")
    if command_id is not None:
        command_id = int(command_id)
    output = str(payload.get("output") or "")[:MAX_OUTPUT]
    rid = db.execute(
        "INSERT INTO ssh_runs(project_id, server_id, command_id, command_name, params, status, exit_code, output, duration_ms) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            project_id,
            server_id,
            command_id,
            str(payload.get("command_name") or ""),
            payload.get("params") or "",
            "error" if payload.get("status") == "error" else "ok",
            payload.get("exit_code"),
            output,
            payload.get("duration_ms"),
        ),
    )
    return {"ok": True, "id": rid}


# ---------- проверка шаблона (для формы в UI) ----------


@router.post("/ssh/commands/check")
def check_template(payload: dict):
    """Проверяет шаблон команды с примером параметров (без подключения к серверу)."""
    command = (payload.get("command") or "").strip()
    if not command:
        raise HTTPException(400, "команда обязательна")
    try:
        rendered = render_command(command, payload.get("arg_regex"), payload.get("params") or {})
    except SshError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "rendered": rendered}
