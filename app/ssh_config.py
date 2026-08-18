"""SSH-доступ с белым списком команд.

Общая логика брокера и ssh-MCP контейнера:
- рендеринг шаблона команды с валидацией параметров (regex, экранирование);
- сборка конфига для контейнера (серверы с ключами + разрешённые команды);
- TOFU-сохранение ключа хоста при проверке подключения в админке.
"""
import base64
import hashlib
import json
import re
import shlex

PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
# Параметр без явного regex: строгий набор символов без пробелов и метасимволов shell.
DEFAULT_ARG_RE = r"^[A-Za-z0-9._:@/\-]{1,128}$"

MAX_OUTPUT = 200_000
OUTPUT_TAIL = 30_000


class SshError(Exception):
    pass


def arg_regexes(raw):
    """JSON-словарь {параметр: regex} из колонки arg_regex."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        raise SshError("arg_regex: корректный JSON, например {\"service\": \"^[a-z0-9-]+$\"}")
    if not isinstance(data, dict):
        raise SshError("arg_regex: JSON-объект {параметр: regex}")
    out = {}
    for k, v in data.items():
        try:
            re.compile(str(v))
        except re.error as exc:
            raise SshError(f"arg_regex.{k}: некорректное регулярное выражение ({exc})")
        out[str(k)] = str(v)
    return out


def render_command(command, arg_regex, params):
    """Подставляет параметры в шаблон команды.

    Каждый параметр валидируется регулярным выражением (из arg_regex или
    DEFAULT_ARG_RE) и экранируется shlex.quote. Лишние и отсутствующие
    параметры — ошибка. Возвращает командную строку для exec (без shell
    со стороны вызывающего — строка выполняется удалённым сервером как есть).
    """
    if not isinstance(params, dict):
        raise SshError("params: объект {имя: значение}")
    regexes = arg_regexes(arg_regex)
    used = set()

    def sub(match):
        name = match.group(1)
        used.add(name)
        if name not in params:
            raise SshError(f"не передан параметр {{{name}}}")
        value = str(params[name])
        pattern = regexes.get(name) or DEFAULT_ARG_RE
        if not re.fullmatch(pattern, value):
            raise SshError(f"параметр {name}={value!r} не прошёл валидацию (regex: {pattern})")
        return shlex.quote(value)

    rendered = PLACEHOLDER_RE.sub(sub, command or "")
    extra = [k for k in params if k not in used]
    if extra:
        raise SshError(f"лишние параметры: {', '.join(sorted(extra))}")
    if not rendered.strip():
        raise SshError("команда пустая")
    return rendered


def known_hosts_line(host, port, key):
    """Строка формата known_hosts: hostname keytype base64 (OpenSSH-текст ключа)."""
    hostpart = f"[{host}]:{port}" if port and int(port) != 22 else host
    return f"{hostpart} {key.export_public_key('openssh').decode()}"


def known_hosts_list(raw):
    return [line.strip() for line in (raw or "").splitlines() if line.strip()]


def known_hosts_obj(raw):
    """asyncssh.SSHKnownHosts из сохранённых строк (None, если пусто)."""
    lines = known_hosts_list(raw)
    if not lines:
        return None
    import asyncssh

    kh = asyncssh.SSHKnownHosts()
    kh.load("\n".join(lines) + "\n")
    return kh


def key_fingerprint(key):
    """SHA256-отпечаток ключа в формате OpenSSH (SHA256:base64)."""
    text = key.export_public_key("openssh").decode().split()
    blob = base64.b64decode(text[1])
    digest = hashlib.sha256(blob).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def agent_config(project_id):
    """Конфиг для ssh-MCP контейнера: включённые серверы (с ключами) и команды."""
    from . import db

    servers = db.query(
        "SELECT * FROM ssh_servers WHERE project_id=? AND enabled=1 ORDER BY id",
        (int(project_id),),
    )
    commands = db.query(
        "SELECT c.* FROM ssh_commands c JOIN ssh_servers s ON s.id=c.server_id "
        "WHERE s.project_id=? AND s.enabled=1 AND c.enabled=1 ORDER BY c.id",
        (int(project_id),),
    )
    return {"servers": [dict(s) for s in servers], "commands": [dict(c) for c in commands]}
