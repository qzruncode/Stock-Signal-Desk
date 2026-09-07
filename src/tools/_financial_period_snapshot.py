"""An explicit historical report-period read, owned by the data service."""

from src.services.market_data_client import read_source


def fetch_financial_period_snapshot(period):
    return read_source("financials.fetch_period", {"period": period})["data"]
