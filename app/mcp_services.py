"""Управление MCP-сервисами из каталога (отдельные docker-контейнеры)."""
import logging
from pathlib import Path

import docker

from .docker_runner import docker_client, ensure_mcp_network

log = logging.getLogger("harness.mcp")

HARNESS_ROOT = Path(__file__).resolve().parent.parent


def build_dir(entry):
    path = entry.get("service_build_dir")
    return (HARNESS_ROOT / path).resolve() if path else None


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
    """Поднимает docker-контейнер сервиса (сборка образа при необходимости)."""
    name = entry["service_container"]
    ensure_mcp_network()
    client = docker_client()
    try:
        c = client.containers.get(name)
        if c.status != "running":
            c.start()
        return c
    except docker.errors.NotFound:
        pass
    bdir = build_dir(entry)
    if not bdir:
        raise RuntimeError(f"для сервиса {name} не задан build_dir")
    tag = f"harness-mcp-{name}:latest"
    try:
        client.images.get(tag)
    except docker.errors.ImageNotFound:
        log.info("building image %s from %s", tag, bdir)
        client.images.build(path=str(bdir), tag=tag, rm=True)
    return client.containers.run(
        tag,
        name=name,
        detach=True,
        network=entry.get("service_network") or "harness-mcp",
        restart_policy={"Name": "unless-stopped"},
    )


def stop_service(container_name):
    if not container_name:
        return False
    try:
        docker_client().containers.get(container_name).stop()
        return True
    except docker.errors.NotFound:
        return False
