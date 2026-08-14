"""Telegram-канал: запуск агентов и диалог с ними из мессенджера.

Конфиг — в таблице telegram_config (страница «Автоматизация → Каналы → Telegram»):
токен, разрешённые пользователи, URL веб-интерфейса, вкл/выкл — на проект.
Один токен = один бот; боты стартуют по apply_config() (при старте брокера и
после каждого изменения конфига).

Env-фолбэк для первого запуска без UI: TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS,
TELEGRAM_WEB_URL — если в БД нет ни одной конфигурации, они засевают конфиг
первого проекта.

Каждый чат привязан к сессии (telegram_chats): следующие сообщения продолжают
диалог в той же сессии. Стриминг ответа — подписка на events сессии (тот же
механизм, что кормит WebSocket UI).
"""
import asyncio
import logging
import os
import time

import httpx

from . import db
from . import session_manager
from .streamer import streams

log = logging.getLogger("vibeprod.tg")

ENV_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ENV_ALLOWED = os.environ.get("TELEGRAM_ALLOWED_USERS", "").strip()
ENV_WEB_URL = os.environ.get("TELEGRAM_WEB_URL", "").strip()

API = "https://api.telegram.org"
MSG_LIMIT = 4000
EDIT_INTERVAL = 1.5
POLL_TIMEOUT = 30

_bots = {}        # project_id -> {"task": Task, "token": str}
_chat_tasks = {}  # chat_id -> Task (стрим сессии в чат)

HELP = (
    "Напишите задачу — запущу агента Vibeprod, ответ придёт сюда же.\n"
    "Дальше можно просто переписываться: диалог продолжается в той же сессии.\n\n"
    "Команды:\n"
    "/agents — список агентов\n"
    "/agent N — выбрать агента (номер из /agents или имя)\n"
    "/new — начать новую сессию\n"
    "/abort — остановить генерацию\n"
    "/status — статус текущей сессии\n"
    "/link — ссылка на сессию в веб-интерфейсе\n"
    "/help — эта справка"
)


def _set_status(project_id, connected=None, username=None, error=None):
    fields = ["updated_at=datetime('now')"]
    params = []
    if connected is not None:
        fields.append("connected=?")
        params.append(1 if connected else 0)
    if username is not None:
        fields.append("bot_username=?")
        params.append(username)
    if error is not None:
        fields.append("last_error=?")
        params.append(str(error)[:500])
    params.append(project_id)
    db.execute(f"UPDATE telegram_config SET {', '.join(fields)} WHERE project_id=?", params)


async def start():
    """Старт каналов при запуске брокера (с засевом конфига из env, если БД пуста)."""
    if ENV_TOKEN and not db.query("SELECT project_id FROM telegram_config"):
        first = db.query_one("SELECT id FROM projects ORDER BY id LIMIT 1")
        if first:
            db.execute(
                "INSERT INTO telegram_config(project_id, token, allowed_users, web_url, enabled) VALUES(?,?,?,?,1)",
                (first["id"], ENV_TOKEN, ENV_ALLOWED, ENV_WEB_URL),
            )
    await apply_config()


async def stop():
    for bot in list(_bots.values()):
        bot["task"].cancel()
        try:
            await bot["task"]
        except Exception:
            pass
    _bots.clear()


async def apply_config():
    """Сверяет запущенные боты с конфигами в БД: запускает новые, гасит удалённые, перезапускает со сменённым токеном."""
    configs = {c["project_id"]: c for c in db.query("SELECT * FROM telegram_config WHERE enabled=1 AND token<>''")}
    for pid in list(_bots):
        bot = _bots.get(pid)
        cfg = configs.get(pid)
        if bot and (not cfg or cfg["token"] != bot["token"]):
            bot["task"].cancel()
            _bots.pop(pid, None)
    for pid, cfg in configs.items():
        bot = _bots.get(pid)
        if bot and not bot["task"].done():
            continue
        task = asyncio.create_task(_run_bot(pid, cfg))
        _bots[pid] = {"task": task, "token": cfg["token"]}


async def _run_bot(pid, cfg):
    token = cfg["token"]
    allowed = {int(x) for x in (cfg.get("allowed_users") or "").split(",") if x.strip().isdigit()}
    web_url = (cfg.get("web_url") or "").strip().rstrip("/")
    async with httpx.AsyncClient(base_url=API, timeout=httpx.Timeout(60.0, connect=10.0), trust_env=False) as client:
        while True:
            try:
                await _api(client, token, "deleteWebhook")
                me = (await _api(client, token, "getMe")).get("result") or {}
                _set_status(pid, connected=True, username=me.get("username"), error=None)
                log.info("telegram bot @%s (project %s) started", me.get("username"), pid)
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _set_status(pid, connected=False, error=exc)
                log.warning("telegram setup (project %s): %s", pid, exc)
                await asyncio.sleep(15)
        offset = 0
        while True:
            try:
                resp = await _api(client, token, "getUpdates", offset=offset, timeout=POLL_TIMEOUT, allowed_updates=["message"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("getUpdates (project %s): %s", pid, exc)
                await asyncio.sleep(5)
                continue
            for upd in resp.get("result") or []:
                offset = upd["update_id"] + 1
                try:
                    await _handle(client, token, pid, web_url, allowed, upd.get("message"))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("update %s", upd["update_id"])


async def _api(client, token, method, **params):
    for _ in range(4):
        r = await client.post(f"/bot{token}/{method}", json=params)
        if r.status_code == 429:
            retry = int((r.json().get("parameters") or {}).get("retry_after", 2)) + 1
            await asyncio.sleep(retry)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


async def _handle(client, token, pid, web_url, allowed, msg):
    if not msg or not msg.get("text"):
        return
    chat_id = msg["chat"]["id"]
    user_id = (msg.get("from") or {}).get("id")
    text = (msg.get("text") or "").strip()
    if allowed and user_id not in allowed:
        await _send(client, token, chat_id, "Доступ запрещён.")
        return
    if text.startswith("/"):
        await _command(client, token, pid, web_url, chat_id, text)
    else:
        await _dialog(client, token, pid, web_url, chat_id, text)


def _get_chat(chat_id):
    return db.query_one("SELECT * FROM telegram_chats WHERE chat_id=?", (chat_id,))


def _save_chat(chat_id, project_id=None, session_id=None, agent_id=None, message_id=None, reset_session=False):
    if _get_chat(chat_id):
        if reset_session:
            db.execute(
                "UPDATE telegram_chats SET session_id=NULL, message_id=NULL, "
                "agent_id=COALESCE(?,agent_id), project_id=COALESCE(?,project_id), updated_at=datetime('now') WHERE chat_id=?",
                (agent_id, project_id, chat_id),
            )
        else:
            db.execute(
                "UPDATE telegram_chats SET session_id=COALESCE(?,session_id), agent_id=COALESCE(?,agent_id), "
                "project_id=COALESCE(?,project_id), message_id=COALESCE(?,message_id), updated_at=datetime('now') WHERE chat_id=?",
                (session_id, agent_id, project_id, message_id, chat_id),
            )
    else:
        db.execute(
            "INSERT INTO telegram_chats(chat_id, session_id, agent_id, project_id, message_id) VALUES(?,?,?,?,?)",
            (chat_id, session_id, agent_id, project_id, message_id),
        )


def _default_agent(pid):
    return db.query_one("SELECT * FROM agents WHERE is_guardian=0 AND project_id=? ORDER BY is_default DESC, id LIMIT 1", (pid,))


def _agents_list(pid):
    return db.query("SELECT * FROM agents WHERE is_guardian=0 AND project_id=? ORDER BY is_default DESC, id", (pid,))


async def _command(client, token, pid, web_url, chat_id, text):
    parts = text.split()
    cmd = parts[0].lower().split("@")[0]
    if cmd in ("/start", "/help"):
        await _send(client, token, chat_id, HELP)
        return
    if cmd == "/agents":
        agents = _agents_list(pid)
        if not agents:
            await _send(client, token, chat_id, "Агентов нет — создайте агента в веб-интерфейсе.")
            return
        rows = "\n".join(
            f"{i + 1}. {a['name']} — {a['model']}"
            + (f" — {a['description']}" if a["description"] else "")
            for i, a in enumerate(agents)
        )
        await _send(client, token, chat_id, "Агенты:\n" + rows + "\n\nСменить: /agent N")
        return
    if cmd == "/agent":
        arg = " ".join(parts[1:]).strip().lower()
        agents = _agents_list(pid)
        idx = -1
        if arg.isdigit():
            idx = int(arg) - 1
        elif arg:
            for i, a in enumerate(agents):
                if (a["name"] or "").lower() == arg:
                    idx = i
                    break
        if 0 <= idx < len(agents):
            _save_chat(chat_id, project_id=pid, agent_id=agents[idx]["id"], reset_session=True)
            await _send(client, token, chat_id, f"Агент: {agents[idx]['name']}. Следующее сообщение начнёт новую сессию.")
        else:
            await _send(client, token, chat_id, "Укажите номер или имя: /agent N (список — /agents)")
        return
    if cmd == "/new":
        _save_chat(chat_id, project_id=pid, reset_session=True)
        await _send(client, token, chat_id, "Новая сессия. Следующее сообщение запустит агента заново.")
        return
    if cmd == "/abort":
        row = _get_chat(chat_id)
        s = db.query_one("SELECT * FROM sessions WHERE id=?", (row["session_id"],)) if row and row["session_id"] else None
        if s and s["opencode_session_id"] and s["container_id"]:
            await session_manager.abort_session(s["id"])
            await _send(client, token, chat_id, "Генерация остановлена.")
        else:
            await _send(client, token, chat_id, "Нет активной сессии.")
        return
    if cmd in ("/status", "/link"):
        row = _get_chat(chat_id)
        s = db.query_one("SELECT * FROM sessions WHERE id=?", (row["session_id"],)) if row and row["session_id"] else None
        if not s:
            await _send(client, token, chat_id, "Нет активной сессии.")
            return
        if cmd == "/status":
            msg = f"Агент: {s['agent_name']}\nСтатус: {s['status']}\nМодель: {s['model']}\nСессия: {s['id']}"
            if s["error"]:
                msg += f"\nОшибка: {s['error'][:300]}"
            if web_url:
                msg += f"\n{web_url}/#sessions/{s['id']}"
            await _send(client, token, chat_id, msg)
        else:
            if web_url:
                await _send(client, token, chat_id, f"{web_url}/#sessions/{s['id']}")
            else:
                await _send(client, token, chat_id, "Укажите URL веб-интерфейса в настройках канала.")
        return
    await _send(client, token, chat_id, "Неизвестная команда. Справка: /help")


async def _dialog(client, token, pid, web_url, chat_id, text):
    chat = _get_chat(chat_id)
    agent_id = chat["agent_id"] if chat else None
    agent = db.query_one("SELECT * FROM agents WHERE id=? AND is_guardian=0 AND project_id=?", (agent_id or 0, pid)) or _default_agent(pid)
    if not agent:
        await _send(client, token, chat_id, "Нет доступных агентов — создайте агента в веб-интерфейсе.")
        return
    sid = None
    if chat and chat["session_id"]:
        s = db.query_one("SELECT * FROM sessions WHERE id=?", (chat["session_id"],))
        if s and s["status"] in ("running", "completed", "queued", "starting"):
            sid = s["id"]
    if not sid:
        try:
            sid = _new_session(chat_id, pid, agent, text)
        except RuntimeError as exc:
            await _send(client, token, chat_id, str(exc))
            return
    else:
        s = db.query_one("SELECT * FROM sessions WHERE id=?", (sid,))
        if s and s["status"] in ("queued", "starting"):
            for _ in range(10):
                await asyncio.sleep(3)
                s = db.query_one("SELECT * FROM sessions WHERE id=?", (sid,))
                if s["status"] not in ("queued", "starting"):
                    break
        try:
            await session_manager.send_prompt(sid, text)
        except RuntimeError as exc:
            log.info("tg %s: %s — новая сессия", chat_id, exc)
            try:
                sid = _new_session(chat_id, pid, agent, text)
            except RuntimeError as exc2:
                await _send(client, token, chat_id, str(exc2))
                return
    await _stream_to_chat(client, token, web_url, chat_id, sid)


def _new_session(chat_id, pid, agent, text):
    try:
        sid = session_manager.create_session(
            agent["id"], text[:60], text, source="telegram", project_id=pid
        )
    except ValueError as exc:
        raise RuntimeError(f"не удалось создать сессию: {exc}")
    _save_chat(chat_id, project_id=pid, session_id=sid, agent_id=agent["id"])
    from .main import spawn_start

    spawn_start(sid, text)
    return sid


async def _stream_to_chat(client, token, web_url, chat_id, sid):
    prev = _chat_tasks.pop(chat_id, None)
    if prev:
        prev.cancel()
    task = asyncio.create_task(_watch_chat(client, token, web_url, chat_id, sid))
    _chat_tasks[chat_id] = task


async def _watch_chat(client, token, web_url, chat_id, sid):
    q = await streams.subscribe(sid)
    parts = {}
    tool_note = ""
    last_sent = None
    last_edit = 0.0
    msg_id = None
    try:
        await _api(client, token, "sendChatAction", chat_id=chat_id, action="typing")
        msg_id = await _send(client, token, chat_id, "…")
        if msg_id:
            _save_chat(chat_id, message_id=msg_id)
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=5)
            except TimeoutError:
                continue
            if event.get("type") == "event":
                ev = event.get("event") or {}
                kind = ev.get("type")
                props = ev.get("properties") or {}
                if kind == "message.part.delta":
                    pid = props.get("partID")
                    if pid:
                        parts[pid] = parts.get(pid, "") + (props.get("delta") or "")
                elif kind == "message.part.updated":
                    part = props.get("part") or {}
                    if part.get("type") == "text":
                        if part.get("text"):
                            parts[part["id"]] = part["text"]
                    elif part.get("type") == "tool":
                        status = (part.get("state") or {}).get("status")
                        if status in ("pending", "running"):
                            tool_note = f"[{part.get('tool') or 'инструмент'}] выполняется"
                        elif status == "completed":
                            tool_note = f"[{part.get('tool') or 'инструмент'}] готово"
                        else:
                            tool_note = ""
                now = time.monotonic()
                if msg_id and now - last_edit >= EDIT_INTERVAL:
                    text = _full_text(parts) + (f"\n\n{tool_note}" if tool_note else "")
                    if text and text != last_sent:
                        last_sent = text
                        last_edit = now
                        await _edit(client, token, chat_id, msg_id, text[:MSG_LIMIT])
                continue
            if event.get("type") == "done":
                await _finish(client, token, web_url, chat_id, msg_id, sid, event, parts)
                return
            if event.get("type") == "status" and event.get("status") in ("failed", "expired"):
                await _finish(client, token, web_url, chat_id, msg_id, sid, event, parts)
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("tg stream %s", sid)
        try:
            await _send(client, token, chat_id, f"Ошибка стриминга сессии {sid}.")
        except Exception:
            pass
    finally:
        streams.unsubscribe(sid, q)
        _chat_tasks.pop(chat_id, None)


async def _finish(client, token, web_url, chat_id, msg_id, sid, event, parts):
    text = _full_text(parts) or "(агент не выдал текста)"
    status = event.get("status")
    if status == "failed":
        text += f"\n\nОшибка: {event.get('error') or '—'}"
    elif status == "expired":
        text += "\n\nСессия истекла."
    else:
        text += f"\n\nСессия: {sid}"
    if web_url:
        text += f"\n{web_url}/#sessions/{sid}"
    chunks = _split(text)
    first = chunks[0] if chunks else text
    if msg_id:
        await _edit(client, token, chat_id, msg_id, first)
    else:
        await _send(client, token, chat_id, first)
    for chunk in chunks[1:]:
        await _send(client, token, chat_id, chunk)


def _full_text(parts):
    return "\n\n".join(t for t in parts.values() if t)


def _split(text, limit=MSG_LIMIT):
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


async def _send(client, token, chat_id, text, reply_to=None):
    last = None
    for chunk in _split(text):
        r = await _api(client, token, "sendMessage", chat_id=chat_id, text=chunk, disable_web_page_preview=True, reply_to_message_id=reply_to)
        last = (r.get("result") or {}).get("message_id")
    return last


async def _edit(client, token, chat_id, message_id, text):
    try:
        await _api(client, token, "editMessageText", chat_id=chat_id, message_id=message_id, text=text, disable_web_page_preview=True)
    except Exception as exc:
        log.debug("edit %s: %s", message_id, exc)
