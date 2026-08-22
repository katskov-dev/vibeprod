import json
import re

from fastapi import APIRouter, HTTPException

from .. import db
from ..provider_check import (
    KNOWN_PROVIDER_IDS,
    check_provider,
    env_var_for,
    fetch_available_providers,
    load_catalog_from_disk,
)

router = APIRouter(prefix="/api")

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def _mask(key):
    if not key:
        return None
    if len(key) <= 8:
        return key[:3] + "…"
    return key[:4] + "…" + key[-4:]


def _parse_custom_models(raw):
    """JSON моделей кастомного провайдера: {model_id: {name?, limit?}}."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            models = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "custom_models: невалидный JSON")
    else:
        models = raw
    if not isinstance(models, dict) or not models:
        raise HTTPException(400, "custom_models: объект {model_id: {name, limit}}")
    for mid, spec in models.items():
        if not isinstance(spec, dict) or not isinstance(mid, str) or not mid.strip():
            raise HTTPException(400, "custom_models: {model_id: {name?, limit?}}")
        limit = spec.get("limit")
        if limit is not None and not isinstance(limit, dict):
            raise HTTPException(400, "custom_models: limit — объект {context?, output?}")
    return json.dumps(models, ensure_ascii=False)


def _validate_custom(payload):
    kind = payload.get("kind") or "builtin"
    if kind not in ("builtin", "openai_compatible"):
        raise HTTPException(400, "kind: builtin | openai_compatible")
    if kind == "openai_compatible":
        base_url = (payload.get("base_url") or "").strip()
        if not base_url.startswith(("http://", "https://")):
            raise HTTPException(400, "base_url обязателен для openai_compatible (http(s)://…)")
        custom_models = _parse_custom_models(payload.get("custom_models") or {})
    else:
        base_url = ""
        custom_models = None
    return kind, base_url, custom_models


def _dict(row):
    d = dict(row)
    d.pop("api_key", None)
    d.pop("models_full", None)
    d["has_key"] = bool(row["api_key"])
    d["key_masked"] = _mask(row["api_key"])
    try:
        d["custom_models"] = json.loads(row["custom_models"]) if row.get("custom_models") else {}
    except ValueError:
        d["custom_models"] = {}
    for field in ("models", "last_gen"):
        try:
            d[field] = json.loads(row[field]) if row.get(field) else ([] if field == "models" else None)
        except ValueError:
            d[field] = [] if field == "models" else None
    return d


def _get(pid):
    row = db.query_one("SELECT * FROM providers WHERE id=?", (pid,))
    if not row:
        raise HTTPException(404, "провайдер не найден")
    return row


@router.get("/providers")
def list_providers(project_id: int = None):
    if project_id is not None:
        rows = db.query(
            "SELECT pr.*, p.name AS project_name FROM providers pr "
            "LEFT JOIN projects p ON p.id=pr.project_id WHERE pr.project_id=? ORDER BY pr.id",
            (project_id,),
        )
    else:
        rows = db.query(
            "SELECT pr.*, p.name AS project_name FROM providers pr "
            "LEFT JOIN projects p ON p.id=pr.project_id ORDER BY pr.id"
        )
    return [_dict(r) for r in rows]


@router.get("/providers/known")
def known_providers():
    catalog = load_catalog_from_disk()
    if catalog:
        ids = [p["id"] for p in catalog.get("providers", []) if p.get("id")]
        if ids:
            return ids
    return KNOWN_PROVIDER_IDS


@router.get("/providers/available")
def available_providers(refresh: bool = False):
    """Каталог всех провайдеров, известных opencode (кэш сутки, refresh=true перечитывает).

    Первый вызов поднимает probe-контейнер и может занять до минуты.
    """
    try:
        return fetch_available_providers(force=refresh)
    except Exception as exc:
        raise HTTPException(502, f"не удалось получить каталог провайдеров: {str(exc)[:500]}")


@router.post("/providers")
def create_provider(payload: dict):
    pid = (payload.get("id") or "").strip().lower()
    if not ID_RE.match(pid):
        raise HTTPException(400, "id: строчные латинские буквы, цифры, дефис")
    kind, base_url, custom_models = _validate_custom(payload)
    if kind == "openai_compatible" and pid in KNOWN_PROVIDER_IDS:
        raise HTTPException(400, f"id '{pid}' совпадает со встроенным провайдером — выберите другое имя")
    if db.query_one("SELECT id FROM providers WHERE id=?", (pid,)):
        raise HTTPException(409, "провайдер уже добавлен")
    project_id = payload.get("project_id")
    if project_id is not None and not db.query_one("SELECT id FROM projects WHERE id=?", (int(project_id),)):
        raise HTTPException(400, "проект не существует")
    db.execute(
        "INSERT INTO providers(id, label, env_var, api_key, enabled, kind, base_url, custom_models, project_id) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            pid,
            payload.get("label") or "",
            env_var_for(pid),
            payload.get("api_key") or "",
            1 if payload.get("enabled", True) else 0,
            kind,
            base_url,
            custom_models or "{}",
            project_id,
        ),
    )
    return _dict(_get(pid))


@router.put("/providers/{pid}")
def update_provider(pid: str, payload: dict):
    row = _get(pid)
    api_key = payload.get("api_key")
    project_id = payload.get("project_id", row["project_id"])
    if project_id is not None and not db.query_one("SELECT id FROM projects WHERE id=?", (int(project_id),)):
        raise HTTPException(400, "проект не существует")
    kind, base_url, custom_models = _validate_custom({**row, **payload})
    if api_key:
        db.execute(
            "UPDATE providers SET label=?, api_key=?, enabled=?, kind=?, base_url=?, custom_models=?, "
            "project_id=?, updated_at=datetime('now') WHERE id=?",
            (payload.get("label", row["label"]), api_key,
             1 if payload.get("enabled", row["enabled"]) else 0, kind,
             base_url if kind == "openai_compatible" else "",
             custom_models if custom_models is not None else (row["custom_models"] or "{}"),
             project_id, pid),
        )
    else:
        db.execute(
            "UPDATE providers SET label=?, enabled=?, kind=?, base_url=?, custom_models=?, "
            "project_id=?, updated_at=datetime('now') WHERE id=?",
            (payload.get("label", row["label"]),
             1 if payload.get("enabled", row["enabled"]) else 0, kind,
             base_url if kind == "openai_compatible" else "",
             custom_models if custom_models is not None else (row["custom_models"] or "{}"),
             project_id, pid),
        )
    return _dict(_get(pid))


@router.delete("/providers/{pid}")
def delete_provider(pid: str):
    _get(pid)
    db.execute("DELETE FROM providers WHERE id=?", (pid,))
    return {"ok": True}


@router.post("/providers/{pid}/check")
def check_provider_endpoint(pid: str, payload: dict = None):
    """Поднимает probe-контейнер opencode и проверяет провайдера по-настоящему.

    deep=true (по умолчанию) дополнительно шлёт реальный тест-запрос к модели.
    """
    payload = payload or {}
    row = _get(pid)
    deep = payload.get("deep", True)
    result = check_provider(
        pid, row["api_key"], deep=bool(deep),
        kind=row["kind"], base_url=row["base_url"],
        custom_models=row["custom_models"], label=row["label"],
    )
    db.execute(
        "UPDATE providers SET models=?, models_full=?, last_check_ok=?, last_check_error=?, last_gen=?, last_check_at=datetime('now') WHERE id=?",
        (
            json.dumps(result.get("models") or [], ensure_ascii=False),
            json.dumps(result.get("model_details") or {}, ensure_ascii=False),
            1 if result.get("ok") else 0,
            result.get("error") or "",
            json.dumps(result.get("gen"), ensure_ascii=False) if result.get("gen") else None,
            pid,
        ),
    )
    return {"provider": pid, **result}


@router.post("/providers/{pid}/refresh-models")
def refresh_models(pid: str):
    """Пробует провайдера в opencode-контейнере и обновляет кэш моделей (без тест-запроса)."""
    row = _get(pid)
    result = check_provider(
        pid, row["api_key"], deep=False,
        kind=row["kind"], base_url=row["base_url"],
        custom_models=row["custom_models"], label=row["label"],
    )
    db.execute(
        "UPDATE providers SET models=?, models_full=?, last_check_ok=?, last_check_error=?, last_check_at=datetime('now') WHERE id=?",
        (
            json.dumps(result.get("models") or [], ensure_ascii=False),
            json.dumps(result.get("model_details") or {}, ensure_ascii=False),
            1 if result.get("ok") else 0,
            result.get("error") or "",
            pid,
        ),
    )
    return {"provider": pid, **result}


@router.get("/providers/{pid}/models")
def provider_models(pid: str):
    """Кэш моделей провайдера (id + варианты/reasoning effort)."""
    row = _get(pid)
    try:
        models = json.loads(row["models"]) if row["models"] else []
    except ValueError:
        models = []
    try:
        details = json.loads(row["models_full"]) if row.get("models_full") else {}
    except ValueError:
        details = {}
    return {
        "provider": pid,
        "models": models,
        "details": details,
        "last_check_at": row["last_check_at"],
        "last_check_ok": row["last_check_ok"],
    }
