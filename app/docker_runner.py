"""Управление opencode-контейнерами через Docker SDK."""
import hashlib
import logging
import os
from pathlib import Path

import docker
from docker.types import Mount

log = logging.getLogger("vibeprod.docker")

IMAGE = os.environ.get("VIBEPROD_OPENCODE_IMAGE", "vibeprod-opencode:latest")
OPENCODE_PORT = 4096
LABEL = "vibeprod.session"

VIBEPROD_ROOT = Path(__file__).resolve().parent.parent
WORKER_BUILD_DIR = Path(os.environ.get("VIBEPROD_WORKER_BUILD_DIR", VIBEPROD_ROOT / "worker"))
DATA_DIR = Path(os.environ.get("VIBEPROD_DATA_DIR", VIBEPROD_ROOT / "data"))
# Хеш контекста сборки воркера: если worker/Dockerfile изменился после
# git pull — образ пересобирается при следующем запуске сессии.
WORKER_HASH_FILE = DATA_DIR / "worker_image.hash"


MCP_NETWORK = "vibeprod-mcp"

# Подсказка для диагностики «opencode serve не поднялся»: чаще всего это
# сеть (брокер в bridge-сети не видит порт воркера на 127.0.0.1) или
# недоступный docker-демон.
NETWORK_HINT = (
    "Брокер должен видеть порт воркера (пробуем 127.0.0.1 и host.docker.internal): "
    "если брокер запущен в docker — в compose.yaml нужен network_mode: host "
    "(в bridge-сети 127.0.0.1 — это loopback самого брокера, а не хоста); "
    "если брокер на хосте — проверьте, что docker-демон запущен и порт опубликован."
)


def daemon_ok():
    """Доступен ли docker-демон (проверка на старте брокера и в /api/health)."""
    try:
        return bool(_client().ping())
    except Exception as exc:
        log.warning("docker daemon unreachable: %s", exc)
        return False


def ensure_mcp_network():
    """Сеть для MCP-сервисов (playwright и т.п.). Воркеры подключаются к ней."""
    client = _client()
    try:
        client.networks.get(MCP_NETWORK)
    except docker.errors.NotFound:
        client.networks.create(MCP_NETWORK, driver="bridge")


def _client():
    return docker.from_env()


def _worker_context_hash():
    h = hashlib.sha256()
    if WORKER_BUILD_DIR.exists():
        for f in sorted(WORKER_BUILD_DIR.rglob("*")):
            if f.is_file():
                h.update(str(f.relative_to(WORKER_BUILD_DIR)).encode())
                h.update(f.read_bytes())
    return h.hexdigest()


def ensure_image():
    c = _client()
    try:
        img = c.images.get(IMAGE)
    except docker.errors.ImageNotFound:
        img = None
    if WORKER_BUILD_DIR.exists():
        digest = _worker_context_hash()
        if (
            img is not None
            and WORKER_HASH_FILE.exists()
            and WORKER_HASH_FILE.read_text(encoding="utf-8").strip() == f"{IMAGE} {digest}"
        ):
            return img
        log.info("building worker image %s from %s", IMAGE, WORKER_BUILD_DIR)
        image, _ = c.images.build(path=str(WORKER_BUILD_DIR), tag=IMAGE, rm=True)
        WORKER_HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
        WORKER_HASH_FILE.write_text(f"{IMAGE} {digest}", encoding="utf-8")
        return image
    if img is not None:
        return img
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
        name=f"vibeprod-{session_id[:12]}",
        network=MCP_NETWORK,
        mem_limit=os.environ.get("VIBEPROD_WORKER_MEM") or "1024m",
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


def list_vibeprod_containers():
    return _client().containers.list(all=True, filters={"label": LABEL})


def list_probe_containers():
    return _client().containers.list(all=True, filters={"label": "vibeprod.probe"})


def container_session_id(container):
    return container.labels.get(LABEL)


def docker_client():
    return _client()
