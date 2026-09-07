"""Existing business routes delegate to persistent data-service jobs."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
from unittest.mock import MagicMock
from api.v1.endpoints.stocks import router
from api.v1.endpoints.stocks import sync
from src.services.market_data_client import MarketDataError


@pytest.fixture
def boundary(monkeypatch):
    service = MagicMock()
    service.post.return_value = {
        "id": "durable-job",
        "status": "queued",
        "message": "等待采集",
        "progress": 0,
        "total": 0,
    }
    monkeypatch.setattr(sync, "get_market_data_client", lambda: service)
    app = FastAPI()
    app.include_router(router, prefix="/stocks")

    @app.exception_handler(MarketDataError)
    async def error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    with TestClient(app) as client:
        yield client, service


@pytest.mark.parametrize(
    "path,dataset",
    [("list", "securities"), ("kline", "kline"), ("financial", "financials")],
)
def test_sync_is_accepted_as_durable_job(boundary, path, dataset):
    client, service = boundary
    response = client.post(f"/stocks/sync/{path}")
    assert response.status_code == 202 and response.json()["job_id"] == "durable-job"
    service.post.assert_called_once_with(
        "/v1/jobs", {"dataset": dataset, "mode": "stale", "symbols": []}
    )


def test_duplicate_submission_reuses_service_identity(boundary):
    client, _ = boundary
    assert (
        client.post("/stocks/sync/kline").json()["job_id"]
        == client.post("/stocks/sync/kline").json()["job_id"]
    )


def test_missing_sync_preserves_explicit_scope(boundary):
    client, service = boundary
    assert (
        client.post(
            "/stocks/kline/sync-missing", json={"codes": ["000001"]}
        ).status_code
        == 202
    )
    service.post.assert_called_once_with(
        "/v1/jobs", {"dataset": "kline", "mode": "missing", "symbols": ["000001"]}
    )


@pytest.mark.parametrize(
    "path,dataset",
    [("list", "securities"), ("kline", "kline"), ("financial", "financials")],
)
def test_status_uses_persisted_service_counts(boundary, path, dataset):
    client, service = boundary
    job = {
        "id": "persisted",
        "status": "partial",
        "progress": 52,
        "succeeded": 50,
        "failed": 2,
        "total": 52,
    }
    service.get.return_value = {"items": [job]}
    assert client.get(f"/stocks/sync/{path}/status").json() == job
    service.get.assert_called_once_with("/v1/jobs", dataset=dataset, limit=1)


def test_unavailable_service_is_not_success(boundary):
    client, service = boundary
    service.post.side_effect = MarketDataError("offline")
    assert client.post("/stocks/sync/list").status_code == 503


def test_no_historical_job_is_idle(boundary):
    client, service = boundary
    service.get.return_value = {"items": []}
    assert client.get("/stocks/sync/list/status").json()["status"] == "idle"
