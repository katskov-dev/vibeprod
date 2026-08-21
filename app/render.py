"""Рендерит настройки из sqlite в нативный opencode-конфиг внутри workspace."""
import json
import re

import yaml

def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "agent"


def _parse_perm(raw):
    """Нормализует permission агента к объекту.

    opencode 1.0.196+ требует объект {edit, bash, webfetch}, старые значения в
    БД/UI — строка "allow"/"ask"/"deny" (иначе ConfigInvalidError, воркер
    отвечает 500 на /config/providers и сессия не стартует).

    Воркер неинтерактивный: разрешения, которые по умолчанию «ask»
    (external_directory, чтение *.env), инжектим как allow — иначе opencode
    ждёт ответа, которого некому дать, и сессия зависает.
    """
    try:
        perm = json.loads(raw or '"allow"')
    except (ValueError, TypeError):
        perm = "allow"
    if isinstance(perm, str):
        perm = {"edit": perm, "bash": perm}
    if isinstance(perm, dict):
        perm.setdefault("external_directory", "allow")
        perm.setdefault("read", "allow")
        return perm
    return {"edit": "allow", "bash": "allow", "external_directory": "allow", "read": "allow"}


def _write_agent_file(wdir, agent):
    fm = {
        "description": agent.get("description") or agent["name"],
        "mode": agent.get("mode") or "primary",
    }
    if agent.get("model"):
        fm["model"] = agent["model"]
    if agent.get("temperature") is not None:
        fm["temperature"] = agent["temperature"]
    if agent.get("variant"):
        fm["variant"] = agent["variant"]
    perm = _parse_perm(agent.get("permission"))
    if perm:
        fm["permission"] = perm
    body = (agent.get("system_prompt") or "").strip()
    if not body:
        body = "You are a helpful assistant."
    memory = (agent.get("memory") or "").strip()
    if memory and agent.get("memory_enabled"):
        body += (
            "\n\n## Память агента\n\n"
            "Твоя долговременная память (сохраняется между сессиями). "
            "В начале новой задачи читай её как контекст и продолжай с места, "
            "где остановился. Обновляй её инструментами memory_get/memory_set.\n\n"
        ) + memory + "\n"
    text = "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n\n" + body + "\n"
    path = wdir / ".opencode" / "agent" / f"{agent['name']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_skill(wdir, skill):
    fm = {
        "name": skill["name"],
        "description": skill.get("description") or skill["name"],
    }
    body = (skill.get("body") or "").strip() or "Skill body."
    text = "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n\n" + body + "\n"
    path = wdir / ".opencode" / "skills" / skill["name"] / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_mcp(mcp_rows):
    mcp = {}
    for m in mcp_rows:
        if not m.get("enabled"):
            continue
        entry = {"type": m["type"], "enabled": True}
        if m["type"] == "local":
            try:
                cmd = json.loads(m.get("command") or "[]")
            except (ValueError, TypeError):
                cmd = []
            if cmd:
                entry["command"] = cmd
            try:
                env = json.loads(m.get("environment") or "{}")
            except (ValueError, TypeError):
                env = {}
            if env:
                entry["environment"] = env
        else:
            entry["url"] = m.get("url") or ""
            try:
                headers = json.loads(m.get("headers") or "{}")
            except (ValueError, TypeError):
                headers = {}
            if headers:
                entry["headers"] = headers
        mcp[m["name"]] = entry
    return mcp


def render_workspace(wdir, agents_rows, mcp_rows, skill_rows, guardian_mcp=None, broker_mcp=None):
    """Пишет opencode.json + .opencode/ в wdir. Возвращает имя default-агента.

    guardian_mcp — синтетическая запись MCP агента-оператора (подмешивается
    только в workspace guardian-сессий, в каталог не попадает).
    broker_mcp — синтетическая запись встроенных инструментов Vibeprod
    (telegram и т.п.), подмешивается в КАЖДУЮ сессию.
    """
    wdir.mkdir(parents=True, exist_ok=True)
    for a in agents_rows:
        _write_agent_file(wdir, a)
    for s in skill_rows:
        _write_skill(wdir, s)

    primary = [a for a in agents_rows if (a.get("mode") or "primary") in ("primary", "all")]
    default = next((a for a in primary if a.get("is_default")), None) or (primary[0] if primary else agents_rows[0])

    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "model": default["model"] or DEFAULT_MODEL_FALLBACK,
        "default_agent": default["name"],
        # opencode 1.0.196+ требует объект (строка "allow" → ConfigInvalidError).
        # external_directory/read по умолчанию «ask» — в неинтерактивном воркере
        # ответить некому, сессия зависает: разрешаем сразу.
        "permission": {"edit": "allow", "bash": "allow", "external_directory": "allow", "read": "allow"},
    }
    synthetic = [m for m in (guardian_mcp, broker_mcp) if m]
    mcp = _build_mcp(list(mcp_rows) + synthetic)
    if mcp:
        cfg["mcp"] = mcp
    (wdir / "opencode.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return default["name"]


DEFAULT_MODEL_FALLBACK = "deepseek/deepseek-chat"
