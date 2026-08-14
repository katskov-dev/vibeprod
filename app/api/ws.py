import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import db
from ..streamer import streams

router = APIRouter()


@router.websocket("/ws/sessions/{session_id}")
async def ws_session(websocket: WebSocket, session_id: str):
    row = db.query_one("SELECT * FROM sessions WHERE id=?", (session_id,))
    if not row:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    queue = await streams.subscribe(session_id)
    try:
        await websocket.send_json({"type": "status", "status": row["status"]})
        for ev in db.query("SELECT type, payload, ts FROM events WHERE session_id=? ORDER BY id", (session_id,)):
            try:
                props = json.loads(ev["payload"])
            except ValueError:
                props = {}
            await websocket.send_json(
                {"type": "event", "event": {"type": ev["type"], "properties": props}, "ts": ev["ts"]}
            )
        if row["result_json"] and row["status"] in ("completed", "failed"):
            try:
                result = json.loads(row["result_json"])
                await websocket.send_json({"type": "done", "status": row["status"], "result": result})
            except ValueError:
                pass
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=30)
            except TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue
            await websocket.send_json(msg)
    except WebSocketDisconnect:
        pass
    finally:
        streams.unsubscribe(session_id, queue)
