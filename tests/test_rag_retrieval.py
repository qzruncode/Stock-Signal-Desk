from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pytest

from src.rag.model_adapters import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_REVISION,
    DEFAULT_RERANK_MODEL,
    DEFAULT_RERANK_REVISION,
    model_version_tag,
)
from src.rag.retrieval import RagSearchError, RagSearchService
from src.rag.retrieval import _explicit_pdf_page_ranges
from src.services.rag_knowledge_base_service import RagKnowledgeBaseService
from src.storage.models import Base, RagChunk, RagDocument, RagIndexVersion, RagKnowledgeBase


class FakeEmbedder:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.queries = []

    def embed_query(self, query: str):
        self.queries.append(query)
        return [0.1] * 4

    def close(self):
        pass


class FakeReranker:
    def __init__(self, **_kwargs):
        pass

    def rerank(self, _query, documents, *, top_n=None):
        assert documents and "主营业务" in documents[0]
        return [SimpleNamespace(index=0, score=0.97)][:top_n]

    def close(self):
        pass


class FakeStore:
    def __init__(self):
        self.search_kwargs = None
        self.search_calls = []

    def hybrid_search(self, **kwargs):
        self.search_kwargs = kwargs
        self.search_calls.append(kwargs)
        return [
            SimpleNamespace(
                payload={
                    "chunk_id": "chunk-1",
                    "document_id": "doc-1",
                    "knowledge_base_id": "kb-1",
                    "index_version_id": "index-1",
                    "chunk_index": 0,
                    "page_start": 3,
                    "page_end": 3,
                    "section": "公司概况",
                    "text": "公司主营业务包括工业视觉检测和智能装备。",
                }
            )
        ]

    def close(self):
        pass


def test_hybrid_retrieval_scopes_search_and_projects_verified_pdf_citation(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'rag-search.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(RagKnowledgeBase(id="kb-1", tenant_id="local", owner_id="admin", name="年报资料"))
        session.add(
            RagDocument(
                id="doc-1",
                knowledge_base_id="kb-1",
                tenant_id="local",
                owner_id="admin",
                filename="annual.pdf",
                size_bytes=100,
                content_hash="a" * 64,
                blob_path="/tmp/annual.pdf",
                status="ready",
                active_index_version_id="index-1",
            )
        )
        session.add(
            RagIndexVersion(
                id="index-1",
                document_id="doc-1",
                version=1,
                status="active",
                parser_version="2.129.0",
                chunking_version="docling-structure-v1-1400-180",
                embedding_model=model_version_tag(DEFAULT_EMBEDDING_MODEL, DEFAULT_EMBEDDING_REVISION),
                vector_dimension=4,
                qdrant_collection="dsa_rag_test",
            )
        )
        session.add(
            RagChunk(
                id="chunk-1",
                document_id="doc-1",
                index_version_id="index-1",
                chunk_index=0,
                page_start=3,
                page_end=3,
                section="公司概况",
                text_content="公司主营业务包括工业视觉检测和智能装备。",
                content_hash="b" * 64,
            )
        )
        session.commit()

    stores = []

    def store_factory():
        item = FakeStore()
        stores.append(item)
        return item

    monkeypatch.setattr(
        "src.rag.retrieval._runtime_config",
        lambda: {
            "RAG_EMBEDDING_BASE_URL": "http://tei-embed:80",
            "RAG_EMBEDDING_MODEL_ID": DEFAULT_EMBEDDING_MODEL,
            "RAG_EMBEDDING_MODEL_REVISION": DEFAULT_EMBEDDING_REVISION,
            "RAG_VECTOR_DIMENSION": "4",
            "RAG_RERANK_BASE_URL": "http://tei-rerank:80",
            "RAG_RERANK_MODEL_ID": DEFAULT_RERANK_MODEL,
            "RAG_RERANK_MODEL_REVISION": DEFAULT_RERANK_REVISION,
            "RAG_RERANK_CANDIDATE_LIMIT": "12",
        },
    )
    service = RagSearchService(
        database=SimpleNamespace(get_session=factory),
        knowledge_service=RagKnowledgeBaseService(SimpleNamespace(get_session=factory)),
        embedding_factory=FakeEmbedder,
        reranker_factory=FakeReranker,
        store_factory=store_factory,
    )

    result = service.search(
        "公司的主要业务是什么？",
        knowledge_base_ids=["kb-1"],
        tenant_id="local",
        owner_id="admin",
    )

    assert result["success"] is True
    assert result["no_evidence"] is False
    assert result["data_time_applicable"] is False
    assert result["results"][0]["filename"] == "annual.pdf"
    assert result["results"][0]["page_start"] == 3
    assert result["results"][0]["evidence_id"].startswith("ev_kb_")
    assert result["results"][0]["url"] == "/api/v1/knowledge-bases/documents/doc-1/content#page=3"
    assert result["result_items"][0]["evidence_id"] == result["results"][0]["evidence_id"]
    assert stores[0].search_kwargs["knowledge_base_ids"] == ["kb-1"]
    assert stores[0].search_kwargs["index_version_ids"] == ["index-1"]
    assert [call["query"] for call in stores[0].search_calls] == ["公司的主要业务是什么？"]
    assert result["retrieval"]["query_variant_count"] == 1

    page_result = service.search(
        "根据所选 PDF 第 7 页，营业收入是多少？只按原文回答并引用页码。",
        knowledge_base_ids=["kb-1"],
        tenant_id="local",
        owner_id="admin",
    )
    assert page_result["retrieval"]["page_filter"] == [(7, 7)]
    assert stores[1].search_kwargs["page_ranges"] == [(7, 7)]

    monkeypatch.setattr(
        "src.rag.retrieval._runtime_config",
        lambda: {
            "RAG_EMBEDDING_MODEL_ID": DEFAULT_EMBEDDING_MODEL,
            "RAG_EMBEDDING_MODEL_REVISION": "different-weights-revision",
            "RAG_VECTOR_DIMENSION": "4",
        },
    )
    with pytest.raises(RagSearchError) as stale_index_error:
        service.search(
            "公司的主要业务是什么？",
            knowledge_base_ids=["kb-1"],
            tenant_id="local",
            owner_id="admin",
        )
    assert stale_index_error.value.code == "index_rebuild_required"
    engine.dispose()


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("根据 PDF 第7页，营业收入是多少？", [(7, 7)]),
        ("报告第 7 页到第 9 页", [(7, 9)]),
        ("compare pages 7 through 9", [(7, 9)]),
        ("use p. 7 and page 51", [(7, 7), (51, 51)]),
        ("报告第 7 章的收入", []),
        ("第 0 页及第 501 页", []),
    ],
)
def test_explicit_pdf_page_references_become_bounded_filter_ranges(query, expected):
    assert _explicit_pdf_page_ranges(query) == expected
