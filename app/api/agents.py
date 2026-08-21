import json
import re

from fastapi import APIRouter, HTTPException

from .. import db

router = APIRouter(prefix="/api")

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _parse_temperature(raw):
    """Температура необязательна: пусто/None → None."""
    if raw is None:
        return None
    if isinstance(raw, str) and not raw.strip():
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise HTTPException(400, "temperature: число (или оставьте пустым)")


def _validate_agent(payload):
    name = (payload.get("name") or "").strip().lower()
    if not NAME_RE.match(name):
        raise HTTPException(400, "имя агента: строчные латинские буквы, цифры, дефис (до 64 символов)")
    mode = payload.get("mode") or "primary"
    if mode not in ("primary", "subagent", "all"):
        raise HTTPException(400, "mode: primary | subagent | all")
    model = (payload.get("model") or "").strip()
    if model and "/" not in model:
        raise HTTPException(400, "модель должна быть в формате provider/model, например deepseek/deepseek-v4-flash")
    temperature = _parse_temperature(payload.get("temperature"))
    perm = payload.get("permission")
    if perm:
        try:
            json.loads(perm)
        except ValueError:
            raise HTTPException(400, "permission: корректный JSON или пусто")
    return name, mode, model, temperature


def _agent_dict(row):
    a = dict(row)
    a["mcp"] = db.query(
        "SELECT id, name, type, command, url, headers, environment, enabled FROM agent_mcp WHERE agent_id=? ORDER BY name",
        (row["id"],),
    )
    a["skills"] = db.query(
        "SELECT s.id, s.name, s.description FROM skills s JOIN agent_skills x ON x.skill_id=s.id "
        "WHERE x.agent_id=? ORDER BY s.name",
        (row["id"],),
    )
    a["calls"] = db.query(
        "SELECT a2.id, a2.name, a2.description, a2.mode FROM agent_calls c "
        "JOIN agents a2 ON a2.id=c.target_id WHERE c.caller_id=? ORDER BY a2.name",
        (row["id"],),
    )
    return a


def _check_project(project_id):
    if project_id is not None:
        if not db.query_one("SELECT id FROM projects WHERE id=?", (int(project_id),)):
            raise HTTPException(400, "проект не существует")
        return int(project_id)
    return None


@router.get("/agents")
def list_agents(project_id: int = None):
    if project_id is not None:
        rows = db.query(
            "SELECT a.*, p.name AS project_name FROM agents a LEFT JOIN projects p ON p.id=a.project_id "
            "WHERE a.project_id=? AND a.is_guardian=0 ORDER BY a.name",
            (project_id,),
        )
    else:
        rows = db.query(
            "SELECT a.*, p.name AS project_name FROM agents a LEFT JOIN projects p ON p.id=a.project_id "
            "WHERE a.is_guardian=0 ORDER BY a.name"
        )
    return [_agent_dict(r) for r in rows]


@router.post("/agents")
def create_agent(payload: dict):
    name, mode, model, temperature = _validate_agent(payload)
    if db.query_one("SELECT id FROM agents WHERE name=?", (name,)):
        raise HTTPException(409, "агент с таким именем уже есть")
    aid = db.execute(
        "INSERT INTO agents(name, description, mode, model, temperature, variant, system_prompt, permission, "
        "memory, memory_enabled, is_default, project_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            name,
            payload.get("description") or "",
            mode,
            model or "deepseek/deepseek-chat",
            temperature,
            payload.get("variant") or None,
            payload.get("system_prompt") or "",
            payload.get("permission") or '"allow"',
            payload.get("memory") or "",
            1 if payload.get("memory_enabled", 1) else 0,
            1 if payload.get("is_default") else 0,
            _check_project(payload.get("project_id")),
        ),
    )
    if payload.get("is_default"):
        db.execute("UPDATE agents SET is_default=0 WHERE id<>?", (aid,))
    return _agent_dict(db.query_one("SELECT * FROM agents WHERE id=?", (aid,)))


@router.get("/agents/{agent_id}")
def get_agent(agent_id: int):
    row = db.query_one("SELECT * FROM agents WHERE id=?", (agent_id,))
    if not row:
        raise HTTPException(404, "агент не найден")
    return _agent_dict(row)


def _guard(agent_id):
    row = db.query_one("SELECT is_guardian FROM agents WHERE id=?", (agent_id,))
    if row and row["is_guardian"]:
        raise HTTPException(409, "guardian — системный агент, его нельзя менять")


@router.put("/agents/{agent_id}")
def update_agent(agent_id: int, payload: dict):
    row = db.query_one("SELECT * FROM agents WHERE id=?", (agent_id,))
    if not row:
        raise HTTPException(404, "агент не найден")
    _guard(agent_id)
    name, mode, model, temperature = _validate_agent({**row, **payload})
    other = db.query_one("SELECT id FROM agents WHERE name=? AND id<>?", (name, agent_id))
    if other:
        raise HTTPException(409, "агент с таким именем уже есть")
    db.execute(
        "UPDATE agents SET name=?, description=?, mode=?, model=?, temperature=?, variant=?, system_prompt=?, "
        "permission=?, memory=?, memory_enabled=?, is_default=?, project_id=?, updated_at=datetime('now') WHERE id=?",
        (
            name,
            payload.get("description", row["description"]),
            mode,
            model or row["model"],
            temperature,
            payload.get("variant", row["variant"]) or None,
            payload.get("system_prompt", row["system_prompt"]),
            payload.get("permission", row["permission"]),
            payload.get("memory", row["memory"]),
            1 if payload.get("memory_enabled", row["memory_enabled"]) else 0,
            1 if payload.get("is_default") else 0,
            _check_project(payload.get("project_id", row["project_id"])),
            agent_id,
        ),
    )
    if payload.get("is_default"):
        db.execute("UPDATE agents SET is_default=0 WHERE id<>?", (agent_id,))
    return _agent_dict(db.query_one("SELECT * FROM agents WHERE id=?", (agent_id,)))


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: int):
    row = db.query_one("SELECT * FROM agents WHERE id=?", (agent_id,))
    if not row:
        raise HTTPException(404, "агент не найден")
    _guard(agent_id)
    db.execute("DELETE FROM agents WHERE id=?", (agent_id,))
    return {"ok": True}


def _validate_mcp(payload):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name обязателен")
    mtype = payload.get("type") or "local"
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
    return name, mtype


@router.post("/agents/{agent_id}/mcp")
def add_mcp(agent_id: int, payload: dict):
    if not db.query_one("SELECT id FROM agents WHERE id=?", (agent_id,)):
        raise HTTPException(404, "агент не найден")
    _guard(agent_id)
    name, mtype = _validate_mcp(payload)
    if db.query_one("SELECT id FROM agent_mcp WHERE agent_id=? AND name=?", (agent_id, name)):
        raise HTTPException(409, "mcp с таким именем уже есть")
    mid = db.execute(
        "INSERT INTO agent_mcp(agent_id, name, type, command, url, headers, environment, enabled) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            agent_id, name, mtype,
            payload.get("command"),
            payload.get("url"),
            payload.get("headers"),
            payload.get("environment"),
            1 if payload.get("enabled", True) else 0,
        ),
    )
    return db.query_one("SELECT * FROM agent_mcp WHERE id=?", (mid,))


@router.put("/agents/{agent_id}/mcp/{mcp_id}")
def update_mcp(agent_id: int, mcp_id: int, payload: dict):
    _guard(agent_id)
    row = db.query_one("SELECT * FROM agent_mcp WHERE id=? AND agent_id=?", (mcp_id, agent_id))
    if not row:
        raise HTTPException(404, "mcp не найден")
    name, mtype = _validate_mcp({**row, **payload})
    db.execute(
        "UPDATE agent_mcp SET name=?, type=?, command=?, url=?, headers=?, environment=?, enabled=? WHERE id=?",
        (
            name, mtype,
            payload.get("command", row["command"]),
            payload.get("url", row["url"]),
            payload.get("headers", row["headers"]),
            payload.get("environment", row["environment"]),
            1 if payload.get("enabled", row["enabled"]) else 0,
            mcp_id,
        ),
    )
    return db.query_one("SELECT * FROM agent_mcp WHERE id=?", (mcp_id,))


@router.delete("/agents/{agent_id}/mcp/{mcp_id}")
def delete_mcp(agent_id: int, mcp_id: int):
    _guard(agent_id)
    db.execute("DELETE FROM agent_mcp WHERE id=? AND agent_id=?", (mcp_id, agent_id))
    return {"ok": True}


@router.put("/agents/{agent_id}/skills")
def set_agent_skills(agent_id: int, payload: dict):
    _guard(agent_id)
    skill_ids = payload.get("skill_ids") or []
    try:
        skill_ids = [int(s) for s in skill_ids]
    except (TypeError, ValueError):
        raise HTTPException(400, "skill_ids: список id")
    db.execute("DELETE FROM agent_skills WHERE agent_id=?", (agent_id,))
    db.exec_many("INSERT INTO agent_skills(agent_id, skill_id) VALUES(?,?)",
                 [(agent_id, s) for s in skill_ids])
    return {"ok": True}


@router.get("/agents/{agent_id}/calls")
def get_agent_calls(agent_id: int):
    if not db.query_one("SELECT id FROM agents WHERE id=?", (agent_id,)):
        raise HTTPException(404, "агент не найден")
    return db.query(
        "SELECT a.id, a.name, a.description, a.mode FROM agent_calls c "
        "JOIN agents a ON a.id=c.target_id WHERE c.caller_id=? ORDER BY a.name",
        (agent_id,),
    )


@router.put("/agents/{agent_id}/calls")
def set_agent_calls(agent_id: int, payload: dict):
    if not db.query_one("SELECT id FROM agents WHERE id=?", (agent_id,)):
        raise HTTPException(404, "агент не найден")
    _guard(agent_id)
    target_ids = payload.get("target_ids") or []
    try:
        target_ids = [int(t) for t in target_ids]
    except (TypeError, ValueError):
        raise HTTPException(400, "target_ids: список id агентов")
    for tid in target_ids:
        if tid == agent_id:
            raise HTTPException(400, "агент не может вызвать сам себя")
        row = db.query_one("SELECT id, is_guardian FROM agents WHERE id=?", (tid,))
        if not row:
            raise HTTPException(400, f"агент {tid} не существует")
        if row["is_guardian"]:
            raise HTTPException(400, "guardian — системный агент, его нельзя вызывать")
    db.execute("DELETE FROM agent_calls WHERE caller_id=?", (agent_id,))
    db.exec_many("INSERT INTO agent_calls(caller_id, target_id) VALUES(?,?)",
                 [(agent_id, t) for t in target_ids])
    return {"ok": True}


@router.get("/skills")
def list_skills():
    return db.query(
        "SELECT s.*, (SELECT COUNT(*) FROM agent_skills x WHERE x.skill_id=s.id) AS agent_count "
        "FROM skills s ORDER BY s.name"
    )


@router.post("/skills")
def create_skill(payload: dict):
    name = (payload.get("name") or "").strip().lower()
    if not NAME_RE.match(name):
        raise HTTPException(400, "имя скилла: строчные латинские буквы, цифры, дефис")
    if db.query_one("SELECT id FROM skills WHERE name=?", (name,)):
        raise HTTPException(409, "скилл с таким именем уже есть")
    sid = db.execute(
        "INSERT INTO skills(name, description, body) VALUES(?,?,?)",
        (name, payload.get("description") or "", payload.get("body") or ""),
    )
    return db.query_one("SELECT * FROM skills WHERE id=?", (sid,))


@router.put("/skills/{skill_id}")
def update_skill(skill_id: int, payload: dict):
    row = db.query_one("SELECT * FROM skills WHERE id=?", (skill_id,))
    if not row:
        raise HTTPException(404, "скилл не найден")
    name = (payload.get("name") or row["name"]).strip().lower()
    if not NAME_RE.match(name):
        raise HTTPException(400, "имя скилла: строчные латинские буквы, цифры, дефис")
    other = db.query_one("SELECT id FROM skills WHERE name=? AND id<>?", (name, skill_id))
    if other:
        raise HTTPException(409, "скилл с таким именем уже есть")
    db.execute(
        "UPDATE skills SET name=?, description=?, body=? WHERE id=?",
        (name, payload.get("description", row["description"]), payload.get("body", row["body"]), skill_id),
    )
    return db.query_one("SELECT * FROM skills WHERE id=?", (skill_id,))


@router.delete("/skills/{skill_id}")
def delete_skill(skill_id: int):
    db.execute("DELETE FROM skills WHERE id=?", (skill_id,))
    return {"ok": True}
