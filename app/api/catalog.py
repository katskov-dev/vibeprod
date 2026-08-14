import json
import re

from fastapi import APIRouter, HTTPException

from .. import db
from .. import mcp_services

router = APIRouter(prefix="/api")

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _status(row):
    d = dict(row)
    d["service_status"] = mcp_services.service_status(d.get("service_container")) if d.get("service_container") else None
    return d


def _validate(payload):
    name = (payload.get("name") or "").strip().lower()
    if not NAME_RE.match(name):
        raise HTTPException(400, "имя: строчные латинские буквы, цифры, дефис")
    mtype = payload.get("type") or "remote"
    if mtype not in ("local", "remote"):
        raise HTTPException(400, "type: local | remote")
    for field in ("command", "headers", "environment"):
        raw = payload.get(field)
        if raw:
            try:
                json.loads(raw)
            except ValueError:
                raise HTTPException(400, f"{field}: корректный JSON или пусто")
    if mtype == "remote" and not payload.get("url"):
        raise HTTPException(400, "url обязателен для remote")
    if mtype == "local" and not payload.get("command"):
        raise HTTPException(400, "command обязателен для local")
    return name, mtype


@router.get("/mcp-catalog")
def list_catalog():
    return [_status(r) for r in db.query("SELECT * FROM mcp_catalog ORDER BY builtin DESC, name")]


@router.post("/mcp-catalog")
def create_entry(payload: dict):
    name, mtype = _validate(payload)
    if db.query_one("SELECT id FROM mcp_catalog WHERE name=?", (name,)):
        raise HTTPException(409, "запись с таким именем уже есть")
    cid = db.execute(
        "INSERT INTO mcp_catalog(name, description, kind, type, command, url, headers, environment) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            name,
            payload.get("description") or "",
            "generic",
            mtype,
            payload.get("command"),
            payload.get("url"),
            payload.get("headers"),
            payload.get("environment"),
        ),
    )
    return _status(db.query_one("SELECT * FROM mcp_catalog WHERE id=?", (cid,)))


@router.put("/mcp-catalog/{cid}")
def update_entry(cid: int, payload: dict):
    row = db.query_one("SELECT * FROM mcp_catalog WHERE id=?", (cid,))
    if not row:
        raise HTTPException(404, "запись не найдена")
    name, mtype = _validate({**row, **payload})
    other = db.query_one("SELECT id FROM mcp_catalog WHERE name=? AND id<>?", (name, cid))
    if other:
        raise HTTPException(409, "запись с таким именем уже есть")
    db.execute(
        "UPDATE mcp_catalog SET name=?, description=?, type=?, command=?, url=?, headers=?, environment=? WHERE id=?",
        (
            name,
            payload.get("description", row["description"]) or "",
            mtype,
            payload.get("command", row["command"]),
            payload.get("url", row["url"]),
            payload.get("headers", row["headers"]),
            payload.get("environment", row["environment"]),
            cid,
        ),
    )
    return _status(db.query_one("SELECT * FROM mcp_catalog WHERE id=?", (cid,)))


@router.delete("/mcp-catalog/{cid}")
def delete_entry(cid: int):
    row = db.query_one("SELECT * FROM mcp_catalog WHERE id=?", (cid,))
    if not row:
        raise HTTPException(404, "запись не найдена")
    if row["builtin"]:
        raise HTTPException(409, "встроенная запись не удаляется")
    db.execute("DELETE FROM mcp_catalog WHERE id=?", (cid,))
    return {"ok": True}


@router.post("/mcp-catalog/{cid}/start")
def start_service(cid: int):
    row = db.query_one("SELECT * FROM mcp_catalog WHERE id=?", (cid,))
    if not row:
        raise HTTPException(404, "запись не найдена")
    if not row["service_container"]:
        raise HTTPException(400, "у записи нет docker-сервиса")
    try:
        mcp_services.ensure_running(row)
    except Exception as exc:
        raise HTTPException(500, f"не удалось запустить сервис: {exc}")
    return _status(db.query_one("SELECT * FROM mcp_catalog WHERE id=?", (cid,)))


@router.post("/mcp-catalog/{cid}/stop")
def stop_service(cid: int):
    row = db.query_one("SELECT * FROM mcp_catalog WHERE id=?", (cid,))
    if not row:
        raise HTTPException(404, "запись не найдена")
    mcp_services.stop_service(row["service_container"])
    return _status(db.query_one("SELECT * FROM mcp_catalog WHERE id=?", (cid,)))


@router.post("/mcp-catalog/{cid}/attach")
def attach_to_agent(cid: int, payload: dict):
    """Добавляет запись каталога в агента (идемпотентно — апдейт по имени)."""
    entry = db.query_one("SELECT * FROM mcp_catalog WHERE id=?", (cid,))
    if not entry:
        raise HTTPException(404, "запись не найдена")
    agent_id = payload.get("agent_id")
    agent = db.query_one("SELECT id, is_guardian FROM agents WHERE id=?", (int(agent_id or 0),)) if agent_id else None
    if not agent:
        raise HTTPException(400, "agent_id обязателен и должен существовать")
    if agent["is_guardian"]:
        raise HTTPException(409, "guardian — системный агент, MCP ему не добавляются")
    db.execute("DELETE FROM agent_mcp WHERE agent_id=? AND name=?", (int(agent_id), entry["name"]))
    mid = db.execute(
        "INSERT INTO agent_mcp(agent_id, name, type, command, url, headers, environment, enabled) "
        "VALUES(?,?,?,?,?,?,?,1)",
        (
            int(agent_id),
            entry["name"],
            entry["type"],
            entry["command"],
            entry["url"],
            entry["headers"],
            entry["environment"],
        ),
    )
    return db.query_one("SELECT * FROM agent_mcp WHERE id=?", (mid,))
