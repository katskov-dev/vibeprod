"""Оркестрация сеанса: workspace -> контейнер -> opencode сессия -> стрим."""
import asyncio
import os
import secrets
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import db
from . import events
from . import files_store
from .docker_runner import (
    NETWORK_HINT,
    container_exists,
    container_logs,
    container_status,
    ensure_running,
    get_host_port,
    kill_worker,
    pause_worker,
    run_worker,
)
from .opencode_client import OpencodeClient, wait_healthy, worker_urls
from .render import render_workspace
from .streamer import streams

DATA_DIR = Path(os.environ.get("VIBEPROD_DATA_DIR", Path(__file__).resolve().parent.parent / "data")).resolve()
WORKSPACES = DATA_DIR / "workspaces"
# Хост-путь к data (для bind-mount в воркеры): если брокер сам в докере,
# внутриконтейнерный путь докеру не виден — задаётся через env.
HOST_DATA_DIR = Path(os.environ.get("VIBEPROD_HOST_DATA_DIR", DATA_DIR))
IDLE_TTL = int(os.environ.get("VIBEPROD_IDLE_TTL_MIN", "60")) * 60
# Двухуровневый idle: после SUSPEND_IDLE контейнер паузится (0 CPU, RAM держится),
# после IDLE_TTL — убивается и лежит на диске до следующего обращения.
SUSPEND_IDLE = int(os.environ.get("VIBEPROD_SUSPEND_IDLE_MIN", "15")) * 60
# Интервал фонового цикла (dispatch очереди + idle-тиры).
LOOP_INTERVAL = int(os.environ.get("VIBEPROD_CLEANUP_INTERVAL_S", "10"))
# Квота параллельно живущих воркеров: сверх лимита сессии ждут в status='queued'.
# 0 — без ограничений (поведение как раньше).
MAX_CONCURRENT = int(os.environ.get("VIBEPROD_MAX_CONCURRENT_SESSIONS", "0") or 0)

# Троттлинг last_activity при активной генерации (иначе долгий прогон
# выглядит «простаивающим» и уходит в pause/expire прямо посреди работы).
_touch_at = {}


def storage_name(sid):
    return f"vibeprod-oc-{sid}"


def ws_dir(sid):
    return WORKSPACES / sid


def host_ws_dir(sid):
    return HOST_DATA_DIR / "workspaces" / sid


KEY_HINTS = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_GENERATIVE_AI_API_KEY",
    "groq": "GROQ_API_KEY",
    "xai": "XAI_API_KEY",
    "opencode": "OPENCODE_API_KEY",
}


def check_model_available(client, model):
    """Проверяет по /config/providers воркера, что провайдер и модель существуют.

    Бросает RuntimeError с понятным сообщением, если нет.
    """
    if "/" not in (model or ""):
        raise RuntimeError(
            f"Модель должна быть в формате provider/model (например deepseek/deepseek-v4-flash), а указано: {model!r}"
        )
    pid, mid = model.split("/", 1)
    try:
        data = client.config_providers()
    except Exception as exc:
        raise RuntimeError(f"Не удалось получить список провайдеров воркера: {exc}")
    avail = {}
    for p in data.get("providers", []):
        avail[p.get("id")] = sorted((p.get("models") or {}).keys())
    if pid not in avail:
        prow = db.query_one("SELECT enabled, api_key FROM providers WHERE id=?", (pid,))
        if prow is not None:
            if not prow["enabled"]:
                key_note = f"Провайдер '{pid}' отключён на странице «Провайдеры» — включите его."
            elif not prow["api_key"]:
                key_note = f"У провайдера '{pid}' не задан API-ключ на странице «Провайдеры»."
            else:
                key_note = (
                    f"Ключ для '{pid}' задан в UI, но провайдер не зарегистрировался в воркере — "
                    f"нажмите «Проверить» на странице «Провайдеры»."
                )
        else:
            hint = KEY_HINTS.get(pid, f"{pid.upper()}_API_KEY")
            has_key = any(
                k == hint and v for k, v in os.environ.items()
            )
            key_note = (
                f"В окружении брокера есть {hint}, но воркер всё равно не видит провайдера — проверьте формат ключа."
                if has_key
                else f"В окружении брокера нет переменной {hint} — провайдер '{pid}' не зарегистрировался. "
                     f"Добавьте ключ на странице «Провайдеры» или экспортируйте {hint} и перезапустите брокер."
            )
        others = ", ".join(f"{k} ({', '.join(v[:3])}{'…' if len(v) > 3 else ''})" for k, v in avail.items()) or "—"
        raise RuntimeError(
            f"Провайдер '{pid}' не зарегистрирован в воркере (модель {model}). {key_note} "
            f"Доступны в воркере: {others}"
        )
    if mid not in avail[pid]:
        raise RuntimeError(
            f"Провайдер '{pid}' не знает модель '{mid}'. Доступные модели: {', '.join(avail[pid]) or 'нет (проверьте API-ключ)'}"
        )


def get_session(sid):
    return db.query_one("SELECT * FROM sessions WHERE id=?", (sid,))


def load_provider_env():
    """Ключи провайдеров из БД (страница «Провайдеры») для env воркеров."""
    env = {}
    for p in db.query("SELECT env_var, api_key, enabled FROM providers"):
        if p["enabled"] and p["api_key"]:
            env[p["env_var"]] = p["api_key"]
    return env


def project_env(project_id):
    """Контекст проекта для воркера: id, токен файлов и URL брокера для ссылок."""
    env = {"VIBEPROD_BROKER_URL": files_store.broker_url()}
    if project_id:
        token = files_store.file_token(project_id)
        if token:
            env["VIBEPROD_PROJECT_ID"] = str(project_id)
            env["VIBEPROD_FILE_TOKEN"] = token
    return env


def _ensure_mcp_services(mcp_rows):
    """Поднимает docker-сервисы каталога, на которые ссылаются MCP агента."""
    from . import mcp_services

    catalog = db.query("SELECT * FROM mcp_catalog WHERE kind='service' AND service_container IS NOT NULL")
    by_url = {c["url"]: c for c in catalog}
    for m in mcp_rows:
        entry = by_url.get((m.get("url") or "").strip())
        if not entry:
            continue
        try:
            mcp_services.ensure_running(entry)
        except Exception as exc:
            import logging

            logging.getLogger("vibeprod").warning("mcp service %s: %s", entry["name"], exc)


def _client_for(row):
    return OpencodeClient(worker_urls(row["host_port"]), row["auth_token"])


def _terminal_fail(sid, error):
    db.execute(
        "UPDATE sessions SET status='failed', error=?, finished_at=datetime('now') WHERE id=?",
        (str(error)[:2000], sid),
    )
    db.execute(
        "UPDATE schedule_runs SET status='failed', error=?, finished_at=datetime('now') "
        "WHERE session_id=? AND status='running'",
        (str(error)[:2000], sid),
    )
    events.emit("session.failed", events.session_event_data(sid))


def touch_session(sid, interval=60):
    """Обновляет last_activity не чаще раза в interval секунд (активная генерация)."""
    now = time.monotonic()
    if now - _touch_at.get(sid, 0) >= interval:
        _touch_at[sid] = now
        db.execute("UPDATE sessions SET last_activity=datetime('now') WHERE id=?", (sid,))


def _active_count():
    return db.query_one(
        "SELECT COUNT(*) AS c FROM sessions WHERE status IN ('starting','running')"
    )["c"]


async def _reserve_start_slot(sid):
    """Пробует занять слот квоты (переход в 'starting').

    При MAX_CONCURRENT=0 — квоты нет, но переход в 'starting' всё равно
    фиксируется синхронно: повторный вызов для уже стартующей/работающей
    сессии возвращает False (защита от двойного подъёма воркера). Если
    слотов нет — сессия остаётся 'queued', её подхватит диспетчер из
    cleanup_loop. Проверка и переход без await — гонок между параллельными
    стартами нет.
    """
    cur = db.query_one("SELECT status FROM sessions WHERE id=?", (sid,))
    if cur is None or cur["status"] in ("starting", "running"):
        return False
    if MAX_CONCURRENT > 0 and _active_count() >= MAX_CONCURRENT:
        db.execute("UPDATE sessions SET status='queued' WHERE id=?", (sid,))
        await streams.broadcast(sid, {"type": "status", "status": "queued"})
        return False
    db.execute(
        "UPDATE sessions SET status='starting', pending_prompt=0, "
        "started_at=datetime('now'), last_activity=datetime('now'), error=NULL WHERE id=?",
        (sid,),
    )
    return True


def create_session(agent_id, title, prompt, source="manual", project_id=None):
    agent = db.query_one("SELECT * FROM agents WHERE id=?", (agent_id,))
    if not agent:
        raise ValueError("agent not found")
    if project_id is None:
        project_id = agent["project_id"]
    sid = uuid.uuid4().hex
    title = (title or "").strip() or (prompt or "Session")[:60]
    db.execute(
        "INSERT INTO sessions(id, agent_id, agent_name, project_id, title, source, prompt, status, model, last_activity, pending_prompt) "
        "VALUES(?,?,?,?,?,?,?,'queued',?,datetime('now'),1)",
        (sid, agent["id"], agent["name"], project_id, title, source or "manual", prompt, agent["model"]),
    )
    events.emit("session.created", events.session_event_data(sid))
    return sid


async def start_session(sid, initial_prompt=None):
    row = get_session(sid)
    if not row:
        raise ValueError("session not found")
    if row.get("pending_prompt") and not initial_prompt:
        initial_prompt = row["prompt"] or None
    agent = db.query_one("SELECT * FROM agents WHERE id=?", (row["agent_id"],))
    if not agent:
        _terminal_fail(sid, "agent deleted")
        return
    if not await _reserve_start_slot(sid):
        return
    wdir = ws_dir(sid)
    wdir.mkdir(parents=True, exist_ok=True)
    try:
        agents_rows = db.query("SELECT * FROM agents")
        mcp_rows = db.query("SELECT * FROM agent_mcp WHERE agent_id=? AND enabled=1", (agent["id"],))
        skill_rows = db.query(
            "SELECT s.* FROM skills s JOIN agent_skills a ON a.skill_id=s.id WHERE a.agent_id=?",
            (agent["id"],),
        )
        if agent.get("is_guardian"):
            from .guardian_mcp import guardian_mcp_entry

            agents_rows = [agent]
            guardian_mcp = guardian_mcp_entry(session_id=sid, project_id=row["project_id"])
        else:
            agents_rows = [a for a in agents_rows if not a.get("is_guardian")]
            guardian_mcp = None
        from .broker_mcp import broker_mcp_entry

        broker_mcp = broker_mcp_entry(session_id=sid, project_id=row["project_id"])
        provider_rows = db.query("SELECT * FROM providers")
        render_workspace(
            wdir, agents_rows, mcp_rows, skill_rows,
            guardian_mcp=guardian_mcp, broker_mcp=broker_mcp, provider_rows=provider_rows,
        )
        await asyncio.to_thread(_ensure_mcp_services, mcp_rows)
    except Exception as exc:
        _terminal_fail(sid, f"render config: {exc}")
        await streams.broadcast(sid, {"type": "status", "status": "failed", "error": str(exc)})
        return

    token = secrets.token_urlsafe(24)
    db.execute(
        "UPDATE sessions SET status='starting', auth_token=?, workspace=?, started_at=datetime('now'), "
        "last_activity=datetime('now'), error=NULL WHERE id=?",
        (token, str(wdir), sid),
    )
    try:
        container_id = await asyncio.to_thread(
            run_worker, sid, host_ws_dir(sid), storage_name(sid), token, {**load_provider_env(), **project_env(row["project_id"])}
        )
        db.execute("UPDATE sessions SET container_id=? WHERE id=?", (container_id, sid))
        port = await asyncio.to_thread(get_host_port, container_id)
        url = await asyncio.to_thread(wait_healthy, worker_urls(port), token)
        if not url:
            logs = await asyncio.to_thread(container_logs, container_id)
            raise RuntimeError(
                f"opencode serve не поднялся (воркер не отвечает на "
                f"{', '.join(worker_urls(port))}). {NETWORK_HINT} "
                f"logs: {logs[-600:]}"
            )
        db.execute("UPDATE sessions SET host_port=? WHERE id=?", (int(port), sid))
        client = OpencodeClient(url, token)
        try:
            check_model_available(client, agent["model"])
            ocs_id = row.get("opencode_session_id")
            if ocs_id:
                r = await asyncio.to_thread(client.get_session, ocs_id)
                if r.status_code == 200:
                    ocs = {"id": ocs_id}
                else:
                    ocs = await asyncio.to_thread(client.create_session, row["title"])
            else:
                ocs = await asyncio.to_thread(client.create_session, row["title"])
        finally:
            client.close()
        db.execute(
            "UPDATE sessions SET opencode_session_id=?, status='running', last_activity=datetime('now') WHERE id=?",
            (ocs["id"], sid),
        )
        events.emit("session.started", events.session_event_data(sid))
        await streams.start(sid, url, token, ocs["id"])
        if initial_prompt:
            await send_prompt(sid, initial_prompt)
    except Exception as exc:
        row2 = get_session(sid)
        if row2 and row2["container_id"]:
            await asyncio.to_thread(kill_worker, row2["container_id"])
        _terminal_fail(sid, exc)
        await streams.broadcast(sid, {"type": "status", "status": "failed", "error": str(exc)})


async def send_prompt(sid, text):
    row = get_session(sid)
    if not row or row["status"] not in ("running", "completed", "failed"):
        raise RuntimeError("сессия не запущена")
    if not row["opencode_session_id"] or not container_exists(row["container_id"]):
        raise RuntimeError("воркер мёртв, перезапустите сессию")
    await asyncio.to_thread(ensure_running, row["container_id"])
    client = _client_for(row)
    try:
        await asyncio.to_thread(client.prompt_async, row["opencode_session_id"], text, agent=row["agent_name"])
    finally:
        client.close()
    streams.unfinalize(sid)
    db.execute(
        "UPDATE sessions SET status='running', last_activity=datetime('now'), finished_at=NULL WHERE id=?",
        (sid,),
    )
    await streams.broadcast(sid, {"type": "status", "status": "running"})


async def restart_session(sid):
    row = get_session(sid)
    if not row:
        raise ValueError("session not found")
    if row["container_id"] and container_exists(row["container_id"]):
        await asyncio.to_thread(kill_worker, row["container_id"])
    # 'queued': рестарт проходит через квоту, как обычный старт (диспетчер
    # поднимет воркер, когда освободится слот).
    db.execute("UPDATE sessions SET container_id=NULL, host_port=NULL, status='queued' WHERE id=?", (sid,))
    await streams.stop(sid)
    # Рестарт не должен заново слать сохранённый prompt: чистим флаг, иначе
    # диспетчер очереди (если упрёмся в квоту) повторит первое сообщение.
    db.execute("UPDATE sessions SET pending_prompt=0 WHERE id=?", (sid,))
    await start_session(sid, initial_prompt=None)


def session_needs_restart(sid):
    """True, если для продолжения сессии нужно поднимать воркер заново.

    Воркер считается живым, когда opencode-сессия есть, контейнер существует,
    а статус позволяет слать промпт напрямую (running/completed/failed).
    """
    row = get_session(sid)
    if not row or row["status"] in ("queued", "starting"):
        return False
    if (
        row["status"] in ("running", "completed", "failed")
        and row["opencode_session_id"]
        and row["container_id"]
        and container_exists(row["container_id"])
    ):
        return False
    return True


async def continue_session(sid, text):
    """Продолжает завершённую/упавшую/истёкшую сессию новым промптом.

    Если воркер жив — просто отправляем промпт. Иначе поднимаем воркер
    заново: opencode-сессия и переписка сохраняются в docker-томе
    (vibeprod-oc-<sid>), workspace — bind-mount на хосте, так что история
    продолжается. Долгая (перезапуск воркера) — вызывать в фоне через spawn.
    """
    row = get_session(sid)
    if not row:
        raise ValueError("session not found")
    if row["status"] in ("queued", "starting"):
        raise RuntimeError("сессия уже запускается — дождитесь и отправьте сообщение")
    if not session_needs_restart(sid):
        await send_prompt(sid, text)
        return
    if row["container_id"] and container_exists(row["container_id"]):
        await asyncio.to_thread(kill_worker, row["container_id"])
    db.execute("UPDATE sessions SET container_id=NULL, host_port=NULL WHERE id=?", (sid,))
    await streams.stop(sid)
    # Сохраняем новое сообщение как отложенный prompt: если квота занята,
    # сессия уйдёт в 'queued', и диспетчер отправит его после старта воркера.
    db.execute("UPDATE sessions SET prompt=?, pending_prompt=1 WHERE id=?", (text, sid))
    await start_session(sid)


async def abort_session(sid):
    row = get_session(sid)
    if not row or not row["opencode_session_id"] or not row["container_id"]:
        return
    await asyncio.to_thread(ensure_running, row["container_id"])
    client = _client_for(row)
    try:
        await asyncio.to_thread(client.abort, row["opencode_session_id"])
    finally:
        client.close()


async def answer_question(sid, request_id, answers):
    row = get_session(sid)
    if not row or row["status"] != "running":
        raise RuntimeError("сессия не запущена")
    if not row["opencode_session_id"] or not container_exists(row["container_id"]):
        raise RuntimeError("воркер мёртв, перезапустите сессию")
    await asyncio.to_thread(ensure_running, row["container_id"])
    client = _client_for(row)
    try:
        await asyncio.to_thread(client.reply_question, request_id, answers)
    finally:
        client.close()


async def reject_question(sid, request_id):
    row = get_session(sid)
    if not row or row["status"] != "running":
        raise RuntimeError("сессия не запущена")
    if not row["opencode_session_id"] or not container_exists(row["container_id"]):
        raise RuntimeError("воркер мёртв, перезапустите сессию")
    await asyncio.to_thread(ensure_running, row["container_id"])
    client = _client_for(row)
    try:
        await asyncio.to_thread(client.reject_question, request_id)
    finally:
        client.close()


async def delete_session(sid):
    row = get_session(sid)
    await streams.stop(sid)
    if row and row["container_id"]:
        await asyncio.to_thread(kill_worker, row["container_id"], storage_name(sid), remove_volume=True)
    shutil.rmtree(str(ws_dir(sid)), ignore_errors=True)
    db.execute("DELETE FROM sessions WHERE id=?", (sid,))


async def expire_session(sid):
    row = get_session(sid)
    if not row:
        return
    was_failed = row["status"] == "failed"
    if was_failed and "воркер удалён по TTL" in (row["error"] or ""):
        return  # уже обработана — не дублируем запись в error
    await streams.stop(sid)
    if row["container_id"]:
        await asyncio.to_thread(kill_worker, row["container_id"])
    db.execute("UPDATE sessions SET container_id=NULL, host_port=NULL WHERE id=?", (sid,))
    if was_failed:
        db.execute(
            "UPDATE sessions SET error=?, finished_at=datetime('now') WHERE id=?",
            ((row["error"] or "") + " | воркер удалён по TTL", sid),
        )
    else:
        db.execute(
            "UPDATE sessions SET status='expired', finished_at=datetime('now') WHERE id=?",
            (sid,),
        )
    db.execute(
        "UPDATE schedule_runs SET status='failed', error='expired', finished_at=datetime('now') "
        "WHERE session_id=? AND status='running'",
        (sid,),
    )
    events.emit("session.expired", events.session_event_data(sid))
    if not was_failed:
        await streams.broadcast(sid, {"type": "status", "status": "expired"})


async def _suspend_worker(row):
    """Паузит контейнер простаивающей сессии (idle-тир 1: 0 CPU)."""
    cid = row["container_id"]
    if not cid or not container_exists(cid):
        return
    try:
        if container_status(cid) == "running":
            await asyncio.to_thread(pause_worker, cid)
    except Exception as exc:
        import logging

        logging.getLogger("vibeprod").warning("pause %s: %s", row["id"], exc)


async def _start_guarded(sid):
    try:
        await start_session(sid)
    except Exception as exc:
        import logging

        logging.getLogger("vibeprod").exception("queued start %s", sid)
        _terminal_fail(sid, exc)


async def _dispatch_queued():
    """Поднимает queued-сессии, пока есть свободные слоты квоты.

    При MAX_CONCURRENT=0 квоты нет — просто разгребаем хвост очереди
    (например, если лимит отключили после рестарта брокера).
    """
    if MAX_CONCURRENT > 0:
        free = MAX_CONCURRENT - _active_count()
        if free <= 0:
            return
        limit = free
    else:
        limit = 10
    rows = db.query("SELECT * FROM sessions WHERE status='queued' ORDER BY created_at LIMIT ?", (limit,))
    for row in rows:
        asyncio.create_task(_start_guarded(row["id"]))


async def cleanup_loop():
    while True:
        await asyncio.sleep(LOOP_INTERVAL)
        try:
            await _dispatch_queued()
        except Exception as exc:
            import logging

            logging.getLogger("vibeprod").warning("dispatch: %s", exc)
        try:
            now = datetime.utcnow()
            for row in db.query(
                "SELECT * FROM sessions WHERE status IN ('running','starting','completed','failed')"
            ):
                la = row["last_activity"]
                if not la:
                    continue
                try:
                    last = datetime.strptime(la, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                idle = (now - last).total_seconds()
                if idle > IDLE_TTL:
                    await expire_session(row["id"])
                elif idle > SUSPEND_IDLE and row["status"] in ("running", "completed", "failed"):
                    await _suspend_worker(row)
        except Exception as exc:
            import logging

            logging.getLogger("vibeprod").warning("cleanup: %s", exc)


async def reattach_streamers():
    """После рестарта брокера: возвращает стримы живым сессиям.

    Если генерация в воркере уже завершилась, финализируем сессию сразу,
    иначе подписываемся на SSE и продолжаем стримить события в UI.
    """
    rows = db.query(
        "SELECT * FROM sessions WHERE status IN ('running','starting') "
        "AND container_id IS NOT NULL AND opencode_session_id IS NOT NULL AND host_port IS NOT NULL"
    )
    for row in rows:
        if not container_exists(row["container_id"]):
            continue
        # Контейнер мог быть заморожен по idle-тир 1 — разбудить перед опросом.
        await asyncio.to_thread(ensure_running, row["container_id"])
        urls = worker_urls(row["host_port"])
        url = await asyncio.to_thread(wait_healthy, urls, row["auth_token"])
        if not url:
            continue
        token = row["auth_token"]
        done = False
        try:
            client = OpencodeClient(url, token)
            try:
                msgs = client.messages(row["opencode_session_id"])
            finally:
                client.close()
            for m in reversed(msgs or []):
                info = m.get("info") or {}
                if info.get("role") == "assistant":
                    done = bool((info.get("time") or {}).get("completed"))
                    break
        except Exception as exc:
            import logging

            logging.getLogger("vibeprod").warning("reattach %s: %s", row["id"], exc)
        if done:
            await streams.finalize_completed(row["id"], url, token, row["opencode_session_id"])
            continue
        await streams.start(row["id"], url, token, row["opencode_session_id"])


def reconcile():
    """Синхронизация БД и докера после рестарта брокера."""
    from .docker_runner import (
        container_session_id,
        list_vibeprod_containers,
        list_probe_containers,
    )

    for c in list_probe_containers():
        try:
            c.remove(force=True)
        except Exception:
            pass
    live = {}
    for c in list_vibeprod_containers():
        sid = container_session_id(c)
        if sid:
            live[sid] = c
    db_running = {r["id"] for r in db.query("SELECT id FROM sessions WHERE status IN ('running','starting')")}
    for sid, c in live.items():
        if sid not in db_running:
            try:
                c.remove(force=True)
            except Exception:
                pass
            db.execute("UPDATE sessions SET container_id=NULL, host_port=NULL WHERE id=?", (sid,))
    for sid in db_running - set(live):
        db.execute(
            "UPDATE sessions SET status='failed', error='container lost', finished_at=datetime('now') WHERE id=?",
            (sid,),
        )
        events.emit("session.failed", events.session_event_data(sid))
