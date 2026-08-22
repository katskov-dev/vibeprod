"""Проверка провайдера: короткоживущий opencode-контейнер с ключом в env.

Проверяет то же, что будет происходить в реальном воркере:
1. регистрация провайдера в opencode (/config/providers);
2. список моделей;
3. опционально — реальный тест-запрос к модели (deep-проверка).
"""
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

import httpx
from docker.types import Mount

from .docker_runner import (
    IMAGE,
    NETWORK_HINT,
    OPENCODE_PORT,
    docker_client,
    ensure_image,
    get_host_port,
    _entrypoint_cmd,
)
from .opencode_client import USERNAME, wait_healthy, worker_urls

ENV_VAR_MAP = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_GENERATIVE_AI_API_KEY",
    "groq": "GROQ_API_KEY",
    "xai": "XAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "opencode": "OPENCODE_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
    "together": "TOGETHER_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "vercel": "AI_PROVIDER_VERCEL_API_KEY",
}

KNOWN_PROVIDER_IDS = sorted(ENV_VAR_MAP.keys())

CATALOG_TTL = int(os.environ.get("VIBEPROD_CATALOG_TTL", str(24 * 3600)))
CATALOG_FILE = Path(
    os.environ.get("VIBEPROD_DATA_DIR", Path(__file__).resolve().parent.parent / "data")
) / "provider_catalog.json"

# Workspace-пробы живут под data/probes: брокер может работать в контейнере,
# и тогда docker-демон (на хосте) не видит пути в его файловой системе.
DATA_DIR = Path(
    os.environ.get("VIBEPROD_DATA_DIR", Path(__file__).resolve().parent.parent / "data")
).resolve()
PROBES_DIR = DATA_DIR / "probes"
# Хост-путь к data: задаётся через env, когда брокер сам в докере
# (compose подставляет VIBEPROD_HOST_DATA_DIR=<каталог на хосте>).
HOST_DATA_DIR = Path(os.environ.get("VIBEPROD_HOST_DATA_DIR", DATA_DIR))


def _host_path(path):
    """Переводит путь внутри брокера в путь на хосте (для bind-mount).

    Docker резолвит source bind-mount в файловой системе ХОСТА. Без этого
    пробный контейнер падает с «bind source path does not exist».
    """
    p = Path(path).resolve()
    try:
        return HOST_DATA_DIR / p.relative_to(DATA_DIR)
    except ValueError:
        return p

_catalog_lock = threading.Lock()
_catalog_mem = None
_catalog_mem_at = 0.0


def load_catalog_from_disk():
    """Кэш каталога провайдеров с диска (без поднятия контейнера)."""
    try:
        with open(CATALOG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _fetch_catalog_live():
    """Поднимает probe-контейнер и спрашивает у opencode полный каталог провайдеров."""
    PROBES_DIR.mkdir(parents=True, exist_ok=True)
    ws = Path(tempfile.mkdtemp(prefix="vibeprod-catalog-", dir=PROBES_DIR))
    token = secrets.token_urlsafe(24)
    container = None
    try:
        client = docker_client()
        env = {
            "OPENCODE_SERVER_PASSWORD": token,
            "OPENCODE_SERVER_USERNAME": USERNAME,
        }
        container = client.containers.run(
            image=IMAGE,
            command=_entrypoint_cmd(ensure_image()),
            detach=True,
            working_dir="/workspace",
            environment=env,
            mounts=[Mount(target="/workspace", source=str(_host_path(ws)), type="bind")],
            ports={f"{OPENCODE_PORT}/tcp": None},
            labels={"vibeprod.probe": "1"},
            name=f"vibeprod-catalog-{uuid.uuid4().hex[:10]}",
        )
        port = get_host_port(container.id)
        url = wait_healthy(worker_urls(port), token, timeout=90)
        if not url:
            logs = container.logs(tail=40).decode("utf-8", "replace")
            raise RuntimeError(
                f"opencode serve не поднялся (нет ответа на {', '.join(worker_urls(port))}). "
                f"{NETWORK_HINT}\nlogs: {logs[-400:]}"
            )
        version = "?"
        try:
            version = httpx.get(
                f"{url}/global/health", auth=(USERNAME, token), timeout=10, trust_env=False
            ).json().get("version", "?")
        except Exception:
            pass
        r = httpx.get(f"{url}/provider", auth=(USERNAME, token), timeout=60, trust_env=False)
        r.raise_for_status()
        data = r.json()
        default = data.get("default") or {}
        providers = []
        for p in data.get("all") or []:
            models = p.get("models") or {}
            providers.append({
                "id": p.get("id"),
                "name": p.get("name") or "",
                "models": sorted(models.keys()),
                "default_model": default.get(p.get("id")),
            })
        providers = [p for p in providers if p.get("id")]
        providers.sort(key=lambda x: x["id"])
        ts = time.time()
        return {
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
            "_fetched_ts": ts,
            "version": version,
            "count": len(providers),
            "providers": providers,
            "connected": data.get("connected") or [],
        }
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass
        shutil.rmtree(ws, ignore_errors=True)


def fetch_available_providers(force=False):
    """Каталог всех провайдеров, известных opencode.

    Кэшируется в памяти и в CATALOG_FILE (TTL — VIBEPROD_CATALOG_TTL, сутки по умолчанию).
    force=True перечитывает у opencode, при ошибке отдаёт устаревший кэш со stale=True.
    """
    global _catalog_mem, _catalog_mem_at
    now = time.time()
    if not force and _catalog_mem and now - _catalog_mem_at < CATALOG_TTL:
        return _catalog_mem
    with _catalog_lock:
        if not force and _catalog_mem and time.time() - _catalog_mem_at < CATALOG_TTL:
            return _catalog_mem
        disk = load_catalog_from_disk()
        if not force and disk and time.time() - float(disk.get("_fetched_ts") or 0) < CATALOG_TTL:
            _catalog_mem, _catalog_mem_at = disk, time.time()
            return disk
        try:
            data = _fetch_catalog_live()
            data["stale"] = False
        except Exception:
            if disk is not None:
                stale = dict(disk)
                stale["stale"] = True
                _catalog_mem, _catalog_mem_at = stale, time.time()
                return stale
            raise
        try:
            CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CATALOG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except OSError:
            pass
        _catalog_mem, _catalog_mem_at = data, time.time()
        return data


def env_var_for(provider_id):
    if provider_id in ENV_VAR_MAP:
        return ENV_VAR_MAP[provider_id]
    return re.sub(r"[^a-z0-9]", "_", provider_id).upper() + "_API_KEY"


def _gen_test(url, token, provider_id, model_id, timeout=120):
    client = httpx.Client(
        auth=(USERNAME, token),
        trust_env=False,
        timeout=httpx.Timeout(180.0, connect=5.0),
    )
    try:
        session = client.post(f"{url}/session", json={"title": "provider check"}).json()
        sid = session["id"]
        client.post(
            f"{url}/session/{sid}/prompt_async",
            json={
                "parts": [{"type": "text", "text": "Ответь одним словом: ok"}],
                "model": {"providerID": provider_id, "modelID": model_id},
            },
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(2)
            try:
                messages = client.get(f"{url}/session/{sid}/message").json()
            except Exception:
                continue
            for m in reversed(messages):
                info = m.get("info") or {}
                if info.get("role") != "assistant":
                    continue
                err = info.get("error")
                if err:
                    if isinstance(err, dict):
                        msg = (err.get("data") or {}).get("message") or err.get("message") or str(err)
                    else:
                        msg = str(err)
                    return {"ok": False, "model": model_id, "error": str(msg)[:500]}
                if (info.get("time") or {}).get("completed"):
                    texts = [p.get("text", "") for p in m.get("parts", []) if p.get("type") == "text"]
                    return {"ok": True, "model": model_id, "reply": " ".join(texts)[:200]}
        return {"ok": False, "model": model_id, "error": "таймаут тест-запроса"}
    except Exception as exc:
        return {"ok": False, "model": model_id, "error": str(exc)[:500]}
    finally:
        client.close()


def check_provider(provider_id, api_key, deep=True, kind="builtin", base_url="", custom_models=None, label=""):
    """Поднимает probe-контейнер и проверяет провайдера в настоящем opencode.

    kind='openai_compatible' — кастомный провайдер через @ai-sdk/openai-compatible:
    в workspace пробы пишется opencode.json с provider-блоком.

    Возвращает {"ok", "models", "error", "gen": {"ok", "model", "reply"/"error"}}.
    """
    PROBES_DIR.mkdir(parents=True, exist_ok=True)
    ws = Path(tempfile.mkdtemp(prefix="vibeprod-probe-", dir=PROBES_DIR))
    token = secrets.token_urlsafe(24)
    container = None
    try:
        client = docker_client()
        env = {
            "OPENCODE_SERVER_PASSWORD": token,
            "OPENCODE_SERVER_USERNAME": USERNAME,
        }
        if api_key:
            env[env_var_for(provider_id)] = api_key
        if kind == "openai_compatible":
            try:
                models = json.loads(custom_models or "{}") if isinstance(custom_models, str) else (custom_models or {})
            except ValueError:
                models = {}
            if not models:
                raise RuntimeError("custom_models пуст — задайте модели провайдера")
            cfg = {
                "$schema": "https://opencode.ai/config.json",
                "provider": {
                    provider_id: {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": label or provider_id,
                        "options": {
                            "baseURL": base_url,
                            "apiKey": f"{{env:{env_var_for(provider_id)}}}",
                        },
                        "models": models,
                    }
                },
            }
            (ws / "opencode.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        container = client.containers.run(
            image=IMAGE,
            command=_entrypoint_cmd(ensure_image()),
            detach=True,
            working_dir="/workspace",
            environment=env,
            mounts=[Mount(target="/workspace", source=str(_host_path(ws)), type="bind")],
            ports={f"{OPENCODE_PORT}/tcp": None},
            labels={"vibeprod.probe": "1"},
            name=f"vibeprod-probe-{uuid.uuid4().hex[:10]}",
        )
        port = get_host_port(container.id)
        url = wait_healthy(worker_urls(port), token, timeout=90)
        if not url:
            logs = container.logs(tail=40).decode("utf-8", "replace")
            raise RuntimeError(
                f"opencode serve не поднялся (нет ответа на {', '.join(worker_urls(port))}). "
                f"{NETWORK_HINT}\nlogs: {logs[-400:]}"
            )
        r = httpx.get(f"{url}/config/providers", auth=(USERNAME, token), timeout=15, trust_env=False)
        r.raise_for_status()
        data = r.json()
        avail = {
            p.get("id"): sorted((p.get("models") or {}).keys())
            for p in data.get("providers", [])
        }
        if provider_id not in avail:
            others = ", ".join(f"{k} ({', '.join(v[:4])})" for k, v in avail.items()) or "—"
            return {
                "ok": False,
                "models": [],
                "error": f"Провайдер '{provider_id}' не зарегистрировался в opencode "
                         f"(env {env_var_for(provider_id)}). Зарегистрированы: {others}",
                "gen": None,
            }
        models = avail[provider_id]
        details = {}
        for p in data.get("providers", []):
            if p.get("id") == provider_id:
                details = p.get("models") or {}
                break
        gen = None
        if deep and models:
            gen = _gen_test(url, token, provider_id, models[0])
        return {"ok": True, "models": models, "model_details": details, "error": None, "gen": gen}
    except Exception as exc:
        return {"ok": False, "models": [], "model_details": {}, "error": str(exc)[:1000], "gen": None}
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass
        shutil.rmtree(ws, ignore_errors=True)
