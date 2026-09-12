from unittest.mock import MagicMock, patch

import pytest

from src.tools.registry import ToolRegistry
from src.tools.search_stocks import TOOL


@pytest.mark.parametrize(
    ("query", "expected_query", "expected_codes"),
    [("600519", "600519", "600519"), (None, "", "")],
)
def test_stock_search_normalizes_nullable_text_filters(
    query, expected_query, expected_codes
):
    client = MagicMock()
    client.securities.return_value = {"items": [], "total": 0, "freshness": "fresh"}

    with patch(
        "src.services.market_data_client.get_market_data_client", return_value=client
    ):
        result = ToolRegistry.from_tools([TOOL]).execute(
            "search_stocks", {"query": query, "sector": None}
        )

    client.securities.assert_called_once_with(
        search="",
        codes=expected_codes,
        market="sh,sz,cyb,kcb,bj",
        sector="",
        page_size=20,
    )
    assert result["query"] == expected_query
    assert result["sector"] is None
    assert result["is_stale"] is None
    assert result["freshness_unknown"] is True
