import asyncio
import re

import httpx
from fastapi import APIRouter, HTTPException

from .. import db
from .. import telegram

router = APIRouter(prefix="/api")

TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")


def _resolve_project(project_id):
    if project_id is not None:
        try:
            pid = int(project_id)
        except (TypeError, ValueError):
            raise HTTPException(400, "project_id должен быть числом")
        if not db.query_one("SELECT id FROM projects WHERE id=?", (pid,)):
            raise HTTPException(404, "проект не найден")
        return pid
    first = db.query_one("SELECT id FROM projects ORDER BY id LIMIT 1")
    return first["id"] if first else None


def _dict(row):
    if not row:
        return None
    d = dict(row)
    token = d.pop("token", "") or ""
    d["has_token"] = bool(token)
    d["token_tail"] = token[-6:] if len(token) > 6 else token
    return d


def _schedule_apply():
    from ..main import MAIN_LOOP

    if MAIN_LOOP is not None:
        asyncio.run_coroutine_threadsafe(telegram.apply_config(), MAIN_LOOP)


@router.get("/telegram")
def get_config(project_id: int = None):
    pid = _resolve_project(project_id)
    row = db.query_one("SELECT * FROM telegram_config WHERE project_id=?", (pid,))
    return _dict(row) or {"project_id": pid, "enabled": True, "connected": False, "has_token": False}


@router.put("/telegram")
def save_config(payload: dict, project_id: int = None):
    pid = _resolve_project(project_id if project_id is not None else payload.get("project_id"))
    token = (payload.get("token") or "").strip()
    existing = db.query_one("SELECT * FROM telegram_config WHERE project_id=?", (pid,))
    if token and not TOKEN_RE.match(token):
        raise HTTPException(400, "токен не похож на токен бота (формат: 123456:ABC…)")
    if not token:
        if existing and existing["token"]:
            token = existing["token"]
        else:
            raise HTTPException(400, "токен бота обязателен")
    allowed_users = (payload.get("allowed_users") or "").strip()
    web_url = (payload.get("web_url") or "").strip()
    notify_chat_id = (payload.get("notify_chat_id") or "").strip()
    if notify_chat_id and not re.fullmatch(r"-?\d+", notify_chat_id):
        raise HTTPException(400, "notify_chat_id должен быть числом (узнать id: команда /chatid боту)")
    notify_mode = (payload.get("notify_mode") or "all").strip()
    if notify_mode not in ("all", "errors"):
        raise HTTPException(400, "notify_mode: 'all' или 'errors'")
    enabled = 1 if payload.get("enabled", True) else 0
    if existing:
        db.execute(
            "UPDATE telegram_config SET token=?, allowed_users=?, web_url=?, notify_chat_id=?, notify_mode=?, "
            "enabled=?, updated_at=datetime('now') WHERE project_id=?",
            (token, allowed_users, web_url, notify_chat_id, notify_mode, enabled, pid),
        )
    else:
        db.execute(
            "INSERT INTO telegram_config(project_id, token, allowed_users, web_url, notify_chat_id, notify_mode, enabled) "
            "VALUES(?,?,?,?,?,?,?)",
            (pid, token, allowed_users, web_url, notify_chat_id, notify_mode, enabled),
        )
    _schedule_apply()
    return _dict(db.query_one("SELECT * FROM telegram_config WHERE project_id=?", (pid,)))


@router.delete("/telegram")
def delete_config(project_id: int = None):
    pid = _resolve_project(project_id)
    db.execute("DELETE FROM telegram_config WHERE project_id=?", (pid,))
    _schedule_apply()
    return {"ok": True}


@router.post("/telegram/test")
def test_token(payload: dict):
    token = (payload.get("token") or "").strip()
    if not token:
        raise HTTPException(400, "токен обязателен")
    try:
        with httpx.Client(base_url=telegram.API, timeout=20.0, trust_env=False) as client:
            r = client.post(f"/bot{token}/getMe")
            if r.status_code == 401:
                raise HTTPException(400, "Telegram отверг токен (401 Unauthorized)")
            r.raise_for_status()
            me = (r.json().get("result") or {})
            return {"ok": True, "username": me.get("username"), "name": me.get("first_name")}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"не удалось проверить токен: {exc}")
