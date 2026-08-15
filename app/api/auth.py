"""HTTP-эндпоинты входа: /api/login, /api/logout, /api/auth (статус)."""

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from .. import auth

router = APIRouter()


@router.post("/api/login")
def login(payload: dict, response: Response):
    if not auth.ENABLED:
        return {"ok": True}
    if not auth.check_credentials(payload.get("login") or "", payload.get("password") or ""):
        return JSONResponse({"detail": "неверный логин или пароль"}, status_code=401)
    auth.set_cookie(response)
    return {"ok": True}


@router.post("/api/logout")
def logout(response: Response):
    auth.clear_cookie(response)
    return {"ok": True}


@router.get("/api/auth")
def auth_status():
    return {"enabled": auth.ENABLED}
