#!/usr/bin/env python3
"""SSH MCP: выполнение команд из белого списка на серверах проекта.

Протокол — streamable HTTP поверх JSON-RPC (как files-mcp). Конфиг (серверы,
ключи, белый список команд) тянется с брокера на каждый вызов инструмента:
источник правды — админка (Автоматизация → SSH).

Безопасность:
- выполняются только команды из белого списка (шаблоны с {параметрами});
- каждый параметр валидируется regex'ом и экранируется shlex.quote;
- ключ хоста проверяется по known_hosts (TOFU-сохранение в админке);
- таймаут на каждую команду; логи пишутся в брокер (таблица ssh_runs).
"""
import asyncio
import json
import logging
import os
import re
import shlex
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import asyncssh

PORT = int(os.environ.get("SSH_MCP_PORT", "8933"))
FALLBACK_BROKER_URL = os.environ.get("VIBEPROD_BROKER_URL", "http://host.docker.internal:8000")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("vibeprod.ssh-mcp")

PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
DEFAULT_ARG_RE = r"^[A-Za-z0-9._:@/\-]{1,128}$"
MAX_OUTPUT = 200_000
OUTPUT_TAIL = 30_000


class ToolError(Exception):
    pass


def arg_regexes(raw):
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        raise ToolError("arg_regex: некорректный JSON в настройках команды")
    return {str(k): str(v) for k, v in (data or {}).items()} if isinstance(data, dict) else {}


def render_command(command, arg_regex, params):
    if not isinstance(params, dict):
        raise ToolError("params: объект {имя: значение}")
    regexes = arg_regexes(arg_regex)
    used = set()

    def sub(match):
        name = match.group(1)
        used.add(name)
        if name not in params:
            raise ToolError(f"не передан параметр {{{name}}}")
        value = str(params[name])
        pattern = regexes.get(name) or DEFAULT_ARG_RE
        if not re.fullmatch(pattern, value):
            raise ToolError(f"параметр {name}={value!r} не прошёл валидацию (regex: {pattern})")
        return shlex.quote(value)

    rendered = PLACEHOLDER_RE.sub(sub, command or "")
    extra = [k for k in params if k not in used]
    if extra:
        raise ToolError(f"лишние параметры: {', '.join(sorted(extra))}")
    if not rendered.strip():
        raise ToolError("команда пустая")
    return rendered


# ---------- конфиг с брокера ----------

_config_cache = {}
CACHE_TTL = 15


def ctx_of(headers):
    project = (headers.get("X-Vibeprod-Project") or "").strip()
    token = (headers.get("X-Vibeprod-Token") or "").strip()
    broker = (headers.get("X-Broker-Url") or FALLBACK_BROKER_URL).strip().rstrip("/")
    if not project.isdigit():
        raise ToolError(
            "Не определён проект воркера (заголовок X-Vibeprod-Project). "
            "Подключите MCP «ssh» из каталога к агенту и перезапустите сессию."
        )
    return {"project": int(project), "token": token, "broker": broker}


def fetch_config(ctx):
    key = (ctx["project"], ctx["broker"])
    cached = _config_cache.get(key)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]
    url = f"{ctx['broker']}/api/ssh/config?project_id={ctx['project']}"
    req = urllib.request.Request(url, headers={"X-Vibeprod-Token": ctx["token"]})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ToolError(f"брокер не отдал конфиг SSH (HTTP {exc.code}): {exc.reason}")
    except Exception as exc:
        raise ToolError(f"брокер недоступен ({url}): {exc}")
    _config_cache[key] = (time.time(), data)
    return data


def _find_server(cfg, ref):
    ref = str(ref or "").strip()
    if not ref:
        raise ToolError("server обязателен: id или имя сервера (см. ssh_list_servers)")
    for s in cfg.get("servers") or []:
        if ref == str(s.get("id")) or ref == s.get("name"):
            return s
    raise ToolError(
        f"сервер {ref!r} не найден среди доступных: "
        + ", ".join(f"{s['name']}(id={s['id']})" for s in cfg.get("servers") or [])
    )


def _find_command(cfg, server, name):
    name = str(name or "").strip()
    if not name:
        raise ToolError("command обязателен: имя команды (см. ssh_list_servers)")
    for c in cfg.get("commands") or []:
        if c["server_id"] == server["id"] and c["name"] == name:
            return c
    raise ToolError(f"команда {name!r} не разрешена для сервера «{server['name']}»")


def _known_hosts(server):
    lines = [ln.strip() for ln in (server.get("known_hosts") or "").splitlines() if ln.strip()]
    if not lines:
        raise ToolError(
            f"Хост {server['host']} не проверен: нажмите «Проверить подключение» "
            f"для сервера «{server['name']}» в админке (Автоматизация → SSH)."
        )
    return lines


def _known_hosts_obj(server):
    lines = _known_hosts(server)
    kh = asyncssh.SSHKnownHosts()
    kh.load("\n".join(lines) + "\n")
    return kh


def _connect_kwargs(server):
    kwargs = {
        "host": server["host"],
        "port": int(server.get("port") or 22),
        "username": server["username"],
        "known_hosts": _known_hosts_obj(server),
        "connect_timeout": 20,
        "encoding": None,  # сырые байты из stdout/stderr (декодируем сами)
    }
    if (server.get("auth_type") or "key") == "password":
        if not server.get("password"):
            raise ToolError(f"у сервера «{server['name']}» не задан пароль")
        kwargs["password"] = server["password"]
    else:
        if not server.get("private_key"):
            raise ToolError(f"у сервера «{server['name']}» не задан приватный ключ")
        try:
            kwargs["client_keys"] = [asyncssh.import_private_key(server["private_key"])]
        except asyncssh.Error as exc:
            raise ToolError(f"приватный ключ сервера «{server['name']}» не читается: {exc}")
    return kwargs


async def _exec(server, command_row, params, timeout):
    rendered = render_command(command_row["command"], command_row.get("arg_regex"), params)
    conn = await asyncssh.connect(**_connect_kwargs(server))
    try:
        process = await conn.create_process(rendered)

        async def read(stream):
            chunks = []
            while True:
                data = await stream.read(65536)
                if not data:
                    break
                chunks.append(data)
            return b"".join(chunks)

        out_task = asyncio.create_task(read(process.stdout))
        err_task = asyncio.create_task(read(process.stderr))
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except (TimeoutError, asyncio.TimeoutError):
                process.kill()
                await process.wait()
            raise ToolError(f"таймаут {timeout}с — команда прервана")
        stdout, stderr = await out_task, await err_task
        code = process.exit_status if process.exit_status is not None else (process.exit_signal or -1)
        return code, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")
    finally:
        conn.close()


def _combine(code, stdout, stderr):
    out = stdout
    if stderr:
        out += ("\n" if out and not out.endswith("\n") else "") + "--- stderr ---\n" + stderr
    if not out.strip():
        out = "(пустой вывод)"
    return code, out


# ---------- лог запусков в брокере ----------

def post_run(ctx, run):
    url = f"{ctx['broker']}/api/ssh/runs?project_id={ctx['project']}"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={"Content-Type": "application/json", "X-Vibeprod-Token": ctx["token"]},
        data=json.dumps(run).encode("utf-8"),
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_runs(ctx, server_id=None, command_name=None, limit=20):
    q = urllib.parse.urlencode(
        {
            "project_id": ctx["project"],
            "server_id": server_id or "",
            "command_name": command_name or "",
            "limit": max(1, min(int(limit or 20), 200)),
        }
    )
    url = f"{ctx['broker']}/api/ssh/runs?{q}"
    req = urllib.request.Request(url, headers={"X-Vibeprod-Token": ctx["token"]})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------- инструменты ----------

async def h_ssh_list_servers(args, ctx):
    cfg = fetch_config(ctx)
    out = []
    for s in cfg.get("servers") or []:
        commands = [
            {
                "name": c["name"],
                "description": c.get("description") or "",
                "template": c["command"],
                "arg_regex": c.get("arg_regex") or "",
                "timeout": c.get("timeout") or 60,
            }
            for c in cfg.get("commands") or []
            if c["server_id"] == s["id"]
        ]
        out.append(
            {
                "id": s["id"],
                "name": s["name"],
                "host": s["host"],
                "port": s.get("port") or 22,
                "username": s["username"],
                "commands": commands,
            }
        )
    if not out:
        return {"servers": [], "note": "SSH-серверы не настроены: админка → Автоматизация → SSH."}
    return {"servers": out}


async def h_ssh_run(args, ctx):
    cfg = fetch_config(ctx)
    server = _find_server(cfg, args.get("server"))
    command_row = _find_command(cfg, server, args.get("command"))
    params = args.get("params") or {}
    try:
        timeout = int(args.get("timeout") or command_row.get("timeout") or 60)
    except (TypeError, ValueError):
        raise ToolError("timeout: целое число секунд")
    timeout = max(1, min(timeout, 3600))
    log.info("ssh_run server=%s command=%s", server["name"], command_row["name"])
    started = time.time()
    try:
        code, output = _combine(*await _exec(server, command_row, params, timeout))
        status = "ok" if code == 0 else "error"
    except ToolError:
        raise
    except asyncssh.HostKeyMismatch:
        raise ToolError(f"ключ хоста {server['host']} изменился! Возможен MITM — проверьте сервер в админке.")
    except asyncssh.Error as exc:
        raise ToolError(f"SSH ({server['host']}): {exc}")
    except (OSError, TimeoutError) as exc:
        raise ToolError(f"соединение с {server['host']}: {exc}")
    duration_ms = int((time.time() - started) * 1000)
    run = {
        "project_id": ctx["project"],
        "server_id": server["id"],
        "command_id": command_row["id"],
        "command_name": command_row["name"],
        "params": json.dumps(params, ensure_ascii=False),
        "status": status,
        "exit_code": code,
        "output": output[:MAX_OUTPUT],
        "duration_ms": duration_ms,
    }
    note = ""
    try:
        post_run(ctx, run)
    except Exception as exc:
        note = f"\n(не удалось сохранить лог в брокере: {exc})"
    tail = output[-OUTPUT_TAIL:] if len(output) > OUTPUT_TAIL else output
    return {
        "server": server["name"],
        "command": command_row["name"],
        "exit_code": code,
        "status": status,
        "duration_ms": duration_ms,
        "output": tail + note,
    }


def h_ssh_logs(args, ctx):
    cfg = fetch_config(ctx)
    server_id = None
    if args.get("server") is not None:
        server_id = _find_server(cfg, args.get("server"))["id"]
    rows = fetch_runs(ctx, server_id=server_id, command_name=args.get("command"), limit=args.get("limit"))
    if not rows:
        return {"runs": [], "note": "Запусков пока нет."}
    for r in rows:
        out = r.get("output") or ""
        r["output_tail"] = out[-OUTPUT_TAIL:] if len(out) > OUTPUT_TAIL else out
    return {"runs": rows}


CALL = {
    "ssh_list_servers": h_ssh_list_servers,
    "ssh_run": h_ssh_run,
    "ssh_logs": h_ssh_logs,
}

TOOLS = [
    {
        "name": "ssh_list_servers",
        "description": "Список доступных SSH-серверов проекта и разрешённых команд на каждом: "
        "id, имя, хост, шаблон команды с {параметрами} и их допустимыми значениями (arg_regex).",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ssh_run",
        "description": "Выполнить разрешённую команду на SSH-сервере (белый список из админки). "
        "server — id или имя сервера из ssh_list_servers; command — имя команды; "
        "params — объект значений параметров шаблона, например {\"service\": \"nginx\", \"lines\": 50}. "
        "Только для чтения: журналы, статусы. Возвращает вывод и код возврата.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "id или имя сервера (из ssh_list_servers)"},
                "command": {"type": "string", "description": "имя команды из белого списка"},
                "params": {"type": "object", "description": "значения параметров шаблона команды"},
                "timeout": {"type": "integer", "description": "таймаут в секундах (по умолчанию — из настроек команды)"},
            },
            "required": ["server", "command"],
        },
    },
    {
        "name": "ssh_logs",
        "description": "История запусков SSH-команд (журнал выполнения): статус, код возврата, вывод. "
        "server/command — фильтры (необязательно), limit — сколько последних (по умолчанию 20).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "id или имя сервера (необязательно)"},
                "command": {"type": "string", "description": "имя команды (необязательно)"},
                "limit": {"type": "integer", "description": "сколько последних запусков (по умолчанию 20)"},
            },
            "required": [],
        },
    },
]


# ---------- streamable HTTP JSON-RPC ----------

def ok(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def err(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def tool_result(text, is_error=False):
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def dispatch_one(msg, headers):
    if not isinstance(msg, dict):
        return err(None, -32700, "Parse error")
    msg_id = msg.get("id")
    method = msg.get("method") or ""
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion") or "2024-11-05"
        return ok(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "vibeprod-ssh", "version": "1.0.0"},
        })
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return ok(msg_id, {})
    if method == "tools/list":
        return ok(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        fn = CALL.get(params.get("name"))
        if not fn:
            return ok(msg_id, tool_result(f"неизвестный инструмент: {params.get('name')}", True))
        try:
            ctx = ctx_of(headers)
            result = fn(params.get("arguments") or {}, ctx)
            if asyncio.iscoroutine(result):
                result = asyncio.run(result)
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
            return ok(msg_id, tool_result(text))
        except ToolError as exc:
            return ok(msg_id, tool_result(str(exc), True))
        except Exception as exc:
            log.exception("tool %s", params.get("name"))
            return ok(msg_id, tool_result(f"{type(exc).__name__}: {exc}", True))
    return err(msg_id, -32601, f"Method not found: {method}")


def dispatch(payload, headers):
    if isinstance(payload, list):
        responses = [dispatch_one(m, headers) for m in payload]
        responses = [r for r in responses if r is not None]
        return responses if responses else None
    return dispatch_one(payload, headers)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.debug(fmt % args)

    def _send(self, status, body=None, extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if data:
            self.wfile.write(data)

    def do_GET(self):
        self._send(405, extra={"Allow": "POST"})

    def do_DELETE(self):
        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            self._send(400, json.dumps(err(None, -32700, "Parse error")))
            return
        headers = {k.lower(): v for k, v in self.headers.items()}
        try:
            responses = dispatch(payload, headers)
        except Exception as exc:
            log.exception("dispatch")
            self._send(500, json.dumps(err(None, -32603, f"Internal error: {exc}")))
            return
        if responses is None or (isinstance(responses, list) and not responses):
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(200, json.dumps(responses, ensure_ascii=False))


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log.info("vibeprod-ssh-mcp listening on :%s", PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
