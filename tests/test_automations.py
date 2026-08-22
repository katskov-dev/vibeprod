"""Тесты автоматизаций: правила, запускающие агентов по событиям брокера."""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBEPROD_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    from app import auth as auth_module
    from app import db as db_module

    importlib.reload(db_module)
    importlib.reload(auth_module)
    from app import main as main_module

    importlib.reload(main_module)

    from app import scheduler, session_manager, telegram

    async def _noop_async(*args, **kwargs):
        return None

    monkeypatch.setattr(session_manager, "reconcile", lambda: None)
    monkeypatch.setattr(session_manager, "reattach_streamers", _noop_async)
    monkeypatch.setattr(session_manager, "cleanup_loop", _noop_async)
    monkeypatch.setattr(scheduler, "init_scheduler", lambda loop: None)
    monkeypatch.setattr(scheduler, "stop_scheduler", lambda: None)
    monkeypatch.setattr(telegram, "start", _noop_async)
    monkeypatch.setattr(telegram, "stop", _noop_async)

    from app import mcp_services

    monkeypatch.setattr(mcp_services, "ensure_running", lambda entry: None)

    with TestClient(main_module.app) as c:
        yield c


def _create(client, **kw):
    payload = {"name": "Правило", "agent_id": 1, "events": ["session.completed"], "prompt": "Событие {event}"}
    payload.update(kw)
    r = client.post("/api/automations", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def test_automations_collection_endpoint(client):
    r = client.get("/api/automations")
    assert r.status_code == 200
    assert r.json() == []


def test_automations_crud_and_validation(client):
    assert client.post("/api/automations", json={"agent_id": 1, "prompt": "x"}).status_code == 400
    assert client.post("/api/automations", json={"agent_id": 999999, "prompt": "x"}).status_code == 400
    assert client.post("/api/automations", json={"agent_id": 1, "prompt": ""}).status_code == 400
    assert client.post("/api/automations", json={"agent_id": 1, "events": ["bogus"], "prompt": "x"}).status_code == 400

    a = _create(client)
    assert a["events"] == ["session.completed"]
    assert a["chain"] == 0
    assert a["enabled"] == 1

    r = client.put(f"/api/automations/{a['id']}", json={"enabled": False, "chain": True, "prompt": "новый"})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] == 0 and r.json()["chain"] == 1 and r.json()["prompt"] == "новый"

    # при частичном обновлении events и prompt сохраняются
    r = client.put(f"/api/automations/{a['id']}", json={"name": "Имя"})
    assert r.json()["prompt"] == "новый" and r.json()["events"] == ["session.completed"]

    assert client.delete(f"/api/automations/{a['id']}").json() == {"ok": True}
    assert client.put(f"/api/automations/{a['id']}", json={"name": "x"}).status_code == 404


def test_build_prompt_templating():
    from app.automations import build_prompt

    data = {"title": "Проверка PR", "status": "failed", "prompt": "оригинал", "missing": None}
    out = build_prompt("Событие {event}. {title} {status}. Неизвестно: {unknown}. JSON:\n{json}", "session.completed", data)
    assert out.startswith("Событие session.completed. Проверка PR failed. Неизвестно: . JSON:\n")
    assert '"title": "Проверка PR"' in out
    assert "{" not in build_prompt("без плейсхолдеров", "session.completed", data)


def test_dispatch_creates_session_for_matching_rule(client, monkeypatch):
    from app import db, events, main as main_module

    spawned = []
    monkeypatch.setattr(main_module, "spawn_start", lambda sid, prompt: spawned.append((sid, prompt)))
    a = _create(client, events=["session.completed"], prompt="Разберись: {title} ({status})")

    events.emit("session.completed", {"id": "s1", "title": "Задача", "status": "completed", "source": "manual", "project_id": 1})

    assert len(spawned) == 1
    runs = db.query("SELECT * FROM automation_runs WHERE automation_id=?", (a["id"],))
    assert len(runs) == 1 and runs[0]["status"] == "running"
    s = db.query_one("SELECT * FROM sessions WHERE id=?", (runs[0]["session_id"],))
    assert s["source"] == "automation"
    assert s["prompt"] == "Разберись: Задача (completed)"
    assert "Задача" in s["title"]

    # несовпадающее событие — тишина
    events.emit("session.failed", {"id": "s2", "title": "T", "source": "manual", "project_id": 1})
    assert len(spawned) == 1


def test_dispatch_skips_other_project(client, monkeypatch):
    from app import db, events, main as main_module

    spawned = []
    monkeypatch.setattr(main_module, "spawn_start", lambda sid, prompt: spawned.append(sid))
    pid2 = db.execute("INSERT INTO projects(name, file_token) VALUES('Второй', 'tok2')")
    _create(client, project_id=pid2, prompt="x")

    events.emit("session.completed", {"id": "s1", "title": "T", "source": "manual", "project_id": 1})
    assert spawned == []
    events.emit("session.completed", {"id": "s2", "title": "T", "source": "manual", "project_id": pid2})
    assert len(spawned) == 1


def test_dispatch_chain_flag_prevents_cascades(client, monkeypatch):
    from app import db, events, main as main_module

    spawned = []
    monkeypatch.setattr(main_module, "spawn_start", lambda sid, prompt: spawned.append(sid))
    a = _create(client, prompt="x")
    no_chain = a["id"]

    # сессия автоматизации не запускает правило без chain
    events.emit("session.completed", {"id": "s1", "title": "T", "source": "automation", "project_id": 1})
    assert spawned == []

    # с chain=1 — запускает
    client.put(f"/api/automations/{no_chain}", json={"chain": True})
    events.emit("session.completed", {"id": "s2", "title": "T", "source": "automation", "project_id": 1})
    assert len(spawned) == 1
    assert db.query_one("SELECT COUNT(*) AS n FROM automation_runs WHERE automation_id=?", (no_chain,))["n"] == 1


def test_dispatch_session_created_no_recursion(client, monkeypatch):
    from app import db, events, main as main_module

    spawned = []
    monkeypatch.setattr(main_module, "spawn_start", lambda sid, prompt: spawned.append(sid))
    # правило на session.created + каскады — реентерабельность всё равно защищает
    a = _create(client, events=["session.created"], prompt="x", chain=True)

    events.emit("session.created", {"id": "s1", "title": "T", "source": "manual", "project_id": 1})

    assert len(spawned) == 1
    assert db.query_one("SELECT COUNT(*) AS n FROM automation_runs WHERE automation_id=?", (a["id"],))["n"] == 1


def test_test_endpoint_runs_rule(client, monkeypatch):
    from app import db, main as main_module

    spawned = []
    monkeypatch.setattr(main_module, "spawn_start", lambda sid, prompt: spawned.append(sid))
    a = _create(client, enabled=False)
    assert client.post(f"/api/automations/{a['id']}/test").status_code == 403

    a = _create(client)
    r = client.post(f"/api/automations/{a['id']}/test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["session_id"]
    assert len(spawned) == 1
    s = db.query_one("SELECT * FROM sessions WHERE id=?", (body["session_id"],))
    assert s["source"] == "automation" and "webhook.test" in s["prompt"]

    runs = client.get(f"/api/automations/{a['id']}/runs").json()
    assert len(runs) == 1 and runs[0]["event"] == "webhook.test"
