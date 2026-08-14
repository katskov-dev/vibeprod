"""Управление opencode-контейнерами через Docker SDK."""
import logging
import os
from pathlib import Path

import docker
from docker.types import Mount

log = logging.getLogger("harness.docker")

IMAGE = os.environ.get("HARNESS_OPENCODE_IMAGE", "harness-opencode:latest")
OPENCODE_PORT = 4096
LABEL = "harness.session"

HARNESS_ROOT = Path(__file__).resolve().parent.parent
WORKER_BUILD_DIR = Path(os.environ.get("HARNESS_WORKER_BUILD_DIR", HARNESS_ROOT / "worker"))


MCP_NETWORK = "harness-mcp"


def ensure_mcp_network():
    """Сеть для MCP-сервисов (playwright и т.п.). Воркеры подключаются к ней."""
    client = _client()
    try:
        client.networks.get(MCP_NETWORK)
    except docker.errors.NotFound:
        client.networks.create(MCP_NETWORK, driver="bridge")


def _client():
    return docker.from_env()


def ensure_image():
    c = _client()
    try:
        return c.images.get(IMAGE)
    except docker.errors.ImageNotFound:
        pass
    if WORKER_BUILD_DIR.exists():
        log.info("building worker image %s from %s", IMAGE, WORKER_BUILD_DIR)
        image, _ = c.images.build(path=str(WORKER_BUILD_DIR), tag=IMAGE, rm=True)
        return image
    log.info("pulling worker image %s", IMAGE)
    return c.images.pull(IMAGE)


def _entrypoint_cmd(image_obj):
    cfg = image_obj.attrs.get("Config", {}) if hasattr(image_obj, "attrs") else {}
    entry = cfg.get("Entrypoint") or []
    joined = " ".join(entry)
    if joined and "opencode" in joined.lower():
        return ["serve", "--hostname", "0.0.0.0", "--port", str(OPENCODE_PORT), "--print-logs"]
    return ["opencode", "serve", "--hostname", "0.0.0.0", "--port", str(OPENCODE_PORT), "--print-logs"]


def worker_env(auth_token, extra_provider_env=None):
    """Env для opencode-контейнера: basic auth + API-ключи провайдеров.

    Ключи из DB (extra_provider_env) имеют приоритет над *_API_KEY из env брокера.
    """
    env = {
        "OPENCODE_SERVER_PASSWORD": auth_token,
        "OPENCODE_SERVER_USERNAME": "opencode",
    }
    extra = extra_provider_env or {}
    for key, value in os.environ.items():
        if key.endswith("_API_KEY") and value and key not in extra:
            env[key] = value
    env.update(extra)
    return env


def run_worker(session_id, host_ws_dir, storage_name, auth_token, extra_provider_env=None):
    """Поднимает контейнер opencode serve. Возвращает container_id."""
    img = ensure_image()
    client = _client()
    ensure_mcp_network()
    container = client.containers.run(
        image=IMAGE,
        command=_entrypoint_cmd(img),
        detach=True,
        working_dir="/workspace",
        environment=worker_env(auth_token, extra_provider_env),
        mounts=[
            Mount(target="/workspace", source=str(host_ws_dir), type="bind"),
            Mount(target="/root/.local/share/opencode", source=storage_name, type="volume"),
        ],
        ports={f"{OPENCODE_PORT}/tcp": None},
        labels={LABEL: session_id},
        name=f"harness-{session_id[:12]}",
        network=MCP_NETWORK,
        extra_hosts={"host.docker.internal": "host-gateway"},
    )
    return container.id


def get_host_port(container_id):
    client = _client()
    container = client.containers.get(container_id)
    container.reload()
    ports = container.ports.get(f"{OPENCODE_PORT}/tcp")
    if ports and ports[0].get("HostPort"):
        return ports[0]["HostPort"]
    raise RuntimeError("container has no published port")


def container_exists(container_id):
    if not container_id:
        return False
    try:
        _client().containers.get(container_id)
        return True
    except docker.errors.NotFound:
        return False


def container_logs(container_id, tail=60):
    try:
        return _client().containers.get(container_id).logs(tail=tail).decode("utf-8", "replace")
    except docker.errors.NotFound:
        return ""


def kill_worker(container_id, storage_name=None, remove_volume=False):
    if container_id:
        try:
            container = _client().containers.get(container_id)
            container.remove(force=True)
        except docker.errors.NotFound:
            pass
    if remove_volume and storage_name:
        try:
            _client().volumes.get(storage_name).remove(force=True)
        except docker.errors.NotFound:
            pass


def list_harness_containers():
    return _client().containers.list(all=True, filters={"label": LABEL})


def list_probe_containers():
    return _client().containers.list(all=True, filters={"label": "harness.probe"})


def container_session_id(container):
    return container.labels.get(LABEL)


def docker_client():
    return _client()
