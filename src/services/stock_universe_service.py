"""Explicit security-master refresh through the independently managed data service."""

from src.services.market_data_client import get_market_data_client


def sync_stock_universe(on_progress=None):
    client = get_market_data_client()
    job = client.post("/v1/jobs", {"dataset": "securities", "mode": "all"})
    client.wait_job(job, on_progress=on_progress)
    return client.securities(page_size=1)
