from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.deps import get_database_manager
from api.v1.endpoints import knowledge_base
from src.services import rag_knowledge_base_service as rag_service_module
from src.services.rag_knowledge_base_service import RagKnowledgeBaseService
from src.storage.models import Base


def test_management_api_creates_lists_and_accepts_idempotent_pdf_upload(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'knowledge-api.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    database = SimpleNamespace(get_session=factory)
    app = FastAPI()
    app.include_router(knowledge_base.router, prefix="/api/v1")
    app.dependency_overrides[get_database_manager] = lambda: database
    monkeypatch.setattr(rag_service_module, "rag_storage_root", lambda: tmp_path / "rag-blobs")
    monkeypatch.setattr(rag_service_module, "count_pdf_pages", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(RagKnowledgeBaseService, "_enqueue_task", lambda *_args, **_kwargs: None)
    client = TestClient(app)

    created = client.post(
        "/api/v1/knowledge-bases",
        json={"name": "产品资料", "description": "年度报告"},
    )
    assert created.status_code == 201
    knowledge_base_id = created.json()["knowledge_base"]["id"]
    assert client.get("/api/v1/knowledge-bases").json()["items"][0]["name"] == "产品资料"

    response = client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("annual.pdf", b"%PDF-1.7\nrepresentative searchable annual report", "application/pdf")},
    )
    assert response.status_code == 202
    assert response.json()["document"]["status"] == "queued"
    assert client.get(f"/api/v1/knowledge-bases/{knowledge_base_id}/documents").json()["items"][0]["filename"] == "annual.pdf"

    engine.dispose()


def test_model_service_test_endpoint_returns_embedding_and_reranker_checks(monkeypatch):
    app = FastAPI()
    app.include_router(knowledge_base.router, prefix="/api/v1")
    expected = {
        "success": False,
        "embedding": {"success": True, "model": "embed@rev", "dimension": 1024},
        "reranker": {"success": False, "model": "rerank@rev", "error_code": "network_error"},
    }
    monkeypatch.setattr(knowledge_base, "check_model_services", lambda: expected)

    response = TestClient(app).post("/api/v1/knowledge-bases/model-services/test", json={})

    assert response.status_code == 200
    assert response.json() == expected
