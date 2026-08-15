"""Подписка на SSE opencode serve, персист и ретрансляция в WebSocket."""
import asyncio
import json
import logging

import httpx

from . import db
from . import events
from .opencode_client import OpencodeClient, USERNAME

log = logging.getLogger("vibeprod.stream")

PERSIST_TYPES = {
    "message.updated",
    "todo.updated",
    "session.failed",
    "session.error",
    "question.asked",
    "question.replied",
    "question.rejected",
}

FINAL_EVENTS = {"session.idle", "session.failed", "session.error"}


def _extract_error(props):
    err = props.get("error") or {}
    if isinstance(err, dict):
        msg = err.get("message") or (err.get("data") or {}).get("message")
        if msg:
            return msg
    return json.dumps(props, ensure_ascii=False)


def _session_of(props):
    if not isinstance(props, dict):
        return None
    sid = props.get("sessionID")
    if sid:
        return sid
    for key in ("info", "part", "todo", "message", "permission", "file"):
        obj = props.get(key)
        if isinstance(obj, dict):
            sid = obj.get("sessionID")
            if sid:
                return sid
    return None


def _result_error(messages):
    for m in reversed(messages or []):
        info = m.get("info") or {}
        if info.get("role") != "assistant":
            continue
        err = info.get("error")
        if not err:
            continue
        if isinstance(err, dict):
            return (
                (err.get("data") or {}).get("message")
                or err.get("message")
                or json.dumps(err, ensure_ascii=False)
            )
        return str(err)
    return None


def _result_text(messages):
    for m in reversed(messages or []):
        info = m.get("info") or {}
        if info.get("role") != "assistant":
            continue
        texts = [
            (p.get("text") or "").strip()
            for p in info.get("parts") or []
            if p.get("type") == "text" and (p.get("text") or "").strip()
        ]
        if texts:
            return "\n\n".join(texts)
    return None


class StreamManager:
    def __init__(self):
        self.tasks = {}
        self.subs = {}
        self.hooks = []

    async def start(self, sid, base_url, token, oc_sid):
        await self.stop(sid)
        self.tasks[sid] = asyncio.create_task(self._watch(sid, base_url, token, oc_sid))

    async def stop(self, sid):
        task = self.tasks.pop(sid, None)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def finalize_completed(self, sid, base_url, token, oc_sid):
        await self._finalize(sid, base_url, token, oc_sid, "completed", None)

    async def subscribe(self, sid):
        q = asyncio.Queue(maxsize=256)
        self.subs.setdefault(sid, set()).add(q)
        return q

    def unsubscribe(self, sid, q):
        self.subs.get(sid, set()).discard(q)

    async def broadcast(self, sid, msg):
        for q in list(self.subs.get(sid, set())):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    async def _watch(self, sid, base_url, token, oc_sid):
        backoff = 1
        while True:
            try:
                async with httpx.AsyncClient(
                    auth=(USERNAME, token),
                    timeout=httpx.Timeout(7200.0, connect=5.0),
                    trust_env=False,
                ) as client:
                    async with client.stream("GET", f"{base_url}/event") as resp:
                        resp.raise_for_status()
                        backoff = 1
                        async for line in resp.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if not data:
                                continue
                            await self._handle(sid, base_url, token, oc_sid, data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("sse %s: %s (retry in %ss)", sid, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15)

    async def _handle(self, sid, base_url, token, oc_sid, data):
        try:
            payload = json.loads(data)
        except ValueError:
            return
        etype = payload.get("type")
        props = payload.get("properties") or {}
        psid = _session_of(props)
        if psid and psid != oc_sid:
            return
        if psid is None:
            return
        await self.broadcast(sid, {"type": "event", "event": {"type": etype, "properties": props}})
        if etype in PERSIST_TYPES:
            db.execute(
                "INSERT INTO events(session_id, type, payload) VALUES(?,?,?)",
                (sid, etype, json.dumps(props, ensure_ascii=False)),
            )
        if etype == "permission.ask":
            pid = props.get("permissionID") or (props.get("permission") or {}).get("id")
            if pid:
                client = OpencodeClient(base_url, token)
                client.respond_permission(oc_sid, pid, "allow")
                client.close()
        if etype == "question.asked":
            # неинтерактивные запуски (вебхуки, расписания) некому отвечать —
            # отклоняем вопрос сразу, чтобы сессия не зависала
            row = db.query_one("SELECT source FROM sessions WHERE id=?", (sid,))
            if row and row["source"] in ("webhook", "schedule", "telegram") and props.get("id"):
                client = OpencodeClient(base_url, token)
                try:
                    client.reject_question(props["id"])
                except Exception as exc:
                    log.warning("auto-reject question %s: %s", props.get("id"), exc)
                finally:
                    client.close()
        if etype in FINAL_EVENTS:
            status = "completed" if etype == "session.idle" else "failed"
            error = None
            if status == "failed":
                error = _extract_error(props)
            await self._finalize(sid, base_url, token, oc_sid, status, error)

    async def _finalize(self, sid, base_url, token, oc_sid, status, error):
        result = None
        try:
            client = OpencodeClient(base_url, token)
            result = client.messages(oc_sid)
            client.close()
        except Exception as exc:
            log.warning("finalize fetch messages %s: %s", sid, exc)
        if status == "completed" and result:
            msg_error = _result_error(result)
            if msg_error:
                status = "failed"
                error = msg_error
        try:
            db.execute(
                "UPDATE sessions SET status=?, result_json=?, error=?, finished_at=datetime('now'), last_activity=datetime('now') WHERE id=?",
                (status, json.dumps(result, ensure_ascii=False) if result is not None else None,
                 (error or "")[:2000], sid),
            )
        except Exception as exc:
            log.warning("finalize db %s: %s", sid, exc)
        db.execute(
            "UPDATE schedule_runs SET status=?, error=?, finished_at=datetime('now') "
            "WHERE session_id=? AND status='running'",
            (status, (error or "")[:2000], sid),
        )
        await self.broadcast(sid, {"type": "done", "status": status, "error": error, "result": result})
        for hook in self.hooks:
            try:
                await hook(sid, status, error, result)
            except Exception:
                log.warning("done hook %s failed", hook, exc_info=True)
        try:
            extra = {}
            if status == "failed" and error:
                extra["error"] = error
            if result:
                text = _result_text(result)
                if text:
                    extra["result_text"] = text
            events.emit(
                "session.completed" if status == "completed" else "session.failed",
                events.session_event_data(sid, **extra),
            )
        except Exception:
            log.warning("emit session done %s failed", sid, exc_info=True)


streams = StreamManager()
