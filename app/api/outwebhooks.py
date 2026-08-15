"""CRUD и журнал исходящих вебхуков: брокер POST'ит события на внешние URL."""
import asyncio
import json

from fastapi import APIRouter, HTTPException

from .. import db
from .. import events
from .. import outwebhooks

router = APIRouter(prefix="/api")


def _dict(row):
    d = dict(row)
    d.pop("secret", None)
    d["has_secret"] = bool(row["secret"])
    try:
        d["events"] = json.loads(row["events"] or "[]")
    except ValueError:
        d["events"] = []
    return d


def _joined(wid):
    return db.query_one(
        "SELECT w.*, (SELECT d.status FROM out_webhook_deliveries d WHERE d.webhook_id=w.id "
        "ORDER BY d.id DESC LIMIT 1) AS last_delivery_status "
        "FROM out_webhooks w WHERE w.id=?",
        (wid,),
    )


@router.get("/out-webhooks/events")
def list_event_types():
    return list(events.EVENT_TYPES)


@router.get("/out-webhooks")
def list_out_webhooks(project_id: int = None):
    if project_id is not None:
        rows = db.query(
            "SELECT w.*, (SELECT d.status FROM out_webhook_deliveries d WHERE d.webhook_id=w.id "
            "ORDER BY d.id DESC LIMIT 1) AS last_delivery_status "
            "FROM out_webhooks w WHERE w.project_id=? ORDER BY w.id",
            (project_id,),
        )
    else:
        rows = db.query(
            "SELECT w.*, (SELECT d.status FROM out_webhook_deliveries d WHERE d.webhook_id=w.id "
            "ORDER BY d.id DESC LIMIT 1) AS last_delivery_status "
            "FROM out_webhooks w ORDER BY w.id"
        )
    return [_dict(r) for r in rows]


def _validate(payload, row=None):
    url = (payload.get("url") or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(400, "url должен начинаться с http:// или https://")
    if len(url) > 2048:
        raise HTTPException(400, "url слишком длинный")
    events_list = payload.get("events")
    if isinstance(events_list, str):
        try:
            events_list = json.loads(events_list)
        except ValueError:
            events_list = None
    if events_list is not None:
        if not isinstance(events_list, list) or not events_list:
            raise HTTPException(400, "events: непустой список событий")
        bad = [e for e in events_list if e not in events.EVENT_TYPES]
        if bad:
            raise HTTPException(400, f"неизвестные события: {', '.join(bad)}")
    project_id = payload.get("project_id", row["project_id"] if row else None)
    if project_id is None:
        first = db.query_one("SELECT id FROM projects ORDER BY id LIMIT 1")
        project_id = first["id"] if first else None
    if project_id is not None and not db.query_one("SELECT id FROM projects WHERE id=?", (int(project_id),)):
        raise HTTPException(400, "проект не существует")
    return url, events_list, project_id


@router.post("/out-webhooks")
def create_out_webhook(payload: dict):
    url, events_list, project_id = _validate(payload)
    events_json = json.dumps(events_list or ["session.completed", "session.failed"], ensure_ascii=False)
    wid = db.execute(
        "INSERT INTO out_webhooks(project_id, name, url, events, secret, enabled) VALUES(?,?,?,?,?,?)",
        (
            project_id,
            (payload.get("name") or "").strip() or url,
            url,
            events_json,
            payload.get("secret") or "",
            1 if payload.get("enabled", True) else 0,
        ),
    )
    return _dict(_joined(wid))


@router.put("/out-webhooks/{webhook_id}")
def update_out_webhook(webhook_id: int, payload: dict):
    row = db.query_one("SELECT * FROM out_webhooks WHERE id=?", (webhook_id,))
    if not row:
        raise HTTPException(404, "вебхук не найден")
    url, events_list, project_id = _validate({**row, **payload}, row)
    secret = payload.get("secret")
    if secret is None:
        secret = row["secret"]
    events_json = json.dumps(events_list or ["session.completed", "session.failed"], ensure_ascii=False)
    db.execute(
        "UPDATE out_webhooks SET name=?, url=?, events=?, secret=?, enabled=?, project_id=? WHERE id=?",
        (
            (payload.get("name") or row["name"] or "").strip() or url,
            url,
            events_json,
            secret,
            1 if payload.get("enabled", row["enabled"]) else 0,
            project_id,
            webhook_id,
        ),
    )
    return _dict(_joined(webhook_id))


@router.delete("/out-webhooks/{webhook_id}")
def delete_out_webhook(webhook_id: int):
    row = db.query_one("SELECT id FROM out_webhooks WHERE id=?", (webhook_id,))
    if not row:
        raise HTTPException(404, "вебхук не найден")
    db.execute("DELETE FROM out_webhooks WHERE id=?", (webhook_id,))
    return {"ok": True}


@router.post("/out-webhooks/{webhook_id}/test")
async def test_out_webhook(webhook_id: int):
    """Шлёт webhook.test на реальный URL — одна попытка, результат в ответе."""
    row = db.query_one("SELECT * FROM out_webhooks WHERE id=?", (webhook_id,))
    if not row:
        raise HTTPException(404, "вебхук не найден")
    did = outwebhooks.enqueue(row, "webhook.test", {"message": "Тестовое событие Vibeprod"})
    await outwebhooks.deliver(did, attempts_limit=1)
    delivery = db.query_one("SELECT * FROM out_webhook_deliveries WHERE id=?", (did,))
    return {"ok": delivery["status"] == "success", "delivery": dict(delivery)}


@router.get("/out-webhooks/{webhook_id}/deliveries")
def list_deliveries(webhook_id: int, limit: int = 50):
    row = db.query_one("SELECT id FROM out_webhooks WHERE id=?", (webhook_id,))
    if not row:
        raise HTTPException(404, "вебхук не найден")
    limit = min(max(1, limit), 200)
    rows = db.query(
        "SELECT * FROM out_webhook_deliveries WHERE webhook_id=? ORDER BY id DESC LIMIT ?",
        (webhook_id, limit),
    )
    for r in rows:
        try:
            r["payload"] = json.loads(r["payload"] or "{}")
        except ValueError:
            r["payload"] = {"error": "unparsed"}
    return rows


@router.post("/out-webhooks/{webhook_id}/deliveries/{delivery_id}/retry")
async def retry_delivery(webhook_id: int, delivery_id: int):
    d = db.query_one(
        "SELECT id FROM out_webhook_deliveries WHERE id=? AND webhook_id=?", (delivery_id, webhook_id)
    )
    if not d:
        raise HTTPException(404, "доставка не найдена")
    db.execute(
        "UPDATE out_webhook_deliveries SET status='pending', attempts=0, error=NULL, "
        "http_status=NULL, finished_at=NULL WHERE id=?",
        (delivery_id,),
    )
    asyncio.create_task(outwebhooks.deliver(delivery_id))
    return {"ok": True, "status": "retrying"}
