"""A-share views backed by the data service, not the business database."""

from fastapi import Query
from pydantic import BaseModel, Field
from api.v1.endpoints.stocks import router
from src.services.market_data_client import get_market_data_client


@router.get("")
def list_stocks(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=500),
    search: str = "",
    market: str = "",
    count: bool = True,
):
    result = get_market_data_client().securities(
        page=page, page_size=page_size, search=search, market=market or "all"
    )
    total_pages = max(1, (result["total"] + page_size - 1) // page_size)
    return {**result, "has_more": page < total_pages, "total_pages": total_pages}


@router.get("/count")
def get_stock_count():
    result = get_market_data_client().securities(page_size=1)
    return {"total": result["total"], "freshness": result["freshness"]}


@router.get("/kline-status")
def get_kline_status():
    client = get_market_data_client()
    item = next(
        row for row in client.get("/v1/datasets")["items"] if row["id"] == "kline"
    )
    missing = []
    page = 1
    while True:
        batch = client.get(
            "/v1/datasets/kline/coverage", status="missing", page=page, page_size=200
        )
        missing.extend(row["symbol"] for row in batch["items"])
        if len(missing) >= batch["total"]:
            break
        page += 1
    return {
        "total_stocks": item["total"],
        "stocks_with_kline": item["total"] - item["missing"],
        "missing": item["missing"],
        "missing_codes": missing,
        "latest_trading_day": item["latest_data_time"],
        "fresh_stocks": item["fresh"],
        "stale_stocks": item["stale"],
        "coverage_percent": item["coverage_percent"],
    }


class KlineBatchRequest(BaseModel):
    codes: list[str] = Field(default_factory=list, max_length=100)
    count: int = Field(default=250, ge=1, le=1000)


@router.post("/kline/batch")
def get_kline_batch(body: KlineBatchRequest):
    response = get_market_data_client().snapshot(
        body.codes, ["kline"], count=body.count
    )
    return {
        "results": {code: value["kline"] for code, value in response["items"].items()},
        "freshness": response["freshness"],
        "partial": response["partial"],
    }
