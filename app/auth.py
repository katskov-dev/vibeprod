"""Простейшая авторизация UI по логину-паролю из .env.

Если VIBEPROD_LOGIN и VIBEPROD_PASSWORD заданы — страницы и /api требуют входа
(исключения: /login, /static, guardian MCP и вызов вебхуков, у которых свой секрет).
Ключ подписи токена живёт в settings.auth_secret, как guardian_secret.
"""

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time

from fastapi import Request, Response

from . import db

log = logging.getLogger("vibeprod.auth")

COOKIE_NAME = "vibeprod_auth"
TOKEN_TTL_SEC = 7 * 24 * 3600

LOGIN = os.environ.get("VIBEPROD_LOGIN", "").strip()
PASSWORD = os.environ.get("VIBEPROD_PASSWORD", "")
ENABLED = bool(LOGIN and PASSWORD)


def _secret():
    row = db.query_one("SELECT value FROM settings WHERE key='auth_secret'")
    return row["value"] if row else None


def _sign(exp: int) -> str:
    sig = hmac.new(_secret().encode(), str(exp).encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")


def make_token() -> str:
    exp = int(time.time()) + TOKEN_TTL_SEC
    return f"{exp}.{_sign(exp)}"


def check_token(token):
    try:
        exp_s, sig = token.split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    return exp >= time.time() and secrets.compare_digest(sig, _sign(exp))


def check_request(request: Request) -> bool:
    return check_token(request.cookies.get(COOKIE_NAME))


def set_cookie(response: Response) -> None:
    response.set_cookie(
        COOKIE_NAME,
        make_token(),
        max_age=TOKEN_TTL_SEC,
        httponly=True,
        samesite="lax",
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)


def check_credentials(login: str, password: str) -> bool:
    return secrets.compare_digest(login or "", LOGIN) and secrets.compare_digest(password or "", PASSWORD)
