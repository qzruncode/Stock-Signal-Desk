"""LangChain adapters for local embedding and reranking services."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence
from urllib.parse import urlparse

import httpx
from langchain_core.embeddings import Embeddings


DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_EMBEDDING_REVISION = "faf4aa4225822f3bc6376869cb1164e8e3feedd0"
DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-base"
DEFAULT_RERANK_REVISION = "2cfc18c9415c912f9d8155881c133215df768a70"
DEFAULT_VECTOR_DIMENSION = 384
DEFAULT_EMBEDDING_BASE_URL = "http://127.0.0.1:8081"
DEFAULT_RERANK_BASE_URL = "http://127.0.0.1:8082"
DEFAULT_EMBEDDING_PATH = "/api/embed"
DEFAULT_RERANK_PATH = "/rerank"
EMBEDDING_BATCH_SIZE = 16
OLLAMA_DEFAULT_EMBEDDING_MODEL = "qwen3-embedding:0.6b"
OLLAMA_DEFAULT_EMBEDDING_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_DEFAULT_VECTOR_DIMENSION = 1024
QWEN_RETRIEVAL_INSTRUCTION = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query\n"
    " Query: "
)


def model_version_tag(model: str, revision: str) -> str:
    """Return a stable index identity that changes when model weights change."""
    normalized_model = str(model or "").strip()
    normalized_revision = str(revision or "").strip()
    return f"{normalized_model}@{normalized_revision}" if normalized_revision else normalized_model


class RAGProviderError(RuntimeError):
    """Safe inference-service error with a stable API classification."""

    def __init__(self, message: str, *, code: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _endpoint_url(base_url: str, path: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    parsed = urlparse(base)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RAGProviderError(
            "本地模型服务地址无效，请检查 RAG Embedding / Reranker 服务地址。",
            code="config_invalid",
        )
    return f"{base}/{path.lstrip('/')}"


def _http_provider_error(capability: str, status_code: int) -> RAGProviderError:
    retryable = status_code == 429 or status_code >= 500
    if status_code in {401, 403}:
        message = f"本地 {capability} 模型服务鉴权失败，请检查服务端配置。"
        code = "authentication_failed"
    elif status_code == 404:
        message = f"本地 {capability} 模型服务接口不存在，请确认服务地址和模型服务类型。"
        code = "endpoint_not_found"
    elif status_code == 405:
        message = f"本地 {capability} 模型服务不接受当前请求方式，请确认接口版本。"
        code = "method_not_allowed"
    elif status_code == 429:
        message = f"本地 {capability} 模型服务繁忙，请稍后重试。"
        code = "rate_limited"
    else:
        message = f"本地 {capability} 模型服务返回 HTTP {status_code}。"
        code = "provider_error"
    return RAGProviderError(message, code=code, retryable=retryable)


def _post_json(
    client: httpx.Client,
    endpoint: str,
    payload: dict[str, Any],
    *,
    timeout: float | None,
    capability: str,
) -> Any:
    try:
        response = client.post(endpoint, json=payload, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise RAGProviderError(
            f"本地 {capability} 模型服务请求超时。", code="timeout", retryable=True
        ) from exc
    except httpx.HTTPError as exc:
        raise RAGProviderError(
            f"无法连接本地 {capability} 模型服务。", code="network_error", retryable=True
        ) from exc
    if response.is_error:
        raise _http_provider_error(capability, response.status_code)
    try:
        return response.json()
    except (ValueError, TypeError) as exc:
        raise RAGProviderError(
            f"本地 {capability} 模型服务返回了无效 JSON。", code="invalid_response"
        ) from exc


class OllamaEmbeddingAdapter(Embeddings):
    """LangChain ``Embeddings`` contract backed by Ollama's native ``/api/embed`` API."""

    def __init__(
        self,
        *,
        base_url: str = OLLAMA_DEFAULT_EMBEDDING_BASE_URL,
        model: str = OLLAMA_DEFAULT_EMBEDDING_MODEL,
        dimension: int = OLLAMA_DEFAULT_VECTOR_DIMENSION,
        timeout: float | None = None,
        batch_size: int = EMBEDDING_BATCH_SIZE,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = str(model or "").strip()
        self.dimension = int(dimension)
        self.endpoint = _endpoint_url(base_url, DEFAULT_EMBEDDING_PATH)
        self.timeout = float(timeout) if timeout is not None else None
        self.batch_size = max(1, min(128, int(batch_size)))
        if not self.model:
            raise RAGProviderError("Embedding 模型 ID 未配置。", code="config_missing")
        if self.dimension <= 0:
            raise RAGProviderError("Embedding 向量维度必须为正整数。", code="config_invalid")
        self._client = client or httpx.Client(timeout=self.timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            vectors.extend(self._embed_batch(batch))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        query = str(text or "").strip()
        if not query:
            raise ValueError("Embedding query must not be empty")
        prepared = (
            query
            if query.startswith(QWEN_RETRIEVAL_INSTRUCTION)
            else QWEN_RETRIEVAL_INSTRUCTION + query
        )
        return self._embed_batch([prepared])[0]

    def _embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        payload = _post_json(
            self._client,
            self.endpoint,
            {
                "model": self.model,
                "input": list(texts),
                "truncate": True,
                "dimensions": self.dimension,
            },
            timeout=self.timeout,
            capability="Embedding",
        )
        try:
            vectors = payload["embeddings"]
            if not isinstance(vectors, list) or len(vectors) != len(texts):
                raise ValueError("response vector count does not match request")
            return [self._validate_vector(vector) for vector in vectors]
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise RAGProviderError(
                "Embedding 服务响应格式或向量维度不符合索引配置。",
                code="invalid_response",
            ) from exc

    def _validate_vector(self, raw: Any) -> list[float]:
        if not isinstance(raw, list) or len(raw) != self.dimension:
            raise ValueError("embedding dimension mismatch")
        vector = [float(value) for value in raw]
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("embedding contains non-finite values")
        return vector


class LocalFastEmbedAdapter(Embeddings):
    """LangChain ``Embeddings`` adapter for the bundled local FastEmbed service."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_EMBEDDING_BASE_URL,
        model: str = DEFAULT_EMBEDDING_MODEL,
        dimension: int = DEFAULT_VECTOR_DIMENSION,
        timeout: float | None = None,
        batch_size: int = EMBEDDING_BATCH_SIZE,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = str(model or "").strip()
        self.dimension = int(dimension)
        self.endpoint = _endpoint_url(base_url, DEFAULT_EMBEDDING_PATH)
        self.timeout = float(timeout) if timeout is not None else None
        self.batch_size = max(1, min(64, int(batch_size)))
        if not self.model:
            raise RAGProviderError("Embedding 模型 ID 未配置。", code="config_missing")
        if self.dimension <= 0:
            raise RAGProviderError("Embedding 向量维度必须为正整数。", code="config_invalid")
        self._client = client or httpx.Client(timeout=self.timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(self._embed_batch(texts[start : start + self.batch_size]))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        query = str(text or "").strip()
        if not query:
            raise ValueError("Embedding query must not be empty")
        return self._embed_batch([query])[0]

    def _embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        payload = _post_json(
            self._client,
            self.endpoint,
            {"model": self.model, "input": list(texts), "dimensions": self.dimension},
            timeout=self.timeout,
            capability="Embedding",
        )
        try:
            vectors = payload["embeddings"]
            if not isinstance(vectors, list) or len(vectors) != len(texts):
                raise ValueError("response vector count does not match request")
            return [self._validate_vector(vector) for vector in vectors]
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise RAGProviderError(
                "Embedding 服务响应格式或向量维度不符合索引配置。",
                code="invalid_response",
            ) from exc

    def _validate_vector(self, raw: Any) -> list[float]:
        if not isinstance(raw, list) or len(raw) != self.dimension:
            raise ValueError("embedding dimension mismatch")
        vector = [float(value) for value in raw]
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("embedding contains non-finite values")
        return vector


@dataclass(frozen=True)
class RerankResult:
    index: int
    score: float


class LocalRerankerAdapter:
    """Pairwise reranker backed by the local BGE reranker service."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_RERANK_BASE_URL,
        model: str = DEFAULT_RERANK_MODEL,
        timeout: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = str(model or "").strip()
        self.endpoint = _endpoint_url(base_url, DEFAULT_RERANK_PATH)
        self.timeout = float(timeout) if timeout is not None else None
        if not self.model:
            raise RAGProviderError("Reranker 模型 ID 未配置。", code="config_missing")
        self._client = client or httpx.Client(timeout=self.timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_n: int | None = None,
    ) -> list[RerankResult]:
        query_text = str(query or "").strip()
        if not query_text:
            raise ValueError("Rerank query must not be empty")
        docs = [str(document or "") for document in documents]
        if not docs:
            return []
        requested = max(1, min(len(docs), int(top_n or len(docs))))
        payload = _post_json(
            self._client,
            self.endpoint,
            {"query": query_text, "texts": docs, "truncate": True, "return_text": False},
            timeout=self.timeout,
            capability="Reranker",
        )
        try:
            rows = payload.get("ranks", payload.get("results")) if isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                raise ValueError("rerank result rows are missing")
            results = [
                RerankResult(index=int(row["index"]), score=float(row.get("score", row.get("relevance_score"))))
                for row in rows
            ]
            if not results or any(
                item.index < 0 or item.index >= len(docs) or not math.isfinite(item.score)
                for item in results
            ):
                raise ValueError("invalid rerank result")
            if len({item.index for item in results}) != len(results):
                raise ValueError("duplicate rerank index")
            return sorted(results, key=lambda item: item.score, reverse=True)[:requested]
        except (KeyError, TypeError, ValueError) as exc:
            raise RAGProviderError("Reranker 服务响应格式无效。", code="invalid_response") from exc


__all__ = [
    "DEFAULT_EMBEDDING_BASE_URL",
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_EMBEDDING_REVISION",
    "DEFAULT_RERANK_BASE_URL",
    "DEFAULT_RERANK_MODEL",
    "DEFAULT_RERANK_REVISION",
    "DEFAULT_VECTOR_DIMENSION",
    "EMBEDDING_BATCH_SIZE",
    "LocalFastEmbedAdapter",
    "LocalRerankerAdapter",
    "OllamaEmbeddingAdapter",
    "QWEN_RETRIEVAL_INSTRUCTION",
    "RAGProviderError",
    "RerankResult",
    "OLLAMA_DEFAULT_EMBEDDING_BASE_URL",
    "OLLAMA_DEFAULT_EMBEDDING_MODEL",
    "OLLAMA_DEFAULT_VECTOR_DIMENSION",
    "model_version_tag",
]
