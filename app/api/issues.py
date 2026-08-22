"""Issues: задачи проекта (название, описание, статус, приоритет, исполнитель, теги, комментарии).

Заводятся вручную в интерфейсе или агентами через встроенные инструменты
broker MCP (issue_create и т.п.). Фильтрация по статусу/приоритету/исполнителю/
тегу и поиск — серверно (?status=, ?priority=, ?assignee_id=, ?tag=, ?q=)
и в интерфейсе.
"""
from fastapi import APIRouter, HTTPException

from .. import db

router = APIRouter(prefix="/api")

STATUSES = ("open", "in_progress", "review", "done", "cancelled")
PRIORITIES = ("low", "medium", "high", "critical")


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


def _comments(issue_id):
    return db.query(
        "SELECT id, agent_id, agent_name, text, created_at FROM issue_comments "
        "WHERE issue_id=? ORDER BY id",
        (issue_id,),
    )


def _issue_row(issue_id):
    row = db.query_one(
        "SELECT i.*, a.name AS assignee_name FROM issues i "
        "LEFT JOIN agents a ON a.id=i.assignee_id WHERE i.id=?",
        (issue_id,),
    )
    if row is None:
        raise HTTPException(404, "issue не найден")
    return row


def _issue_out(row):
    d = _tags_out(row)
    d["comments"] = _comments(d["id"])
    return d


def _assignee_id(raw):
    """Исполнитель по id или имени агента; None — без исполнителя."""
    if raw is None:
        return None
    if isinstance(raw, int) or (isinstance(raw, str) and raw.strip().isdigit()):
        row = db.query_one("SELECT id FROM agents WHERE id=? AND is_guardian=0", (int(raw),))
    else:
        row = db.query_one("SELECT id FROM agents WHERE name=? AND is_guardian=0", (str(raw).strip(),))
    if not row:
        raise HTTPException(400, "исполнитель не найден (id или имя агента)")
    return row["id"]


def _validated(payload, existing=None):
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title обязателен")
    status = payload.get("status") or (existing["status"] if existing else None) or "open"
    if status not in STATUSES:
        raise HTTPException(400, f"status: один из {', '.join(STATUSES)}")
    priority = payload.get("priority") or (existing["priority"] if existing else None) or "medium"
    if priority not in PRIORITIES:
        raise HTTPException(400, f"priority: один из {', '.join(PRIORITIES)}")
    if "assignee_id" in payload:
        assignee_id = _assignee_id(payload.get("assignee_id"))
    else:
        assignee_id = existing["assignee_id"] if existing else None
    if payload.get("tags") is not None:
        tags = ",".join(_tags_in(payload.get("tags")))
    else:
        tags = existing["tags"] if existing else ""
    return title, status, priority, assignee_id, tags


@router.get("/issues")
def list_issues(project_id: int = None, status: str = None, priority: str = None,
                assignee_id: int = None, tag: str = None, q: str = None):
    sql = ("SELECT i.*, a.name AS assignee_name FROM issues i "
           "LEFT JOIN agents a ON a.id=i.assignee_id WHERE 1=1")
    params = []
    if project_id is not None:
        sql += " AND i.project_id=?"
        params.append(project_id)
    if status in STATUSES:
        sql += " AND i.status=?"
        params.append(status)
    if priority in PRIORITIES:
        sql += " AND i.priority=?"
        params.append(priority)
    if assignee_id is not None:
        sql += " AND i.assignee_id=?"
        params.append(assignee_id)
    if tag:
        sql += " AND (',' || i.tags || ',') LIKE ?"
        params.append(f"%,{tag.strip()},%")
    if q:
        like = f"%{q.strip()}%"
        sql += " AND (i.title LIKE ? OR i.description LIKE ?)"
        params.extend((like, like))
    order = " ".join(f"WHEN '{s}' THEN {i}" for i, s in enumerate(STATUSES))
    sql += f" ORDER BY CASE i.status {order} ELSE 9 END, i.id DESC"
    return [_issue_out(r) for r in db.query(sql, params)]


@router.post("/issues")
def create_issue(payload: dict):
    title, status, priority, assignee_id, tags = _validated(payload)
    project_id = payload.get("project_id")
    if project_id is not None and not db.query_one("SELECT id FROM projects WHERE id=?", (project_id,)):
        raise HTTPException(400, "проект не существует")
    iid = db.execute(
        "INSERT INTO issues(project_id, title, description, status, priority, assignee_id, tags, created_by) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (project_id, title, payload.get("description") or "", status, priority, assignee_id, tags,
         payload.get("created_by") or "manual"),
    )
    if payload.get("comment"):
        db.execute(
            "INSERT INTO issue_comments(issue_id, agent_name, text) VALUES(?,?,?)",
            (iid, payload.get("comment_agent") or "", payload.get("comment")),
        )
    return _issue_out(_issue_row(iid))


@router.get("/issues/{issue_id}")
def get_issue(issue_id: int):
    return _issue_out(_issue_row(issue_id))


@router.put("/issues/{issue_id}")
def update_issue(issue_id: int, payload: dict):
    row = db.query_one("SELECT * FROM issues WHERE id=?", (issue_id,))
    if not row:
        raise HTTPException(404, "issue не найден")
    merged = {**row, **payload}
    title, status, priority, assignee_id, tags = _validated(merged, existing=row)
    db.execute(
        "UPDATE issues SET title=?, description=?, status=?, priority=?, assignee_id=?, tags=?, "
        "updated_at=datetime('now') WHERE id=?",
        (title, merged.get("description") or "", status, priority, assignee_id, tags, issue_id),
    )
    if payload.get("comment"):
        db.execute(
            "INSERT INTO issue_comments(issue_id, agent_name, text) VALUES(?,?,?)",
            (issue_id, payload.get("comment_agent") or "", payload.get("comment")),
        )
    return _issue_out(_issue_row(issue_id))


@router.delete("/issues/{issue_id}")
def delete_issue(issue_id: int):
    row = db.query_one("SELECT * FROM issues WHERE id=?", (issue_id,))
    if not row:
        raise HTTPException(404, "issue не найден")
    db.execute("DELETE FROM issues WHERE id=?", (issue_id,))
    return {"ok": True}


@router.get("/issues/{issue_id}/comments")
def list_comments(issue_id: int):
    if not db.query_one("SELECT id FROM issues WHERE id=?", (issue_id,)):
        raise HTTPException(404, "issue не найден")
    return _comments(issue_id)


@router.post("/issues/{issue_id}/comments")
def add_comment(issue_id: int, payload: dict):
    if not db.query_one("SELECT id FROM issues WHERE id=?", (issue_id,)):
        raise HTTPException(404, "issue не найден")
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text обязателен")
    agent_id = payload.get("agent_id")
    if agent_id is not None and not db.query_one(
        "SELECT id FROM agents WHERE id=? AND is_guardian=0", (int(agent_id),)
    ):
        raise HTTPException(400, "agent_id: агент не найден")
    cid = db.execute(
        "INSERT INTO issue_comments(issue_id, agent_id, agent_name, text) VALUES(?,?,?,?)",
        (issue_id, agent_id, payload.get("agent_name") or "", text),
    )
    db.execute("UPDATE issues SET updated_at=datetime('now') WHERE id=?", (issue_id,))
    return db.query_one("SELECT * FROM issue_comments WHERE id=?", (cid,))


@router.delete("/issues/{issue_id}/comments/{comment_id}")
def delete_comment(issue_id: int, comment_id: int):
    if not db.query_one("SELECT id FROM issues WHERE id=?", (issue_id,)):
        raise HTTPException(404, "issue не найден")
    if not db.query_one(
        "SELECT id FROM issue_comments WHERE id=? AND issue_id=?", (comment_id, issue_id)
    ):
        raise HTTPException(404, "комментарий не найден")
    db.execute("DELETE FROM issue_comments WHERE id=?", (comment_id,))
    db.execute("UPDATE issues SET updated_at=datetime('now') WHERE id=?", (issue_id,))
    return {"ok": True}
