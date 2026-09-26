"""Qdrant adapter for dense + server-side multilingual BM25 retrieval."""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

DENSE_VECTOR_NAME = "dense"
BM25_VECTOR_NAME = "bm25"
BM25_MODEL = "qdrant/bm25"
BM25_OPTIONS = {"tokenizer": "multilingual"}
RRF_K = 60


def collection_name(model_id: str, dimension: int) -> str:
    """Return a stable collection namespace for one vector-space contract."""
    digest = hashlib.sha256(str(model_id).encode("utf-8")).hexdigest()[:12]
    return f"dsa_rag_{int(dimension)}_{digest}"


class QdrantStore:
    """One connection boundary shared by ingestion, retrieval and health checks."""

    def __init__(
        self,
        *,
        url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        client: Any | None = None,
    ) -> None:
        try:
            from qdrant_client import QdrantClient
        except ImportError as exc:
            raise RuntimeError("qdrant-client is not installed") from exc
        endpoint = str(url or os.getenv("RAG_QDRANT_URL", "http://127.0.0.1:6333")).strip()
        token = str(api_key if api_key is not None else os.getenv("RAG_QDRANT_API_KEY", "")).strip() or None
        connect_timeout = float(timeout or os.getenv("RAG_QDRANT_TIMEOUT_SECONDS", "10"))
        self.client = client or QdrantClient(
            url=endpoint,
            api_key=token,
            timeout=connect_timeout,
            prefer_grpc=False,
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def ensure_collection(self, name: str, dimension: int) -> None:
        from qdrant_client import models

        existing = self.client.collection_exists(name)
        if not existing:
            try:
                self.client.create_collection(
                    collection_name=name,
                    vectors_config={
                        DENSE_VECTOR_NAME: models.VectorParams(
                            size=int(dimension),
                            distance=models.Distance.COSINE,
                        )
                    },
                    sparse_vectors_config={
                        BM25_VECTOR_NAME: models.SparseVectorParams(
                            modifier=models.Modifier.IDF,
                        )
                    },
                )
            except Exception:
                # Concurrent first writers can race between collection_exists
                # and create_collection. Re-read the authoritative state before
                # treating the failure as a real storage error.
                if not self.client.collection_exists(name):
                    raise
        self._ensure_payload_indexes(name)
        self._verify_vector_contract(name, int(dimension))

    def _ensure_payload_indexes(self, name: str) -> None:
        from qdrant_client import models

        for field in ("tenant_id", "owner_id", "knowledge_base_id", "document_id", "index_version_id"):
            try:
                self.client.create_payload_index(
                    collection_name=name,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                    wait=True,
                )
            except Exception as exc:
                # Existing index errors vary across server versions. Verify the
                # schema before swallowing a duplicate-index response.
                info = self.client.get_collection(name)
                schema = getattr(info, "payload_schema", {}) or {}
                if field not in schema:
                    raise exc
        for field in ("page_start", "page_end"):
            try:
                self.client.create_payload_index(
                    collection_name=name,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.INTEGER,
                    wait=True,
                )
            except Exception as exc:
                info = self.client.get_collection(name)
                schema = getattr(info, "payload_schema", {}) or {}
                if field not in schema:
                    raise exc

    def _verify_vector_contract(self, name: str, dimension: int) -> None:
        info = self.client.get_collection(name)
        params = getattr(info.config.params, "vectors", None)
        dense = params.get(DENSE_VECTOR_NAME) if isinstance(params, Mapping) else params
        size = getattr(dense, "size", None)
        if size is not None and int(size) != dimension:
            raise RuntimeError(
                f"Qdrant collection {name} vector dimension mismatch: expected {dimension}, got {size}"
            )

    def upsert_chunks(
        self,
        *,
        collection: str,
        dimension: int,
        tenant_id: str,
        owner_id: str,
        knowledge_base_id: str,
        document_id: str,
        index_version_id: str,
        chunks: Sequence[Mapping[str, Any]],
        vectors: Sequence[Sequence[float]],
    ) -> int:
        from qdrant_client import models

        if len(chunks) != len(vectors):
            raise ValueError("embedding count does not match chunk count")
        self.ensure_collection(collection, dimension)
        points = []
        for chunk, dense in zip(chunks, vectors):
            text = str(chunk.get("text") or "").strip()
            if len(dense) != dimension:
                raise ValueError("embedding vector dimension does not match Qdrant collection")
            point_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"dsa-rag:{index_version_id}:{chunk.get('id')}",
                )
            )
            points.append(
                models.PointStruct(
                    id=point_id,
                    vector={
                        DENSE_VECTOR_NAME: [float(value) for value in dense],
                        BM25_VECTOR_NAME: models.Document(
                            text=text,
                            model=BM25_MODEL,
                            options=BM25_OPTIONS,
                        ),
                    },
                    payload={
                        "tenant_id": tenant_id,
                        "owner_id": owner_id,
                        "knowledge_base_id": knowledge_base_id,
                        "document_id": document_id,
                        "index_version_id": index_version_id,
                        "chunk_id": str(chunk.get("id") or ""),
                        "chunk_index": int(chunk.get("chunk_index") or 0),
                        "page_start": int(chunk.get("page_start") or 1),
                        "page_end": int(chunk.get("page_end") or chunk.get("page_start") or 1),
                        "section": str(chunk.get("section") or ""),
                        "text": text,
                    },
                )
            )
        if points:
            self.client.upsert(collection_name=collection, points=points, wait=True)
        return len(points)

    def delete_index_version(self, collection: str, index_version_id: str) -> None:
        from qdrant_client import models

        if not self.client.collection_exists(collection):
            return
        self.client.delete(
            collection_name=collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="index_version_id",
                            match=models.MatchValue(value=index_version_id),
                        )
                    ]
                )
            ),
            wait=True,
        )

    def count_index_version(self, collection: str, index_version_id: str) -> int:
        from qdrant_client import models

        if not self.client.collection_exists(collection):
            return 0
        result = self.client.count(
            collection_name=collection,
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="index_version_id",
                        match=models.MatchValue(value=index_version_id),
                    )
                ]
            ),
            exact=True,
        )
        return int(result.count)

    def hybrid_search(
        self,
        *,
        collection: str,
        query: str,
        query_vector: Sequence[float],
        tenant_id: str,
        owner_id: str,
        knowledge_base_ids: Sequence[str],
        index_version_ids: Sequence[str],
        page_ranges: Sequence[tuple[int, int]] = (),
        limit: int = 30,
        prefetch_limit: int = 60,
    ) -> list[Any]:
        from qdrant_client import models

        if not knowledge_base_ids or not index_version_ids:
            return []
        if not self.client.collection_exists(collection):
            return []
        scope_conditions = [
            models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
            models.FieldCondition(key="owner_id", match=models.MatchValue(value=owner_id)),
            models.FieldCondition(key="knowledge_base_id", match=models.MatchAny(any=list(knowledge_base_ids))),
            models.FieldCondition(key="index_version_id", match=models.MatchAny(any=list(index_version_ids))),
        ]
        normalized_page_ranges = [
            (int(start), int(end))
            for start, end in page_ranges
            if 1 <= int(start) <= int(end) <= 500
        ]
        page_conditions = [
            models.Filter(
                must=[
                    models.FieldCondition(
                        key="page_start",
                        range=models.Range(lte=end),
                    ),
                    models.FieldCondition(
                        key="page_end",
                        range=models.Range(gte=start),
                    ),
                ]
            )
            for start, end in normalized_page_ranges
        ]
        scope_filter = models.Filter(
            must=scope_conditions,
            **({"should": page_conditions} if page_conditions else {}),
        )
        response = self.client.query_points(
            collection_name=collection,
            prefetch=[
                models.Prefetch(
                    query=[float(value) for value in query_vector],
                    using=DENSE_VECTOR_NAME,
                    filter=scope_filter,
                    limit=max(limit, prefetch_limit),
                ),
                models.Prefetch(
                    query=models.Document(text=query, model=BM25_MODEL, options=BM25_OPTIONS),
                    using=BM25_VECTOR_NAME,
                    filter=scope_filter,
                    limit=max(limit, prefetch_limit),
                ),
            ],
            query=models.RrfQuery(rrf=models.Rrf(k=RRF_K)),
            query_filter=scope_filter,
            limit=max(1, min(100, int(limit))),
            with_payload=True,
        )
        return list(response.points or [])

    def health(self) -> dict[str, Any]:
        collections = self.client.get_collections()
        return {"ok": True, "collection_count": len(collections.collections or [])}


__all__ = [
    "BM25_MODEL",
    "BM25_OPTIONS",
    "BM25_VECTOR_NAME",
    "DENSE_VECTOR_NAME",
    "QdrantStore",
    "collection_name",
]
