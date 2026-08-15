"""Issues: простые задачи проекта (название, описание, дата, статус, теги).

Заводятся вручную в интерфейсе или агентами через встроенные инструменты
broker MCP (issue_create и т.п.). Фильтрация по статусу/тегу и поиск —
серверно (?status=, ?tag=, ?q=) и в интерфейсе.
"""
from fastapi import APIRouter, HTTPException

from .. import db

router = APIRouter(prefix="/api")

STATUSES = ("open", "in_progress", "done")


def _tags_in(raw):
    if isinstance(raw, list):
        parts = [str(t).strip() for t in raw]
    else:
        parts = [t.strip() for t in str(raw or "").split(",")]
    return [t for t in parts if t][:10]


def _tags_out(row):
    d = dict(row)
    d["tags"] = [t for t in str(d.get("tags") or "").split(",") if t]
    return d


def _validated(payload, existing=None):
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title обязателен")
    status = payload.get("status") or (existing["status"] if existing else None) or "open"
    if status not in STATUSES:
        raise HTTPException(400, f"status: один из {', '.join(STATUSES)}")
    if payload.get("tags") is not None:
        tags = ",".join(_tags_in(payload.get("tags")))
    else:
        tags = existing["tags"] if existing else ""
    return title, status, tags


@router.get("/issues")
def list_issues(project_id: int = None, status: str = None, tag: str = None, q: str = None):
    sql = "SELECT * FROM issues WHERE 1=1"
    params = []
    if project_id is not None:
        sql += " AND project_id=?"
        params.append(project_id)
    if status in STATUSES:
        sql += " AND status=?"
        params.append(status)
    if tag:
        sql += " AND (',' || tags || ',') LIKE ?"
        params.append(f"%,{tag.strip()},%")
    if q:
        like = f"%{q.strip()}%"
        sql += " AND (title LIKE ? OR description LIKE ?)"
        params.extend((like, like))
    sql += " ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END, id DESC"
    return [_tags_out(r) for r in db.query(sql, params)]


@router.post("/issues")
def create_issue(payload: dict):
    title, status, tags = _validated(payload)
    project_id = payload.get("project_id")
    if project_id is not None and not db.query_one("SELECT id FROM projects WHERE id=?", (project_id,)):
        raise HTTPException(400, "проект не существует")
    iid = db.execute(
        "INSERT INTO issues(project_id, title, description, status, tags, created_by) VALUES(?,?,?,?,?,?)",
        (project_id, title, payload.get("description") or "", status, tags, payload.get("created_by") or "manual"),
    )
    return _tags_out(db.query_one("SELECT * FROM issues WHERE id=?", (iid,)))


@router.put("/issues/{issue_id}")
def update_issue(issue_id: int, payload: dict):
    row = db.query_one("SELECT * FROM issues WHERE id=?", (issue_id,))
    if not row:
        raise HTTPException(404, "issue не найден")
    merged = {**row, **payload}
    title, status, tags = _validated(merged, existing=row)
    db.execute(
        "UPDATE issues SET title=?, description=?, status=?, tags=?, updated_at=datetime('now') WHERE id=?",
        (title, merged.get("description") or "", status, tags, issue_id),
    )
    return _tags_out(db.query_one("SELECT * FROM issues WHERE id=?", (issue_id,)))


@router.delete("/issues/{issue_id}")
def delete_issue(issue_id: int):
    row = db.query_one("SELECT * FROM issues WHERE id=?", (issue_id,))
    if not row:
        raise HTTPException(404, "issue не найден")
    db.execute("DELETE FROM issues WHERE id=?", (issue_id,))
    return {"ok": True}
