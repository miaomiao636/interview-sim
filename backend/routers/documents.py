"""本地文档提取 API。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, UploadFile

from ..document_parser import DocumentParseError, MAX_UPLOAD_BYTES, extract_document


router = APIRouter()


@router.post("/api/documents/extract")
async def extract_uploaded_document(file: UploadFile):
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    try:
        return extract_document(file.filename or "", content, file.content_type or "")
    except DocumentParseError as exc:
        status = 413 if exc.code in {"FILE_TOO_LARGE", "IMAGE_TOO_LARGE"} else 422
        raise HTTPException(
            status_code=status,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
