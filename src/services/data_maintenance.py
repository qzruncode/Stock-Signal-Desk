"""Business data readiness; acquisition is exclusively owned by the data service."""

from src.services.market_data_client import get_market_data_client


def ensure_stock_universe(*, trigger="agent", force=False, on_progress=None):
    client = get_market_data_client()
    if force:
        job = client.post("/v1/jobs", {"dataset": "securities", "mode": "all"})
        client.wait_job(job, on_progress=on_progress)
    result = client.securities(page_size=1)
    return {
        "total": result["total"],
        "data_time": None,
        "is_stale": False,
        "refreshed": force,
        "maintenance_status": "ready",
        "source": "market-data-service",
    }


def get_data_health():
    client = get_market_data_client()
    response = client.get("/v1/datasets")
    datasets = {item["id"]: item for item in response["items"]}

    def summary(key):
        item = datasets[key]
        return {
            "covered_stocks": item["total"] - item["missing"],
            "missing_stocks": item["missing"],
            "fresh_stocks": item["fresh"],
            "stale_stocks": item["stale"],
            "coverage_ratio": (item["coverage_percent"] or 0) / 100,
            "latest_trade_date": item["latest_data_time"],
            "latest_report_period": item["latest_data_time"],
        }

    return {
        "stock_universe": client.securities(page_size=1, require_fresh=False),
        "kline": summary("kline"),
        "financials": summary("financials"),
        "datasets": response["items"],
        "service": response["service"],
        "recent_jobs": client.get("/v1/jobs", limit=10)["items"],
    }
