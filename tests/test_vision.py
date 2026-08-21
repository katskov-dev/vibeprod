"""Тесты vision-подсистемы: конфиг DeepSeek vision на брокере и vision-MCP сервер.

Без реальных ключей и docker: endpoint /api/vision/config проверяется на
временной базе, а node-сервер vision-MCP поднимается как подпроцесс с
фейковым брокером и фейковым DeepSeek API (перехватываем тело запроса).
"""
import base64
import importlib
import json
import os
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_VISION_MCP_PATH = Path(__file__).resolve().parent.parent / "mcp" / "playwright" / "vision-mcp" / "server.mjs"

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


# ---------- API брокера ----------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBEPROD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VIBEPROD_LOGIN", "admin")
    monkeypatch.setenv("VIBEPROD_PASSWORD", "secret")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
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


def _project_and_token():
    from app import db

    row = db.query_one("SELECT id, file_token FROM projects ORDER BY id LIMIT 1")
    return row["id"], row["file_token"]


def test_vision_config_requires_token(client):
    pid, _ = _project_and_token()
    assert client.get(f"/api/vision/config?project_id={pid}").status_code in (401, 403)
    assert client.get(f"/api/vision/config?project_id={pid}", headers={"X-Vibeprod-Token": "wrong"}).status_code in (401, 403)


def test_vision_config_not_configured_hint(client):
    pid, token = _project_and_token()
    r = client.get(f"/api/vision/config?project_id={pid}", headers={"X-Vibeprod-Token": token})
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["configured"] is False
    assert "api_key" not in cfg
    assert "«Провайдеры»" in cfg["hint"] or "Провайдеры" in cfg["hint"]
    assert "DEEPSEEK_API_KEY" in cfg["hint"]
    assert cfg["model"] == "deepseek-v4-flash-vision-exp"
    assert cfg["base_url"] == "https://api.deepseek.com"


def test_vision_config_from_provider(client):
    from app import db

    pid, token = _project_and_token()
    db.execute(
        "INSERT INTO providers(id, label, env_var, api_key, enabled, project_id) VALUES(?,?,?,?,1,?)",
        ("deepseek", "DeepSeek", "DEEPSEEK_API_KEY", "sk-test-key-123", pid),
    )
    r = client.get(f"/api/vision/config?project_id={pid}", headers={"X-Vibeprod-Token": token})
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["configured"] is True
    assert cfg["api_key"] == "sk-test-key-123"
    assert cfg["source"] == "provider:deepseek"
    assert cfg["api_key_masked"].startswith("sk-t")


def test_vision_config_disabled_provider(client):
    from app import db

    pid, token = _project_and_token()
    db.execute(
        "INSERT INTO providers(id, label, env_var, api_key, enabled, project_id) VALUES(?,?,?,?,0,?)",
        ("deepseek", "DeepSeek", "DEEPSEEK_API_KEY", "sk-test", pid),
    )
    cfg = client.get(f"/api/vision/config?project_id={pid}", headers={"X-Vibeprod-Token": token}).json()
    assert cfg["configured"] is False
    assert "отключён" in cfg["hint"]


def test_vision_config_from_env_fallback(client, monkeypatch):
    pid, token = _project_and_token()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
    cfg = client.get(f"/api/vision/config?project_id={pid}", headers={"X-Vibeprod-Token": token}).json()
    assert cfg["configured"] is True
    assert cfg["api_key"] == "sk-from-env"
    assert cfg["source"] == "env:DEEPSEEK_API_KEY"


def test_vision_config_not_leaking_other_project_key(client):
    from app import db

    pid, token = _project_and_token()
    pid2 = db.execute("INSERT INTO projects(name, file_token) VALUES('Второй', 'tok2')")
    db.execute(
        "INSERT INTO providers(id, label, env_var, api_key, enabled, project_id) VALUES(?,?,?,?,1,?)",
        ("deepseek", "DeepSeek", "DEEPSEEK_API_KEY", "sk-other-project", pid2),
    )
    cfg = client.get(f"/api/vision/config?project_id={pid}", headers={"X-Vibeprod-Token": token}).json()
    assert cfg["configured"] is False, "ключ чужого проекта не должен утекать"


def test_vision_catalog_seeded(client):
    assert client.post("/api/login", json={"login": "admin", "password": "secret"}).status_code == 200
    catalog = client.get("/api/mcp-catalog").json()
    vision = next((m for m in catalog if m["name"] == "vision"), None)
    assert vision, "каталог должен содержать встроенный MCP «vision»"
    assert vision["service_container"] == "vibeprod-playwright"
    assert vision["url"] == "http://vibeprod-playwright:8934/mcp"

    skills = client.get("/api/skills").json()
    assert any(s["name"] == "vision-analyze" for s in skills)


# ---------- vision-MCP сервер (node) ----------


class _FakeHandler(BaseHTTPRequestHandler):
    """Отдаёт ответы по маршруту; брокером или DeepSeek притворяется вышестоящий тест."""

    def log_message(self, *args):
        pass

    def _route(self):
        router = self.server.router
        return router(self)

    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()


class _FakeServer:
    def __init__(self, handler):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.router = lambda req: None
        self.httpd.requests = []
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _json_route(payload, status=200):
    def route(req):
        body = req.rfile.read(int(req.headers.get("Content-Length") or 0))
        req.server.requests.append((req.path, req.headers, body))
        data = json.dumps(payload()).encode("utf-8")
        req.send_response(status)
        req.send_header("Content-Type", "application/json")
        req.send_header("Content-Length", str(len(data)))
        req.end_headers()
        req.wfile.write(data)

    return route


def _wait_mcp(port):
    import urllib.request

    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/mcp",
                data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=2)
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("vision-MCP сервер не поднялся")


@pytest.fixture()
def vision(tmp_path):
    broker = _FakeServer(_FakeHandler)
    deepseek = _FakeServer(_FakeHandler)
    port = _free_port()
    shots = tmp_path / "shots"
    shots.mkdir()
    env = {
        "VISION_MCP_PORT": str(port),
        "VIBEPROD_SHOTS_DIR": str(shots),
        "VIBEPROD_VISION_BASE_URL": deepseek.url,
        "VIBEPROD_BROKER_URL": broker.url,
    }
    proc = subprocess.Popen(["node", str(_VISION_MCP_PATH)], env={**os.environ, **env})
    try:
        _wait_mcp(port)
        yield {
            "proc": proc,
            "port": port,
            "broker": broker,
            "deepseek": deepseek,
            "shots": shots,
            "url": f"http://127.0.0.1:{port}/mcp",
        }
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        broker.close()
        deepseek.close()


def _mcp_call(url, name, args, project="7", token="tok"):
    import urllib.request

    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    req = urllib.request.Request(
        url,
        data=body.encode(),
        headers={
            "Content-Type": "application/json",
            "X-Vibeprod-Project": project,
            "X-Vibeprod-Token": token,
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))["result"]


def _configured_cfg(base_url):
    return {
        "configured": True,
        "api_key": "sk-fake-key",
        "source": "provider:deepseek",
        "api_key_masked": "sk-f…ey",
        "base_url": base_url,
        "model": "deepseek-v4-flash-vision-exp",
    }


def _vision_ok_route():
    return _json_route(
        lambda: {
            "model": "deepseek-v4-flash-vision-exp",
            "choices": [{"message": {"content": "На картинке красный квадрат"}}],
            "usage": {"total_tokens": 12},
        }
    )


def test_vision_mcp_tools_list(vision):
    import urllib.request

    req = urllib.request.Request(
        vision["url"],
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        names = {t["name"] for t in json.loads(r.read().decode("utf-8"))["result"]["tools"]}
    assert {"vision_analyze", "vision_status"} <= names


def test_vision_mcp_analyze_local_file(vision):
    vision["broker"].httpd.router = _json_route(lambda: _configured_cfg(vision["deepseek"].url))
    vision["deepseek"].httpd.router = _vision_ok_route()
    shot = vision["shots"] / "page.png"
    shot.write_bytes(PNG_BYTES)

    res = _mcp_call(vision["url"], "vision_analyze", {"image": "page.png", "prompt": "Что на картинке?"})
    assert not res["isError"], res["content"][0]["text"]
    out = json.loads(res["content"][0]["text"])
    assert out["answer"] == "На картинке красный квадрат"

    assert len(vision["deepseek"].httpd.requests) == 1
    path_, headers, body = vision["deepseek"].httpd.requests[0]
    assert path_ == "/chat/completions"
    assert headers.get("Authorization") == "Bearer sk-fake-key"
    sent = json.loads(body)
    assert sent["model"] == "deepseek-v4-flash-vision-exp"
    blocks = sent["messages"][0]["content"]
    assert blocks[0] == {"type": "text", "text": "Что на картинке?"}
    assert blocks[1]["type"] == "image_url"
    assert blocks[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert blocks[1]["image_url"]["url"].endswith(base64.b64encode(PNG_BYTES).decode())


def test_vision_mcp_analyze_multiple_images_and_detail(vision):
    vision["broker"].httpd.router = _json_route(lambda: _configured_cfg(vision["deepseek"].url))
    vision["deepseek"].httpd.router = _vision_ok_route()
    (vision["shots"] / "a.png").write_bytes(PNG_BYTES)
    (vision["shots"] / "b.png").write_bytes(PNG_BYTES)

    res = _mcp_call(
        vision["url"],
        "vision_analyze",
        {
            "image": "/vibeprod-shots-not-exist",  # нет такого файла — упадёт
            "prompt": "x",
        },
    )
    assert res["isError"] and "файл не найден" in res["content"][0]["text"]

    res = _mcp_call(
        vision["url"],
        "vision_analyze",
        {"images": ["a.png", "b.png"], "prompt": "Сравни", "detail": "low"},
    )
    assert not res["isError"], res["content"][0]["text"]
    blocks = json.loads(vision["deepseek"].httpd.requests[-1][2])["messages"][0]["content"]
    images = [b for b in blocks if b["type"] == "image_url"]
    assert len(images) == 2
    assert all(b["image_url"]["detail"] == "low" for b in images)


def test_vision_mcp_not_configured_tells_what_to_do(vision):
    vision["broker"].httpd.router = _json_route(
        lambda: {"configured": False, "hint": "задайте DEEPSEEK_API_KEY", "model": "deepseek-v4-flash-vision-exp"}
    )
    res = _mcp_call(vision["url"], "vision_analyze", {"image": "x.png", "prompt": "x"})
    assert res["isError"]
    assert "не настроен" in res["content"][0]["text"]
    assert "DEEPSEEK_API_KEY" in res["content"][0]["text"]
    assert not vision["deepseek"].httpd.requests, "без ключа запрос в DeepSeek не должен уходить"

    res = _mcp_call(vision["url"], "vision_status", {})
    assert not res["isError"]
    status = json.loads(res["content"][0]["text"])
    assert status["configured"] is False


def test_vision_mcp_validation(vision):
    vision["broker"].httpd.router = _json_route(lambda: _configured_cfg(vision["deepseek"].url))
    vision["deepseek"].httpd.router = _vision_ok_route()
    (vision["shots"] / "a.png").write_bytes(PNG_BYTES)

    res = _mcp_call(vision["url"], "vision_analyze", {"image": "a.png"})
    assert res["isError"] and "prompt обязателен" in res["content"][0]["text"]

    res = _mcp_call(vision["url"], "vision_analyze", {"prompt": "x"})
    assert res["isError"] and "Не передано изображение" in res["content"][0]["text"]

    res = _mcp_call(vision["url"], "vision_analyze", {"image": "a.png", "prompt": "x", "detail": "wat"})
    assert res["isError"] and "detail" in res["content"][0]["text"]


def test_vision_mcp_requires_project_header(vision):
    import urllib.request

    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "vision_status", "arguments": {}}}
    )
    req = urllib.request.Request(vision["url"], data=body.encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        res = json.loads(r.read().decode("utf-8"))["result"]
    assert res["isError"] and "X-Vibeprod-Project" in res["content"][0]["text"]


def test_vision_mcp_deepseek_error_mapped(vision):
    vision["broker"].httpd.router = _json_route(lambda: _configured_cfg(vision["deepseek"].url))
    vision["deepseek"].httpd.router = _json_route(lambda: {"error": {"message": "Invalid API key"}}, status=401)
    (vision["shots"] / "a.png").write_bytes(PNG_BYTES)
    res = _mcp_call(vision["url"], "vision_analyze", {"image": "a.png", "prompt": "x"})
    assert res["isError"] and "API-ключ" in res["content"][0]["text"]

    vision["deepseek"].httpd.router = _json_route(lambda: {"error": {"message": "This model does not support image"}}, status=400)
    res = _mcp_call(vision["url"], "vision_analyze", {"image": "a.png", "prompt": "x"})
    assert res["isError"] and "не поддерживает изображения" in res["content"][0]["text"]
