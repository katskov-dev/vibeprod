import json

from fastapi import APIRouter, HTTPException

from .. import db
from .. import session_manager

router = APIRouter(prefix="/api")


def _session_dict(row):
    d = dict(row)
    d.pop("auth_token", None)
    return d


# Лёгкий набор полей для списка: без result_json/prompt/workspace/токенов,
# чтобы не тащить переписку целиком на каждый рендер списка.
_LIST_COLUMNS = (
    "s.id, s.agent_id, s.agent_name, s.project_id, s.title, s.source, "
    "s.status, s.model, s.error, s.created_at, s.started_at, s.finished_at, "
    "s.last_activity, p.name AS project_name"
)


@router.get("/sessions")
def list_sessions(project_id: int = None, page: int = None, page_size: int = None):
    where = "WHERE s.project_id=? " if project_id is not None else ""
    params = (project_id,) if project_id is not None else ()
    if page is not None:
        # Паджинация: {items, total, page, page_size, pages}. Без page
        # отдаётся полный список, как раньше.
        page = max(1, page)
        page_size = min(max(page_size or 25, 1), 100)
        total = db.query_one(f"SELECT COUNT(*) AS n FROM sessions s {where}", params)["n"]
        sql = (
            f"SELECT {_LIST_COLUMNS} FROM sessions s "
            "LEFT JOIN projects p ON p.id=s.project_id "
            f"{where}"
            "ORDER BY s.created_at DESC LIMIT ? OFFSET ?"
        )
        rows = db.query(sql, params + (page_size, (page - 1) * page_size))
        return {
            "items": [_session_dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, -(-total // page_size)),
        }
    sql = (
        f"SELECT {_LIST_COLUMNS} FROM sessions s "
        "LEFT JOIN projects p ON p.id=s.project_id "
        f"{where}"
        "ORDER BY s.created_at DESC"
    )
    return [_session_dict(r) for r in db.query(sql, params)]


@router.post("/sessions")
def create_session(payload: dict):
    agent_id = payload.get("agent_id")
    if not agent_id:
        raise HTTPException(400, "agent_id обязателен")
    try:
        sid = session_manager.create_session(
            int(agent_id),
            payload.get("title"),
            payload.get("prompt") or "",
            source=payload.get("source") or "manual",
            project_id=payload.get("project_id"),
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    row = db.query_one("SELECT * FROM sessions WHERE id=?", (sid,))
    from ..main import spawn_start

    spawn_start(sid, row["prompt"] or None)
    return _session_dict(row)


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    row = db.query_one("SELECT * FROM sessions WHERE id=?", (session_id,))
    if not row:
        raise HTTPException(404, "сессия не найдена")
    return _session_dict(row)


@router.post("/sessions/{session_id}/prompt")
async def send_prompt(session_id: str, payload: dict):
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text обязателен")
    row = db.query_one("SELECT * FROM sessions WHERE id=?", (session_id,))
    if not row:
        raise HTTPException(404, "сессия не найдена")
    try:
        await session_manager.send_prompt(session_id, text)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True}


@router.post("/sessions/{session_id}/restart")
async def restart_session(session_id: str):
    row = db.query_one("SELECT * FROM sessions WHERE id=?", (session_id,))
    if not row:
        raise HTTPException(404, "сессия не найдена")
    from ..main import spawn_start

    spawn_start(session_id, None, restart=True)
    return {"ok": True}


@router.post("/sessions/{session_id}/continue")
async def continue_session(session_id: str, payload: dict):
    """Продолжить завершённую сессию новым сообщением.

    Если воркер ещё жив — промпт уходит сразу (restarted=false). Если воркер
    уже удалён по TTL (или упал) — перезапуск воркера и промпт выполняются в
    фоне (restarted=true): история opencode-сессии сохраняется в docker-томе,
    workspace — на хосте (снапшоты файлов вне рамок этой версии).
    """
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text обязателен")
    row = db.query_one("SELECT * FROM sessions WHERE id=?", (session_id,))
    if not row:
        raise HTTPException(404, "сессия не найдена")
    if row["status"] in ("queued", "starting"):
        raise HTTPException(409, "сессия уже запускается — дождитесь и отправьте сообщение")
    if not session_manager.session_needs_restart(session_id):
        try:
            await session_manager.send_prompt(session_id, text)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))
        return {"ok": True, "restarted": False}
    from ..main import spawn_start

    spawn_start(session_id, text, continue_=True)
    return {"ok": True, "restarted": True}


@router.post("/sessions/{session_id}/abort")
async def abort_session(session_id: str):
    row = db.query_one("SELECT * FROM sessions WHERE id=?", (session_id,))
    if not row:
        raise HTTPException(404, "сессия не найдена")
    await session_manager.abort_session(session_id)
    return {"ok": True}


@router.post("/sessions/{session_id}/question/{request_id}/answer")
async def answer_question(session_id: str, request_id: str, payload: dict):
    """Ответ на вопрос агента: {answers: [["label1"], ["label2"]]}."""
    row = db.query_one("SELECT * FROM sessions WHERE id=?", (session_id,))
    if not row:
        raise HTTPException(404, "сессия не найдена")
    answers = payload.get("answers")
    if not isinstance(answers, list) or not all(isinstance(a, list) for a in answers):
        raise HTTPException(400, "answers: список списков выбранных label")
    try:
        await session_manager.answer_question(session_id, request_id, answers)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True}


@router.post("/sessions/{session_id}/question/{request_id}/reject")
async def reject_question(session_id: str, request_id: str):
    row = db.query_one("SELECT * FROM sessions WHERE id=?", (session_id,))
    if not row:
        raise HTTPException(404, "сессия не найдена")
    try:
        await session_manager.reject_question(session_id, request_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    await session_manager.delete_session(session_id)
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
def get_messages(session_id: str):
    row = db.query_one("SELECT * FROM sessions WHERE id=?", (session_id,))
    if not row:
        raise HTTPException(404, "сессия не найдена")
    result = None
    questions = []
    if row["status"] == "running" and row["opencode_session_id"] and row["container_id"]:
        # рабочая сессия: отдаём живой транскрипт из воркера, чтобы чат
        # переживал обновление страницы
        try:
            client = session_manager._client_for(row)
            try:
                result = client.messages(row["opencode_session_id"])
                pending = client.questions()
                questions = [q for q in pending if q.get("sessionID") == row["opencode_session_id"]]
            finally:
                client.close()
        except Exception:
            result = None
    if result is None:
        # завершённая/упавшая сессия: читаем сообщения отдельными строками,
        # на старых сессиях откатываемся на result_json.
        result = db.load_session_messages(session_id)
    if result is None and row["result_json"]:
        try:
            result = json.loads(row["result_json"])
        except ValueError:
            result = None
    return {
        "status": row["status"],
        "result": result,
        "questions": questions,
        "events": db.query("SELECT type, payload, ts FROM events WHERE session_id=? ORDER BY id", (session_id,)),
    }
