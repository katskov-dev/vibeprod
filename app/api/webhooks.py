import asyncio
import json
import re
import time

from fastapi import APIRouter, HTTPException, Request

from .. import db
from .. import events
from .. import session_manager

router = APIRouter(prefix="/api")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _dict(row):
    d = dict(row)
    d.pop("secret", None)
    d["has_secret"] = bool(row["secret"])
    return d


def _joined(wid):
    return db.query_one(
        "SELECT w.*, a.name AS agent_name, p.name AS project_name FROM webhooks w "
        "LEFT JOIN agents a ON a.id=w.agent_id LEFT JOIN projects p ON p.id=w.project_id WHERE w.id=?",
        (wid,),
    )


@router.get("/webhooks")
def list_webhooks(project_id: int = None):
    if project_id is not None:
        rows = db.query(
            "SELECT w.*, a.name AS agent_name, p.name AS project_name FROM webhooks w "
            "LEFT JOIN agents a ON a.id=w.agent_id LEFT JOIN projects p ON p.id=w.project_id "
            "WHERE w.project_id=? ORDER BY w.slug",
            (project_id,),
        )
    else:
        rows = db.query(
            "SELECT w.*, a.name AS agent_name, p.name AS project_name FROM webhooks w "
            "LEFT JOIN agents a ON a.id=w.agent_id LEFT JOIN projects p ON p.id=w.project_id "
            "ORDER BY w.slug"
        )
    return [_dict(r) for r in rows]


def _validate(payload):
    slug = (payload.get("slug") or "").strip().lower()
    if not SLUG_RE.match(slug):
        raise HTTPException(400, "slug: строчные латинские буквы, цифры, дефис")
    agent_id = payload.get("agent_id")
    agent = db.query_one("SELECT * FROM agents WHERE id=?", (int(agent_id or 0),)) if agent_id else None
    if not agent:
        raise HTTPException(400, "agent_id обязателен и должен существовать")
    project_id = payload.get("project_id", agent["project_id"])
    if project_id is None:
        first = db.query_one("SELECT id FROM projects ORDER BY id LIMIT 1")
        project_id = first["id"] if first else None
    if project_id is not None and not db.query_one("SELECT id FROM projects WHERE id=?", (int(project_id),)):
        raise HTTPException(400, "проект не существует")
    return slug, agent, project_id


@router.post("/webhooks")
def create_webhook(payload: dict):
    slug, agent, project_id = _validate(payload)
    if db.query_one("SELECT id FROM webhooks WHERE slug=?", (slug,)):
        raise HTTPException(409, "webhook с таким slug уже есть")
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
    return _dict(_joined(wid))


@router.put("/webhooks/{webhook_id}")
def update_webhook(webhook_id: int, payload: dict):
    row = db.query_one("SELECT * FROM webhooks WHERE id=?", (webhook_id,))
    if not row:
        raise HTTPException(404, "webhook не найден")
    slug, agent, project_id = _validate({**row, **payload})
    other = db.query_one("SELECT id FROM webhooks WHERE slug=? AND id<>?", (slug, webhook_id))
    if other:
        raise HTTPException(409, "webhook с таким slug уже есть")
    secret = payload.get("secret")
    if secret is None:
        secret = row["secret"]
    db.execute(
        "UPDATE webhooks SET slug=?, agent_id=?, project_id=?, title=?, prompt=?, secret=?, enabled=? WHERE id=?",
        (
            slug,
            agent["id"],
            project_id,
            payload.get("title", row["title"]) or "",
            payload.get("prompt", row["prompt"]) or "",
            secret,
            1 if payload.get("enabled", row["enabled"]) else 0,
            webhook_id,
        ),
    )
    return _dict(_joined(webhook_id))


@router.delete("/webhooks/{webhook_id}")
def delete_webhook(webhook_id: int):
    row = db.query_one("SELECT * FROM webhooks WHERE id=?", (webhook_id,))
    if not row:
        raise HTTPException(404, "webhook не найден")
    db.execute("DELETE FROM webhooks WHERE id=?", (webhook_id,))
    return {"ok": True}


@router.post("/webhooks/{slug}/run")
async def run_webhook(slug: str, payload: dict = None, wait: int = 0, request: Request = None):
    """Запуск агента по вебхуку. Тело: {prompt?, title?}; wait=сек — дождаться результата."""
    row = db.query_one("SELECT * FROM webhooks WHERE slug=?", (slug,))
    if not row:
        raise HTTPException(404, "webhook не найден")
    if not row["enabled"]:
        raise HTTPException(403, "webhook выключен")
    if row["secret"]:
        secret = request.headers.get("x-webhook-secret") or request.headers.get("authorization", "")
        if secret != row["secret"]:
            raise HTTPException(401, "неверный секрет")
    payload = payload or {}
    prompt = (payload.get("prompt") or "").strip() or (row["prompt"] or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt обязателен (в теле запроса или как промпт webhook по умолчанию)")
    title = (payload.get("title") or "").strip() or row["title"] or f"webhook:{slug}"
    try:
        sid = session_manager.create_session(row["agent_id"], title, prompt, source="webhook")
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    db.execute("UPDATE webhooks SET last_run=datetime('now') WHERE id=?", (row["id"],))
    events.emit("webhook.received", {"slug": slug, "session_id": sid, "prompt": prompt, "title": title})
    from ..main import spawn_start

    spawn_start(sid, prompt)
    result = {"ok": True, "session_id": sid, "status": "queued"}
    if wait and int(wait) > 0:
        deadline = time.time() + min(int(wait), 600)
        while time.time() < deadline:
            await asyncio.sleep(2)
            s = db.query_one("SELECT status, result_json, error FROM sessions WHERE id=?", (sid,))
            if s["status"] in ("completed", "failed", "expired"):
                result["status"] = s["status"]
                result["error"] = s["error"]
                if s["result_json"]:
                    try:
                        result["result"] = json.loads(s["result_json"])
                    except ValueError:
                        pass
                return result
        result["status"] = "running"
    return result
