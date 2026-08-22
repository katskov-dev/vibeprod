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


def test_custom_provider_crud_and_render(client, monkeypatch, tmp_path):
    import json as jsonlib

    from app import db, render

    models = {
        "qwen3.8-27b": {"name": "Qwen3.8-27B", "limit": {"context": 262144, "output": 8192}}
    }

    # создать кастомного провайдера
    r = client.post("/api/providers", json={
        "id": "local-qwen", "label": "Qwen (local)", "kind": "openai_compatible",
        "base_url": "https://host.runpod.net/v1", "api_key": "sk-test",
        "custom_models": models, "project_id": 1,
    })
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["kind"] == "openai_compatible" and p["base_url"] == "https://host.runpod.net/v1"
    assert p["custom_models"] == models and p["env_var"] == "LOCAL_QWEN_API_KEY"

    # валидация
    assert client.post("/api/providers", json={"id": "x1", "kind": "openai_compatible", "base_url": "nope"}).status_code == 400
    assert client.post("/api/providers", json={"id": "x2", "kind": "openai_compatible", "base_url": "https://h/v1", "custom_models": "не-json"}).status_code == 400
    assert client.post("/api/providers", json={"id": "openai", "kind": "openai_compatible", "base_url": "https://h/v1"}).status_code == 400

    # обновление
    r = client.put("/api/providers/local-qwen", json={"base_url": "https://h2/v1"})
    assert r.json()["base_url"] == "https://h2/v1"

    # рендер workspace: custom попадает в opencode.json, builtin — нет
    db.execute(
        "INSERT INTO providers(id, label, env_var, api_key, enabled, kind) VALUES('deepseek','d','DEEPSEEK_API_KEY','k',1,'builtin')"
    )
    rows = db.query("SELECT * FROM providers")
    wdir = tmp_path / "ws"
    render.render_workspace(
        wdir, db.query("SELECT * FROM agents WHERE is_guardian=0"), [], [], provider_rows=rows
    )
    cfg = jsonlib.loads((wdir / "opencode.json").read_text(encoding="utf-8"))
    assert "local-qwen" in cfg["provider"] and "deepseek" not in cfg["provider"]
    block = cfg["provider"]["local-qwen"]
    assert block["npm"] == "@ai-sdk/openai-compatible"
    assert block["options"]["baseURL"] == "https://h2/v1"
    assert block["options"]["apiKey"] == "{env:LOCAL_QWEN_API_KEY}"
    assert block["models"] == models

    # выключенный или без ключа — не рендерится
    db.execute("UPDATE providers SET enabled=0 WHERE id='local-qwen'")
    rows = db.query("SELECT * FROM providers")
    render.render_workspace(
        wdir, db.query("SELECT * FROM agents WHERE is_guardian=0"), [], [], provider_rows=rows
    )
    cfg = jsonlib.loads((wdir / "opencode.json").read_text(encoding="utf-8"))
    assert "provider" not in cfg


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


def test_files_update(client, monkeypatch):
    from app.api import files as files_api

    ups = {}
    monkeypatch.setattr(files_api.files_store, "upload", lambda pid, path, data, ct, size=None: ups.update(pid=pid, path=path, data=data, ct=ct))
    r = client.put("/api/files?project_id=1", json={"path": "docs/readme.md", "content": "# Привет\nобновлено"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "docs/readme.md" and r.json()["size"] == len("# Привет\nобновлено".encode("utf-8"))
    assert ups["data"] == "# Привет\nобновлено".encode("utf-8") and ups["ct"] == "text/markdown"

    assert client.put("/api/files?project_id=1", json={"path": "x.md"}).status_code == 400
    assert client.put("/api/files?project_id=1", json={"path": "../x.md", "content": "x"}).status_code == 400
    assert client.put("/api/files?project_id=999999", json={"path": "x.md", "content": "x"}).status_code == 404


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

    monkeypatch.setattr(session_manager, "ws_dir", lambda sid: ws)
    ctx = {"session_id": "sess1", "project_id": 1}

    async def run():
        r1 = await guardian_mcp.call_tool("file_put", {"project_id": 1, "path": "reports/отчёт.md", "content": "# Отчёт"}, ctx)
        r2 = await guardian_mcp.call_tool("file_list", {"project_id": 1, "prefix": "reports"}, ctx)
        r3 = await guardian_mcp.call_tool("file_delete", {"project_id": 1, "path": "reports/отчёт.md"}, ctx)
        r4 = await guardian_mcp.call_tool("file_put", {"project_id": 1, "path": "x.md"}, ctx)
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
        return await guardian_mcp.call_tool("file_list", {"project_id": 999999}, {"session_id": "s", "project_id": 1})

    r = asyncio.run(run())
    assert r["isError"] and "другого проекта" in r["content"][0]["text"]


def test_guardian_scoped_to_own_project(client):
    import asyncio
    import json

    from app import db, guardian_mcp

    pid2 = db.execute("INSERT INTO projects(name, file_token) VALUES('Второй', 'tok2')")
    aid2 = db.execute(
        "INSERT INTO agents(name, mode, model, project_id) VALUES('other-agent', 'primary', 'm/m', ?)", (pid2,)
    )
    db.execute("INSERT INTO providers(id, env_var, project_id) VALUES('other-prov', 'X_API_KEY', ?)", (pid2,))
    db.execute(
        "INSERT INTO sessions(id, agent_id, agent_name, project_id, title, source, status) "
        "VALUES('ses-other', ?, 'other-agent', ?, 'x', 'manual', 'running')",
        (aid2, pid2),
    )
    db.execute(
        "INSERT INTO webhooks(slug, agent_id, project_id, title) VALUES('other-hook', ?, ?, 'x')",
        (aid2, pid2),
    )
    ctx = {"session_id": "sess1", "project_id": 1}

    def call(name, args):
        return asyncio.run(guardian_mcp.call_tool(name, args, ctx))

    def txt(r):
        return r["content"][0]["text"]

    def deny(name, args):
        r = call(name, args)
        assert r["isError"] and "друг" in txt(r), (name, txt(r))

    # project_list — только свой проект
    assert [p["id"] for p in json.loads(txt(call("project_list", {})))] == [1]
    # project_create запрещён
    assert call("project_create", {"name": "x"})["isError"]
    deny("project_update", {"id": pid2, "name": "x"})
    deny("project_delete", {"id": pid2})

    # агенты
    names = [a["name"] for a in json.loads(txt(call("agent_list", {})))]
    assert "other-agent" not in names
    deny("agent_list", {"project_id": pid2})
    deny("agent_get", {"id": aid2})
    deny("agent_update", {"id": aid2, "description": "x"})
    deny("agent_delete", {"id": aid2})
    deny("agent_create", {"name": "scoped-test", "project_id": pid2})
    # создание в своём проекте работает
    r = call("agent_create", {"name": "scoped-test"})
    assert not r["isError"], txt(r)
    db.execute("DELETE FROM agents WHERE name='scoped-test'")

    # провайдеры
    provs = [p["id"] for p in json.loads(txt(call("provider_list", {})))]
    assert "other-prov" not in provs
    deny("provider_add", {"id": "x1", "project_id": pid2})
    deny("provider_update", {"id": "other-prov", "label": "x"})
    deny("provider_delete", {"id": "other-prov"})
    deny("provider_check", {"id": "other-prov", "deep": False})

    # вебхуки
    hooks = [w["slug"] for w in json.loads(txt(call("webhook_list", {})))]
    assert "other-hook" not in hooks
    deny("webhook_delete", {"id": db.query_one("SELECT id FROM webhooks WHERE slug='other-hook'")["id"]})

    # расписания
    db.execute(
        "INSERT INTO schedules(agent_id, project_id, title, prompt, cron) VALUES(?, ?, 's', 'p', '0 9 * * *')",
        (aid2, pid2),
    )
    sched_other = db.query_one("SELECT id FROM schedules WHERE project_id=?", (pid2,))["id"]
    assert all(s["id"] != sched_other for s in json.loads(txt(call("schedule_list", {}))))
    deny("schedule_update", {"id": sched_other, "title": "x"})
    deny("schedule_delete", {"id": sched_other})
    deny("schedule_run_now", {"id": sched_other})
    deny("schedule_create", {"agent_id": aid2, "prompt": "p", "cron": "0 9 * * *"})

    # сессии
    assert "ses-other" not in txt(call("session_list", {}))
    deny("session_abort", {"session_id": "ses-other"})
    deny("session_delete", {"session_id": "ses-other"})
    deny("session_run", {"agent_id": aid2, "prompt": "x"})

    # файлы
    deny("file_list", {"project_id": pid2})
    deny("file_put", {"project_id": pid2, "path": "x.txt", "content": "x"})
    deny("file_delete", {"project_id": pid2, "path": "x.txt"})


def test_agent_memory_crud_and_default(client):
    r = client.post("/api/agents", json={"name": "memory-agent", "description": "d", "project_id": 1})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["memory_enabled"] == 1, "память включена по умолчанию"
    assert a["memory"] == ""

    r = client.put(f"/api/agents/{a['id']}", json={"memory": "важно: любим nginx", "memory_enabled": False})
    assert r.status_code == 200, r.text
    a2 = r.json()
    assert a2["memory"] == "важно: любим nginx"
    assert a2["memory_enabled"] == 0

    # обновление без полей памяти — значения сохраняются
    r = client.put(f"/api/agents/{a['id']}", json={"description": "x"})
    assert r.json()["memory_enabled"] == 0
    assert r.json()["memory"] == "важно: любим nginx"


def test_broker_memory_tools(client):
    import asyncio

    from app import broker_mcp, db

    aid = db.execute(
        "INSERT INTO agents(name, mode, model, memory, memory_enabled) VALUES('mem-a', 'primary', 'm/m', 'старая память', 1)"
    )
    sid = "mem-sess"
    db.execute(
        "INSERT INTO sessions(id, agent_id, agent_name, project_id, title, source, status) "
        "VALUES(?, ?, 'mem-a', 1, 't', 'manual', 'running')",
        (sid, aid),
    )
    ctx = {"session_id": sid, "project_id": 1}

    r = asyncio.run(broker_mcp.call_tool("memory_get", {}, ctx))
    assert not r["isError"] and "старая память" in r["content"][0]["text"]

    r = asyncio.run(broker_mcp.call_tool("memory_set", {"memory": "новая память"}, ctx))
    assert not r["isError"], r["content"][0]["text"]
    assert db.query_one("SELECT memory FROM agents WHERE id=?", (aid,))["memory"] == "новая память"

    r = asyncio.run(broker_mcp.call_tool("memory_set", {}, ctx))
    assert r["isError"] and "memory обязателен" in r["content"][0]["text"]

    # tools_for: выключили — инструментов нет, вызовы падают с понятной ошибкой
    assert {"memory_get", "memory_set"} <= {t["name"] for t in broker_mcp.tools_for(ctx)}
    db.execute("UPDATE agents SET memory_enabled=0 WHERE id=?", (aid,))
    assert not ({"memory_get", "memory_set"} & {t["name"] for t in broker_mcp.tools_for(ctx)})
    r = asyncio.run(broker_mcp.call_tool("memory_get", {}, ctx))
    assert r["isError"] and "выключена" in r["content"][0]["text"]

    # без сессии воркера память недоступна
    r = asyncio.run(broker_mcp.call_tool("memory_get", {}, {"project_id": 1}))
    assert r["isError"]


def test_render_injects_memory(tmp_path):
    from app.render import render_workspace

    wdir = tmp_path / "ws"
    render_workspace(
        wdir,
        [
            {"name": "mem-agent", "mode": "primary", "model": "m/m", "is_default": 1,
             "memory": "важное: деплой на 200.165.236.68", "memory_enabled": 1},
            {"name": "no-mem", "mode": "primary", "model": "m/m",
             "memory": "важное", "memory_enabled": 0},
        ],
        [],
        [],
    )
    text_on = (wdir / ".opencode" / "agent" / "mem-agent.md").read_text(encoding="utf-8")
    assert "## Память агента" in text_on and "деплой на 200.165.236.68" in text_on
    text_off = (wdir / ".opencode" / "agent" / "no-mem.md").read_text(encoding="utf-8")
    assert "Память агента" not in text_off and "важное" not in text_off


def test_guardian_agent_memory(client):
    import asyncio
    import json

    from app import guardian_mcp

    ctx = {"session_id": "sess1", "project_id": 1}
    r = asyncio.run(guardian_mcp.call_tool(
        "agent_create", {"name": "guardian-mem", "memory": "память g", "memory_enabled": False}, ctx
    ))
    assert not r["isError"], r["content"][0]["text"]
    a = json.loads(r["content"][0]["text"])
    assert a["memory"] == "память g" and a["memory_enabled"] == 0

    r = asyncio.run(guardian_mcp.call_tool("agent_update", {"id": a["id"], "memory_enabled": True}, ctx))
    assert not r["isError"], r["content"][0]["text"]
    a2 = json.loads(r["content"][0]["text"])
    assert a2["memory_enabled"] == 1 and a2["memory"] == "память g"

    # guardian не может трогать чужой проект и системного агента
    r = asyncio.run(guardian_mcp.call_tool("agent_update", {"id": a["id"], "memory": "x"}, {"session_id": "s", "project_id": 999}))
    assert r["isError"]


def test_unknown_session_is_404(client):
    assert client.get("/api/sessions/does-not-exist").status_code == 404


def test_expire_session_does_not_duplicate_error(client, monkeypatch):
    import asyncio

    from app import db, session_manager

    _seed_finished_session(sid="ttl-1", status="failed")
    db.execute("UPDATE sessions SET error='критическая ошибка' WHERE id='ttl-1'")
    monkeypatch.setattr(session_manager, "kill_worker", lambda cid: None)
    monkeypatch.setattr(session_manager.events, "emit", lambda *a, **kw: None)
    asyncio.run(session_manager.expire_session("ttl-1"))
    asyncio.run(session_manager.expire_session("ttl-1"))
    row = db.query_one("SELECT error FROM sessions WHERE id='ttl-1'")
    assert row["error"] == "критическая ошибка | воркер удалён по TTL"


def _seed_finished_session(sid="cont-1", status="completed"):
    from app import db

    pid = db.query_one("SELECT id FROM projects ORDER BY id LIMIT 1")["id"]
    aid = db.query_one("SELECT id FROM agents WHERE is_guardian=0 ORDER BY id LIMIT 1")["id"]
    db.execute(
        "INSERT INTO sessions(id, agent_id, agent_name, project_id, title, source, status, "
        "opencode_session_id, container_id, last_activity) "
        "VALUES(?, ?, 'general', ?, 'Продолжаемая', 'manual', ?, 'ocs-1', 'fake-container', datetime('now'))",
        (sid, aid, pid, status),
    )
    return sid


def test_continue_session_live_worker_sends_prompt(client, monkeypatch):
    from app import session_manager

    _seed_finished_session()
    sent = {}
    monkeypatch.setattr(session_manager, "container_exists", lambda cid: True)

    async def fake_send(sid, text):
        sent.update(sid=sid, text=text)

    monkeypatch.setattr(session_manager, "send_prompt", fake_send)
    r = client.post("/api/sessions/cont-1/continue", json={"text": "продолжим?"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "restarted": False}
    assert sent == {"sid": "cont-1", "text": "продолжим?"}


def test_continue_session_dead_worker_spawns_restart(client, monkeypatch):
    from app import main as main_module
    from app import session_manager

    _seed_finished_session(status="expired")
    monkeypatch.setattr(session_manager, "container_exists", lambda cid: False)
    spawned = {}
    monkeypatch.setattr(main_module, "spawn_start", lambda sid, prompt, **kw: spawned.update(sid=sid, prompt=prompt, kw=kw))
    r = client.post("/api/sessions/cont-1/continue", json={"text": "продолжим после TTL"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "restarted": True}
    assert spawned == {"sid": "cont-1", "prompt": "продолжим после TTL", "kw": {"continue_": True}}


def test_continue_session_validation(client):
    _seed_finished_session(status="queued")
    assert client.post("/api/sessions/nope/continue", json={"text": "x"}).status_code == 404
    assert client.post("/api/sessions/cont-1/continue", json={"text": ""}).status_code == 400
    assert client.post("/api/sessions/cont-1/continue", json={"text": "x"}).status_code == 409


def test_session_needs_restart_logic(client, monkeypatch):
    from app import session_manager

    _seed_finished_session(status="completed")
    monkeypatch.setattr(session_manager, "container_exists", lambda cid: True)
    assert session_manager.session_needs_restart("cont-1") is False

    monkeypatch.setattr(session_manager, "container_exists", lambda cid: False)
    assert session_manager.session_needs_restart("cont-1") is True

    _seed_finished_session(sid="cont-2", status="expired")
    monkeypatch.setattr(session_manager, "container_exists", lambda cid: True)
    assert session_manager.session_needs_restart("cont-2") is True

    _seed_finished_session(sid="cont-3", status="starting")
    assert session_manager.session_needs_restart("cont-3") is False


# ---------- квота параллельных сессий и idle-тиры ----------


def _seed_running_session(sid):
    from app import db

    pid = db.query_one("SELECT id FROM projects ORDER BY id LIMIT 1")["id"]
    aid = db.query_one("SELECT id FROM agents WHERE is_guardian=0 ORDER BY id LIMIT 1")["id"]
    db.execute(
        "INSERT INTO sessions(id, agent_id, agent_name, project_id, title, source, prompt, status, model, "
        "opencode_session_id, container_id, last_activity) "
        "VALUES(?, ?, 'general', ?, 'Активная', 'manual', 'задача', 'running', 'm/m', 'ocs', 'c', datetime('now'))",
        (sid, aid, pid),
    )
    return sid


def test_reserve_start_slot_respects_quota(client, monkeypatch):
    from app import db
    from app import session_manager

    monkeypatch.setattr(session_manager, "MAX_CONCURRENT", 2)
    _seed_running_session("q-act-1")
    _seed_running_session("q-act-2")
    sid = session_manager.create_session(
        db.query_one("SELECT id FROM agents WHERE is_guardian=0 ORDER BY id LIMIT 1")["id"],
        "В очереди",
        "промпт",
    )
    assert db.query_one("SELECT status FROM sessions WHERE id=?", (sid,))["status"] == "queued"

    assert asyncio.run(session_manager._reserve_start_slot(sid)) is False
    assert db.query_one("SELECT status FROM sessions WHERE id=?", (sid,))["status"] == "queued"

    db.execute("UPDATE sessions SET status='failed' WHERE id='q-act-1'")
    assert asyncio.run(session_manager._reserve_start_slot(sid)) is True
    row = db.query_one("SELECT status, pending_prompt FROM sessions WHERE id=?", (sid,))
    assert row["status"] == "starting" and row["pending_prompt"] == 0


def test_dispatch_queued_starts_waiting_sessions(client, monkeypatch):
    from app import db
    from app import session_manager

    monkeypatch.setattr(session_manager, "MAX_CONCURRENT", 2)
    _seed_running_session("q-act-1")
    sid = session_manager.create_session(
        db.query_one("SELECT id FROM agents WHERE is_guardian=0 ORDER BY id LIMIT 1")["id"],
        "В очереди",
        "промпт",
    )
    started = []

    async def fake_start(s):
        started.append(s)

    monkeypatch.setattr(session_manager, "start_session", fake_start)

    async def run():
        await session_manager._dispatch_queued()
        await asyncio.sleep(0)

    asyncio.run(run())
    assert started == [sid]


def test_continue_session_at_cap_goes_queued(client, monkeypatch):
    from app import db
    from app import session_manager

    monkeypatch.setattr(session_manager, "MAX_CONCURRENT", 1)
    _seed_running_session("q-act-1")
    _seed_finished_session(sid="q-cont", status="expired")
    monkeypatch.setattr(session_manager, "container_exists", lambda cid: False)

    async def run():
        await session_manager.continue_session("q-cont", "продолжим")

    asyncio.run(run())
    row = db.query_one("SELECT status, prompt, pending_prompt FROM sessions WHERE id='q-cont'")
    assert row["status"] == "queued"
    assert row["prompt"] == "продолжим"
    assert row["pending_prompt"] == 1


def test_restart_session_clears_pending_prompt(client, monkeypatch):
    from app import db
    from app import session_manager

    started = {}

    async def fake_start(sid, initial_prompt=None):
        started.update(sid=sid, prompt=initial_prompt)

    monkeypatch.setattr(session_manager, "start_session", fake_start)
    monkeypatch.setattr(session_manager, "container_exists", lambda cid: False)
    monkeypatch.setattr(session_manager, "kill_worker", lambda *a, **k: None)
    _seed_finished_session(sid="q-rest", status="completed")
    db.execute("UPDATE sessions SET pending_prompt=1, prompt='старый' WHERE id='q-rest'")

    asyncio.run(session_manager.restart_session("q-rest"))
    assert started == {"sid": "q-rest", "prompt": None}
    assert db.query_one("SELECT pending_prompt FROM sessions WHERE id='q-rest'")["pending_prompt"] == 0


def test_suspend_worker_pauses_idle_container(client, monkeypatch):
    from app import db
    from app import session_manager

    paused = []

    monkeypatch.setattr(session_manager, "container_exists", lambda cid: True)
    monkeypatch.setattr(session_manager, "container_status", lambda cid: "running")
    monkeypatch.setattr(session_manager, "pause_worker", lambda cid: paused.append(cid))
    _seed_running_session("q-susp")
    row = db.query_one("SELECT * FROM sessions WHERE id='q-susp'")

    asyncio.run(session_manager._suspend_worker(row))
    assert paused == ["c"]

    # уже замороженный контейнер повторно не паузится
    monkeypatch.setattr(session_manager, "container_status", lambda cid: "paused")
    asyncio.run(session_manager._suspend_worker(row))
    assert paused == ["c"]


def test_touch_session_throttles_last_activity(client, monkeypatch):
    from app import db
    from app import session_manager

    _seed_running_session("q-touch")
    session_manager._touch_at.pop("q-touch", None)
    monkeypatch.setattr(session_manager.time, "monotonic", lambda: 100.0)
    db.execute("UPDATE sessions SET last_activity='2000-01-01 00:00:00' WHERE id='q-touch'")
    session_manager.touch_session("q-touch")
    assert db.query_one("SELECT last_activity FROM sessions WHERE id='q-touch'")["last_activity"] != "2000-01-01 00:00:00"

    db.execute("UPDATE sessions SET last_activity='2000-01-01 00:00:00' WHERE id='q-touch'")
    session_manager.touch_session("q-touch")
    assert db.query_one("SELECT last_activity FROM sessions WHERE id='q-touch'")["last_activity"] == "2000-01-01 00:00:00"


def test_guardian_ssh_tools(client):
    import asyncio
    import json

    import asyncssh

    from app import db, guardian_mcp

    pid2 = db.execute("INSERT INTO projects(name, file_token) VALUES('Второй', 'tok2')")
    db.execute(
        "INSERT INTO ssh_servers(project_id, name, host, port, username, auth_type, password) "
        "VALUES(?, 'other-srv', 'other.example.com', 22, 'root', 'password', 'hunter2')",
        (pid2,),
    )
    other_srv = db.query_one("SELECT id FROM ssh_servers WHERE project_id=?", (pid2,))["id"]
    db.execute(
        "INSERT INTO ssh_commands(server_id, name, command) VALUES(?, 'other-cmd', 'echo {x}')", (other_srv,)
    )
    other_cmd = db.query_one("SELECT id FROM ssh_commands WHERE server_id=?", (other_srv,))["id"]
    db.execute(
        "INSERT INTO ssh_runs(project_id, server_id, command_name, output) VALUES(?, ?, 'other-cmd', 'x')",
        (pid2, other_srv),
    )

    ctx = {"session_id": "sess1", "project_id": 1}

    def call(name, args):
        return asyncio.run(guardian_mcp.call_tool(name, args, ctx))

    def txt(r):
        return r["content"][0]["text"]

    def deny(name, args):
        r = call(name, args)
        assert r["isError"] and "друг" in txt(r), (name, txt(r))

    # данные чужого проекта недоступны
    deny("ssh_server_list", {"project_id": pid2})
    deny("ssh_server_update", {"id": other_srv, "host": "x.example.com"})
    deny("ssh_server_delete", {"id": other_srv})
    deny("ssh_server_test", {"id": other_srv})
    deny("ssh_command_list", {"server_id": other_srv})
    deny("ssh_command_update", {"id": other_cmd, "command": "echo {y}"})
    deny("ssh_command_delete", {"id": other_cmd})
    deny("ssh_run_list", {"server_id": other_srv})
    deny("ssh_server_create", {"name": "x", "host": "h", "username": "u", "password": "p",
                               "auth_type": "password", "project_id": pid2})
    runs = call("ssh_run_list", {})
    assert not runs["isError"] and "other-cmd" not in txt(runs)

    # создание сервера с паролем
    r = call("ssh_server_create", {"name": "prod", "host": "example.com", "username": "deploy",
                                   "auth_type": "password", "password": "s3cret"})
    assert not r["isError"], txt(r)
    srv = json.loads(txt(r))
    assert srv["has_password"] and not srv["has_key"] and "s3cret" not in txt(r)
    sid = srv["id"]

    assert [s["name"] for s in json.loads(txt(call("ssh_server_list", {})))] == ["prod"]

    # создание сервера с ключом + валидация ключа
    key = asyncssh.generate_private_key("ssh-ed25519")
    pem = key.export_private_key("openssh").decode()
    r = call("ssh_server_create", {"name": "dev", "host": "dev.example.com", "username": "deploy",
                                   "private_key": pem})
    assert not r["isError"], txt(r)
    r = call("ssh_server_create", {"name": "bad", "host": "h", "username": "u", "private_key": "not a key"})
    assert r["isError"] and "ключ" in txt(r)

    # частичное обновление сервера (enabled), ключ не затирается
    r = call("ssh_server_update", {"id": sid, "enabled": 0})
    upd = json.loads(txt(r))
    assert not r["isError"] and upd["enabled"] == 0 and upd["name"] == "prod" and upd["has_password"]

    # команды белого списка
    r = call("ssh_command_create", {
        "server_id": sid, "name": "logs",
        "command": "journalctl -u {service} -n {lines} --no-pager",
        "arg_regex": '{"service": "^[a-z0-9-]{1,40}$", "lines": "^[1-9][0-9]{0,3}$"}',
    })
    assert not r["isError"], txt(r)
    cid = json.loads(txt(r))["id"]
    r = call("ssh_command_create", {"server_id": sid, "name": "logs", "command": "ls"})
    assert r["isError"] and "уже есть" in txt(r)
    assert [c["name"] for c in json.loads(txt(call("ssh_command_list", {"server_id": sid})))] == ["logs"]

    r = call("ssh_command_update", {"id": cid, "timeout": 120, "description": "логи сервиса"})
    upd = json.loads(txt(r))
    assert not r["isError"] and upd["timeout"] == 120 and upd["description"] == "логи сервиса"
    assert upd["command"].startswith("journalctl")

    # проверка шаблона
    r = call("ssh_command_check", {"command": "echo {a}", "arg_regex": '{"a": "^[^\\n]{1,10}$"}',
                                   "params": {"a": "x y"}})
    assert not r["isError"] and json.loads(txt(r))["rendered"] == "echo 'x y'"
    r = call("ssh_command_check", {"command": "echo {a}", "params": {"a": "x; rm -rf /"}})
    assert r["isError"] and "валидацию" in txt(r)

    # удаление: каскадно уходит команда
    assert not call("ssh_command_delete", {"id": cid})["isError"]
    assert call("ssh_command_delete", {"id": cid})["isError"]
    assert not call("ssh_server_delete", {"id": sid})["isError"]
    assert call("ssh_server_list", {})


def test_guardian_mcp_lists_ssh_tools(client):
    from app.guardian_mcp import TOOLS

    names = {t["name"] for t in TOOLS}
    assert {
        "ssh_server_list", "ssh_server_create", "ssh_server_update", "ssh_server_delete",
        "ssh_server_test", "ssh_command_list", "ssh_command_create", "ssh_command_update",
        "ssh_command_delete", "ssh_command_check", "ssh_run_list",
    } <= names


def test_ssh_test_connection_saves_host_key(client):
    import asyncio
    import socket

    import asyncssh
    import pytest

    from app import db
    from app.ssh_config import SshConnectError, known_hosts_line, test_server_connection

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    client_key = asyncssh.generate_private_key("ssh-ed25519")
    server_key = asyncssh.generate_private_key("ssh-ed25519")
    pem = client_key.export_private_key("openssh").decode()

    class NoAuth(asyncssh.SSHServer):
        def begin_auth(self, username):
            return False

        def session_requested(self):
            return True

    async def run():
        listener = await asyncssh.listen("127.0.0.1", port, server_factory=NoAuth, server_host_keys=[server_key])
        try:
            pid = db.query_one("SELECT id FROM projects ORDER BY id LIMIT 1")["id"]
            sid = db.execute(
                "INSERT INTO ssh_servers(project_id, name, host, port, username, auth_type, private_key) "
                "VALUES(?, 't', '127.0.0.1', ?, 'tester', 'key', ?)",
                (pid, port, pem),
            )
            result = await test_server_connection(db.query_one("SELECT * FROM ssh_servers WHERE id=?", (sid,)))
            assert result["ok"] and result["host_key_saved"]
            assert result["fingerprint"].startswith("SHA256:")
            saved = db.query_one("SELECT known_hosts, last_error FROM ssh_servers WHERE id=?", (sid,))
            assert saved["known_hosts"] and saved["last_error"] is None

            # повторная проверка: ключ уже сохранён, пересохранения нет
            result = await test_server_connection(db.query_one("SELECT * FROM ssh_servers WHERE id=?", (sid,)))
            assert result["ok"] and not result["host_key_saved"]

            # чужой ключ хоста — несоответствие (409)
            other = asyncssh.generate_private_key("ssh-ed25519")
            db.execute(
                "UPDATE ssh_servers SET known_hosts=? WHERE id=?", (known_hosts_line("127.0.0.1", port, other), sid)
            )
            with pytest.raises(SshConnectError) as excinfo:
                await test_server_connection(db.query_one("SELECT * FROM ssh_servers WHERE id=?", (sid,)))
            assert excinfo.value.http_status == 409

            # replace_host_key пересохраняет ключ
            result = await test_server_connection(
                db.query_one("SELECT * FROM ssh_servers WHERE id=?", (sid,)), replace_host_key=True
            )
            assert result["ok"] and result["host_key_saved"]
        finally:
            listener.close()
            await listener.wait_closed()

    asyncio.run(run())


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


# ---------- issues ----------


def test_issues_crud_and_filters(client):
    r = client.post("/api/issues", json={"title": "Упал деплой", "description": "не поднимается воркер", "tags": ["баг", "деплой"], "project_id": 1})
    assert r.status_code == 200, r.text
    i1 = r.json()
    assert i1["status"] == "open"
    assert i1["tags"] == ["баг", "деплой"]
    assert i1["created_by"] == "manual"

    client.post("/api/issues", json={"title": "Рефакторинг стримера", "status": "in_progress", "tags": ["рефакторинг"], "project_id": 1})
    client.post("/api/issues", json={"title": "Релизная заметка", "status": "done", "project_id": 2})

    # проект + статус
    assert [i["title"] for i in client.get("/api/issues?project_id=1&status=open").json()] == ["Упал деплой"]
    assert [i["title"] for i in client.get("/api/issues?project_id=1&status=in_progress").json()] == ["Рефакторинг стримера"]
    # тег
    assert [i["title"] for i in client.get("/api/issues?project_id=1&tag=баг").json()] == ["Упал деплой"]
    # поиск по названию и описанию
    assert [i["title"] for i in client.get("/api/issues?project_id=1&q=воркер").json()] == ["Упал деплой"]
    assert [i["title"] for i in client.get("/api/issues?project_id=1&q=стримера").json()] == ["Рефакторинг стримера"]
    assert client.get("/api/issues?project_id=1&q=неттакого").json() == []

    # обновление
    r = client.put(f"/api/issues/{i1['id']}", json={"status": "done", "tags": ["баг"]})
    assert r.json()["status"] == "done" and r.json()["tags"] == ["баг"]
    # неизменённые поля остаются
    assert r.json()["title"] == "Упал деплой"

    # отдельный GET
    one = client.get(f"/api/issues/{i1['id']}").json()
    assert one["title"] == "Упал деплой" and one["comments"] == []
    assert client.get("/api/issues/999999").status_code == 404

    # валидация
    assert client.post("/api/issues", json={"title": ""}).status_code == 400
    assert client.post("/api/issues", json={"title": "x", "status": "wat"}).status_code == 400
    assert client.put(f"/api/issues/{i1['id']}", json={"status": "wat"}).status_code == 400
    assert client.put("/api/issues/999999", json={"title": "x"}).status_code == 404

    # удаление
    assert client.delete(f"/api/issues/{i1['id']}").json() == {"ok": True}
    assert client.get("/api/issues?project_id=1&q=деплой").json() == []


def test_broker_issue_tools(client, monkeypatch):
    import asyncio
    import json

    from app import broker_mcp

    ctx = {"session_id": "sess1", "project_id": 1}

    r = asyncio.run(broker_mcp.call_tool("issue_create", {"title": "От агента", "tags": ["авто"]}, {}))
    assert r["isError"] and "не привязана" in r["content"][0]["text"]

    r = asyncio.run(broker_mcp.call_tool("issue_create", {"title": "От агента", "description": "нашёл баг", "tags": ["авто"]}, ctx))
    assert not r["isError"], r["content"][0]["text"]
    created = json.loads(r["content"][0]["text"])
    assert created["created_by"] == "agent" and created["tags"] == ["авто"]

    r = asyncio.run(broker_mcp.call_tool("issue_list", {"q": "баг"}, ctx))
    listed = json.loads(r["content"][0]["text"])
    assert len(listed) == 1 and listed[0]["title"] == "От агента"

    r = asyncio.run(broker_mcp.call_tool("issue_update", {"id": created["id"], "status": "in_progress"}, ctx))
    assert json.loads(r["content"][0]["text"])["status"] == "in_progress"

    r = asyncio.run(broker_mcp.call_tool("issue_get", {"id": created["id"]}, ctx))
    got = json.loads(r["content"][0]["text"])
    assert got["title"] == "От агента" and got["comments"] == []
    r = asyncio.run(broker_mcp.call_tool("issue_get", {"id": 999999}, ctx))
    assert r["isError"] and "не найден" in r["content"][0]["text"]

    r = asyncio.run(broker_mcp.call_tool("issue_comment", {"id": created["id"], "text": "мой коммент"}, ctx))
    c = json.loads(r["content"][0]["text"])
    assert c["text"] == "мой коммент"

    r = asyncio.run(broker_mcp.call_tool("issue_comment_delete", {"issue_id": created["id"], "comment_id": c["id"]}, ctx))
    assert not r["isError"], r["content"][0]["text"]
    r = asyncio.run(broker_mcp.call_tool("issue_comment_delete", {"issue_id": created["id"], "comment_id": c["id"]}, ctx))
    assert r["isError"] and "не найден" in r["content"][0]["text"]
    assert json.loads(asyncio.run(broker_mcp.call_tool("issue_get", {"id": created["id"]}, ctx))["content"][0]["text"])["comments"] == []

    r = asyncio.run(broker_mcp.call_tool("issue_delete", {"id": created["id"]}, ctx))
    assert not r["isError"]
    r = asyncio.run(broker_mcp.call_tool("issue_delete", {"id": created["id"]}, ctx))
    assert r["isError"] and "не найден" in r["content"][0]["text"]


def test_issue_priority_assignee_comments(client):
    from app import db

    aid = db.query_one("SELECT id FROM agents WHERE is_guardian=0 ORDER BY id LIMIT 1")["id"]
    aname = db.query_one("SELECT name FROM agents WHERE id=?", (aid,))["name"]

    r = client.post("/api/issues", json={"title": "Критичный баг", "priority": "critical", "assignee_id": aid, "project_id": 1})
    assert r.status_code == 200, r.text
    i = r.json()
    assert i["priority"] == "critical" and i["assignee_id"] == aid and i["assignee_name"] == aname
    assert i["comments"] == []

    # исполнитель по имени агента
    r = client.post("/api/issues", json={"title": "По имени", "assignee_id": aname, "project_id": 1})
    assert r.json()["assignee_id"] == aid

    # фильтры по приоритету и исполнителю
    assert [x["title"] for x in client.get("/api/issues?project_id=1&priority=critical").json()] == ["Критичный баг"]
    assert [x["title"] for x in client.get(f"/api/issues?project_id=1&assignee_id={aid}").json()] == ["По имени", "Критичный баг"]

    # новые статусы
    r = client.put(f"/api/issues/{i['id']}", json={"status": "review"})
    assert r.json()["status"] == "review"
    assert client.post("/api/issues", json={"title": "cancelled", "status": "cancelled", "project_id": 1}).json()["status"] == "cancelled"

    # комментарии
    assert client.post("/api/issues/999999/comments", json={"text": "x"}).status_code == 404
    assert client.post(f"/api/issues/{i['id']}/comments", json={"text": ""}).status_code == 400
    c = client.post(f"/api/issues/{i['id']}/comments", json={"text": "проверяю", "agent_name": aname}).json()
    assert c["text"] == "проверяю" and c["agent_name"] == aname
    listed = client.get("/api/issues?project_id=1&priority=critical").json()
    assert [x["text"] for x in listed[0]["comments"]] == ["проверяю"]

    # комментарий можно добавить прямо в update
    client.put(f"/api/issues/{i['id']}", json={"comment": "ещё коммент"})
    assert len(client.get(f"/api/issues/{i['id']}/comments").json()) == 2

    # удаление комментария
    assert client.delete(f"/api/issues/{i['id']}/comments/999999").status_code == 404
    assert client.delete(f"/api/issues/{i['id']}/comments/{c['id']}").json() == {"ok": True}
    assert len(client.get(f"/api/issues/{i['id']}/comments").json()) == 1

    # валидация
    assert client.post("/api/issues", json={"title": "x", "priority": "wat"}).status_code == 400
    assert client.post("/api/issues", json={"title": "x", "assignee_id": 999999}).status_code == 400
    assert client.put(f"/api/issues/{i['id']}", json={"priority": "wat"}).status_code == 400

    # удаление каскадно удаляет комментарии
    client.delete(f"/api/issues/{i['id']}")
    assert client.get(f"/api/issues/{i['id']}/comments").status_code == 404


def test_broker_issue_own_only(client):
    import asyncio
    import json

    from app import broker_mcp, db

    aid = db.execute(
        "INSERT INTO agents(name, mode, model, issues_own_only) VALUES('solo', 'primary', 'm/m', 1)"
    )
    db.execute(
        "INSERT INTO sessions(id, agent_id, agent_name, project_id, status) "
        "VALUES('sess-solo', ?, 'solo', 1, 'running')",
        (aid,),
    )
    ctx = {"session_id": "sess-solo", "project_id": 1}

    # создаёт issue и автоматически становится исполнителем
    r = asyncio.run(broker_mcp.call_tool("issue_create", {"title": "моя задача", "priority": "high"}, ctx))
    created = json.loads(r["content"][0]["text"])
    assert created["assignee_id"] == aid and created["created_by"] == "solo"
    assert created["priority"] == "high"

    # чужая задача
    other_aid = db.query_one("SELECT id FROM agents WHERE is_guardian=0 AND id<>?", (aid,))["id"]
    other_id = db.execute(
        "INSERT INTO issues(project_id, title, status, priority, assignee_id) "
        "VALUES(1, 'чужая', 'open', 'medium', ?)",
        (other_aid,),
    )

    # list видит только свои
    r = asyncio.run(broker_mcp.call_tool("issue_list", {}, ctx))
    titles = [x["title"] for x in json.loads(r["content"][0]["text"])]
    assert "моя задача" in titles and "чужая" not in titles

    # чужие нельзя менять/комментировать/удалять
    for name, args in (("issue_update", {"id": other_id, "status": "done"}),
                       ("issue_comment", {"id": other_id, "text": "привет"}),
                       ("issue_delete", {"id": other_id})):
        r = asyncio.run(broker_mcp.call_tool(name, args, ctx))
        assert r["isError"] and "только свои" in r["content"][0]["text"], name

    # комментарий к своей — с именем агента
    r = asyncio.run(broker_mcp.call_tool("issue_comment", {"id": created["id"], "text": "делаю"}, ctx))
    c = json.loads(r["content"][0]["text"])
    assert c["agent_name"] == "solo"

    # удалять можно только свои комментарии
    other_cid = db.execute(
        "INSERT INTO issue_comments(issue_id, agent_id, agent_name, text) VALUES(?,?,?,?)",
        (created["id"], other_aid, "другой", "чужой коммент"),
    )
    r = asyncio.run(broker_mcp.call_tool("issue_comment_delete", {"issue_id": created["id"], "comment_id": other_cid}, ctx))
    assert r["isError"] and "только свои" in r["content"][0]["text"]
    r = asyncio.run(broker_mcp.call_tool("issue_comment_delete", {"issue_id": created["id"], "comment_id": c["id"]}, ctx))
    assert not r["isError"], r["content"][0]["text"]

    # исполнителем своей можно назначить только себя
    r = asyncio.run(broker_mcp.call_tool("issue_update", {"id": created["id"], "assignee": other_aid}, ctx))
    assert r["isError"] and "только свои" in r["content"][0]["text"]

    # обычный агент видит все issues проекта
    db.execute("UPDATE agents SET issues_own_only=0 WHERE id=?", (aid,))
    r = asyncio.run(broker_mcp.call_tool("issue_list", {}, ctx))
    titles = [x["title"] for x in json.loads(r["content"][0]["text"])]
    assert "чужая" in titles and "моя задача" in titles

    # настройка сохраняется через API
    a = client.get(f"/api/agents/{aid}").json()
    assert a["issues_own_only"] == 0
    client.put(f"/api/agents/{aid}", json={"issues_own_only": True})
    assert db.query_one("SELECT issues_own_only FROM agents WHERE id=?", (aid,))["issues_own_only"] == 1


def test_dashboard_aggregates(client):
    from app import db

    db.execute(
        "INSERT INTO issues(project_id, title, status, priority) VALUES(1, 'критично', 'open', 'critical')"
    )
    db.execute(
        "INSERT INTO sessions(id, agent_id, agent_name, project_id, status, title, created_at, finished_at) "
        "VALUES('dash-1', NULL, 'test', 1, 'completed', 'задача', datetime('now'), datetime('now'))"
    )
    db.execute(
        "INSERT INTO sessions(id, agent_id, agent_name, project_id, status, title, error, created_at, finished_at) "
        "VALUES('dash-2', NULL, 'test', 1, 'failed', 'упала', 'err', datetime('now'), datetime('now'))"
    )
    db.execute(
        "INSERT INTO telegram_config(project_id, token, enabled, connected) VALUES(1, 't', 1, 0)"
    )

    d = client.get("/api/dashboard?project_id=1").json()
    assert d["issues"]["by_status"]["open"] >= 1
    assert d["issues"]["critical_open"] >= 1
    assert d["issues"]["total"] >= 1
    assert any(s["id"] == "dash-2" for s in d["failed_sessions"])
    assert any(f["id"] == "dash-1" for f in d["feed"])
    assert any(f["id"] == "dash-2" for f in d["feed"])
    assert len(d["activity"]) == 14
    assert d["activity"][-1]["total"] >= 2
    assert isinstance(d["agents"], list) and isinstance(d["providers"], list)
    assert "schedules_total" in d and "triggers" in d
    assert d["channel"] is not None and "connected" in d["channel"]
    assert isinstance(d["files"], list)

    d2 = client.get("/api/dashboard").json()
    assert d2["channel"] is None and d2["files"] == []


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


# ---------- broker MCP: встроенные telegram-инструменты воркеров ----------


def test_broker_mcp_requires_bearer_secret(client):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert client.post("/broker/mcp", json=body).status_code == 401
    assert client.post("/broker/mcp", json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_broker_mcp_tools_list(client):
    from app.guardian_mcp import get_secret

    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    r = client.post("/broker/mcp", json=body, headers={"Authorization": f"Bearer {get_secret()}"})
    assert r.status_code == 200, r.text
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"telegram_send", "telegram_send_file", "telegram_info"} <= names


def test_broker_mcp_auth_machine_path_not_blocked_by_ui_auth(auth_client):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert auth_client.post("/broker/mcp", json=body).status_code == 401  # bearer, а не cookie-редирект


def test_broker_telegram_tools(client, monkeypatch, tmp_path):
    import asyncio

    from app import broker_mcp, channel, session_manager

    sent = {}
    filed = {}

    async def fake_send(project_id, chat_id, text, reply_to=None):
        sent.update(project_id=project_id, chat_id=chat_id, text=text, reply_to=reply_to)
        return 123

    async def fake_send_file(project_id, chat_id, content, filename, caption=None):
        filed.update(project_id=project_id, chat_id=chat_id, content=content, filename=filename, caption=caption)
        return 456

    monkeypatch.setattr(channel, "send", fake_send)
    monkeypatch.setattr(channel, "send_file", fake_send_file)

    ws = tmp_path / "workspaces" / "sess1"
    ws.mkdir(parents=True)
    (ws / "report.html").write_text("<h1>отчёт</h1>", encoding="utf-8")
    monkeypatch.setattr(session_manager, "ws_dir", lambda sid: ws)

    # без конфига канала — понятная ошибка
    r = asyncio.run(broker_mcp.call_tool("telegram_send", {"text": "привет"}, {"project_id": 1}))
    assert r["isError"] and "не настроен" in r["content"][0]["text"]

    _seed_config(notify_chat_id="111")
    r = asyncio.run(broker_mcp.call_tool("telegram_send", {"text": "привет"}, {"project_id": 1}))
    assert not r["isError"], r["content"][0]["text"]
    assert sent["chat_id"] == "111" and sent["text"] == "привет" and sent["project_id"] == 1

    # явный chat_id перекрывает notify_chat_id
    r = asyncio.run(broker_mcp.call_tool("telegram_send", {"text": "x", "chat_id": "222"}, {"project_id": 1}))
    assert sent["chat_id"] == "222"

    # файл из workspace + подпись
    ctx = {"session_id": "sess1", "project_id": 1}
    r = asyncio.run(broker_mcp.call_tool("telegram_send_file", {"path": "report.html", "caption": "см. файл"}, ctx))
    assert not r["isError"], r["content"][0]["text"]
    assert filed["filename"] == "report.html" and filed["content"] == "<h1>отчёт</h1>".encode() and filed["caption"] == "см. файл"

    # файл из текста
    r = asyncio.run(broker_mcp.call_tool("telegram_send_file", {"content": '{"a": 1}', "filename": "data.json"}, ctx))
    assert not r["isError"] and filed["content"] == b'{"a": 1}' and filed["filename"] == "data.json"

    # выход за пределы workspace запрещён
    r = asyncio.run(broker_mcp.call_tool("telegram_send_file", {"path": "../secret.txt"}, ctx))
    assert r["isError"] and "пределы workspace" in r["content"][0]["text"]

    # нет ни chat_id, ни notify_chat_id
    from app import db

    pid = db.query_one("SELECT id FROM projects ORDER BY id LIMIT 1")["id"]
    db.execute("UPDATE telegram_config SET notify_chat_id='' WHERE project_id=?", (pid,))
    r = asyncio.run(broker_mcp.call_tool("telegram_send", {"text": "x"}, {"project_id": 1}))
    assert r["isError"] and "чат" in r["content"][0]["text"]


def test_broker_exa_search(client, monkeypatch):
    import asyncio
    import json

    from app import broker_mcp, db

    aid = db.execute(
        "INSERT INTO agents(name, mode, model, exa_enabled) VALUES('poiskovik', 'primary', 'm/m', 0)"
    )
    db.execute(
        "INSERT INTO sessions(id, agent_id, agent_name, project_id, status) "
        "VALUES('sess-exa', ?, 'poiskovik', 1, 'running')",
        (aid,),
    )
    ctx = {"session_id": "sess-exa", "project_id": 1}

    # выключен — инструмента нет в tools_for и вызов отклоняется
    names = [t["name"] for t in broker_mcp.tools_for(ctx)]
    assert "exa_search" not in names
    r = asyncio.run(broker_mcp.call_tool("exa_search", {"query": "тест"}, ctx))
    assert r["isError"] and "выключен" in r["content"][0]["text"]

    # включаем через API — инструмент появляется
    r = client.put(f"/api/agents/{aid}", json={"exa_enabled": True})
    assert r.status_code == 200, r.text
    assert db.query_one("SELECT exa_enabled FROM agents WHERE id=?", (aid,))["exa_enabled"] == 1
    assert "exa_search" in [t["name"] for t in broker_mcp.tools_for(ctx)]

    # без ключа — ошибка
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    r = asyncio.run(broker_mcp.call_tool("exa_search", {"query": "тест"}, ctx))
    assert r["isError"] and "EXA_API_KEY" in r["content"][0]["text"]

    # с ключом и фейковым ответом
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    monkeypatch.setattr(broker_mcp, "_exa_request", lambda payload: {"results": [
        {"title": "Статья", "url": "https://example.com", "text": "текст"}
    ]})
    r = asyncio.run(broker_mcp.call_tool("exa_search", {"query": "тест", "num_results": 3}, ctx))
    assert not r["isError"], r["content"][0]["text"]
    out = json.loads(r["content"][0]["text"])
    assert out["results"][0]["url"] == "https://example.com" and out["results"][0]["text"] == "текст"

    # без сессии нельзя
    r = asyncio.run(broker_mcp.call_tool("exa_search", {"query": "тест"}, {"project_id": 1}))
    assert r["isError"] and "сессии воркера" in r["content"][0]["text"]


def test_broker_file_download(client, monkeypatch, tmp_path):
    import asyncio

    from app import broker_mcp, files_store, session_manager

    class FakeObj:
        def __init__(self, data=b"hello!"):
            self.headers = {"Content-Length": str(len(data))}
            self._data = data

        def stream(self, amt=1024 * 1024):
            yield self._data

    monkeypatch.setattr(files_store, "get_object", lambda pid, path: FakeObj())

    ws = tmp_path / "workspaces" / "sess1"
    ws.mkdir(parents=True)
    monkeypatch.setattr(session_manager, "ws_dir", lambda sid: ws)

    # в корень workspace под именем из path
    r = asyncio.run(
        broker_mcp.call_tool("file_download", {"path": "shots/отчёт.txt"}, {"session_id": "sess1", "project_id": 1})
    )
    assert not r["isError"], r["content"][0]["text"]
    import json

    out = json.loads(r["content"][0]["text"])
    assert out["path"] == "отчёт.txt"
    assert (ws / "отчёт.txt").read_bytes() == b"hello!"

    # в подпапку (относительный dest)
    r = asyncio.run(
        broker_mcp.call_tool(
            "file_download", {"path": "shots/отчёт.txt", "dest": "reports/2026/x.txt"}, {"session_id": "sess1", "project_id": 1}
        )
    )
    assert not r["isError"], r["content"][0]["text"]
    assert (ws / "reports" / "2026" / "x.txt").read_bytes() == b"hello!"

    # абсолютный dest внутри /workspace
    r = asyncio.run(
        broker_mcp.call_tool(
            "file_download", {"path": "shots/отчёт.txt", "dest": "/workspace/ssh/deploy_key.pub"},
            {"session_id": "sess1", "project_id": 1},
        )
    )
    assert not r["isError"], r["content"][0]["text"]
    assert (ws / "ssh" / "deploy_key.pub").read_bytes() == b"hello!"

    # абсолютный dest в папку с "/" — имя файла из path
    r = asyncio.run(
        broker_mcp.call_tool(
            "file_download", {"path": "shots/отчёт.txt", "dest": "/workspace/reports/"},
            {"session_id": "sess1", "project_id": 1},
        )
    )
    assert not r["isError"], r["content"][0]["text"]
    assert (ws / "reports" / "отчёт.txt").read_bytes() == b"hello!"

    # dest с "/" — имя файла из path
    r = asyncio.run(
        broker_mcp.call_tool("file_download", {"path": "shots/отчёт.txt", "dest": "reports/"}, {"session_id": "sess1", "project_id": 1})
    )
    assert not r["isError"], r["content"][0]["text"]
    assert (ws / "reports" / "отчёт.txt").read_bytes() == b"hello!"

    # абсолютный dest вне /workspace запрещён
    r = asyncio.run(
        broker_mcp.call_tool(
            "file_download", {"path": "x", "dest": "/tmp/secret.txt"}, {"session_id": "sess1", "project_id": 1}
        )
    )
    assert r["isError"] and "/workspace" in r["content"][0]["text"]

    # выход за пределы workspace запрещён
    r = asyncio.run(
        broker_mcp.call_tool("file_download", {"path": "x", "dest": "../secret.txt"}, {"session_id": "sess1", "project_id": 1})
    )
    assert r["isError"] and "пределы workspace" in r["content"][0]["text"]

    # без сессии нельзя
    r = asyncio.run(broker_mcp.call_tool("file_download", {"path": "x"}, {"project_id": 1}))
    assert r["isError"] and "сессии воркера" in r["content"][0]["text"]


def test_broker_file_upload(client, monkeypatch, tmp_path):
    import asyncio
    import json

    from app import broker_mcp, files_store, session_manager

    ups = {}
    monkeypatch.setattr(files_store, "upload", lambda pid, path, data, ct, size=None: ups.update(pid=pid, path=path, data=data, ct=ct, size=size))
    monkeypatch.setattr(files_store, "content_url", lambda pid, path: f"/api/files/content?project_id={pid}&path={path}")

    ws = tmp_path / "workspaces" / "sess1"
    ws.mkdir(parents=True)
    (ws / "shots").mkdir()
    (ws / "shots" / "скрин.png").write_bytes(b"PNGDATA")
    monkeypatch.setattr(session_manager, "ws_dir", lambda sid: ws)

    ctx = {"session_id": "sess1", "project_id": 1}

    # файл из workspace в корень файлов проекта
    r = asyncio.run(broker_mcp.call_tool("file_upload", {"path": "shots/скрин.png"}, ctx))
    assert not r["isError"], r["content"][0]["text"]
    out = json.loads(r["content"][0]["text"])
    assert out["path"] == "скрин.png" and out["size"] == 7
    assert ups["data"] == b"PNGDATA" and ups["path"] == "скрин.png"

    # dest в подпапку
    r = asyncio.run(broker_mcp.call_tool("file_upload", {"path": "shots/скрин.png", "dest": "отчёты/2026/скрин.png"}, ctx))
    assert not r["isError"], r["content"][0]["text"]
    assert ups["path"] == "отчёты/2026/скрин.png"

    # dest с "/" — имя файла из path
    r = asyncio.run(broker_mcp.call_tool("file_upload", {"path": "shots/скрин.png", "dest": "отчёты/"}, ctx))
    assert not r["isError"], r["content"][0]["text"]
    assert ups["path"] == "отчёты/скрин.png"

    # текст через content + filename
    r = asyncio.run(broker_mcp.call_tool("file_upload", {"content": "итог работы", "filename": "итог.md"}, ctx))
    assert not r["isError"], r["content"][0]["text"]
    assert ups["data"] == "итог работы".encode("utf-8") and ups["path"] == "итог.md"

    # несуществующий файл
    r = asyncio.run(broker_mcp.call_tool("file_upload", {"path": "нет.txt"}, ctx))
    assert r["isError"] and "не найден" in r["content"][0]["text"]

    # .. в dest запрещён
    r = asyncio.run(broker_mcp.call_tool("file_upload", {"path": "shots/скрин.png", "dest": "../секрет.txt"}, ctx))
    assert r["isError"] and "недопустимый путь" in r["content"][0]["text"]

    # без сессии нельзя
    r = asyncio.run(broker_mcp.call_tool("file_upload", {"content": "x"}, {"project_id": 1}))
    assert r["isError"] and "сессии воркера" in r["content"][0]["text"]


def test_agent_calls_api(client):
    from app import db

    def create(name):
        r = client.post("/api/agents", json={"name": name, "description": f"агент {name}", "project_id": 1})
        assert r.status_code == 200, r.text
        return r.json()

    manager = create("manager")
    dev = create("developer")
    tester = create("tester")

    r = client.put(f"/api/agents/{manager['id']}/calls", json={"target_ids": [dev["id"], tester["id"]]})
    assert r.status_code == 200, r.text
    assert {a["id"] for a in client.get(f"/api/agents/{manager['id']}/calls").json()} == {dev["id"], tester["id"]}
    assert len(client.get(f"/api/agents/{manager['id']}").json()["calls"]) == 2

    # замена списка целиком
    r = client.put(f"/api/agents/{manager['id']}/calls", json={"target_ids": [dev["id"]]})
    assert {a["id"] for a in client.get(f"/api/agents/{manager['id']}/calls").json()} == {dev["id"]}

    # сам себя / несуществующий / guardian — ошибка, список не пострадал
    assert client.put(f"/api/agents/{manager['id']}/calls", json={"target_ids": [manager["id"]]}).status_code == 400
    assert client.put(f"/api/agents/{manager['id']}/calls", json={"target_ids": [999999]}).status_code == 400
    gid = db.query_one("SELECT id FROM agents WHERE is_guardian=1")["id"]
    assert client.put(f"/api/agents/{manager['id']}/calls", json={"target_ids": [gid]}).status_code == 400
    assert {a["id"] for a in client.get(f"/api/agents/{manager['id']}/calls").json()} == {dev["id"]}

    # несуществующий агент
    assert client.get("/api/agents/999999/calls").status_code == 404
    assert client.put("/api/agents/999999/calls", json={"target_ids": []}).status_code == 404


def test_broker_agent_run_tools(client, monkeypatch):
    import asyncio
    import json

    from app import broker_mcp, db, main, session_manager

    pid = db.query_one("SELECT id FROM projects ORDER BY id LIMIT 1")["id"]
    manager_id = db.execute(
        "INSERT INTO agents(name, mode, model) VALUES('manager', 'primary', 'm/m')"
    )
    dev_id = db.execute("INSERT INTO agents(name, mode, model) VALUES('developer', 'primary', 'm/m')")
    tester_id = db.execute("INSERT INTO agents(name, mode, model) VALUES('tester', 'primary', 'm/m')")
    db.execute("INSERT INTO agent_calls(caller_id, target_id) VALUES(?,?),(?,?)",
               (manager_id, dev_id, manager_id, tester_id))
    sid = "caller-sess"
    db.execute(
        "INSERT INTO sessions(id, agent_id, agent_name, project_id, title, source, status) "
        "VALUES(?, ?, 'manager', ?, 't', 'manual', 'running')",
        (sid, manager_id, pid),
    )
    ctx = {"session_id": sid, "project_id": pid}

    # список доступных для вызова
    r = asyncio.run(broker_mcp.call_tool("agent_call_list", {}, ctx))
    assert not r["isError"], r["content"][0]["text"]
    names = {a["name"] for a in json.loads(r["content"][0]["text"])}
    assert names == {"developer", "tester"}

    spawned = []
    monkeypatch.setattr(main, "spawn_start", lambda sid_, prompt: spawned.append((sid_, prompt)))

    # fire-and-forget: сессия создаётся в проекте вызывающего
    r = asyncio.run(broker_mcp.call_tool(
        "agent_run", {"agent": "developer", "prompt": "напиши тест", "wait": False}, ctx
    ))
    assert not r["isError"], r["content"][0]["text"]
    out = json.loads(r["content"][0]["text"])
    assert out["status"] == "queued" and out["session_id"]
    row = db.query_one("SELECT * FROM sessions WHERE id=?", (out["session_id"],))
    assert row["agent_id"] == dev_id and row["source"] == "agent" and row["project_id"] == pid
    assert spawned and spawned[0][0] == out["session_id"]

    # вызов по id
    r = asyncio.run(broker_mcp.call_tool(
        "agent_run", {"agent": tester_id, "prompt": "прогони тесты", "wait": False}, ctx
    ))
    assert not r["isError"], r["content"][0]["text"]

    # запрещённый агент
    other_id = db.execute("INSERT INTO agents(name, mode, model) VALUES('other', 'primary', 'm/m')")
    r = asyncio.run(broker_mcp.call_tool(
        "agent_run", {"agent": other_id, "prompt": "x", "wait": False}, ctx
    ))
    assert r["isError"] and "не может вызывать" in r["content"][0]["text"]

    # сам себя
    r = asyncio.run(broker_mcp.call_tool(
        "agent_run", {"agent": "manager", "prompt": "x", "wait": False}, ctx
    ))
    assert r["isError"] and "сам себя" in r["content"][0]["text"]

    # неизвестный агент и пустой prompt
    r = asyncio.run(broker_mcp.call_tool("agent_run", {"agent": "nope", "prompt": "x", "wait": False}, ctx))
    assert r["isError"] and "не найден" in r["content"][0]["text"]
    r = asyncio.run(broker_mcp.call_tool("agent_run", {"agent": "developer", "prompt": "  ", "wait": False}, ctx))
    assert r["isError"] and "prompt обязателен" in r["content"][0]["text"]

    # без сессии воркера
    r = asyncio.run(broker_mcp.call_tool("agent_run", {"agent": "developer", "prompt": "x"}, {"project_id": pid}))
    assert r["isError"] and "сессии воркера" in r["content"][0]["text"]

    # wait=true дожидается завершения
    done_sid = "done-sess"
    db.execute(
        "INSERT INTO sessions(id, agent_id, agent_name, project_id, title, source, status, result_json) "
        "VALUES(?, ?, 'developer', ?, 't', 'agent', 'completed', '{\"итог\": 42}')",
        (done_sid, dev_id, pid),
    )
    monkeypatch.setattr(session_manager, "create_session", lambda *a, **k: done_sid)
    r = asyncio.run(broker_mcp.call_tool("agent_run", {"agent": "developer", "prompt": "x"}, ctx))
    assert not r["isError"], r["content"][0]["text"]
    out = json.loads(r["content"][0]["text"])
    assert out["status"] == "completed" and out["result"] == {"итог": 42}

    # wait=true с таймаутом: сессия висит — возвращается note
    monkeypatch.setattr(session_manager, "create_session", lambda *a, **k: "hang-sess")
    db.execute(
        "INSERT INTO sessions(id, agent_id, agent_name, project_id, title, source, status) "
        "VALUES('hang-sess', ?, 'developer', ?, 't', 'agent', 'running')",
        (dev_id, pid),
    )
    monkeypatch.setattr(broker_mcp, "CALL_WAIT_MIN", 0)
    monkeypatch.setattr(broker_mcp, "CALL_POLL", 0)
    r = asyncio.run(broker_mcp.call_tool(
        "agent_run", {"agent": "developer", "prompt": "x", "timeout": 1}, ctx
    ))
    assert not r["isError"], r["content"][0]["text"]
    out = json.loads(r["content"][0]["text"])
    assert out["status"] == "running" and "agent_status" in out["note"]

    # agent_status: своя сессия и чужая (другой проект)
    r = asyncio.run(broker_mcp.call_tool("agent_status", {"session_id": done_sid}, ctx))
    assert not r["isError"], r["content"][0]["text"]
    assert json.loads(r["content"][0]["text"])["result"] == {"итог": 42}
    foreign = "foreign-sess"
    pid2 = db.execute("INSERT INTO projects(name) VALUES('другой проект')")
    db.execute(
        "INSERT INTO sessions(id, agent_id, agent_name, project_id, title, source, status) "
        "VALUES(?, ?, 'x', ?, 't', 'manual', 'running')",
        (foreign, dev_id, pid2),
    )
    r = asyncio.run(broker_mcp.call_tool("agent_status", {"session_id": foreign}, ctx))
    assert r["isError"] and "другого проекта" in r["content"][0]["text"]
    r = asyncio.run(broker_mcp.call_tool("agent_status", {"session_id": "nope"}, ctx))
    assert r["isError"] and "не найдена" in r["content"][0]["text"]

    # tools_for: инструменты вызовов только при настроенных вызовах
    assert broker_mcp.TEAM_TOOLS <= {t["name"] for t in broker_mcp.tools_for(ctx)}
    db.execute("DELETE FROM agent_calls WHERE caller_id=?", (manager_id,))
    assert not (broker_mcp.TEAM_TOOLS & {t["name"] for t in broker_mcp.tools_for(ctx)})


def test_render_workspace_injects_broker_mcp(tmp_path):
    from app.render import render_workspace

    wdir = tmp_path / "ws"
    render_workspace(
        wdir,
        [{"name": "general", "mode": "primary", "model": "m/m", "is_default": 1}],
        [],
        [],
        broker_mcp={"name": "vibeprod", "type": "remote", "url": "http://h/broker/mcp", "headers": '{"Authorization":"Bearer x"}', "enabled": 1},
    )
    import json

    cfg = json.loads((wdir / "opencode.json").read_text(encoding="utf-8"))
    assert "vibeprod" in cfg["mcp"]
    assert cfg["mcp"]["vibeprod"]["url"] == "http://h/broker/mcp"
    assert cfg["mcp"]["vibeprod"]["headers"]["Authorization"] == "Bearer x"


def test_render_perm_allows_headless(tmp_path):
    """external_directory/read по умолчанию «ask» — в воркере ответить некому.

    В opencode.json и во frontmatter агента они должны быть allow, иначе
    opencode зависает в ожидании ответа на разрешение.
    """
    from app.render import render_workspace

    wdir = tmp_path / "ws"
    render_workspace(
        wdir,
        [
            {
                "name": "general",
                "mode": "primary",
                "model": "m/m",
                "is_default": 1,
                "permission": '{"edit": "allow", "bash": "allow"}',
            },
            {"name": "guardian", "mode": "primary", "model": "m/m", "permission": '"allow"'},
        ],
        [],
        [],
    )
    import json

    cfg = json.loads((wdir / "opencode.json").read_text(encoding="utf-8"))
    assert cfg["permission"]["external_directory"] == "allow"
    assert cfg["permission"]["read"] == "allow"

    text = (wdir / ".opencode" / "agent" / "general.md").read_text(encoding="utf-8")
    assert "external_directory: allow" in text
    assert "read: allow" in text


def test_streamer_permission_helpers():
    from app.streamer import _permission_id, _permission_name, _session_of

    # форк opencode: permission.asked, properties.id
    props = {"id": "per_1", "sessionID": "s1", "permission": "external_directory", "patterns": ["/etc/*"]}
    assert _session_of(props) == "s1"
    assert _permission_id(props) == "per_1"
    assert _permission_name(props) == "external_directory"

    # v2-событие без bridge: sessionID вложен в data
    assert _session_of({"data": {"sessionID": "s2"}}) == "s2"

    # сырое v2-событие: requestID в data.id, имя — data.action
    assert _permission_id({"data": {"id": "per_v2"}}) == "per_v2"
    assert _permission_name({"data": {"action": "external_directory"}}) == "external_directory"

    # старый opencode: permissionID / permission.id
    assert _permission_id({"permissionID": "p9"}) == "p9"
    assert _permission_id({"permission": {"id": "p8"}}) == "p8"
    assert _permission_id({}) is None
