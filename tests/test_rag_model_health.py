from __future__ import annotations

from types import SimpleNamespace

from src.rag.model_adapters import RAGProviderError
from src.rag.retrieval import check_model_services


def test_model_service_check_runs_real_adapter_contracts_and_reports_model_revisions():
    calls = []

    class FakeEmbedding:
        def __init__(self, **kwargs):
            calls.append(("embedding_init", kwargs))

        def embed_query(self, query):
            calls.append(("embedding_query", query))
            return [0.1, 0.2, 0.3]

        def close(self):
            calls.append(("embedding_close", None))

    class FakeReranker:
        def __init__(self, **kwargs):
            calls.append(("reranker_init", kwargs))

        def rerank(self, query, documents, *, top_n=None):
            calls.append(("rerank", (query, documents, top_n)))
            return [SimpleNamespace(index=0, score=0.9)]

        def close(self):
            calls.append(("reranker_close", None))

    result = check_model_services(
        embedding_factory=FakeEmbedding,
        reranker_factory=FakeReranker,
        config={
            "RAG_EMBEDDING_BASE_URL": "http://embedding:80",
            "RAG_EMBEDDING_MODEL_ID": "test/embed",
            "RAG_EMBEDDING_MODEL_REVISION": "revision-a",
            "RAG_VECTOR_DIMENSION": "3",
            "RAG_RERANK_BASE_URL": "http://reranker:80",
            "RAG_RERANK_MODEL_ID": "test/rerank",
            "RAG_RERANK_MODEL_REVISION": "revision-b",
            "RAG_MODEL_TIMEOUT_SECONDS": "120",
        },
    )

    assert result["success"] is True
    assert result["embedding"]["model"] == "test/embed@revision-a"
    assert result["embedding"]["dimension"] == 3
    assert result["reranker"]["model"] == "test/rerank@revision-b"
    assert calls[-2:] == [("rerank", ("公司的主营业务是什么？", ["公司主营业务是工业视觉检测。", "今天的天气晴朗。"], 2)), ("reranker_close", None)]
    assert ("embedding_close", None) in calls
    assert all(kwargs["timeout"] is None for name, kwargs in calls if name.endswith("_init"))


def test_model_service_check_reports_one_unavailable_local_service_without_hiding_the_other():
    class FakeEmbedding:
        def __init__(self, **_kwargs):
            pass

        def embed_query(self, _query):
            return [0.1]

        def close(self):
            pass

    class UnavailableReranker:
        def __init__(self, **_kwargs):
            pass

        def rerank(self, *_args, **_kwargs):
            raise RAGProviderError("Reranker service unavailable", code="network_error", retryable=True)

        def close(self):
            pass

    result = check_model_services(
        embedding_factory=FakeEmbedding,
        reranker_factory=UnavailableReranker,
        config={"RAG_VECTOR_DIMENSION": "1"},
    )

    assert result["success"] is False
    assert result["embedding"]["success"] is True
    assert result["reranker"]["success"] is False
    assert result["reranker"]["error_code"] == "network_error"
    assert result["reranker"]["retryable"] is True
