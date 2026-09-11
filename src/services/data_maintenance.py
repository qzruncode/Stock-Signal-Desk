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
