from fastapi import APIRouter, HTTPException

from .. import db

router = APIRouter(prefix="/api")


@router.get("/projects")
def list_projects():
    return db.query(
        """
        SELECT p.*,
          (SELECT COUNT(*) FROM agents a WHERE a.project_id=p.id) AS agent_count,
          (SELECT COUNT(*) FROM sessions s WHERE s.project_id=p.id) AS session_count,
          (SELECT COUNT(*) FROM schedules sc WHERE sc.project_id=p.id) AS schedule_count,
          (SELECT COUNT(*) FROM providers pr WHERE pr.project_id=p.id) AS provider_count
        FROM projects p ORDER BY p.name
        """
    )


@router.post("/projects")
def create_project(payload: dict):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name обязателен")
    pid = db.execute(
        "INSERT INTO projects(name, description) VALUES(?,?)",
        (name, payload.get("description") or ""),
    )
    return db.query_one("SELECT * FROM projects WHERE id=?", (pid,))


@router.put("/projects/{project_id}")
def update_project(project_id: int, payload: dict):
    row = db.query_one("SELECT * FROM projects WHERE id=?", (project_id,))
    if not row:
        raise HTTPException(404, "проект не найден")
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name обязателен")
    db.execute(
        "UPDATE projects SET name=?, description=? WHERE id=?",
        (name, payload.get("description", row["description"]), project_id),
    )
    return db.query_one("SELECT * FROM projects WHERE id=?", (project_id,))


@router.delete("/projects/{project_id}")
async def delete_project(project_id: int):
    row = db.query_one("SELECT * FROM projects WHERE id=?", (project_id,))
    if not row:
        raise HTTPException(404, "проект не найден")
    remaining = db.query_one("SELECT COUNT(*) AS n FROM projects")["n"]
    if remaining <= 1:
        raise HTTPException(409, "нельзя удалить последний проект")
    from .. import scheduler
    from .. import session_manager

    # сессии: убить контейнеры, тома и workspace
    for s in db.query("SELECT id FROM sessions WHERE project_id=?", (project_id,)):
        await session_manager.delete_session(s["id"])
    # расписания: снять cron-джобы и удалить (runs каскадом)
    schedule_ids = [r["id"] for r in db.query("SELECT id FROM schedules WHERE project_id=?", (project_id,))]
    db.execute("DELETE FROM schedules WHERE project_id=?", (project_id,))
    for sid in schedule_ids:
        scheduler.apply_schedule(sid)
    # агенты (mcp и скиллы каскадом)
    db.execute("DELETE FROM agents WHERE project_id=?", (project_id,))
    db.execute("DELETE FROM providers WHERE project_id=?", (project_id,))
    db.execute("DELETE FROM projects WHERE id=?", (project_id,))
    return {"ok": True}
