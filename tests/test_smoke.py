"""Дымовой тест: приложение поднимается на временной базе и отвечает по API.

Docker и ключи провайдеров не нужны — всё, что ходит в докер, на время теста
заменяется заглушками.
"""

import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient

FAKE_TOKEN = "123456:" + "A" * 35


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBEPROD_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    # модули читают VIBEPROD_DATA_DIR на импорте — перечитываем их с новым путём
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


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Vibeprod" in r.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/projects",
        "/api/agents",
        "/api/providers",
        "/api/providers/known",
        "/api/sessions",
        "/api/skills",
        "/api/mcp-catalog",
        "/api/webhooks",
        "/api/schedules",
        "/api/out-webhooks",
        "/api/out-webhooks/events",
    ],
)
def test_collection_endpoints_return_lists(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


def test_providers_available_serves_catalog(client, monkeypatch):
    from app.api import providers as providers_api

    monkeypatch.setattr(
        providers_api,
        "fetch_available_providers",
        lambda force=False: {
            "count": 2,
            "version": "1.0",
            "fetched_at": "now",
            "providers": [
                {"id": "aaa", "name": "AAA", "models": ["m1"], "default_model": "m1"},
                {"id": "bbb", "name": "BBB", "models": [], "default_model": None},
            ],
        },
    )
    r = client.get("/api/providers/available")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    assert body["providers"][0]["id"] == "aaa"


def test_files_list_mocked(client, monkeypatch):
    from app.api import files as files_api

    monkeypatch.setattr(files_api.files_store, "healthy", lambda: True)
    monkeypatch.setattr(
        files_api.files_store,
        "list_objects",
        lambda pid, prefix="": [
            {"name": "a.png", "size": 123, "last_modified": None, "content_type": "image/png",
             "url": "/api/files/content?project_id=1&path=a.png&token=x"}
        ],
    )
    r = client.get("/api/files?project_id=1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["storage_ok"] is True
    assert body["files"][0]["name"] == "a.png"


def test_files_content_requires_valid_token(auth_client, monkeypatch):
    from app.api import files as files_api

    class FakeObj:
        headers = {"Content-Type": "image/png"}

        def stream(self, amt):
            yield b"x"

    calls = []
    monkeypatch.setattr(files_api.files_store, "check_file_token", lambda pid, t: t == "good")
    monkeypatch.setattr(files_api.files_store, "get_object", lambda pid, path: calls.append(path) or FakeObj())
    assert auth_client.get("/api/files/content?project_id=1&path=a.png").status_code == 401
    assert auth_client.get("/api/files/content?project_id=1&path=a.png&token=bad").status_code == 401
    r = auth_client.get("/api/files/content?project_id=1&path=a.png&token=good")
    assert r.status_code == 200, r.text
    assert calls == ["a.png"]


def test_project_has_file_token(client):
    from app import db

    row = db.query_one("SELECT id, file_token FROM projects ORDER BY id LIMIT 1")
    assert row["file_token"], "проект должен получить file_token при инициализации БД"


def test_db_bootstrap_creates_defaults(client):
    projects = client.get("/api/projects").json()
    assert len(projects) == 1, "первый запуск должен создать проект по умолчанию"

    agents = client.get("/api/agents").json()
    names = {a["name"] for a in agents}
    assert "general" in names
    assert "guardian" not in names, "guardian скрыт из списка агентов"
    assert "vibeprod" not in names, "системный агент vibeprod скрыт из списка агентов"

    catalog = client.get("/api/mcp-catalog").json()
    assert any(m["name"] == "playwright" for m in catalog)


def test_guardian_mcp_requires_bearer_secret(client):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert client.post("/guardian/mcp", json=body).status_code == 401
    assert client.post("/guardian/mcp", json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_guardian_file_tools(monkeypatch, tmp_path):
    from app import guardian_mcp

    calls = {}
    ups = []
    monkeypatch.setattr(guardian_mcp.files_store, "list_objects", lambda pid, prefix="": calls.update(ls=(pid, prefix)) or [])
    monkeypatch.setattr(guardian_mcp.files_store, "upload", lambda pid, path, data, ct: ups.append((pid, path, data, ct)))
    monkeypatch.setattr(guardian_mcp.files_store, "stat", lambda pid, path: True)
    monkeypatch.setattr(guardian_mcp.files_store, "delete", lambda pid, path: calls.update(dl=(pid, path)))
    monkeypatch.setattr(guardian_mcp.files_store, "content_url", lambda pid, path: f"/api/files/content?project_id={pid}&path={path}")

    ws = tmp_path / "workspaces" / "sess1"
    ws.mkdir(parents=True)
    (ws / "index.html").write_text("<html>calc</html>", encoding="utf-8")
    from app import session_manager

    monkeypatch.setattr(session_manager, "host_ws_dir", lambda sid: ws)
    ctx = {"session_id": "sess1", "project_id": 1}

    async def run():
        r1 = await guardian_mcp.call_tool("file_put", {"project_id": 1, "path": "reports/отчёт.md", "content": "# Отчёт"})
        r2 = await guardian_mcp.call_tool("file_list", {"project_id": 1, "prefix": "reports"})
        r3 = await guardian_mcp.call_tool("file_delete", {"project_id": 1, "path": "reports/отчёт.md"})
        r4 = await guardian_mcp.call_tool("file_put", {"project_id": 1, "path": "x.md"})
        r5 = await guardian_mcp.call_tool("file_put", {"path": "calc.html", "workspace_path": "index.html"}, ctx)
        r6 = await guardian_mcp.call_tool("file_put", {"path": "calc.html", "workspace_path": "../outside.html"}, ctx)
        r7 = await guardian_mcp.call_tool("file_list", {"prefix": "reports"}, ctx)
        return r1, r2, r3, r4, r5, r6, r7

    r1, r2, r3, r4, r5, r6, r7 = asyncio.run(run())
    assert not r1["isError"] and "url" in r1["content"][0]["text"]
    assert not r2["isError"] and calls["ls"] == (1, "reports")
    assert not r3["isError"] and calls["dl"] == (1, "reports/отчёт.md")
    assert r4["isError"], "content обязателен"
    assert ups[0][:2] == (1, "reports/отчёт.md")
    assert ups[0][2].startswith(b"# \xd0\x9e\xd1\x82\xd1\x87\xd1\x91\xd1\x82")
    assert not r5["isError"], r5["content"][0]["text"]
    assert ups[1][0:2] == (1, "calc.html") and ups[1][2] == b"<html>calc</html>"
    assert r6["isError"], "выход за пределы workspace запрещён"
    assert not r7["isError"] and calls["ls"] == (1, "reports"), "project_id из контекста сессии"


def test_guardian_file_tools_project_missing(monkeypatch):
    from app import guardian_mcp

    async def run():
        return await guardian_mcp.call_tool("file_list", {"project_id": 999999})

    r = asyncio.run(run())
    assert r["isError"] and "не найден" in r["content"][0]["text"]


def test_unknown_session_is_404(client):
    assert client.get("/api/sessions/does-not-exist").status_code == 404


@pytest.fixture()
def auth_client(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBEPROD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VIBEPROD_LOGIN", "admin")
    monkeypatch.setenv("VIBEPROD_PASSWORD", "secret")
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


def test_auth_redirects_until_login(auth_client):
    r = auth_client.get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/login"
    assert auth_client.get("/api/projects").status_code == 401


def test_auth_login_flow(auth_client):
    assert auth_client.post("/api/login", json={"login": "admin", "password": "wrong"}).status_code == 401
    r = auth_client.post("/api/login", json={"login": "admin", "password": "secret"})
    assert r.status_code == 200
    assert r.cookies.get("vibeprod_auth")

    assert auth_client.get("/").status_code == 200
    assert auth_client.get("/api/projects").status_code == 200

    auth_client.post("/api/logout")
    assert auth_client.get("/api/projects").status_code == 401


def test_auth_keeps_machine_endpoints_public(auth_client):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert auth_client.post("/guardian/mcp", json=body).status_code == 401  # проверка bearer, не cookie
    assert auth_client.post("/api/webhooks/nope/run", json={}).status_code == 404  # не 401 от middleware


# ---------- уведомления в каналы ----------


def _noop_apply():
    async def _f():
        return None

    return _f


def _seed_config(notify_chat_id="111", notify_mode="all"):
    from app import db

    pid = db.query_one("SELECT id FROM projects ORDER BY id LIMIT 1")["id"]
    db.execute(
        "INSERT INTO telegram_config(project_id, token, notify_chat_id, notify_mode, enabled) VALUES(?,?,?,?,1)",
        (pid, FAKE_TOKEN, notify_chat_id, notify_mode),
    )
    return pid


def _seed_session(source="schedule", title="Задача"):
    from app import db

    pid = db.query_one("SELECT id FROM projects ORDER BY id LIMIT 1")["id"]
    aid = db.query_one("SELECT id FROM agents WHERE is_guardian=0 ORDER BY id LIMIT 1")["id"]
    db.execute(
        "INSERT INTO sessions(id, agent_id, agent_name, project_id, title, source, status) "
        "VALUES('test-sess', ?, 'general', ?, ?, ?, 'completed')",
        (aid, pid, title, source),
    )


def _run_notify(monkeypatch, source="schedule", status="completed", error=None, result=None, mode="all"):
    from app import notify

    sent = {}

    async def fake_send(project_id, chat_id, text, reply_to=None):
        sent["project_id"] = project_id
        sent["chat_id"] = chat_id
        sent["text"] = text

    monkeypatch.setattr(notify.channel, "send", fake_send)
    _seed_config(notify_mode=mode)
    _seed_session(source=source, title="Проверка PR")
    asyncio.run(notify.on_session_done("test-sess", status, error, result))
    return sent


def test_telegram_config_roundtrip(client, monkeypatch):
    from app import telegram as tg

    monkeypatch.setattr(tg, "apply_config", _noop_apply())
    r = client.put("/api/telegram", json={"token": FAKE_TOKEN, "notify_chat_id": "42", "notify_mode": "errors"})
    assert r.status_code == 200, r.text
    assert r.json()["notify_chat_id"] == "42"
    assert r.json()["notify_mode"] == "errors"
    cfg = client.get("/api/telegram").json()
    assert cfg["notify_chat_id"] == "42"
    assert cfg["notify_mode"] == "errors"
    assert cfg["has_token"] is True


def test_telegram_config_validates_notify_fields(client, monkeypatch):
    from app import telegram as tg

    monkeypatch.setattr(tg, "apply_config", _noop_apply())
    assert client.put("/api/telegram", json={"token": FAKE_TOKEN, "notify_chat_id": "abc"}).status_code == 400
    assert client.put("/api/telegram", json={"token": FAKE_TOKEN, "notify_mode": "nope"}).status_code == 400
    assert client.put("/api/telegram", json={"token": FAKE_TOKEN, "notify_chat_id": "-100123456789"}).status_code == 200


def test_notify_sends_summary_for_schedule(client, monkeypatch):
    sent = _run_notify(
        monkeypatch,
        result=[{"info": {"role": "assistant", "parts": [{"type": "text", "text": "Всё готово."}]}}],
    )
    assert sent["chat_id"] == "111"
    assert "Расписание: Проверка PR" in sent["text"]
    assert "Статус: готово" in sent["text"]
    assert "Всё готово." in sent["text"]


def test_notify_failed_includes_error(client, monkeypatch):
    sent = _run_notify(monkeypatch, status="failed", error="модель не найдена")
    assert "Статус: ошибка" in sent["text"]
    assert "модель не найдена" in sent["text"]


def test_notify_skips_manual_source(client, monkeypatch):
    sent = _run_notify(monkeypatch, source="manual")
    assert not sent


def test_notify_errors_mode_skips_completed(client, monkeypatch):
    sent = _run_notify(monkeypatch, mode="errors")
    assert not sent


def test_notify_errors_mode_sends_failed(client, monkeypatch):
    sent = _run_notify(monkeypatch, status="failed", error="boom", mode="errors")
    assert "Статус: ошибка" in sent["text"]


def test_notify_skips_without_config(client, monkeypatch):
    from app import notify

    monkeypatch.setattr(
        notify.channel,
        "send",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("не должно зваться")),
    )
    _seed_session(source="schedule")
    asyncio.run(notify.on_session_done("test-sess", "completed", None, []))


def test_channel_split_text():
    from app.channel import split_text

    assert split_text("короткий текст") == ["короткий текст"]
    assert split_text("") == []
    long = ("x" * 100 + "\n") * 60
    chunks = split_text(long)
    assert all(len(c) <= 4000 for c in chunks)
    assert "".join(chunks).count("x") == long.count("x")


def test_channel_send_without_config_returns_none(client):
    from app.channel import send

    assert asyncio.run(send(999, 1, "x")) is None


# ---------- исходящие вебхуки ----------


def _fake_http(monkeypatch, outwebhooks, codes):
    """Заглушка httpx.AsyncClient: каждый POST отдаёт очередной код из codes."""
    from app import outwebhooks as ow

    sent = {}

    class FakeResp:
        def __init__(self, code):
            self.status_code = code

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, content=None, headers=None):
            sent["url"] = url
            sent["body"] = content
            sent["headers"] = headers
            code = codes[min(len(sent.setdefault("calls", [])), len(codes) - 1)]
            sent["calls"].append(code)
            return FakeResp(code)

    monkeypatch.setattr(ow.httpx, "AsyncClient", FakeClient)

    async def _noop_sleep(*a, **kw):
        return None

    monkeypatch.setattr(ow.asyncio, "sleep", _noop_sleep)
    return sent


def test_out_webhook_crud_and_secret_masked(client):
    assert client.post("/api/out-webhooks", json={"url": "ftp://x"}).status_code == 400
    assert client.post("/api/out-webhooks", json={"url": "https://x/h", "events": ["bogus"]}).status_code == 400
    r = client.post(
        "/api/out-webhooks",
        json={"url": "https://example.com/hook", "name": "Тест", "secret": "s3cret", "events": ["session.completed"]},
    )
    assert r.status_code == 200, r.text
    w = r.json()
    assert w["events"] == ["session.completed"]
    assert w["has_secret"] is True
    assert "secret" not in w
    assert "s3cret" not in r.text

    r = client.put(f"/api/out-webhooks/{w['id']}", json={"enabled": False, "url": "https://example.com/hook2"})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] == 0
    assert r.json()["url"] == "https://example.com/hook2"
    assert r.json()["has_secret"] is True, "секрет сохраняется, если не передан"

    assert client.delete(f"/api/out-webhooks/{w['id']}").json() == {"ok": True}
    assert client.put(f"/api/out-webhooks/{w['id']}", json={}).status_code == 404


def test_dispatch_creates_deliveries_only_for_subscribed(client, monkeypatch):
    import json

    from app import db, outwebhooks

    recorded = []
    monkeypatch.setattr(outwebhooks, "_schedule", lambda did, loop: recorded.append(did))
    r = client.post(
        "/api/out-webhooks", json={"url": "https://example.com/hook", "events": ["session.completed"]}
    )
    wid = r.json()["id"]

    outwebhooks.dispatch("session.failed", {"id": "x"})
    assert recorded == []

    outwebhooks.dispatch("session.completed", {"id": "x"})
    assert len(recorded) == 1
    rows = db.query("SELECT * FROM out_webhook_deliveries WHERE webhook_id=?", (wid,))
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["event"] == "session.completed"
    assert payload["data"] == {"id": "x"}
    assert "timestamp" in payload


def test_emit_routes_to_outwebhooks(client, monkeypatch):
    from app import events, outwebhooks

    recorded = []
    monkeypatch.setattr(outwebhooks, "_schedule", lambda did, loop: recorded.append(did))
    client.post("/api/out-webhooks", json={"url": "https://example.com/hook", "events": ["session.failed"]})
    events.emit("session.failed", {"id": "x"})
    assert len(recorded) == 1
    events.emit("no.such.event")
    assert len(recorded) == 1


def test_session_event_data(client):
    from app import events

    _seed_session(source="manual", title="Проверка PR")
    data = events.session_event_data("test-sess")
    assert data["title"] == "Проверка PR"
    assert data["source"] == "manual"
    assert data["status"] == "completed"
    assert data["agent_name"] == "general"


def test_out_webhook_delivery_success_and_signature(client, monkeypatch):
    import asyncio
    import hashlib
    import hmac

    from app import db, outwebhooks

    r = client.post(
        "/api/out-webhooks",
        json={"url": "https://example.com/hook", "secret": "s3cret", "events": ["session.completed"]},
    )
    wid = r.json()["id"]
    row = db.query_one("SELECT * FROM out_webhooks WHERE id=?", (wid,))
    did = outwebhooks.enqueue(row, "session.completed", {"title": "T"})
    sent = _fake_http(monkeypatch, outwebhooks, [204])

    asyncio.run(outwebhooks.deliver(did, attempts_limit=1))

    d = db.query_one("SELECT * FROM out_webhook_deliveries WHERE id=?", (did,))
    assert d["status"] == "success"
    assert d["http_status"] == 204
    assert d["attempts"] == 1
    expected = "sha256=" + hmac.new(b"s3cret", sent["body"], hashlib.sha256).hexdigest()
    assert sent["headers"]["X-Vibeprod-Signature"] == expected
    assert sent["headers"]["X-Vibeprod-Event"] == "session.completed"
    assert sent["headers"]["X-Vibeprod-Delivery"] == str(did)
    assert sent["headers"]["Content-Type"] == "application/json"


def test_out_webhook_delivery_retries_then_succeeds(client, monkeypatch):
    import asyncio

    from app import db, outwebhooks

    r = client.post("/api/out-webhooks", json={"url": "https://example.com/hook"})
    wid = r.json()["id"]
    row = db.query_one("SELECT * FROM out_webhooks WHERE id=?", (wid,))
    did = outwebhooks.enqueue(row, "session.failed", {})
    sent = _fake_http(monkeypatch, outwebhooks, [500, 200])

    asyncio.run(outwebhooks.deliver(did, attempts_limit=2))

    d = db.query_one("SELECT * FROM out_webhook_deliveries WHERE id=?", (did,))
    assert d["status"] == "success"
    assert d["attempts"] == 2
    assert len(sent["calls"]) == 2


def test_out_webhook_delivery_4xx_fails_without_retry(client, monkeypatch):
    import asyncio

    from app import db, outwebhooks

    r = client.post("/api/out-webhooks", json={"url": "https://example.com/hook"})
    wid = r.json()["id"]
    row = db.query_one("SELECT * FROM out_webhooks WHERE id=?", (wid,))
    did = outwebhooks.enqueue(row, "session.failed", {})
    sent = _fake_http(monkeypatch, outwebhooks, [404])

    asyncio.run(outwebhooks.deliver(did, attempts_limit=3))

    d = db.query_one("SELECT * FROM out_webhook_deliveries WHERE id=?", (did,))
    assert d["status"] == "failed"
    assert d["attempts"] == 1
    assert "HTTP 404" in d["error"]
    assert len(sent["calls"]) == 1


def test_out_webhook_test_endpoint(client, monkeypatch):
    from app import outwebhooks

    r = client.post("/api/out-webhooks", json={"url": "https://example.com/hook"})
    wid = r.json()["id"]
    _fake_http(monkeypatch, outwebhooks, [200])

    tr = client.post(f"/api/out-webhooks/{wid}/test")
    assert tr.status_code == 200, tr.text
    body = tr.json()
    assert body["ok"] is True
    assert body["delivery"]["status"] == "success"
    assert body["delivery"]["event"] == "webhook.test"

    deliveries = client.get(f"/api/out-webhooks/{wid}/deliveries").json()
    assert len(deliveries) == 1
    assert deliveries[0]["event"] == "webhook.test"
