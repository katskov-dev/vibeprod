"""CRUD автоматизаций: правила, запускающие агентов по событиям брокера."""
import json

from fastapi import APIRouter, HTTPException

from .. import automations
from .. import db
from .. import events

router = APIRouter(prefix="/api")


def _dict(row):
    d = dict(row)
    try:
        d["events"] = json.loads(row["events"] or "[]")
    except ValueError:
        d["events"] = []
    return d


def _joined(aid):
    return db.query_one(
        "SELECT a.*, ag.name AS agent_name, p.name AS project_name, "
        "(SELECT r.status FROM automation_runs r WHERE r.automation_id=a.id "
        "ORDER BY r.id DESC LIMIT 1) AS last_run_status "
        "FROM automations a "
        "LEFT JOIN agents ag ON ag.id=a.agent_id LEFT JOIN projects p ON p.id=a.project_id "
        "WHERE a.id=?",
        (aid,),
    )


@router.get("/automations")
def list_automations(project_id: int = None):
    if project_id is not None:
        rows = db.query(
            "SELECT a.*, ag.name AS agent_name, p.name AS project_name, "
            "(SELECT r.status FROM automation_runs r WHERE r.automation_id=a.id "
            "ORDER BY r.id DESC LIMIT 1) AS last_run_status "
            "FROM automations a "
            "LEFT JOIN agents ag ON ag.id=a.agent_id LEFT JOIN projects p ON p.id=a.project_id "
            "WHERE a.project_id=? ORDER BY a.id",
            (project_id,),
        )
    else:
        rows = db.query(
            "SELECT a.*, ag.name AS agent_name, p.name AS project_name, "
            "(SELECT r.status FROM automation_runs r WHERE r.automation_id=a.id "
            "ORDER BY r.id DESC LIMIT 1) AS last_run_status "
            "FROM automations a "
            "LEFT JOIN agents ag ON ag.id=a.agent_id LEFT JOIN projects p ON p.id=a.project_id "
            "ORDER BY a.id"
        )
    return [_dict(r) for r in rows]


def _validate(payload, row=None):
    agent_id = payload.get("agent_id", row["agent_id"] if row else None)
    agent = db.query_one("SELECT * FROM agents WHERE id=?", (int(agent_id or 0),)) if agent_id else None
    if not agent:
        raise HTTPException(400, "agent_id обязателен и должен существовать")
    events_list = payload.get("events", json.loads(row["events"] or "[]") if row else None)
    if isinstance(events_list, str):
        try:
            events_list = json.loads(events_list)
        except ValueError:
            events_list = None
    if not isinstance(events_list, list) or not events_list:
        raise HTTPException(400, "events: непустой список событий")
    bad = [e for e in events_list if e not in events.EVENT_TYPES]
    if bad:
        raise HTTPException(400, f"неизвестные события: {', '.join(bad)}")
    prompt = (payload.get("prompt", row["prompt"] if row else "") or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt обязателен")
    project_id = payload.get("project_id", row["project_id"] if row else None)
    if project_id is None:
        project_id = agent["project_id"]
    if project_id is not None and not db.query_one("SELECT id FROM projects WHERE id=?", (int(project_id),)):
        raise HTTPException(400, "проект не существует")
    return agent, events_list, prompt, project_id


@router.post("/automations")
def create_automation(payload: dict):
    agent, events_list, prompt, project_id = _validate(payload)
    aid = db.execute(
        "INSERT INTO automations(project_id, agent_id, name, events, prompt, enabled, chain) VALUES(?,?,?,?,?,?,?)",
        (
            project_id,
            agent["id"],
            (payload.get("name") or "").strip(),
            json.dumps(events_list, ensure_ascii=False),
            prompt,
            1 if payload.get("enabled", True) else 0,
            1 if payload.get("chain") else 0,
        ),
    )
    return _dict(_joined(aid))


@router.put("/automations/{automation_id}")
def update_automation(automation_id: int, payload: dict):
    row = db.query_one("SELECT * FROM automations WHERE id=?", (automation_id,))
    if not row:
        raise HTTPException(404, "автоматизация не найдена")
    agent, events_list, prompt, project_id = _validate(payload, row)
    db.execute(
        "UPDATE automations SET agent_id=?, project_id=?, name=?, events=?, prompt=?, enabled=?, chain=? WHERE id=?",
        (
            agent["id"],
            project_id,
            (payload.get("name", row["name"]) or "").strip(),
            json.dumps(events_list, ensure_ascii=False),
            prompt,
            1 if payload.get("enabled", row["enabled"]) else 0,
            1 if payload.get("chain", row["chain"]) else 0,
            automation_id,
        ),
    )
    return _dict(_joined(automation_id))


@router.delete("/automations/{automation_id}")
def delete_automation(automation_id: int):
    row = db.query_one("SELECT id FROM automations WHERE id=?", (automation_id,))
    if not row:
        raise HTTPException(404, "автоматизация не найдена")
    db.execute("DELETE FROM automations WHERE id=?", (automation_id,))
    return {"ok": True}


@router.post("/automations/{automation_id}/test")
def test_automation(automation_id: int):
    """Запускает правило один раз с тестовым событием (webhook.test)."""
    row = db.query_one("SELECT * FROM automations WHERE id=?", (automation_id,))
    if not row:
        raise HTTPException(404, "автоматизация не найдена")
    if not row["enabled"]:
        raise HTTPException(403, "автоматизация выключена")
    run_id = automations.test(automation_id)
    run = db.query_one("SELECT * FROM automation_runs WHERE id=?", (run_id,))
    return {"ok": run["status"] == "running", "run_id": run_id, "session_id": run["session_id"], "status": run["status"]}


@router.get("/automations/{automation_id}/runs")
def list_runs(automation_id: int, limit: int = 50):
    row = db.query_one("SELECT id FROM automations WHERE id=?", (automation_id,))
    if not row:
        raise HTTPException(404, "автоматизация не найдена")
    limit = min(max(1, limit), 200)
    rows = db.query(
        "SELECT * FROM automation_runs WHERE automation_id=? ORDER BY id DESC LIMIT ?",
        (automation_id, limit),
    )
    for r in rows:
        try:
            r["payload"] = json.loads(r["payload"] or "{}")
        except ValueError:
            r["payload"] = {"error": "unparsed"}
    return rows
