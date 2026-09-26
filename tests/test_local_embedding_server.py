from __future__ import annotations

import threading

import pytest
from fastapi import HTTPException

from src.rag import local_embedding_server as server


class FakeEmbedding:
    def embed(self, texts, *, batch_size):
        assert batch_size == 2
        for index, _text in enumerate(texts):
            yield [float(index), 0.0, 1.0]


def _configure_fake_model() -> None:
    server.app.state.embedding_model = FakeEmbedding()
    server.app.state.model_id = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    server.app.state.revision = "revision-a"
    server.app.state.dimension = 3
    server.app.state.threads = 2
    server.app.state.batch_size = 2
    server.app.state.inference_lock = threading.Lock()


def test_local_embedding_endpoint_returns_vectors_and_health_contract():
    _configure_fake_model()
    response = server.embed(
        server.EmbeddingRequest(
            model=server.app.state.model_id,
            input=["中文段落一", "中文段落二"],
            dimensions=3,
        )
    )
    assert response == {"embeddings": [[0.0, 0.0, 1.0], [1.0, 0.0, 1.0]]}
    assert server.health() == {
        "status": "ok",
        "model": server.app.state.model_id,
        "revision": "revision-a",
        "dimension": 3,
        "device": "cpu",
        "threads": 2,
    }


@pytest.mark.parametrize(
    "model,dimensions,inputs,expected_status",
    [
        ("wrong-model", 3, ["text"], 422),
        ("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", 4, ["text"], 422),
        ("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", 3, [" "], 422),
    ],
)
def test_local_embedding_endpoint_rejects_invalid_contract(model, dimensions, inputs, expected_status):
    _configure_fake_model()
    with pytest.raises(HTTPException) as error:
        server.embed(server.EmbeddingRequest(model=model, input=inputs, dimensions=dimensions))
    assert error.value.status_code == expected_status
