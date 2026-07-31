"""Authenticated access to conversation-owned text document resources."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from src.services.text_document_service import read_text_resource
from src.storage import DatabaseManager


def _owned_document(
    resource_id: str,
    request: Request,
    db: DatabaseManager,
) -> dict:
    document = db.get_text_document(
        resource_id,
        tenant_id=str(getattr(request.state, "tenant_id", "local")),
        owner_id=str(getattr(request.state, "owner_id", "admin")),
    )
    if document is None:
        raise HTTPException(status_code=404, detail="文本资源不存在")
    return document


@router.get("/agent/resources/{resource_id}")
def get_agent_text_resource(
    resource_id: str,
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(12, ge=1, le=100),
    page_start: int | None = Query(None, ge=1),
    page_end: int | None = Query(None, ge=1),
    db: DatabaseManager = Depends(get_database_manager),
):
    document = _owned_document(resource_id, request, db)
    resource, chunks = read_text_resource(
        db=db,
        resource_id=resource_id,
        offset=offset,
        limit=limit,
        page_start=page_start,
        page_end=page_end,
    )
    return {
        "resource": resource,
        "chunks": chunks,
        "returned_count": len(chunks),
        "has_more": offset + len(chunks)
        < int(document.get("chunk_count") or 0),
    }


@router.get("/agent/resources/{resource_id}/content")
def get_agent_text_resource_content(
    resource_id: str,
    request: Request,
    disposition: str = Query(
        "inline",
        pattern=r"^(inline|attachment)$",
    ),
    db: DatabaseManager = Depends(get_database_manager),
):
    document = _owned_document(resource_id, request, db)
    path = Path(str(document.get("blob_path") or ""))
    if not path.is_file():
        raise HTTPException(status_code=410, detail="原文件已不可用")
    declared_mime = str(
        document.get("mime_type")
        or "application/octet-stream"
    )
    response_mime = (
        "text/plain; charset=utf-8"
        if disposition == "inline"
        and declared_mime
        in {
            "application/xhtml+xml",
            "application/xml",
            "text/html",
        }
        else declared_mime
    )
    return FileResponse(
        path,
        media_type=response_mime,
        filename=str(document.get("filename") or "document"),
        content_disposition_type=disposition,
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = [
    "get_agent_text_resource",
    "get_agent_text_resource_content",
]
