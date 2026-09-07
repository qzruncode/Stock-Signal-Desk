"""Batch data-service access shared by screening; no acquisition or local SQL."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from src.services.market_data_client import get_market_data_client


def financial_rows(codes):
    response = get_market_data_client().snapshot(codes, ["financials"], wait=1)
    result = {}
    for code, item in response["items"].items():
        row = item.get("financials")
        if not row:
            continue
        result[code] = {
            field: row[field]
            for field in (
                "revenue_ttm",
                "parent_net_profit_ttm",
                "deducted_net_profit_ttm",
                "debt_ratio",
            )
            if row.get(field) is not None
        }
        result[code].update(
            financial_report_period=row.get("report_date"),
            financial_source="market-data-service",
            data_versions=item.get("versions", {}),
        )
    periods = [
        row["financial_report_period"]
        for row in result.values()
        if row["financial_report_period"]
    ]
    return result, min(periods) if periods else None


def daily_rows(codes, count):
    """Bound request sizes, preserve per-security failures, never fail-open."""
    bars, failures, sources = {}, {}, {}
    client = get_market_data_client()
    batch_size = min(50, max(1, 200000 // count))
    batches = [
        codes[start : start + batch_size] for start in range(0, len(codes), batch_size)
    ]

    def read(batch):
        return client.snapshot(batch, ["kline"], count=count, wait=1)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(read, batch): batch for batch in batches}
        for future in as_completed(futures):
            batch = futures[future]
            try:
                result = future.result()
                for code, item in result["items"].items():
                    rows = item.get("kline") or []
                    if not rows:
                        failures[code] = "日线缺失"
                    else:
                        bars[code] = rows
                        source = str(
                            rows[-1].get("data_source") or "market-data-service"
                        )
                        sources[source] = sources.get(source, 0) + 1
            except Exception as exc:
                failures.update({code: str(exc) for code in batch})
    return bars, failures, sources
