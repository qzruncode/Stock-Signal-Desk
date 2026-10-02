from __future__ import annotations

import json

import httpx
import pytest

from src.rag.model_adapters import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_RERANK_MODEL,
    DEFAULT_VECTOR_DIMENSION,
    QWEN_RETRIEVAL_INSTRUCTION,
    RAGProviderError,
    LocalFastEmbedAdapter,
    LocalRerankerAdapter,
    OllamaEmbeddingAdapter,
    OLLAMA_DEFAULT_EMBEDDING_MODEL,
    OLLAMA_DEFAULT_VECTOR_DIMENSION,
    model_version_tag,
)


def _json_response(request: httpx.Request, payload, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        headers={"content-type": "application/json"},
        content=json.dumps(payload).encode(),
        request=request,
    )


def test_ollama_embedding_batches_and_adds_query_instruction_once() -> None:
    requests: list[tuple[str, dict, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append((request.url.path, body, dict(request.headers)))
        vectors = [
            [float(index)] + [0.0] * (OLLAMA_DEFAULT_VECTOR_DIMENSION - 1)
            for index in range(len(body["input"]))
        ]
        return _json_response(request, {"embeddings": vectors})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = OllamaEmbeddingAdapter(base_url="http://ollama:11434", batch_size=2, client=client)
    try:
        vectors = adapter.embed_documents(["文档一", "文档二", "文档三"])
        query_vector = adapter.embed_query("查询内容")
        already_prefixed = adapter.embed_query(QWEN_RETRIEVAL_INSTRUCTION + "已加指令")

        assert len(vectors) == 3
        assert len(query_vector) == len(already_prefixed) == OLLAMA_DEFAULT_VECTOR_DIMENSION
        assert [len(item[1]["input"]) for item in requests] == [2, 1, 1, 1]
        assert [item[0] for item in requests] == ["/api/embed"] * 4
        assert requests[0][1]["input"] == ["文档一", "文档二"]
        assert requests[1][1]["input"] == ["文档三"]
        assert requests[2][1]["input"] == [QWEN_RETRIEVAL_INSTRUCTION + "查询内容"]
        assert requests[3][1]["input"] == [QWEN_RETRIEVAL_INSTRUCTION + "已加指令"]
        assert all(item[1]["dimensions"] == OLLAMA_DEFAULT_VECTOR_DIMENSION for item in requests)
        assert all(item[1]["truncate"] is True for item in requests)
        assert all(item[1]["model"] == OLLAMA_DEFAULT_EMBEDDING_MODEL for item in requests)
        assert all("authorization" not in item[2] for item in requests)
        assert vectors[0][0] == 0.0 and vectors[1][0] == 1.0
    finally:
        client.close()


def test_embedding_rejects_dimension_mismatch_and_bad_ordering() -> None:
    cases = [
        {"embeddings": [[0.1, 0.2]]},
        {"embeddings": [[0.1] * 3, [0.2] * 3]},
    ]
    for payload in cases:
        client = httpx.Client(transport=httpx.MockTransport(lambda request, p=payload: _json_response(request, p)))
        adapter = OllamaEmbeddingAdapter(base_url="http://ollama:11434", dimension=3, client=client)
        try:
            with pytest.raises(RAGProviderError) as error:
                adapter.embed_query("query")
            assert error.value.code == "invalid_response"
        finally:
            client.close()


def test_local_reranker_uses_native_contract_and_returns_original_document_indexes() -> None:
    requests: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, json.loads(request.content)))
        return _json_response(
            request,
            [{"index": 1, "score": 0.9}, {"index": 0, "score": 0.2}],
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = LocalRerankerAdapter(base_url="http://local-reranker:8082", client=client)
    try:
        results = adapter.rerank("问题", ["文档 A", "文档 B"], top_n=1)
        assert requests == [
            (
                "/rerank",
                {"query": "问题", "texts": ["文档 A", "文档 B"], "truncate": True, "return_text": False},
            )
        ]
        assert adapter.model == DEFAULT_RERANK_MODEL
        assert [(item.index, item.score) for item in results] == [(1, 0.9)]
    finally:
        client.close()


def test_inference_http_failures_are_safe_and_retryable_only_when_transient() -> None:
    for status, expected_code, retryable in (
        (401, "authentication_failed", False),
        (405, "method_not_allowed", False),
        (503, "provider_error", True),
    ):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request, code=status: _json_response(request, {"detail": "private upstream detail"}, code)
            )
        )
        adapter = LocalRerankerAdapter(base_url="http://local-reranker:8082", client=client)
        try:
            with pytest.raises(RAGProviderError) as error:
                adapter.rerank("query", ["document"])
            assert error.value.code == expected_code
            assert error.value.retryable is retryable
            assert "private upstream detail" not in str(error.value)
        finally:
            client.close()


def test_model_version_tag_changes_when_weights_revision_changes() -> None:
    assert model_version_tag("model", "revision-a") == "model@revision-a"
    assert model_version_tag("model", "") == "model"


def test_embedding_base_url_must_not_include_a_service_route() -> None:
    with pytest.raises(RAGProviderError) as error:
        OllamaEmbeddingAdapter(base_url="http://ollama:11434/api")
    assert error.value.code == "config_invalid"


def test_local_fastembed_uses_the_pinned_multilingual_contract_without_query_prefix() -> None:
    requests: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        return _json_response(
            request,
            {"embeddings": [[float(index)] + [0.0] * (DEFAULT_VECTOR_DIMENSION - 1) for index in range(len(body["input"]))]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = LocalFastEmbedAdapter(base_url="http://fastembed:8081", batch_size=2, client=client)
    try:
        vectors = adapter.embed_documents(["第一段", "第二段", "第三段"])
        query = adapter.embed_query("中国公司如何进行风险管理？")
        assert [len(vector) for vector in vectors] == [DEFAULT_VECTOR_DIMENSION] * 3
        assert len(query) == DEFAULT_VECTOR_DIMENSION
        assert [len(body["input"]) for _path, body in requests] == [2, 1, 1]
        assert all(path == "/api/embed" for path, _body in requests)
        assert requests[0][1] == {
            "model": DEFAULT_EMBEDDING_MODEL,
            "input": ["第一段", "第二段"],
            "dimensions": DEFAULT_VECTOR_DIMENSION,
        }
        assert requests[1][1]["input"] == ["第三段"]
        assert requests[2][1]["input"] == ["中国公司如何进行风险管理？"]
        assert requests[2][1]["input"][0].startswith("中国")
    finally:
        client.close()


def test_local_fastembed_rejects_wrong_dimension() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: _json_response(request, {"embeddings": [[0.1, 0.2]]})
        )
    )
    adapter = LocalFastEmbedAdapter(base_url="http://fastembed:8081", dimension=3, client=client)
    try:
        with pytest.raises(RAGProviderError) as error:
            adapter.embed_query("query")
        assert error.value.code == "invalid_response"
    finally:
        client.close()


@pytest.mark.parametrize("adapter_type", [OllamaEmbeddingAdapter, LocalFastEmbedAdapter, LocalRerankerAdapter])
def test_inference_transport_has_no_default_or_legacy_environment_timeout(monkeypatch, adapter_type):
    monkeypatch.setenv("RAG_MODEL_TIMEOUT_SECONDS", "0.001")
    requests = []

    def handler(request):
        requests.append(request.extensions["timeout"])
        body = json.loads(request.content)
        payload = ({"ranks": [{"index": 0, "score": 0.9}]} if "texts" in body else
                   {"embeddings": [[0.1] * body["dimensions"] for _ in body["input"]]})
        return _json_response(request, payload)

    # Even a caller-supplied client's finite default must not cap inference.
    with httpx.Client(transport=httpx.MockTransport(handler), timeout=0.001) as client:
        adapter = adapter_type(client=client)
        assert adapter.timeout is None
        if isinstance(adapter, LocalRerankerAdapter):
            adapter.rerank("query", ["document"])
        else:
            adapter.embed_query("query")
        assert requests == [{"connect": None, "read": None, "write": None, "pool": None}]


@pytest.mark.parametrize("adapter_type", [OllamaEmbeddingAdapter, LocalFastEmbedAdapter, LocalRerankerAdapter])
def test_owned_inference_client_also_has_no_transport_deadline(adapter_type):
    adapter = adapter_type()
    try:
        assert adapter._client.timeout.as_dict() == {"connect": None, "read": None, "write": None, "pool": None}
    finally:
        adapter.close()
