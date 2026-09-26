"""Small local CPU service for the pinned Qdrant FastEmbed ONNX model."""

from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class EmbeddingRequest(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    input: list[str] = Field(min_length=1, max_length=64)
    dimensions: int = Field(gt=0, le=4096)


@asynccontextmanager
async def lifespan(app: FastAPI):
    model_path = str(os.getenv("RAG_FASTEMBED_MODEL_PATH") or "").strip()
    model_id = str(os.getenv("RAG_EMBEDDING_MODEL_ID") or "").strip()
    revision = str(os.getenv("RAG_EMBEDDING_MODEL_REVISION") or "").strip()
    dimension = int(os.getenv("RAG_VECTOR_DIMENSION", "384"))
    if not model_path or not model_id or not revision:
        raise RuntimeError("FastEmbed model path, model id and revision must be configured")
    if dimension != 384:
        raise RuntimeError(f"The configured FastEmbed model returns 384 dimensions, not {dimension}")
    if not os.path.isfile(os.path.join(model_path, "model_optimized.onnx")):
        raise RuntimeError("The pinned FastEmbed ONNX model file is missing")

    from fastembed import TextEmbedding

    threads = max(1, min(4, int(os.getenv("RAG_FASTEMBED_CPU_THREADS", "2"))))
    model = TextEmbedding(
        model_name=model_id,
        specific_model_path=model_path,
        threads=threads,
        providers=["CPUExecutionProvider"],
    )
    app.state.embedding_model = model
    app.state.model_id = model_id
    app.state.revision = revision
    app.state.dimension = dimension
    app.state.threads = threads
    app.state.batch_size = max(1, min(16, int(os.getenv("RAG_FASTEMBED_BATCH_SIZE", "8"))))
    app.state.inference_lock = threading.Lock()
    yield
    app.state.embedding_model = None


app = FastAPI(title="Local FastEmbed ONNX", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    model = getattr(app.state, "embedding_model", None)
    if model is None:
        raise HTTPException(status_code=503, detail="Embedding model is not loaded")
    return {
        "status": "ok",
        "model": app.state.model_id,
        "revision": app.state.revision,
        "dimension": app.state.dimension,
        "device": "cpu",
        "threads": app.state.threads,
    }


@app.post("/api/embed")
def embed(payload: EmbeddingRequest) -> dict[str, list[list[float]]]:
    model = getattr(app.state, "embedding_model", None)
    if model is None:
        raise HTTPException(status_code=503, detail="Embedding model is not loaded")
    if payload.model != app.state.model_id or payload.dimensions != app.state.dimension:
        raise HTTPException(status_code=422, detail="Embedding model or vector dimension does not match the loaded index")
    if any(not text.strip() for text in payload.input):
        raise HTTPException(status_code=422, detail="Embedding inputs must not be empty")
    if any(len(text) > 40_000 for text in payload.input):
        raise HTTPException(status_code=413, detail="An embedding input exceeds the local size limit")
    try:
        with app.state.inference_lock:
            vectors = [
                [float(value) for value in vector]
                for vector in model.embed(payload.input, batch_size=app.state.batch_size)
            ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Local FastEmbed inference failed") from exc
    if len(vectors) != len(payload.input) or any(len(vector) != app.state.dimension for vector in vectors):
        raise HTTPException(status_code=500, detail="Local FastEmbed returned an invalid vector contract")
    return {"embeddings": vectors}
