"""Дашборд проекта: сводка по сессиям, issues, агентам, провайдерам, триггерам.

Один вызов /api/dashboard?project_id= вместо десятка запросов с главной страницы.
Без project_id агрегирует все проекты; файлы и Telegram-канал привязаны к
конкретному проекту и в этом случае пустые.
"""
import json
from datetime import date, timedelta

from fastapi import APIRouter

from .. import db
from .. import files_store

router = APIRouter(prefix="/api")

ACTIVE_STATUSES = ("queued", "starting", "running")


def _pf(project_id, alias):
    """WHERE-фрагмент фильтра по проекту и параметры."""
    if project_id is None:
        return "", ()
    return f" AND {alias}.project_id=?", (project_id,)


def _result_preview(raw):
    """Короткий текст последнего ответа ассистента из result_json."""
    if not raw:
        return None
    try:
        data = json.loads(raw[:100_000])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, list):
        return None
    text = None
    for msg in data:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        for p in msg.get("parts") or []:
            if isinstance(p, dict) and p.get("type") == "text" and p.get("text"):
                text = p["text"]
    if not text:
        return None
    return " ".join(text.split())[:220]


@router.get("/dashboard")
def dashboard(project_id: int = None):
    pf_s, p_s = _pf(project_id, "s")

    active = db.query(
        "SELECT s.id, s.agent_name, s.title, s.status, s.model, s.started_at, s.created_at "
        f"FROM sessions s WHERE s.status IN ('queued','starting','running'){pf_s} "
        "ORDER BY s.created_at DESC LIMIT 20",
        p_s,
    )

    failed = db.query(
        "SELECT s.id, s.agent_name, s.title, s.error, s.source, s.finished_at "
        f"FROM sessions s WHERE s.status='failed'{pf_s} "
        "ORDER BY s.finished_at DESC LIMIT 8",
        p_s,
    )

    feed = db.query(
        "SELECT s.id, s.agent_name, s.title, s.status, s.error, s.model, "
        "s.created_at, s.finished_at, s.source, substr(s.result_json, 1, 100000) AS result_json "
        f"FROM sessions s WHERE s.status IN ('completed','failed'){pf_s} "
        "ORDER BY COALESCE(s.finished_at, s.created_at) DESC LIMIT 12",
        p_s,
    )
    for row in feed:
        row["preview"] = _result_preview(row.pop("result_json"))

    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    raw_activity = {
        r["d"]: r for r in db.query(
            "SELECT date(s.created_at) AS d, COUNT(*) AS total, "
            "SUM(CASE WHEN s.status='failed' THEN 1 ELSE 0 END) AS failed "
            f"FROM sessions s WHERE s.created_at >= date('now', '-13 days'){pf_s} "
            "GROUP BY date(s.created_at)",
            p_s,
        )
    }
    activity = [
        {"d": d, "total": raw_activity[d]["total"] if d in raw_activity else 0,
         "failed": raw_activity[d]["failed"] if d in raw_activity else 0}
        for d in days
    ]

    pf_i, p_i = _pf(project_id, "i")
    by_status = {
        r["status"]: r["n"]
        for r in db.query(
            f"SELECT i.status, COUNT(*) AS n FROM issues i WHERE 1=1{pf_i} GROUP BY i.status", p_i
        )
    }
    critical_open = db.query_one(
        "SELECT COUNT(*) AS n FROM issues i WHERE i.priority='critical' "
        f"AND i.status NOT IN ('done','cancelled'){pf_i}",
        p_i,
    )["n"]
    issues = {
        "by_status": by_status,
        "critical_open": critical_open,
        "total": sum(by_status.values()),
    }

    pf_a, p_a = _pf(project_id, "a")
    agents = db.query(
        "SELECT a.id, a.name, a.description, a.mode, a.model, a.memory_enabled, "
        "a.issues_own_only, a.is_default, "
        "(SELECT COUNT(*) FROM agent_calls c WHERE c.caller_id=a.id) AS calls_count, "
        "(SELECT MAX(s2.created_at) FROM sessions s2 WHERE s2.agent_id=a.id) AS last_run "
        f"FROM agents a WHERE a.is_guardian=0{pf_a} ORDER BY a.is_default DESC, a.name",
        p_a,
    )

    pf_pr, p_pr = _pf(project_id, "p")
    providers = db.query(
        "SELECT p.id, p.label, p.enabled, (p.api_key <> '') AS has_key, "
        "p.last_check_ok, p.last_check_at, p.last_check_error "
        f"FROM providers p WHERE 1=1{pf_pr} ORDER BY p.enabled DESC, p.id",
        p_pr,
    )

    pf_sc, p_sc = _pf(project_id, "s")
    sched_counts = db.query_one(
        "SELECT COUNT(*) AS total, COALESCE(SUM(CASE WHEN s.enabled=1 THEN 1 ELSE 0 END), 0) AS enabled "
        f"FROM schedules s WHERE 1=1{pf_sc}",
        p_sc,
    )
    schedules = db.query(
        "SELECT s.id, s.title, s.enabled, s.cron, s.last_run, a.name AS agent_name "
        f"FROM schedules s JOIN agents a ON a.id=s.agent_id WHERE 1=1{pf_sc} "
        "ORDER BY s.enabled DESC, s.title LIMIT 12",
        p_sc,
    )

    def _count_enabled(table):
        pf, p = _pf(project_id, "w")
        return db.query_one(f"SELECT COUNT(*) AS n FROM {table} w WHERE w.enabled=1{pf}", p)["n"]

    triggers = {
        "webhooks": _count_enabled("webhooks"),
        "automations": _count_enabled("automations"),
        "out_webhooks": _count_enabled("out_webhooks"),
    }

    channel = None
    files = []
    if project_id is not None:
        cfg = db.query_one(
            "SELECT token, enabled, bot_username, connected, last_error "
            "FROM telegram_config WHERE project_id=?",
            (project_id,),
        )
        if cfg:
            channel = {
                "configured": bool(cfg["token"]),
                "enabled": bool(cfg["enabled"]),
                "connected": bool(cfg["connected"]),
                "bot_username": cfg["bot_username"],
                "last_error": cfg["last_error"],
            }
        try:
            if files_store.healthy():
                objs = files_store.list_objects(project_id)
                objs.sort(key=lambda x: x["last_modified"] or "", reverse=True)
                files = objs[:8]
        except Exception:
            pass

    pf_d, p_d = _pf(project_id, "w")
    failed_deliveries = db.query(
        "SELECT d.id, d.webhook_id, d.event, d.error, d.started_at, w.name AS webhook_name "
        f"FROM out_webhook_deliveries d JOIN out_webhooks w ON w.id=d.webhook_id "
        f"WHERE d.status='failed'{pf_d} ORDER BY d.started_at DESC LIMIT 5",
        p_d,
    )

    failed_runs = db.query(
        "SELECT r.id, r.schedule_id, r.error, r.started_at, s.title AS schedule_title "
        f"FROM schedule_runs r JOIN schedules s ON s.id=r.schedule_id "
        f"WHERE r.status='failed'{pf_s} ORDER BY r.started_at DESC LIMIT 5",
        p_s,
    )

    return {
        "active_sessions": active,
        "failed_sessions": failed,
        "feed": feed,
        "activity": activity,
        "issues": issues,
        "agents": agents,
        "providers": providers,
        "schedules": schedules,
        "schedules_total": sched_counts["total"],
        "schedules_enabled": sched_counts["enabled"],
        "triggers": triggers,
        "channel": channel,
        "files": files,
        "failed_deliveries": failed_deliveries,
        "failed_runs": failed_runs,
    }
