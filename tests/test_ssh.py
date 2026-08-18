"""Тесты SSH-подсистемы: рендеринг шаблонов команд и API белого списка.

Без docker и SSH-серверов: проверяем валидацию/экранирование параметров,
CRUD серверов и команд, доступ контейнера ssh-MCP по токену проекта.
"""
import asyncio
import importlib
import importlib.util
import socket
from pathlib import Path

import asyncssh
import pytest
from fastapi.testclient import TestClient

from app.ssh_config import SshError, arg_regexes, key_fingerprint, known_hosts_line, render_command

_SSH_MCP_PATH = Path(__file__).resolve().parent.parent / "mcp" / "ssh" / "server.py"


def _ssh_mcp_module():
    spec = importlib.util.spec_from_file_location("vibeprod_ssh_mcp", _SSH_MCP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- рендеринг шаблонов ----------


def test_render_simple():
    rendered = render_command(
        "journalctl -u {service} -n {lines}",
        '{"lines": "^[1-9][0-9]{0,3}$"}',
        {"service": "nginx", "lines": "50"},
    )
    assert rendered == "journalctl -u nginx -n 50"


def test_render_quotes_params():
    rendered = render_command("echo {arg}", '{"arg": "^[^\\n]{1,40}$"}', {"arg": "hello world"})
    assert rendered == "echo 'hello world'"


def test_render_rejects_injection_by_default_regex():
    with pytest.raises(SshError, match="не прошёл валидацию"):
        render_command("echo {arg}", "", {"arg": "x; rm -rf /"})


def test_render_rejects_custom_regex():
    with pytest.raises(SshError, match="не прошёл валидацию"):
        render_command("journalctl -u {service}", '{"service": "^[a-z-]+$"}', {"service": "bad name"})


def test_render_missing_param():
    with pytest.raises(SshError, match="не передан параметр"):
        render_command("journalctl -u {service}", "", {})


def test_render_extra_params():
    with pytest.raises(SshError, match="лишние параметры"):
        render_command("echo {a}", "", {"a": "x", "b": "y"})


def test_render_bad_regex():
    with pytest.raises(SshError, match="некорректное регулярное выражение"):
        render_command("echo {a}", '{"a": "["}', {"a": "x"})


def test_arg_regexes_bad_json():
    with pytest.raises(SshError, match="JSON"):
        arg_regexes("{not json")


# ---------- known_hosts ----------


def test_known_hosts_line_and_fingerprint():
    key = asyncssh.generate_private_key("ssh-ed25519")
    line = known_hosts_line("example.com", 22, key)
    parts = line.split()
    assert parts[0] == "example.com"
    assert parts[1] == "ssh-ed25519"
    with_port = known_hosts_line("example.com", 2222, key)
    assert with_port.startswith("[example.com]:2222 ")
    assert key_fingerprint(key).startswith("SHA256:")


# ---------- HTTP-контекст и диспатч ----------


def test_ctx_from_headers():
    mod = _ssh_mcp_module()
    ctx = mod.ctx_of(
        {
            "x-vibeprod-project": "42",
            "x-vibeprod-token": "tok",
            "x-broker-url": "http://broker:8000/",
        }
    )
    assert ctx == {"project": 42, "token": "tok", "broker": "http://broker:8000"}


def test_ctx_missing_project():
    mod = _ssh_mcp_module()
    with pytest.raises(mod.ToolError, match="X-Vibeprod-Project"):
        mod.ctx_of({})


def test_dispatch_tools_call_requires_project():
    mod = _ssh_mcp_module()
    res = mod.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "ssh_list_servers", "arguments": {}}},
        {},
    )
    assert res["result"]["isError"] is True
    assert "проект" in res["result"]["content"][0]["text"]


# ---------- выполнение команд (локальный asyncssh-сервер) ----------


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class _NoAuthServer(asyncssh.SSHServer):
    def begin_auth(self, username):
        return False

    def session_requested(self):
        return True


def _echo_process(process):
    """Возвращает переданную команду как stdout и завершается с кодом 0."""
    process.stdout.write("echo:" + process.command)
    process.stdout.write_eof()
    process.exit(0)


async def _hang_process(process):
    """Команда «висит», пока клиент не пришлёт сигнал (SIGTERM)."""
    try:
        await process.stdin.read()
    except asyncssh.SignalReceived:
        process.exit(128 + 15)


def test_exec_whitelist_end_to_end():
    mod = _ssh_mcp_module()
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    server_key = asyncssh.generate_private_key("ssh-ed25519")
    port = _free_port()

    async def run():
        listener = await asyncssh.listen(
            "127.0.0.1", port, server_factory=_NoAuthServer, server_host_keys=[server_key], process_factory=_echo_process
        )
        try:
            server = {
                "id": 1,
                "name": "test",
                "host": "127.0.0.1",
                "port": port,
                "username": "tester",
                "auth_type": "key",
                "private_key": client_key.export_private_key("openssh").decode(),
                "known_hosts": known_hosts_line("127.0.0.1", port, server_key),
            }
            command = {"command": "echo {x}", "arg_regex": '{"x": "^[^\\n]{1,40}$"}'}
            code, stdout, stderr = await mod._exec(server, command, {"x": "hello world"}, 30)
            assert code == 0
            assert stdout.strip() == "echo:echo 'hello world'"
            assert not stderr.strip()

            # параметр, не прошедший regex — команда не выполняется
            with pytest.raises(mod.ToolError, match="не прошёл валидацию"):
                await mod._exec(server, {"command": "echo {x}", "arg_regex": ""}, {"x": "bad; rm -rf /"}, 30)

            # чужой ключ хоста — подключение отклоняется
            other_key = asyncssh.generate_private_key("ssh-ed25519")
            server["known_hosts"] = known_hosts_line("127.0.0.1", port, other_key)
            with pytest.raises(asyncssh.Error):
                await mod._exec(server, command, {"x": "hi"}, 30)
        finally:
            listener.close()
            await listener.wait_closed()

    asyncio.run(run())


def test_exec_timeout():
    mod = _ssh_mcp_module()
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    server_key = asyncssh.generate_private_key("ssh-ed25519")
    port = _free_port()

    async def run():
        listener = await asyncssh.listen(
            "127.0.0.1", port, server_factory=_NoAuthServer, server_host_keys=[server_key], process_factory=_hang_process
        )
        try:
            server = {
                "id": 1,
                "name": "test",
                "host": "127.0.0.1",
                "port": port,
                "username": "tester",
                "auth_type": "key",
                "private_key": client_key.export_private_key("openssh").decode(),
                "known_hosts": known_hosts_line("127.0.0.1", port, server_key),
            }
            with pytest.raises(mod.ToolError, match="таймаут"):
                await mod._exec(server, {"command": "sleep 30", "arg_regex": ""}, {}, 1)
        finally:
            listener.close()
            await listener.wait_closed()

    asyncio.run(run())


# ---------- API ----------


@pytest.fixture()
def client(tmp_path, monkeypatch):
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


def _login(client):
    r = client.post("/api/login", json={"login": "admin", "password": "secret"})
    assert r.status_code == 200, r.text
    return client


def _project_and_token():
    from app import db

    row = db.query_one("SELECT id, file_token FROM projects ORDER BY id LIMIT 1")
    return row["id"], row["file_token"]


def _create_server(client):
    key = asyncssh.generate_private_key("ssh-ed25519")
    pem = key.export_private_key("openssh").decode()
    r = client.post(
        "/api/ssh/servers",
        json={"name": "prod", "host": "example.com", "port": 22, "username": "deploy", "private_key": pem},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_ssh_config_requires_token(client):
    pid, _ = _project_and_token()
    r = client.get(f"/api/ssh/config?project_id={pid}")
    assert r.status_code in (401, 403)


def test_ssh_config_denies_wrong_token(client):
    pid, _ = _project_and_token()
    r = client.get(f"/api/ssh/config?project_id={pid}", headers={"X-Vibeprod-Token": "wrong"})
    assert r.status_code in (401, 403)


def test_server_crud_and_agent_config(client):
    _login(client)
    pid, token = _project_and_token()
    srv = _create_server(client)
    assert srv["has_key"] and "private_key" not in srv

    servers = client.get(f"/api/ssh/servers?project_id={pid}").json()
    assert len(servers) == 1 and servers[0]["name"] == "prod"

    r = client.get(f"/api/ssh/config?project_id={pid}", headers={"X-Vibeprod-Token": token})
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["servers"][0]["private_key"].startswith("-----BEGIN")
    assert "password" not in cfg["servers"][0] or not cfg["servers"][0]["password"]

    r = client.post(
        "/api/ssh/commands",
        json={
            "server_id": srv["id"],
            "name": "logs",
            "command": "journalctl -u {service} -n {lines} --no-pager",
            "arg_regex": '{"service": "^[a-z0-9-]{1,40}$", "lines": "^[1-9][0-9]{0,3}$"}',
            "timeout": 30,
        },
    )
    assert r.status_code == 200, r.text
    cmd = r.json()
    assert cmd["name"] == "logs"

    cfg = client.get(f"/api/ssh/config?project_id={pid}", headers={"X-Vibeprod-Token": token}).json()
    assert cfg["commands"][0]["command"] == "journalctl -u {service} -n {lines} --no-pager"


def test_command_template_check(client):
    _login(client)
    pid, _ = _project_and_token()
    r = client.post(
        "/api/ssh/commands/check",
        json={"command": "echo {a}", "arg_regex": '{"a": "^[^\\n]{1,10}$"}', "params": {"a": "x y"}},
    )
    assert r.status_code == 200 and r.json()["rendered"] == "echo 'x y'"
    r = client.post(
        "/api/ssh/commands/check",
        json={"command": "echo {a}", "arg_regex": "", "params": {"a": "x; rm -rf /"}},
    )
    assert r.status_code == 400


def test_runs_token_flow(client):
    _login(client)
    pid, token = _project_and_token()
    srv = _create_server(client)
    r = client.post(
        "/api/ssh/runs?project_id={pid}",
        headers={"X-Vibeprod-Token": token},
        json={
            "project_id": pid,
            "server_id": srv["id"],
            "command_name": "logs",
            "params": '{"service": "nginx"}',
            "status": "ok",
            "exit_code": 0,
            "output": "nginx logs",
            "duration_ms": 12,
        },
    )
    assert r.status_code == 200, r.text
    runs = client.get(f"/api/ssh/runs?project_id={pid}&server_id={srv['id']}").json()
    assert len(runs) == 1 and runs[0]["output"] == "nginx logs"

    client.cookies.clear()
    r = client.post(
        "/api/ssh/runs?project_id={pid}",
        headers={"X-Vibeprod-Token": "wrong"},
        json={"project_id": pid, "output": "x"},
    )
    assert r.status_code in (401, 403)
