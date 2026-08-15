"""HTTP-эндпоинты guardian: MCP поверх streamable HTTP + информация для UI.

POST /guardian/mcp принимает JSON-RPC (одиночный запрос или батч) и отвечает
application/json. Доступ закрыт секретом: Authorization: Bearer <secret>,
который знает только воркер guardian-агента (вставляется в opencode.json
при рендере workspace).
"""
import json
import logging
import secrets

from fastapi import APIRouter, Header, Request, Response

from .. import db
from ..guardian_mcp import TOOLS, call_tool, get_secret

log = logging.getLogger("vibeprod.guardian")

router = APIRouter()


def _check_auth(authorization):
    expected = f"Bearer {get_secret()}"
    return bool(authorization) and secrets.compare_digest(authorization, expected)


def _ok(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


async def _dispatch_one(msg, ctx=None, tools=None, call_fn=None):
    tools = TOOLS if tools is None else tools
    call_fn = call_tool if call_fn is None else call_fn
    if not isinstance(msg, dict):
        return _err(None, -32700, "Parse error")
    msg_id = msg.get("id")
    method = msg.get("method") or ""
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion") or "2024-11-05"
        return _ok(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "vibeprod-guardian", "version": "1.0.0"},
        })
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return _ok(msg_id, {})
    if method == "tools/list":
        return _ok(msg_id, {"tools": tools})
    if method == "tools/call":
        params = msg.get("params") or {}
        return _ok(msg_id, await call_fn(params.get("name"), params.get("arguments") or {}, ctx))
    return _err(msg_id, -32601, f"Method not found: {method}")


async def _dispatch(payload, ctx=None, tools=None, call_fn=None):
    if isinstance(payload, list):
        responses = [await _dispatch_one(m, ctx, tools, call_fn) for m in payload]
        responses = [r for r in responses if r is not None]
        return responses if responses else None
    return await _dispatch_one(payload, ctx, tools, call_fn)


def _request_ctx(request: Request):
    """Контекст вызова инструментов: сессия и проект воркера из заголовков."""
    ctx = {}
    sid = request.headers.get("X-Vibeprod-Session") or ""
    if sid:
        row = db.query_one("SELECT id, project_id FROM sessions WHERE id=?", (sid,))
        if row:
            ctx["session_id"] = row["id"]
            if row["project_id"] is not None:
                ctx["project_id"] = row["project_id"]
    else:
        pid = request.headers.get("X-Vibeprod-Project") or ""
        if pid.isdigit():
            ctx["project_id"] = int(pid)
    return ctx


@router.post("/guardian/mcp")
async def mcp_endpoint(request: Request, authorization: str = Header(default="")):
    if not _check_auth(authorization):
        return _json_response(401, _err(None, -32001, "unauthorized"))
    try:
        body = await request.json()
    except ValueError:
        return _json_response(400, _err(None, -32700, "Parse error"))
    try:
        responses = await _dispatch(body, _request_ctx(request))
    except Exception as exc:
        log.exception("guardian mcp dispatch")
        return _json_response(500, _err(None, -32603, f"Internal error: {exc}"))
    if responses is None:
        return Response(status_code=202)
    return _json_response(200, responses)


@router.get("/guardian/mcp")
async def mcp_get():
    return Response(status_code=405, headers={"Allow": "POST"})


@router.delete("/guardian/mcp")
async def mcp_delete():
    return Response(status_code=202)


@router.get("/api/guardian")
def guardian_info():
    """Информация об агенте-операторе для главной страницы."""
    row = db.query_one("SELECT id, name, model, project_id FROM agents WHERE is_guardian=1 LIMIT 1")
    if not row:
        return {"ready": False, "agent_id": None}
    return {
        "ready": True,
        "agent_id": row["id"],
        "name": row["name"],
        "model": row["model"],
        "project_id": row["project_id"],
    }


def _json_response(status, payload):
    return Response(
        content=json.dumps(payload, ensure_ascii=False),
        media_type="application/json",
        status_code=status,
    )
