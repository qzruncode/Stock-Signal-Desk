"""Batch current quotes; source I/O and refresh policies belong to the data service."""

from concurrent.futures import ThreadPoolExecutor
from src.services.market_data_client import MarketDataError, read_source
from src.tools.symbols import resolve_local_symbol

REALTIME_QUOTES_DESCRIPTION = (
    "通过独立数据服务获取实时行情，保留来源报价时间、数据版本与新鲜度。"
)


def get_realtime_quotes(symbols: list[str]) -> dict:
    codes = list(dict.fromkeys(resolve_local_symbol(symbol) for symbol in symbols))

    def read(code):
        try:
            return read_source("quotes", {"symbol": code, "source_id": "auto"})
        except MarketDataError as exc:
            return {
                "success": False,
                "items": [],
                "errors": [f"{code}: {exc}"],
                "symbol": code,
            }

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(codes)))) as pool:
        values = list(pool.map(read, codes))
    items = [item for result in values for item in result.get("items", [])]
    errors = [error for result in values for error in result.get("errors", [])]
    times = [result.get("data_time") for result in values if result.get("data_time")]
    return {
        "success": bool(items),
        "partial": bool(errors),
        "items": items,
        "total": len(items),
        "requested_symbols": codes,
        "source": "market-data-service",
        "errors": errors,
        "warnings": [],
        "data_time": min(times) if times else None,
        "data_time_provenance": "source" if times else "unavailable",
        "is_stale": False if items and not errors else None,
        "freshness_unknown": not bool(times),
        "data_versions": {
            code: result.get("data_service") for code, result in zip(codes, values)
        },
    }
