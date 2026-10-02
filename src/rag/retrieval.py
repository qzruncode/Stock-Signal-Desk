"""Scoped hybrid retrieval and citation projection for PDF knowledge bases."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from collections import defaultdict
from typing import Any, Mapping, Sequence

from sqlalchemy import select

from src.core.config_manager import ConfigManager
from src.rag.model_adapters import (
    DEFAULT_EMBEDDING_BASE_URL,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_REVISION,
    DEFAULT_RERANK_BASE_URL,
    DEFAULT_RERANK_MODEL,
    DEFAULT_RERANK_REVISION,
    DEFAULT_VECTOR_DIMENSION,
    LocalFastEmbedAdapter,
    LocalRerankerAdapter,
    RAGProviderError,
    model_version_tag,
)
from src.rag.qdrant_store import RRF_K, QdrantStore
from src.services.rag_knowledge_base_service import RagKnowledgeBaseService, RagServiceError
from src.storage import DatabaseManager
from src.storage.models import RagChunk, RagDocument, RagKnowledgeBase

logger = logging.getLogger(__name__)
DEFAULT_RERANK_CANDIDATE_LIMIT = 12
_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_PATTERN = re.compile(r"[A-Za-z]")
_PDF_PAGE_RANGE_PATTERN = re.compile(
    r"第\s*(\d{1,3})\s*页?\s*(?:到|至|[-–—])\s*(?:第\s*)?(\d{1,3})\s*页"
)
_PDF_PAGE_PATTERN = re.compile(r"第\s*(\d{1,3})\s*页(?!\d)")
_LATIN_PAGE_RANGE_PATTERN = re.compile(
    r"\bpages?\s+(\d{1,3})\s*(?:to|through|[-–—])\s*(\d{1,3})\b", re.IGNORECASE
)
_LATIN_PAGE_PATTERN = re.compile(
    r"\bpages?\s*(?:no\.?\s*)?(\d{1,3})\b|\bpp?\.\s*(\d{1,3})\b",
    re.IGNORECASE,
)


def _query_script(query: str) -> str | None:
    cjk_count = len(_CJK_PATTERN.findall(query))
    latin_count = len(_LATIN_PATTERN.findall(query))
    if cjk_count > latin_count:
        return "cjk"
    if latin_count:
        return "latin"
    return None


def _explicit_pdf_page_ranges(query: str) -> list[tuple[int, int]]:
    """Extract explicit PDF page references so retrieval can enforce them."""
    text = str(query or "")
    ranges: list[tuple[int, int]] = []
    occupied: list[tuple[int, int]] = []
    for pattern in (_PDF_PAGE_RANGE_PATTERN, _LATIN_PAGE_RANGE_PATTERN):
        for match in pattern.finditer(text):
            start, end = int(match.group(1)), int(match.group(2))
            if start < 1 or end < start or end > 500:
                continue
            ranges.append((start, end))
            occupied.append(match.span())

    for pattern in (_PDF_PAGE_PATTERN, _LATIN_PAGE_PATTERN):
        for match in pattern.finditer(text):
            if any(start <= match.start() < end for start, end in occupied):
                continue
            number = next((int(value) for value in match.groups() if value), 0)
            if 1 <= number <= 500:
                ranges.append((number, number))

    return list(dict.fromkeys(ranges))


def _rerank_query(queries: Sequence[str], candidates: Sequence[Mapping[str, Any]]) -> str:
    """Use the query language that best matches the retrieved passages."""
    text = "\n".join(str(item.get("text") or "")[:1_000] for item in candidates[:8])
    passage_script = _query_script(text)
    if passage_script is None:
        return queries[0]
    return next((query for query in queries if _query_script(query) == passage_script), queries[0])


def _fuse_hybrid_and_reranker_rankings(
    candidates: Sequence[Mapping[str, Any]],
    reranked: Sequence[Any],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    """Fuse first-stage hybrid and cross-encoder ranks without letting either erase the other."""
    if not candidates or not reranked:
        return []

    by_id: dict[str, Mapping[str, Any]] = {}
    scores: dict[str, float] = defaultdict(float)
    reranker_positions: dict[str, int] = {}
    hybrid_positions: dict[str, int] = {}
    for position, item in enumerate(candidates, start=1):
        identifier = str(item.get("chunk_id") or "")
        if not identifier:
            continue
        by_id[identifier] = item
        hybrid_positions[identifier] = position
        scores[identifier] += 1.0 / (RRF_K + position)

    for position, result in enumerate(reranked, start=1):
        try:
            candidate = candidates[int(result.index)]
        except (AttributeError, IndexError, TypeError, ValueError):
            continue
        identifier = str(candidate.get("chunk_id") or "")
        if not identifier or identifier not in by_id:
            continue
        reranker_positions[identifier] = position
        scores[identifier] += 1.0 / (RRF_K + position)

    ordered = sorted(
        by_id,
        key=lambda identifier: (
            scores[identifier],
            -reranker_positions.get(identifier, len(reranked) + 1),
            -hybrid_positions[identifier],
        ),
        reverse=True,
    )
    return [dict(by_id[identifier]) for identifier in ordered[: max(1, int(top_k))]]


class RagSearchError(RuntimeError):
    def __init__(self, message: str, *, code: str = "retrieval_failed", retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _runtime_config() -> dict[str, str]:
    from src.config import setup_env

    setup_env()
    values = ConfigManager().read_config_map()
    for key in (
        "RAG_EMBEDDING_BASE_URL",
        "RAG_EMBEDDING_MODEL_ID",
        "RAG_EMBEDDING_MODEL_REVISION",
        "RAG_RERANK_BASE_URL",
        "RAG_RERANK_MODEL_ID",
        "RAG_RERANK_MODEL_REVISION",
        "RAG_RERANK_CANDIDATE_LIMIT",
        "RAG_VECTOR_DIMENSION",
    ):
        if key in os.environ:
            values[key] = str(os.environ[key])
    return values


def check_model_services(
    *,
    embedding_factory: Any = LocalFastEmbedAdapter,
    reranker_factory: Any = LocalRerankerAdapter,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Exercise both configured local inference services with small real requests."""
    settings = dict(config) if config is not None else _runtime_config()
    embedding_model = str(settings.get("RAG_EMBEDDING_MODEL_ID") or DEFAULT_EMBEDDING_MODEL).strip()
    embedding_revision = str(
        settings.get("RAG_EMBEDDING_MODEL_REVISION") or DEFAULT_EMBEDDING_REVISION
    ).strip()
    reranker_model = str(settings.get("RAG_RERANK_MODEL_ID") or DEFAULT_RERANK_MODEL).strip()
    reranker_revision = str(settings.get("RAG_RERANK_MODEL_REVISION") or DEFAULT_RERANK_REVISION).strip()
    embedding_result: dict[str, Any] = {
        "success": False,
        "model": model_version_tag(embedding_model, embedding_revision),
        "dimension": None,
        "latency_ms": None,
        "message": "Embedding 服务尚未检查。",
        "error_code": None,
        "retryable": False,
    }
    embedding = None
    started_at = time.perf_counter()
    try:
        embedding = embedding_factory(
            base_url=str(settings.get("RAG_EMBEDDING_BASE_URL") or DEFAULT_EMBEDDING_BASE_URL),
            model=embedding_model,
            dimension=int(settings.get("RAG_VECTOR_DIMENSION") or DEFAULT_VECTOR_DIMENSION),
            timeout=None,
        )
        vector = embedding.embed_query("知识库中的主要结论是什么？")
        embedding_result.update(
            success=bool(vector),
            dimension=len(vector),
            latency_ms=int((time.perf_counter() - started_at) * 1000),
            message=(f"Embedding 服务正常，返回 {len(vector)} 维向量。" if vector else "Embedding 服务未返回向量。"),
            error_code=None if vector else "empty_response",
        )
    except RAGProviderError as exc:
        embedding_result.update(message=str(exc), error_code=exc.code, retryable=exc.retryable)
    except Exception:
        embedding_result.update(
            message="Embedding 服务检查失败，请核对服务地址、模型 revision 和日志。",
            error_code="model_check_failed",
        )
    finally:
        if embedding is not None:
            embedding.close()

    reranker_result: dict[str, Any] = {
        "success": False,
        "model": model_version_tag(reranker_model, reranker_revision),
        "latency_ms": None,
        "message": "Reranker 服务尚未检查。",
        "error_code": None,
        "retryable": False,
    }
    reranker = None
    started_at = time.perf_counter()
    try:
        reranker = reranker_factory(
            base_url=str(settings.get("RAG_RERANK_BASE_URL") or DEFAULT_RERANK_BASE_URL),
            model=reranker_model,
            timeout=None,
        )
        ranked = reranker.rerank(
            "公司的主营业务是什么？",
            ["公司主营业务是工业视觉检测。", "今天的天气晴朗。"],
            top_n=2,
        )
        reranker_result.update(
            success=bool(ranked),
            latency_ms=int((time.perf_counter() - started_at) * 1000),
            message=(f"Reranker 服务正常，返回 {len(ranked)} 条排序结果。" if ranked else "Reranker 服务未返回排序结果。"),
            error_code=None if ranked else "empty_response",
        )
    except RAGProviderError as exc:
        reranker_result.update(message=str(exc), error_code=exc.code, retryable=exc.retryable)
    except Exception:
        reranker_result.update(
            message="Reranker 服务检查失败，请核对服务地址、模型 revision 和日志。",
            error_code="model_check_failed",
        )
    finally:
        if reranker is not None:
            reranker.close()

    return {
        "success": embedding_result["success"] and reranker_result["success"],
        "embedding": embedding_result,
        "reranker": reranker_result,
    }


class RagSearchService:
    """One implementation shared by chat, tool execution, and the search UI."""

    def __init__(
        self,
        *,
        knowledge_service: RagKnowledgeBaseService | None = None,
        database: DatabaseManager | None = None,
        embedding_factory: Any = LocalFastEmbedAdapter,
        reranker_factory: Any = LocalRerankerAdapter,
        store_factory: Any = QdrantStore,
    ) -> None:
        self.db = database or DatabaseManager.get_instance()
        self.knowledge = knowledge_service or RagKnowledgeBaseService(self.db)
        self.embedding_factory = embedding_factory
        self.reranker_factory = reranker_factory
        self.store_factory = store_factory

    def search(
        self,
        query: str,
        *,
        knowledge_base_ids: Sequence[str],
        tenant_id: str,
        owner_id: str,
        top_k: int = 5,
        candidate_limit: int = 30,
    ) -> dict[str, Any]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            raise RagSearchError("检索问题不能为空。", code="query_empty")
        normalized_queries = [normalized_query]
        explicit_page_ranges = _explicit_pdf_page_ranges(normalized_query)
        selected_ids = list(dict.fromkeys(str(item).strip() for item in knowledge_base_ids if str(item).strip()))
        if not selected_ids:
            return {
                "success": True,
                "query": normalized_query,
                "results": [],
                "result_items": [],
                "no_evidence": True,
                "data_time_applicable": False,
            }
        if len(selected_ids) > 8:
            raise RagSearchError("一次最多选择 8 个知识库。", code="too_many_knowledge_bases")
        try:
            for identifier in selected_ids:
                self.knowledge.assert_knowledge_base(identifier, tenant_id=tenant_id, owner_id=owner_id)
        except RagServiceError as exc:
            raise RagSearchError(str(exc), code=exc.code) from exc

        active_versions = self.knowledge.active_index_versions(
            selected_ids,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
        if not active_versions:
            return {
                "success": True,
                "query": normalized_query,
                "results": [],
                "result_items": [],
                "no_evidence": True,
                "message": "所选知识库尚无完成索引的文档。",
                "data_time_applicable": False,
            }
        config = _runtime_config()
        embedding_base_url = str(
            config.get("RAG_EMBEDDING_BASE_URL") or DEFAULT_EMBEDDING_BASE_URL
        ).strip()
        embedding_model = str(config.get("RAG_EMBEDDING_MODEL_ID") or DEFAULT_EMBEDDING_MODEL).strip()
        embedding_revision = str(
            config.get("RAG_EMBEDDING_MODEL_REVISION") or DEFAULT_EMBEDDING_REVISION
        ).strip()
        expected_embedding_version = model_version_tag(embedding_model, embedding_revision)
        expected_dimension = int(config.get("RAG_VECTOR_DIMENSION") or DEFAULT_VECTOR_DIMENSION)
        if any(
            item["embedding_model"] != expected_embedding_version
            or int(item["vector_dimension"]) != expected_dimension
            for item in active_versions
        ):
            raise RagSearchError(
                "知识库中仍有旧版 Embedding 索引。请在知识库管理页重建文档索引后再检索。",
                code="index_rebuild_required",
            )
        rerank_base_url = str(config.get("RAG_RERANK_BASE_URL") or DEFAULT_RERANK_BASE_URL).strip()
        rerank_model = str(config.get("RAG_RERANK_MODEL_ID") or DEFAULT_RERANK_MODEL).strip()
        rerank_revision = str(
            config.get("RAG_RERANK_MODEL_REVISION") or DEFAULT_RERANK_REVISION
        ).strip()

        grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
        for item in active_versions:
            grouped[(item["collection"], item["embedding_model"], int(item["vector_dimension"]))].append(item)
        try:
            store = self.store_factory()
        except Exception as exc:
            logger.exception(
                "RAG vector store client initialization failed error_type=%s",
                type(exc).__name__,
            )
            raise RagSearchError(
                "知识库向量检索服务暂不可用。",
                code="vector_store_unavailable",
                retryable=True,
            ) from exc
        candidates: dict[str, dict[str, Any]] = {}
        try:
            for (collection, model, dimension), versions in grouped.items():
                embedder = self.embedding_factory(
                    base_url=embedding_base_url,
                    model=embedding_model,
                    dimension=dimension,
                    timeout=None,
                )
                try:
                    query_vectors = [embedder.embed_query(item) for item in normalized_queries]
                finally:
                    embedder.close()
                for search_query, query_vector in zip(normalized_queries, query_vectors, strict=True):
                    points = store.hybrid_search(
                        collection=collection,
                        query=search_query,
                        query_vector=query_vector,
                        tenant_id=tenant_id,
                        owner_id=owner_id,
                        knowledge_base_ids=selected_ids,
                        index_version_ids=[item["index_version_id"] for item in versions],
                        page_ranges=explicit_page_ranges,
                        limit=max(10, min(100, candidate_limit)),
                    )
                    for rank, point in enumerate(points, start=1):
                        payload = dict(getattr(point, "payload", {}) or {})
                        chunk_id = str(payload.get("chunk_id") or "")
                        if not chunk_id:
                            continue
                        reciprocal_score = 1.0 / (60 + rank)
                        candidate = candidates.setdefault(
                            chunk_id,
                            {
                                "chunk_id": chunk_id,
                                "document_id": str(payload.get("document_id") or ""),
                                "knowledge_base_id": str(payload.get("knowledge_base_id") or ""),
                                "index_version_id": str(payload.get("index_version_id") or ""),
                                "chunk_index": int(payload.get("chunk_index") or 0),
                                "page_start": int(payload.get("page_start") or 1),
                                "page_end": int(payload.get("page_end") or payload.get("page_start") or 1),
                                "section": str(payload.get("section") or ""),
                                "text": str(payload.get("text") or ""),
                                "rrf_score": 0.0,
                            },
                        )
                        candidate["rrf_score"] += reciprocal_score
            ranked = sorted(candidates.values(), key=lambda item: item["rrf_score"], reverse=True)
            if not ranked:
                return {
                    "success": True,
                    "query": normalized_query,
                    "results": [],
                    "result_items": [],
                    "no_evidence": True,
                    "message": "没有找到与问题相关的文档内容。",
                    "data_time_applicable": False,
                }
            reranker = self.reranker_factory(
                base_url=rerank_base_url,
                model=rerank_model,
                timeout=None,
            )
            rerank_limit = max(
                int(top_k),
                int(config.get("RAG_RERANK_CANDIDATE_LIMIT") or DEFAULT_RERANK_CANDIDATE_LIMIT),
            )
            rerank_candidates = ranked[: min(len(ranked), rerank_limit)]
            rerank_query = _rerank_query(normalized_queries, rerank_candidates)
            try:
                reranked = reranker.rerank(
                    rerank_query,
                    [item["text"] for item in rerank_candidates],
                    # The reranker scores every candidate already; retain the full
                    # ranking so the cross-encoder and hybrid recall can be fused.
                    top_n=len(rerank_candidates),
                )
            finally:
                reranker.close()
            selected = _fuse_hybrid_and_reranker_rankings(
                rerank_candidates,
                reranked,
                top_k=top_k,
            )
        except RAGProviderError as exc:
            raise RagSearchError(
                str(exc), code=exc.code, retryable=exc.retryable
            ) from exc
        except Exception as exc:
            raise RagSearchError(
                "知识库检索暂时失败，请稍后重试。",
                code="retrieval_provider_error",
                retryable=True,
            ) from exc
        finally:
            store.close()

        projected: list[dict[str, Any]] = []
        for item in selected:
            located = self._load_citation_window(
                item,
                knowledge_base_ids=selected_ids,
                tenant_id=tenant_id,
                owner_id=owner_id,
            )
            if located is None:
                continue
            page = int(item["page_start"])
            page_end = int(item.get("page_end") or page)
            # Keep complete chunks, but never attach another page's content
            # to this hit's narrower citation. Input fitting belongs to the
            # existing model context budget, not a character preview here.
            context_text = "\n\n".join(
                str(part["text"])
                for part in located["window"]
                if part["text"] and page <= int(part["page_start"])
                and int(part["page_end"]) <= page_end
            )
            citation_url = f"/api/v1/knowledge-bases/documents/{item['document_id']}/content#page={page}"
            citation_id = hashlib.sha256(
                f"{item['document_id']}:{item['index_version_id']}:{item['chunk_id']}".encode("utf-8")
            ).hexdigest()[:24]
            projected.append(
                {
                    **item,
                    "citation_id": f"kb_{citation_id}",
                    "evidence_id": f"ev_kb_{citation_id}",
                    "filename": located["filename"],
                    "knowledge_base_name": located["knowledge_base_name"],
                    "snippet": str(item["text"]),
                    "text": context_text,
                    "url": citation_url,
                    "source_url": citation_url,
                }
            )
        evidence_items = [
            {
                "id": item["citation_id"],
                "evidence_id": item["evidence_id"],
                "title": f"{item['filename']} · 第 {item['page_start']} 页",
                "url": item["url"],
                "summary": item["snippet"],
                "source": "PDF 知识库",
            }
            for item in projected
        ]
        return {
            "success": True,
            "query": normalized_query,
            "results": projected,
            "result_items": evidence_items,
            "no_evidence": not bool(projected),
            "retrieval": {
                "dense_sparse_fusion": "qdrant_rrf",
                "rerank_fusion": "rrf",
                "query_variant_count": len(normalized_queries),
                "page_filter": explicit_page_ranges,
                "reranker_model": model_version_tag(rerank_model, rerank_revision),
                "candidate_count": len(candidates),
                "rerank_candidate_count": len(rerank_candidates),
            },
            "warnings": [],
            "errors": [],
            # A retrieved PDF is a static source, not a live data observation.
            # Its relative-time wording is checked against the cited document,
            # while live-data tools retain their freshness requirement.
            "data_time_applicable": False,
        }

    def _load_citation_window(
        self,
        candidate: Mapping[str, Any],
        *,
        knowledge_base_ids: Sequence[str],
        tenant_id: str,
        owner_id: str,
    ) -> dict[str, Any] | None:
        session = self.db.get_session()
        try:
            document, kb = session.execute(
                select(RagDocument, RagKnowledgeBase)
                .join(RagKnowledgeBase, RagKnowledgeBase.id == RagDocument.knowledge_base_id)
                .where(
                    RagDocument.id == candidate["document_id"],
                    RagDocument.active_index_version_id == candidate["index_version_id"],
                    RagDocument.status == "ready",
                    RagDocument.knowledge_base_id.in_(knowledge_base_ids),
                    RagDocument.tenant_id == tenant_id,
                    RagDocument.owner_id == owner_id,
                    RagKnowledgeBase.status == "active",
                    RagKnowledgeBase.tenant_id == tenant_id,
                    RagKnowledgeBase.owner_id == owner_id,
                )
            ).first() or (None, None)
            if document is None:
                return None
            chunks = session.scalars(
                select(RagChunk)
                .where(
                    RagChunk.document_id == document.id,
                    RagChunk.index_version_id == candidate["index_version_id"],
                    RagChunk.chunk_index >= max(0, int(candidate["chunk_index"]) - 1),
                    RagChunk.chunk_index <= int(candidate["chunk_index"]) + 1,
                )
                .order_by(RagChunk.chunk_index)
            ).all()
            if not any(chunk.id == candidate["chunk_id"] for chunk in chunks):
                return None
            return {
                "filename": document.filename,
                "knowledge_base_name": kb.name,
                "window": [
                    {
                        "text": chunk.text_content,
                        "page_start": chunk.page_start,
                        "page_end": chunk.page_end,
                    }
                    for chunk in chunks
                ],
            }
        finally:
            session.close()


__all__ = ["RagSearchError", "RagSearchService"]
