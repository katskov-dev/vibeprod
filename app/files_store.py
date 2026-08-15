"""Хранилище файлов проектов: MinIO (S3), бакет на проект.

Бакет проекта: vibeprod-p{project_id}. Токен доступа к файлам проекта —
projects.file_token, проверяется при отдаче контента (позволяет вставлять
ссылки на файлы в сообщения агентов и делиться ими без cookie-авторизации).
"""
import logging
import mimetypes
import os
import secrets
import threading

from minio import Minio

from . import db

log = logging.getLogger("vibeprod.files")

ENDPOINT = os.environ.get("VIBEPROD_S3_ENDPOINT", "http://127.0.0.1:9000").rstrip("/")
ACCESS_KEY = os.environ.get("VIBEPROD_S3_ACCESS_KEY", "minioadmin")
SECRET_KEY = os.environ.get("VIBEPROD_S3_SECRET_KEY", "minioadmin")

_client = None
_lock = threading.Lock()


def client():
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = Minio(
                    ENDPOINT.replace("http://", "").replace("https://", ""),
                    access_key=ACCESS_KEY,
                    secret_key=SECRET_KEY,
                    secure=ENDPOINT.startswith("https://"),
                )
    return _client


def bucket_name(project_id):
    return f"vibeprod-p{project_id}"


def ensure_bucket(project_id):
    c = client()
    b = bucket_name(project_id)
    if not c.bucket_exists(b):
        c.make_bucket(b)
    return b


def healthy():
    try:
        client().list_buckets()
        return True
    except Exception as exc:
        log.warning("s3 health: %s", exc)
        return False


def file_token(project_id):
    row = db.query_one("SELECT file_token FROM projects WHERE id=?", (int(project_id),))
    return row["file_token"] if row else None


def check_file_token(project_id, token):
    if not token:
        return False
    stored = file_token(project_id)
    return bool(stored) and secrets.compare_digest(stored, token)


def content_url(project_id, path, token=None):
    token = token or file_token(project_id) or ""
    return f"/api/files/content?project_id={project_id}&path={path}&token={token}"


def list_objects(project_id, prefix=""):
    items = []
    try:
        for obj in client().list_objects(ensure_bucket(project_id), prefix=prefix or "", recursive=True):
            items.append({
                "name": obj.object_name,
                "size": obj.size,
                "last_modified": obj.last_modified.isoformat(timespec="seconds") if obj.last_modified else None,
                "content_type": obj.content_type
                or mimetypes.guess_type(obj.object_name)[0]
                or "",
                "url": content_url(project_id, obj.object_name),
            })
    except Exception as exc:
        log.exception("list files %s: %s", project_id, exc)
        raise
    items.sort(key=lambda x: x["name"])
    return items


def upload(project_id, path, data, content_type, size=None):
    import io

    if isinstance(data, str):
        data = data.encode("utf-8")
    if isinstance(data, (bytes, bytearray)):
        size = size if size is not None else len(data)
        data = io.BytesIO(data)
    client().put_object(
        ensure_bucket(project_id),
        path,
        data,
        length=size if size is not None else getattr(data, "getbuffer", None).nbytes,
        content_type=content_type or "application/octet-stream",
    )


def get_object(project_id, path):
    return client().get_object(bucket_name(project_id), path)


def stat(project_id, path):
    return client().stat_object(bucket_name(project_id), path)


def delete(project_id, path):
    client().remove_object(bucket_name(project_id), path)


def delete_all(project_id):
    """Удаляет бакет проекта вместе с файлами."""
    b = bucket_name(project_id)
    c = client()
    if c.bucket_exists(b):
        for obj in c.list_objects(b, recursive=True):
            c.remove_object(b, obj.object_name)
        c.remove_bucket(b)


def broker_url():
    """URL брокера, который воркеры вставляют в ссылки на файлы проекта."""
    env = os.environ.get("VIBEPROD_BROKER_URL")
    if env:
        return env.rstrip("/")
    port = os.environ.get("VIBEPROD_PORT", "8000")
    return f"http://host.docker.internal:{port}"
