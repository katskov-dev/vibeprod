"""Конфиг vision-MCP контейнера: ключ DeepSeek для vision-модели.

Эндпоинт за токеном проекта (X-Vibeprod-Token), как /api/ssh/config. Ключ
берётся из провайдера «deepseek» на странице «Провайдеры» (приоритет) или
из переменной окружения брокера DEEPSEEK_API_KEY (резерв). Если ни того,
ни другого нет — отвечаем configured=false с подсказкой, что настроить.
"""
import os

from fastapi import APIRouter, HTTPException, Query, Request

from .. import db
from .. import files_store

router = APIRouter(prefix="/api")

PROVIDER_ID = "deepseek"
ENV_KEY = "DEEPSEEK_API_KEY"
DEFAULT_MODEL = "deepseek-v4-flash-vision-exp"
DEFAULT_BASE_URL = "https://api.deepseek.com"

HINT_NOT_CONFIGURED = (
    "DeepSeek vision не настроен. Добавьте API-ключ одним из способов: "
    "1) страница «Провайдеры» → провайдер с id «deepseek» → вставить ключ и нажать «Проверить»; "
    "2) задайте переменную окружения DEEPSEEK_API_KEY в окружении брокера и перезапустите его."
)


def _allowed(request: Request, project_id) -> bool:
    """Доступ по токену проекта (контейнер vision-MCP) или cookie-сессии (UI)."""
    token = request.headers.get("X-Vibeprod-Token") or ""
    if files_store.check_file_token(project_id, token):
        return True
    from .. import auth

    return not auth.ENABLED or auth.check_request(request)


def resolve_key(project_id):
    """Ищет ключ deepseek: провайдер в БД (проект → глобальный) → env брокера."""
    rows = db.query(
        "SELECT api_key, enabled, project_id FROM providers WHERE id=? ORDER BY enabled DESC",
        (PROVIDER_ID,),
    )
    for r in rows:
        if r["project_id"] not in (None, int(project_id or 0)):
            continue
        if not r["enabled"]:
            return {"configured": False, "hint": (
                "Провайдер «deepseek» отключён: включите его на странице «Провайдеры» "
                "и нажмите «Проверить»."
            )}
        if r["api_key"]:
            return {"configured": True, "api_key": r["api_key"], "source": "provider:deepseek"}
    key = os.environ.get(ENV_KEY) or ""
    if key:
        return {"configured": True, "api_key": key, "source": f"env:{ENV_KEY}"}
    return {"configured": False, "hint": HINT_NOT_CONFIGURED}


@router.get("/vision/config")
def vision_config(request: Request, project_id: int = Query(...)):
    if not _allowed(request, project_id):
        raise HTTPException(403, "нет доступа к конфигу vision")
    cfg = resolve_key(project_id)
    cfg["base_url"] = (os.environ.get("VIBEPROD_VISION_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    cfg["model"] = os.environ.get("VIBEPROD_VISION_MODEL") or DEFAULT_MODEL
    if cfg.get("configured"):
        cfg["api_key_masked"] = (cfg["api_key"][:4] + "…" + cfg["api_key"][-2:]) if len(cfg["api_key"]) > 6 else "…"
    return cfg
