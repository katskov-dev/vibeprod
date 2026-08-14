"""Дымовой тест: приложение поднимается на временной базе и отвечает по API.

Docker и ключи провайдеров не нужны — всё, что ходит в докер, на время теста
заменяется заглушками.
"""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBEPROD_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    # модули читают VIBEPROD_DATA_DIR на импорте — перечитываем их с новым путём
    from app import db as db_module

    importlib.reload(db_module)
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

    with TestClient(main_module.app) as c:
        yield c


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Vibeprod" in r.text


@pytest.mark.parametrize(
    "path",
    ["/api/projects", "/api/agents", "/api/providers", "/api/sessions",
     "/api/skills", "/api/mcp-catalog", "/api/webhooks", "/api/schedules"],
)
def test_collection_endpoints_return_lists(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


def test_db_bootstrap_creates_defaults(client):
    projects = client.get("/api/projects").json()
    assert len(projects) == 1, "первый запуск должен создать проект по умолчанию"

    agents = client.get("/api/agents").json()
    names = {a["name"] for a in agents}
    assert "general" in names
    assert "guardian" not in names, "guardian скрыт из списка агентов"

    catalog = client.get("/api/mcp-catalog").json()
    assert any(m["name"] == "playwright" for m in catalog)


def test_guardian_mcp_requires_bearer_secret(client):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert client.post("/guardian/mcp", json=body).status_code == 401
    assert client.post(
        "/guardian/mcp", json=body, headers={"Authorization": "Bearer wrong"}
    ).status_code == 401


def test_unknown_session_is_404(client):
    assert client.get("/api/sessions/does-not-exist").status_code == 404
