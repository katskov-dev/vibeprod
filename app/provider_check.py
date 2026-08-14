"""Проверка провайдера: короткоживущий opencode-контейнер с ключом в env.

Проверяет то же, что будет происходить в реальном воркере:
1. регистрация провайдера в opencode (/config/providers);
2. список моделей;
3. опционально — реальный тест-запрос к модели (deep-проверка).
"""
import re
import secrets
import shutil
import tempfile
import time
import uuid

import httpx
from docker.types import Mount

from .docker_runner import (
    IMAGE,
    OPENCODE_PORT,
    docker_client,
    ensure_image,
    get_host_port,
    _entrypoint_cmd,
)
from .opencode_client import USERNAME, wait_healthy

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


def check_provider(provider_id, api_key, deep=True):
    """Поднимает probe-контейнер и проверяет провайдера в настоящем opencode.

    Возвращает {"ok", "models", "error", "gen": {"ok", "model", "reply"/"error"}}.
    """
    ws = tempfile.mkdtemp(prefix="harness-probe-")
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
        container = client.containers.run(
            image=IMAGE,
            command=_entrypoint_cmd(ensure_image()),
            detach=True,
            working_dir="/workspace",
            environment=env,
            mounts=[Mount(target="/workspace", source=ws, type="bind")],
            ports={f"{OPENCODE_PORT}/tcp": None},
            labels={"harness.probe": "1"},
            name=f"harness-probe-{uuid.uuid4().hex[:10]}",
        )
        port = get_host_port(container.id)
        url = f"http://127.0.0.1:{port}"
        if not wait_healthy(url, token, timeout=90):
            logs = container.logs(tail=40).decode("utf-8", "replace")
            raise RuntimeError(f"opencode serve не поднялся: {logs[-400:]}")
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
