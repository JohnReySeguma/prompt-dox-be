"""Local file storage for uploads: UPLOAD_DIR/{workspaceId}/{documentId}{ext}."""

import shutil
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings

_READ_CHUNK = 1024 * 1024


class FileTooLargeError(Exception):
    pass


def upload_root() -> Path:
    return settings.upload_dir.resolve()


def document_path(workspace_id: str, document_id: str, extension: str) -> Path:
    return upload_root() / workspace_id / f"{document_id}{extension}"


async def save_upload(upload: UploadFile, destination: Path, max_bytes: int) -> int:
    """Stream the upload to disk, enforcing `max_bytes` on the bytes actually read."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    written = 0
    try:
        with temporary.open("wb") as handle:
            while chunk := await upload.read(_READ_CHUNK):
                written += len(chunk)
                if written > max_bytes:
                    raise FileTooLargeError
                handle.write(chunk)
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return written


def delete_file(path: str | None) -> None:
    if path:
        Path(path).unlink(missing_ok=True)


def delete_workspace_files(workspace_id: str) -> None:
    shutil.rmtree(upload_root() / workspace_id, ignore_errors=True)
