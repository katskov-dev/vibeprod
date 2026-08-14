import asyncio

from fastapi import APIRouter, HTTPException

from .. import db
from .. import scheduler

router = APIRouter(prefix="/api")


def _schedule_dict(row):
    d = dict(row)
    d["next_run"] = scheduler.job_next_run(row["id"])
    d["last_run_status"] = db.query_one(
        "SELECT status FROM schedule_runs WHERE schedule_id=? ORDER BY id DESC LIMIT 1",
        (row["id"],),
    )
    d["last_run_status"] = d["last_run_status"]["status"] if d["last_run_status"] else None
    return d


@router.get("/schedules")
def list_schedules(project_id: int = None):
    sql = (
        "SELECT s.*, a.name AS agent_name, p.name AS project_name FROM schedules s "
        "LEFT JOIN agents a ON a.id=s.agent_id "
        "LEFT JOIN projects p ON p.id=s.project_id "
    )
    params = ()
    if project_id is not None:
        sql += "WHERE s.project_id=? "
        params = (project_id,)
    sql += "ORDER BY s.id"
    return [_schedule_dict(r) for r in db.query(sql, params)]


@router.post("/schedules")
def create_schedule(payload: dict):
    agent_id = payload.get("agent_id")
    if not agent_id or not db.query_one("SELECT id FROM agents WHERE id=?", (int(agent_id),)):
        raise HTTPException(400, "agent_id обязателен и должен существовать")
    agent = db.query_one("SELECT project_id FROM agents WHERE id=?", (int(agent_id),))
    project_id = payload.get("project_id", agent["project_id"])
    if project_id is not None and not db.query_one("SELECT id FROM projects WHERE id=?", (int(project_id),)):
        raise HTTPException(400, "проект не существует")
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt обязателен")
    cron = (payload.get("cron") or "").strip()
    if not cron:
        raise HTTPException(400, "cron обязателен")
    tz = payload.get("timezone") or "Europe/Moscow"
    try:
        scheduler.validate_cron(cron, tz)
    except ValueError as exc:
        raise HTTPException(400, f"некорректный cron: {exc}")
    sid = db.execute(
        "INSERT INTO schedules(agent_id, project_id, title, prompt, cron, timezone, enabled) VALUES(?,?,?,?,?,?,?)",
        (int(agent_id), project_id, payload.get("title") or "", prompt, cron, tz, 1 if payload.get("enabled", True) else 0),
    )
    scheduler.apply_schedule(sid)
    return _schedule_dict(db.query_one("SELECT * FROM schedules WHERE id=?", (sid,)))


@router.put("/schedules/{schedule_id}")
def update_schedule(schedule_id: int, payload: dict):
    row = db.query_one("SELECT * FROM schedules WHERE id=?", (schedule_id,))
    if not row:
        raise HTTPException(404, "расписание не найдено")
    merged = {**row, **payload}
    cron = merged["cron"]
    tz = merged["timezone"] or "Europe/Moscow"
    try:
        scheduler.validate_cron(cron, tz)
    except ValueError as exc:
        raise HTTPException(400, f"некорректный cron: {exc}")
    project_id = payload.get("project_id", row["project_id"])
    if project_id is not None and not db.query_one("SELECT id FROM projects WHERE id=?", (int(project_id),)):
        raise HTTPException(400, "проект не существует")
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
            schedule_id,
        ),
    )
    scheduler.apply_schedule(schedule_id)
    return _schedule_dict(db.query_one("SELECT * FROM schedules WHERE id=?", (schedule_id,)))


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: int):
    db.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
    scheduler.apply_schedule(schedule_id)
    return {"ok": True}


@router.post("/schedules/{schedule_id}/run-now")
async def run_now(schedule_id: int):
    row = db.query_one("SELECT * FROM schedules WHERE id=?", (schedule_id,))
    if not row:
        raise HTTPException(404, "расписание не найдено")
    await asyncio.to_thread(scheduler._fire, schedule_id)
    return {"ok": True}


@router.get("/schedules/{schedule_id}/runs")
def list_runs(schedule_id: int):
    return db.query(
        "SELECT r.*, s.title AS session_title, s.status AS session_status "
        "FROM schedule_runs r LEFT JOIN sessions s ON s.id=r.session_id "
        "WHERE r.schedule_id=? ORDER BY r.id DESC LIMIT 100",
        (schedule_id,),
    )
