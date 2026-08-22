"""Автоматизации: вызов агентов по событиям брокера.

Правила живут в таблице automations (агент + список событий + промпт-шаблон).
dispatch() вызывается шиной событий (events.py) из любого потока: для каждого
подходящего правила создаётся строка журнала в automation_runs и сессия
(source="automation"), которую фоново запускает spawn_start — так же, как
расписания и входящие вебхуки.

В промпте-шаблоне доступны плейсхолдеры {ключ} по полям данных события
(title, status, prompt, agent_name, project_name, error, url, session_id…),
плюс {event} и {json} — полный пейлоад события. Неизвестные ключи
заменяются пустой строкой.

События доставляются синхронно и теряются при падении процесса — тот же
осознанный компромисс, что и у исходящих вебхуков.

Защита от циклов: по умолчанию правило не реагирует на сессии, которые сами
созданы автоматизациями (source="automation") — иначе «сессия завершилась →
новая сессия → завершилась → …» крутилось бы бесконечно. Цепочки правил
включаются явно флагом chain=1. Вдобавок правило не может сработать на себя
рекурсивно в одном синхронном стеке (session.created внутри fire()).
"""

import json
import logging
import threading
from collections import defaultdict

from . import db

log = logging.getLogger("vibeprod.automations")

MAX_RUNS = 200

_local = threading.local()


def _events_of(row):
    try:
        return json.loads(row["events"] or "[]")
    except ValueError:
        return []


def matching(event):
    rows = []
    for row in db.query("SELECT * FROM automations WHERE enabled=1"):
        if event in _events_of(row):
            rows.append(row)
    return rows


def build_prompt(template, event, data):
    """Подстановка плейсхолдеров: {event}, {json} и любые ключи данных события."""
    vals = defaultdict(str, {k: v for k, v in (data or {}).items()})
    vals["event"] = event
    vals["json"] = json.dumps(data or {}, ensure_ascii=False, indent=2)
    try:
        return (template or "").format_map(vals)
    except (KeyError, ValueError):
        return template


def _project_ok(row, data):
    """Правило с project_id срабатывает только на события своего проекта."""
    pid = row["project_id"]
    if not pid:
        return True
    data_pid = data.get("project_id")
    if data_pid is None and data.get("session_id"):
        s = db.query_one("SELECT project_id FROM sessions WHERE id=?", (data["session_id"],))
        data_pid = s["project_id"] if s else None
    return data_pid is None or int(data_pid) == int(pid)


def _chain_ok(row, data):
    """Без флага chain правило не реагирует на сессии автоматизаций (защита от циклов)."""
    return bool(row["chain"]) or data.get("source") != "automation"


def _session_title(row, event, data):
    if data.get("title"):
        return f"{row['name'] or 'Автоматизация'}: {str(data['title'])[:80]}"
    return f"{row['name'] or 'Автоматизация'}: {event}"


def fire(row, event, data, main_loop=None):
    """Создать сессию агента по правилу и запланировать её запуск. Возвращает run_id."""
    run_id = db.execute(
        "INSERT INTO automation_runs(automation_id, event, payload, status, started_at) "
        "VALUES(?,?,?,'running',datetime('now'))",
        (row["id"], event, json.dumps(data or {}, ensure_ascii=False)),
    )
    try:
        from . import session_manager

        prompt = build_prompt(row["prompt"] or "", event, data)
        sid = session_manager.create_session(
            row["agent_id"],
            _session_title(row, event, data),
            prompt,
            source="automation",
            project_id=row["project_id"],
        )
        db.execute(
            "UPDATE automation_runs SET session_id=?, payload=? WHERE id=?",
            (sid, json.dumps(data or {}, ensure_ascii=False), run_id),
        )
        db.execute("UPDATE automations SET last_run=datetime('now') WHERE id=?", (row["id"],))
        from .main import spawn_start

        spawn_start(sid, prompt)
        return run_id
    except Exception as exc:
        log.exception("automation %s: %s", row["id"], event)
        db.execute(
            "UPDATE automation_runs SET status='failed', error=?, finished_at=datetime('now') WHERE id=?",
            (str(exc)[:2000], run_id),
        )
        return run_id


def dispatch(event, data=None, main_loop=None):
    """Разослать событие всем подходящим правилам. Потокобезопасно."""
    data = data or {}
    firing = getattr(_local, "firing", None)
    if firing is None:
        firing = _local.firing = set()
    for row in matching(event):
        if row["id"] in firing:
            continue
        if not _chain_ok(row, data):
            continue
        if not _project_ok(row, data):
            continue
        firing.add(row["id"])
        try:
            fire(row, event, data, main_loop=main_loop)
        finally:
            firing.discard(row["id"])
        _trim_journal(row["id"])


def test(automation_id, main_loop=None):
    """Запуск правила с синтетическим событием webhook.test (кнопка «тест» в UI)."""
    row = db.query_one("SELECT * FROM automations WHERE id=?", (automation_id,))
    if not row:
        return None
    data = {
        "id": "test",
        "title": "Тестовое событие",
        "status": "test",
        "message": "Тестовый запуск автоматизации",
    }
    return fire(row, "webhook.test", data, main_loop=main_loop)


def _trim_journal(automation_id):
    """Держим не более MAX_RUNS строк журнала на правило."""
    n = db.query_one(
        "SELECT COUNT(*) AS n FROM automation_runs WHERE automation_id=?", (automation_id,)
    )["n"]
    if n <= MAX_RUNS:
        return
    db.execute(
        "DELETE FROM automation_runs WHERE id IN "
        "(SELECT id FROM automation_runs WHERE automation_id=? ORDER BY id DESC LIMIT -1 OFFSET ?)",
        (automation_id, MAX_RUNS),
    )
