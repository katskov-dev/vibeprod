"""API файлов проекта: список/загрузка/скачивание/удаление в MinIO."""
import mimetypes
from pathlib import PurePosixPath

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from minio import S3Error

from .. import auth
from .. import db
from .. import files_store

router = APIRouter(prefix="/api")

MAX_SIZE = 512 * 1024 * 1024


def _project(project_id):
    row = db.query_one("SELECT id, name FROM projects WHERE id=?", (int(project_id),))
    if not row:
        raise HTTPException(404, "проект не найден")
    return row


def _clean_path(raw, allow_empty=False):
    raw = (raw or "").strip().lstrip("/")
    p = PurePosixPath(raw)
    if ".." in p.parts or (not raw and not allow_empty):
        raise HTTPException(400, "недопустимый путь")
    return str(p)


def _content_allowed(project_id, token, request: Request):
    """Контент доступен по токену проекта или авторизованной cookie-сессии."""
    if files_store.check_file_token(project_id, token):
        return True
    return not auth.ENABLED or auth.check_request(request)


@router.get("/files")
def list_files(project_id: int = Query(...), prefix: str = Query(default="")):
    _project(project_id)
    base = prefix.strip("/")
    effective = base + "/" if base else ""
    storage_ok = files_store.healthy()
    files = []
    folders = []
    if storage_ok:
        try:
            objects = files_store.list_objects(project_id, prefix=effective)
        except S3Error as exc:
            raise HTTPException(500, f"хранилище: {exc}")
        seen = set()
        for obj in objects:
            rest = obj["name"][len(effective):]
            if not rest:
                continue
            if "/" in rest:
                folder = rest.split("/", 1)[0]
                if folder and folder not in seen:
                    seen.add(folder)
                    folders.append({"name": folder, "path": f"{base}/{folder}" if base else folder})
            else:
                files.append(obj)
        folders.sort(key=lambda x: x["name"])
    return {
        "project_id": project_id,
        "storage_ok": storage_ok,
        "prefix": base,
        "folders": folders,
        "files": files,
    }


@router.post("/files")
async def upload_file(project_id: int = Query(...), folder: str = Form(default=""), file: UploadFile = File(...)):
    _project(project_id)
    folder = _clean_path(folder, allow_empty=True)
    target = str(PurePosixPath(folder) / PurePosixPath(file.filename or "file").name) if folder else PurePosixPath(file.filename or "file").name
    data = await file.read()
    if len(data) > MAX_SIZE:
        raise HTTPException(413, "файл больше 512 МБ")
    try:
        files_store.upload(project_id, target, data, file.content_type)
    except S3Error as exc:
        raise HTTPException(500, f"хранилище: {exc}")
    except Exception as exc:
        raise HTTPException(500, f"хранилище: {exc}")
    return {"ok": True, "name": target, "url": files_store.content_url(project_id, target)}


@router.get("/files/content")
def file_content(
    request: Request,
    project_id: int = Query(...),
    path: str = Query(...),
    token: str = Query(default=""),
    download: bool = Query(default=False),
):
    _project(project_id)
    if not _content_allowed(project_id, token, request):
        raise HTTPException(403, "нет доступа к файлу")
    name = _clean_path(path)
    try:
        obj = files_store.get_object(project_id, name)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise HTTPException(404, "файл не найден")
        raise HTTPException(500, f"хранилище: {exc}")
    except Exception as exc:
        raise HTTPException(500, f"хранилище: {exc}")
    media = obj.headers.get("Content-Type") or mimetypes.guess_type(name)[0] or "application/octet-stream"
    disp = "attachment" if download else "inline"
    return StreamingResponse(
        obj.stream(64 * 1024),
        media_type=media,
        headers={"Content-Disposition": f'{disp}; filename="{PurePosixPath(name).name}"'},
    )


@router.get("/files/stat")
def file_stat(
    request: Request,
    project_id: int = Query(...),
    path: str = Query(...),
    token: str = Query(default=""),
):
    _project(project_id)
    if not _content_allowed(project_id, token, request):
        raise HTTPException(403, "нет доступа к файлу")
    name = _clean_path(path)
    try:
        st = files_store.stat(project_id, name)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise HTTPException(404, "файл не найден")
        raise HTTPException(500, f"хранилище: {exc}")
    return {
        "name": PurePosixPath(name).name,
        "size": st.size,
        "content_type": st.content_type or mimetypes.guess_type(name)[0] or "",
    }


@router.put("/files")
async def update_file(project_id: int = Query(...), payload: dict = Body(...)):
    """Перезапись содержимого файла (редактор markdown)."""
    _project(project_id)
    path = _clean_path(payload.get("path") or "")
    content = payload.get("content")
    if content is None:
        raise HTTPException(400, "content обязателен")
    data = content.encode("utf-8") if isinstance(content, str) else content
    if len(data) > MAX_SIZE:
        raise HTTPException(413, "файл больше 512 МБ")
    md_suffixes = (".md", ".markdown", ".mdown", ".mkd")
    content_type = (
        payload.get("content_type")
        or mimetypes.guess_type(path)[0]
        or ("text/markdown" if PurePosixPath(path).suffix.lower() in md_suffixes else "application/octet-stream")
    )
    try:
        files_store.upload(project_id, path, data, content_type)
    except S3Error as exc:
        raise HTTPException(500, f"хранилище: {exc}")
    except Exception as exc:
        raise HTTPException(500, f"хранилище: {exc}")
    return {"ok": True, "name": path, "size": len(data)}


@router.delete("/files")
def delete_file(project_id: int = Query(...), path: str = Query(...)):
    _project(project_id)
    name = _clean_path(path)
    try:
        files_store.delete(project_id, name)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise HTTPException(404, "файл не найден")
        raise HTTPException(500, f"хранилище: {exc}")
    except Exception as exc:
        raise HTTPException(500, f"хранилище: {exc}")
    return {"ok": True}
