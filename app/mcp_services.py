"""Управление MCP-сервисами из каталога (отдельные docker-контейнеры)."""
import logging
import os
from pathlib import Path

import docker

from .docker_runner import docker_client, ensure_mcp_network

log = logging.getLogger("vibeprod.mcp")

VIBEPROD_ROOT = Path(__file__).resolve().parent.parent


def _service_env():
    """Env для docker-сервисов: прокидываем S3-доступ (файлы проекта).

    Endpoint брокера в host-сети — 127.0.0.1:9000, но для контейнеров-сервисов
    (другая сеть) это их собственный loopback: заменяем на имя контейнера
    MinIO в сети vibeprod-mcp. Внешний S3 прокидывается как есть.
    """
    env = {k: v for k, v in os.environ.items() if k.startswith("VIBEPROD_S3_") and v}
    endpoint = env.get("VIBEPROD_S3_ENDPOINT") or ""
    if "127.0.0.1" in endpoint or "localhost" in endpoint:
        env["VIBEPROD_S3_ENDPOINT"] = "http://vibeprod-minio:9000"
    return env


def build_dir(entry):
    path = entry.get("service_build_dir")
    return (VIBEPROD_ROOT / path).resolve() if path else None


def service_status(container_name):
    if not container_name:
        return None
    try:
        c = docker_client().containers.get(container_name)
        return c.status
    except docker.errors.NotFound:
        return None
    except docker.errors.APIError as exc:
        log.warning("service_status %s: %s", container_name, exc)
        return None


def ensure_running(entry):
    """Поднимает docker-контейнер сервиса (сборка образа при необходимости).

    Если контейнер уже есть, но env изменился (например, S3-креды/endpoint) —
    пересоздаём, иначе старые значения остались бы навсегда.
    """
    name = entry["service_container"]
    ensure_mcp_network()
    client = docker_client()
    want_env = _service_env()
    try:
        c = client.containers.get(name)
        got_env = {}
        for e in c.attrs.get("Config", {}).get("Env") or []:
            if "=" in e:
                k, v = e.split("=", 1)
                got_env[k] = v
        mismatch = {k: v for k, v in want_env.items() if got_env.get(k) != v}
        if not mismatch:
            if c.status != "running":
                c.start()
            return c
        log.info("пересоздаю %s: изменился env (%s)", name, ", ".join(mismatch))
        c.remove(force=True)
    except docker.errors.NotFound:
        pass
    bdir = build_dir(entry)
    if not bdir:
        raise RuntimeError(f"для сервиса {name} не задан build_dir")
    tag = f"vibeprod-mcp-{name}:latest"
    try:
        client.images.get(tag)
    except docker.errors.ImageNotFound:
        log.info("building image %s from %s", tag, bdir)
        client.images.build(path=str(bdir), tag=tag, rm=True)
    return client.containers.run(
        tag,
        name=name,
        detach=True,
        network=entry.get("service_network") or "vibeprod-mcp",
        environment=want_env,
        mem_limit=os.environ.get("VIBEPROD_MCP_SERVICE_MEM") or "768m",
        restart_policy={"Name": "unless-stopped"},
        extra_hosts={"host.docker.internal": "host-gateway"},
    )


def stop_service(container_name):
    if not container_name:
        return False
    try:
        docker_client().containers.get(container_name).stop()
        return True
    except docker.errors.NotFound:
        return False
