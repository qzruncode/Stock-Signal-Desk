"""Authenticated business-side façade for the independent data control plane."""

from typing import Literal

import anyio
import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from src.services.market_data_client import get_market_data_client

router = APIRouter()
Dataset = Literal[
    "securities",
    "calendar",
    "kline",
    "financials",
    "quotes",
    "news",
    "announcements",
    "market",
    "macro",
    "rss",
]


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: Dataset
    mode: Literal["stale", "missing", "all"] = "stale"
    symbols: list[str] = Field(default_factory=list, max_length=10000)


class PolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    interval_seconds: int = Field(ge=30, le=604800)
    max_age_seconds: int = Field(ge=30, le=1209600)


@router.get("/events")
async def events(
    request: Request,
    after: str = "0-0",
    last_event_id: str | None = Query(None, alias="lastEventId"),
):
    """Authenticated streaming proxy; the service credential never reaches JS."""
    client = get_market_data_client()
    upstream = httpx.AsyncClient(
        base_url=client.base_url,
        headers=client.http.headers,
        trust_env=False,
        timeout=httpx.Timeout(40, connect=3),
    )
    headers = {"Accept": "text/event-stream"}
    if cursor := request.headers.get("Last-Event-ID"):
        headers["Last-Event-ID"] = cursor
    try:
        response = await upstream.send(
            upstream.build_request(
                "GET",
                "/v1/events",
                params={"after": last_event_id or after},
                headers=headers,
            ),
            stream=True,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        await upstream.aclose()
        raise HTTPException(503, "数据推送连接暂不可用") from exc

    async def close():
        with anyio.CancelScope(shield=True):
            await response.aclose()
            await upstream.aclose()

    async def content():
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await close()

    return StreamingResponse(
        content(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=BackgroundTask(close),
    )


@router.get("/health")
def health():
    return get_market_data_client().get("/v1/health")


@router.get("/datasets")
def datasets():
    return get_market_data_client().get("/v1/datasets")


@router.get("/datasets/{dataset}/coverage")
def coverage(
    dataset: Dataset,
    status: str = "all",
    search: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    return get_market_data_client().get(
        f"/v1/datasets/{dataset}/coverage",
        status=status,
        search=search,
        page=page,
        page_size=page_size,
    )


@router.get("/datasets/{dataset}/coverage.csv")
def export_coverage(dataset: Dataset):
    response = get_market_data_client().request(
        "GET", f"/v1/datasets/{dataset}/coverage.csv"
    )
    return Response(
        response.content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{dataset}-coverage.csv"'
        },
    )


@router.put("/datasets/{dataset}/policy")
def policy(dataset: Dataset, body: PolicyUpdate):
    return (
        get_market_data_client()
        .request("PUT", f"/v1/datasets/{dataset}/policy", json=body.model_dump())
        .json()
    )


@router.post("/jobs", status_code=202)
def start_job(body: SyncRequest):
    return get_market_data_client().post("/v1/jobs", body.model_dump())


@router.get("/jobs")
def jobs(
    dataset: Dataset | None = None,
    limit: int = Query(30, ge=1, le=100),
    page: int = Query(1, ge=1),
):
    return get_market_data_client().get(
        "/v1/jobs", dataset=dataset, limit=limit, page=page
    )


@router.get("/jobs/{job_id}")
def job_detail(
    job_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    failures_only: bool = False,
):
    return get_market_data_client().get(
        f"/v1/jobs/{job_id}",
        page=page,
        page_size=page_size,
        failures_only=failures_only,
    )


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    return get_market_data_client().post(f"/v1/jobs/{job_id}/cancel")


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: str):
    return get_market_data_client().post(f"/v1/jobs/{job_id}/retry")
