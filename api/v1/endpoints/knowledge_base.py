"""Workspace PDF knowledge-base management and retrieval APIs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api.deps import get_database_manager
from src.rag.retrieval import RagSearchError, RagSearchService, check_model_services
from src.services.rag_knowledge_base_service import (
    RagKnowledgeBaseService,
    RagServiceError,
    resolve_rag_blob_path,
)
from src.storage import DatabaseManager

router = APIRouter()


class KnowledgeBaseCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2_000)


class KnowledgeBaseSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    knowledge_base_ids: list[str] = Field(min_length=1, max_length=8)
    top_k: int = Field(default=5, ge=1, le=10)


class DocumentRetryRequest(BaseModel):
    rebuild_index: bool = False


def _scope(request: Request) -> tuple[str, str]:
    return (
        str(getattr(request.state, "tenant_id", "local") or "local"),
        str(getattr(request.state, "owner_id", "admin") or "admin"),
    )


def _service_error(exc: RagServiceError | RagSearchError) -> HTTPException:
    status = 503 if bool(getattr(exc, "retryable", False)) else int(getattr(exc, "status_code", 400))
    return HTTPException(
        status_code=status,
        detail={
            "error": str(getattr(exc, "code", "rag_error")),
            "message": str(exc),
        },
    )


@router.get("/knowledge-bases")
def list_knowledge_bases(
    request: Request,
    db: DatabaseManager = Depends(get_database_manager),
) -> dict[str, Any]:
    tenant_id, owner_id = _scope(request)
    return {
        "items": RagKnowledgeBaseService(db).list_knowledge_bases(
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
    }


@router.post("/knowledge-bases", status_code=201)
def create_knowledge_base(
    payload: KnowledgeBaseCreateRequest,
    request: Request,
    db: DatabaseManager = Depends(get_database_manager),
) -> dict[str, Any]:
    tenant_id, owner_id = _scope(request)
    try:
        item = RagKnowledgeBaseService(db).create_knowledge_base(
            name=payload.name,
            description=payload.description,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
    except RagServiceError as exc:
        raise _service_error(exc) from exc
    return {"knowledge_base": item}


@router.delete("/knowledge-bases/{knowledge_base_id}", status_code=202)
def delete_knowledge_base(
    knowledge_base_id: str,
    request: Request,
    db: DatabaseManager = Depends(get_database_manager),
) -> dict[str, Any]:
    tenant_id, owner_id = _scope(request)
    try:
        result = RagKnowledgeBaseService(db).delete_knowledge_base(
            knowledge_base_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
    except RagServiceError as exc:
        raise _service_error(exc) from exc
    return result


@router.get("/knowledge-bases/{knowledge_base_id}/documents")
def list_knowledge_base_documents(
    knowledge_base_id: str,
    request: Request,
    db: DatabaseManager = Depends(get_database_manager),
) -> dict[str, Any]:
    tenant_id, owner_id = _scope(request)
    try:
        documents = RagKnowledgeBaseService(db).list_documents(
            knowledge_base_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
    except RagServiceError as exc:
        raise _service_error(exc) from exc
    return {"items": documents}


@router.post("/knowledge-bases/{knowledge_base_id}/documents", status_code=202)
async def upload_knowledge_base_document(
    knowledge_base_id: str,
    request: Request,
    file: UploadFile = File(...),
    db: DatabaseManager = Depends(get_database_manager),
) -> dict[str, Any]:
    tenant_id, owner_id = _scope(request)
    try:
        document = await RagKnowledgeBaseService(db).upload_pdf(
            knowledge_base_id,
            file.filename or "document.pdf",
            file,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
    except RagServiceError as exc:
        raise _service_error(exc) from exc
    finally:
        await file.close()
    return {"document": document, "duplicate": bool(document.get("duplicate"))}


@router.get("/knowledge-bases/documents/{document_id}")
def get_knowledge_base_document(
    document_id: str,
    request: Request,
    db: DatabaseManager = Depends(get_database_manager),
) -> dict[str, Any]:
    tenant_id, owner_id = _scope(request)
    try:
        return {"document": RagKnowledgeBaseService(db).get_document(
            document_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )}
    except RagServiceError as exc:
        raise _service_error(exc) from exc


@router.get("/knowledge-bases/documents/{document_id}/preview")
def preview_knowledge_base_document(
    document_id: str,
    request: Request,
    page: int | None = Query(None, ge=1),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: DatabaseManager = Depends(get_database_manager),
) -> dict[str, Any]:
    tenant_id, owner_id = _scope(request)
    try:
        return RagKnowledgeBaseService(db).document_preview(
            document_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            page=page,
            offset=offset,
            limit=limit,
        )
    except RagServiceError as exc:
        raise _service_error(exc) from exc


@router.post("/knowledge-bases/documents/{document_id}/retry", status_code=202)
def retry_knowledge_base_document(
    document_id: str,
    request: Request,
    payload: DocumentRetryRequest = DocumentRetryRequest(),
    db: DatabaseManager = Depends(get_database_manager),
) -> dict[str, Any]:
    tenant_id, owner_id = _scope(request)
    try:
        document = RagKnowledgeBaseService(db).retry_document(
            document_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            rebuild=payload.rebuild_index,
        )
    except RagServiceError as exc:
        raise _service_error(exc) from exc
    return {"document": document}


@router.delete("/knowledge-bases/documents/{document_id}", status_code=202)
def delete_knowledge_base_document(
    document_id: str,
    request: Request,
    db: DatabaseManager = Depends(get_database_manager),
) -> dict[str, Any]:
    tenant_id, owner_id = _scope(request)
    try:
        return {"document": RagKnowledgeBaseService(db).delete_document(
            document_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )}
    except RagServiceError as exc:
        raise _service_error(exc) from exc


@router.get("/knowledge-bases/documents/{document_id}/content")
def get_knowledge_base_document_content(
    document_id: str,
    request: Request,
    db: DatabaseManager = Depends(get_database_manager),
) -> FileResponse:
    tenant_id, owner_id = _scope(request)
    try:
        document = RagKnowledgeBaseService(db).get_document(
            document_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
    except RagServiceError as exc:
        raise _service_error(exc) from exc
    session = db.get_session()
    try:
        from src.storage.models import RagDocument

        record = session.get(RagDocument, document_id)
        path = resolve_rag_blob_path(record.blob_path) if record is not None else Path()
        if record is None or record.tenant_id != tenant_id or record.owner_id != owner_id:
            raise HTTPException(status_code=404, detail="PDF 文档不存在")
        if document["status"] == "deleted" or not path.is_file():
            raise HTTPException(status_code=410, detail="PDF 原件已清理")
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=document["filename"],
            content_disposition_type="inline",
            headers={
                "Cache-Control": "private, max-age=3600",
                "Content-Security-Policy": "sandbox",
                "X-Content-Type-Options": "nosniff",
            },
        )
    finally:
        session.close()


@router.post("/knowledge-bases/search")
def search_knowledge_bases(
    payload: KnowledgeBaseSearchRequest,
    request: Request,
    db: DatabaseManager = Depends(get_database_manager),
) -> dict[str, Any]:
    tenant_id, owner_id = _scope(request)
    try:
        return RagSearchService(database=db).search(
            payload.query,
            knowledge_base_ids=payload.knowledge_base_ids,
            tenant_id=tenant_id,
            owner_id=owner_id,
            top_k=payload.top_k,
        )
    except (RagServiceError, RagSearchError) as exc:
        raise _service_error(exc) from exc


@router.post("/knowledge-bases/model-services/test")
def test_knowledge_base_model_services() -> dict[str, Any]:
    """Probe the configured local embedding and reranker models with real requests."""
    return check_model_services()


__all__ = ["router"]
