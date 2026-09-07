"""Quote adapters over the authoritative data service."""

from src.services.market_data_client import read_source
from src.tools.symbols import resolve_local_symbol


def _read_source(symbol, source_key):
    return read_source(
        "quotes", {"symbol": resolve_local_symbol(symbol), "source_id": source_key}
    )


def _read_auto_source(symbol):
    return _read_source(symbol, "auto")
