"""Concept constituents come from the independent data service."""

from src.services.market_data_client import get_market_data_client, read_source


def _load_local_universe():
    rows = get_market_data_client().securities(page_size=10000)["items"]
    return {
        row["code"]: {
            "symbol": row["code"],
            "name": row["name"],
            "sector": row.get("sector"),
            "revenue_latest": None,
            "net_profit_latest": None,
            "report_date": None,
        }
        for row in rows
    }


def get_theme_stock_candidates(
    theme, *, board_code=None, local_universe=None, maintenance_result=None
):
    # The service validates its own universe; caller-supplied identities never
    # become authoritative source data across the network boundary.
    return read_source(
        "market.theme_candidates", {"theme": theme, "board_code": board_code}
    )
