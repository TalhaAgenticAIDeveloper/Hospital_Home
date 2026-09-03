"""
File upload utilities for saving, validating, and deleting uploaded files.
"""

import os
import shutil
import uuid
from pathlib import Path
from typing import List, Tuple

from fastapi import HTTPException, UploadFile, status

from app.core.config import get_settings

settings = get_settings()


def validate_file(
    file: UploadFile,
    max_size_bytes: int | None = None,
    allowed_types: List[str] | None = None,
) -> None:
    """
    Validate uploaded file size and content type.

    Raises:
        HTTPException(422): If file is invalid or too large.
    """
    max_size = max_size_bytes or settings.max_upload_size_bytes
    allowed = allowed_types or settings.allowed_upload_types_list

    # Check content type
    if file.content_type and allowed and file.content_type not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File type '{file.content_type}' is not allowed. Allowed types: {', '.join(allowed)}",
        )

    # Validate file size
    file.file.seek(0, os.SEEK_END)
    size = file.file.tell()
    file.file.seek(0)

    if size == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="File cannot be empty.",
        )

    if size > max_size:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File size exceeds maximum allowed size of {settings.MAX_UPLOAD_SIZE_MB}MB.",
        )


async def save_upload_file(
    file: UploadFile,
    custom_dir: str | None = None,
) -> Tuple[str, str, str, int, str]:
    """
    Save an uploaded file to disk with a secure UUID filename.

    Returns:
        (original_filename, stored_filename, file_path, file_size, mime_type)
    """
    upload_dir = Path(custom_dir or settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    original_filename = file.filename or "unknown"
    ext = Path(original_filename).suffix.lower()
    stored_filename = f"{uuid.uuid4()}{ext}"
    dest_path = upload_dir / stored_filename

    # Read and save file content
    content = await file.read()
    file_size = len(content)

    with open(dest_path, "wb") as f:
        f.write(content)

    mime_type = file.content_type or "application/octet-stream"

    return original_filename, stored_filename, str(dest_path), file_size, mime_type


def delete_file_from_disk(file_path: str) -> None:
    """Safely delete a file from disk if it exists."""
    try:
        path = Path(file_path)
        if path.exists() and path.is_file():
            path.unlink()
    except Exception:
        pass
