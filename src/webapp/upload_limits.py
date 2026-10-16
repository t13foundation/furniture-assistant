from __future__ import annotations

from fastapi import HTTPException, UploadFile


async def check_file_size(file: UploadFile, max_mb: int) -> None:
    limit = max_mb * 1024 * 1024
    if file.size is not None:
        size = file.size
    else:
        size = 0
        while chunk := await file.read(64 * 1024):
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, f"File {file.filename or 'upload'} exceeds {max_mb} MB")
        await file.seek(0)
    if size > limit:
        raise HTTPException(413, f"File {file.filename or 'upload'} exceeds {max_mb} MB")
