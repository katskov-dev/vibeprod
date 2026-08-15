"""HTTP-эндпоинт broker MCP: встроенные инструменты Vibeprod для воркеров.

POST /broker/mcp принимает JSON-RPC (одиночный запрос или батч) и отвечает
application/json. Доступ закрыт тем же Bearer-секретом, что и guardian MCP —
он вставляется в opencode.json воркера при рендере workspace. Контекст
(сессия/проект) берётся из заголовков X-Vibeprod-Session / X-Vibeprod-Project.
"""
import logging

from fastapi import APIRouter, Header, Request, Response

from ..broker_mcp import BROKER_TOOLS, call_tool
from .guardian import _check_auth, _dispatch, _json_response, _request_ctx

log = logging.getLogger("vibeprod.broker-mcp")

router = APIRouter()


@router.post("/broker/mcp")
async def mcp_endpoint(request: Request, authorization: str = Header(default="")):
    if not _check_auth(authorization):
        return _json_response(401, {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "unauthorized"}})
    try:
        body = await request.json()
    except ValueError:
        return _json_response(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
    try:
        responses = await _dispatch(body, _request_ctx(request), BROKER_TOOLS, call_tool)
    except Exception as exc:
        log.exception("broker mcp dispatch")
        return _json_response(500, {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": f"Internal error: {exc}"}})
    if responses is None:
        return Response(status_code=202)
    return _json_response(200, responses)


@router.get("/broker/mcp")
async def mcp_get():
    return Response(status_code=405, headers={"Allow": "POST"})


@router.delete("/broker/mcp")
async def mcp_delete():
    return Response(status_code=202)
