"""Small local HTTP facade for the cached BAAI BGE cross-encoder reranker."""

from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class RerankRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8_000)
    texts: list[str] = Field(min_length=1, max_length=100)
    truncate: bool = True
    return_text: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    model_path = str(os.getenv("RAG_RERANK_MODEL_PATH") or "").strip()
    if not model_path:
        raise RuntimeError("RAG_RERANK_MODEL_PATH must point to a local BGE model snapshot")

    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch.set_num_threads(max(1, min(4, int(os.getenv("RAG_RERANK_CPU_THREADS", "2")))))
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_path,
        local_files_only=True,
        use_safetensors=True,
    )
    model.eval()
    app.state.tokenizer = tokenizer
    app.state.model = model
    app.state.torch = torch
    app.state.inference_lock = threading.Lock()
    app.state.model_id = os.getenv("RAG_RERANK_MODEL_ID", "BAAI/bge-reranker-base")
    app.state.batch_size = max(1, min(32, int(os.getenv("RAG_RERANK_BATCH_SIZE", "8"))))
    yield
    app.state.model = None
    app.state.tokenizer = None


app = FastAPI(title="Local BGE Reranker", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    loaded = getattr(app.state, "model", None) is not None
    if not loaded:
        raise HTTPException(status_code=503, detail="Reranker model is not loaded")
    return {"status": "ok", "model": app.state.model_id, "device": "cpu"}


@app.post("/rerank")
def rerank(payload: RerankRequest) -> dict[str, list[dict[str, float | int | str]]]:
    model = getattr(app.state, "model", None)
    tokenizer = getattr(app.state, "tokenizer", None)
    if model is None or tokenizer is None:
        raise HTTPException(status_code=503, detail="Reranker model is not loaded")
    if any(len(text) > 40_000 for text in payload.texts):
        raise HTTPException(status_code=413, detail="A reranker passage exceeds the local size limit")

    scores: list[float] = []
    batch_size = app.state.batch_size
    try:
        with app.state.inference_lock:
            for start in range(0, len(payload.texts), batch_size):
                batch = payload.texts[start : start + batch_size]
                pairs = [[payload.query, text] for text in batch]
                inputs = tokenizer(
                    pairs,
                    padding=True,
                    truncation="longest_first" if payload.truncate else False,
                    return_tensors="pt",
                    max_length=512,
                )
                with app.state.torch.inference_mode():
                    logits = model(**inputs, return_dict=True).logits.reshape(-1).float()
                scores.extend(float(score) for score in logits.cpu().tolist())
    except Exception as exc:
        # Do not return passage text, tokenizer inputs, or model internals.
        raise HTTPException(status_code=500, detail="Local reranker inference failed") from exc

    ranks: list[dict[str, float | int | str]] = [
        {"index": index, "score": score}
        for index, score in sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
    ]
    if payload.return_text:
        for row in ranks:
            row["text"] = payload.texts[int(row["index"])]
    return {"ranks": ranks}
