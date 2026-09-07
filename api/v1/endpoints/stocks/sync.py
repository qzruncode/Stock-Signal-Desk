"""Compatibility routes for existing clients; no business-side collectors or state."""

from pydantic import BaseModel, Field
from api.v1.endpoints.stocks import router
from src.services.market_data_client import get_market_data_client


class MissingRequest(BaseModel):
    codes: list[str] = Field(default_factory=list, max_length=10000)


def start(dataset, *, mode="stale", codes=None):
    job = get_market_data_client().post(
        "/v1/jobs", {"dataset": dataset, "mode": mode, "symbols": codes or []}
    )
    return {
        **job,
        "success": True,
        "job_id": job["id"],
        "message": job["message"] or "任务已交给独立数据服务",
    }


def latest(dataset):
    items = get_market_data_client().get("/v1/jobs", dataset=dataset, limit=1)["items"]
    return (
        items[0]
        if items
        else {"status": "idle", "progress": 0, "total": 0, "message": "尚未执行"}
    )


@router.post("/sync/list", status_code=202)
def sync_stock_list():
    return start("securities")


@router.get("/sync/list/status")
def get_stock_list_sync_status():
    return latest("securities")


@router.post("/sync/kline", status_code=202)
def sync_stock_kline():
    return start("kline")


@router.get("/sync/kline/status")
def get_stock_kline_sync_status():
    return latest("kline")


@router.post("/kline/sync-missing", status_code=202)
def sync_missing_kline(body: MissingRequest):
    return start("kline", mode="missing", codes=body.codes)


@router.get("/kline/sync-missing/status")
def get_missing_kline_sync_status():
    return latest("kline")


@router.post("/sync/financial", status_code=202)
def sync_stock_financial():
    return start("financials")


@router.get("/sync/financial/status")
def get_stock_financial_sync_status():
    return latest("financials")
