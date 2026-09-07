"""Financial source API contracts; report collection belongs to the data service."""

from src.services.market_data_client import read_source


def get_financial_bundle(symbol, periods=6, *, use_cache=True):
    return read_source(
        "financials.get_financial_bundle", {"symbol": symbol, "periods": periods}
    )


def get_financial_section(
    symbol, section, periods=4, *, use_cache=True, local_identity=False
):
    return read_source(
        "financials.get_financial_section",
        {
            "symbol": symbol,
            "section": section,
            "periods": periods,
            "local_identity": local_identity,
        },
    )


def fetch_core_indicators(symbol, periods):
    return read_source(
        "financials.fetch_core_indicators", {"symbol": symbol, "periods": periods}
    )["data"]
