"""Exact live-catalog board retrieval without semantic aliases."""

from __future__ import annotations

from unittest.mock import patch

from src.tools.get_theme_stock_candidates import (
    _fetch_eastmoney_constituents,
    _same_catalog_identifier,
    get_theme_stock_candidates,
)


def test_catalog_identifier_normalizes_format_only_not_business_language() -> None:
    assert _same_catalog_identifier("人形机器人", "人形机器人") is True
    assert _same_catalog_identifier("AI·手机", "AI手机") is True
    assert _same_catalog_identifier(
        "人形机器人",
        "人形机器人上游核心零部件方向",
    ) is False
    assert _same_catalog_identifier("机器人概念", "人形机器人") is False


def test_eastmoney_fetches_every_page_of_the_exact_board() -> None:
    requested_pages: list[int] = []

    class Response:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    def fake_get(_url: str, *, params: dict, **_kwargs):
        page = int(params["pn"])
        fs = params["fs"]
        if fs == "m:90 t:3 f:!50":
            return Response({
                "data": {
                    "total": 2,
                    "diff": [
                        {"f12": "BK1184", "f14": "人形机器人"},
                        {"f12": "BK0001", "f14": "机器人概念"},
                    ],
                },
            })
        requested_pages.append(page)
        start = (page - 1) * 100
        size = min(100, 205 - start)
        return Response({
            "data": {
                "total": 205,
                "diff": [
                    {"f12": f"{index:06d}", "f14": f"公司{index}"}
                    for index in range(start + 1, start + size + 1)
                ],
            },
        })

    with patch("requests.get", side_effect=fake_get):
        items, boards, errors = _fetch_eastmoney_constituents("人形机器人")

    assert errors == []
    assert len(items) == 205
    assert requested_pages == [1, 2, 3]
    assert [board["name"] for board in boards] == ["人形机器人"]
    assert boards[0]["coverage"] == "full"


def test_theme_candidates_intersect_exact_constituents_with_local_universe() -> None:
    local = {
        "000001": {
            "symbol": "000001",
            "name": "公司一",
            "sector": "专用设备制造业",
        },
        "000002": {
            "symbol": "000002",
            "name": "公司二",
            "sector": "专用设备制造业",
        },
    }
    exact_items = [
        {
            "symbol": "000001",
            "source_name": "公司一",
            "board": "人形机器人",
            "primary_theme": True,
            "source": "测试概念板块",
            "source_url": "https://example.test/board",
        },
        {
            "symbol": "999999",
            "source_name": "不在本地证券库",
            "board": "人形机器人",
            "primary_theme": True,
            "source": "测试概念板块",
            "source_url": "https://example.test/board",
        },
    ]
    board = [{
        "name": "人形机器人",
        "coverage": "full",
        "primary_theme": True,
    }]

    with patch(
        "src.tools.get_theme_stock_candidates._fetch_eastmoney_constituents",
        return_value=(exact_items, board, []),
    ), patch(
        "src.tools.get_theme_stock_candidates._fetch_sina_constituents",
        return_value=([], [], []),
    ), patch(
        "src.tools.get_theme_stock_candidates._fetch_ths_constituents",
        return_value=([], [], []),
    ):
        result = get_theme_stock_candidates(
            "人形机器人",
            local_universe=local,
            maintenance_result={"total": 2},
        )

    assert result["success"] is True
    assert result["coverage_complete"] is True
    assert [item["symbol"] for item in result["items"]] == ["000001"]
    assert result["items"][0]["evidence_level"] == "L1"


def test_free_form_phrase_does_not_expand_to_a_catalog_alias() -> None:
    with patch(
        "src.tools.get_theme_stock_candidates._fetch_eastmoney_constituents",
        return_value=([], [], []),
    ), patch(
        "src.tools.get_theme_stock_candidates._fetch_sina_constituents",
        return_value=([], [], []),
    ), patch(
        "src.tools.get_theme_stock_candidates._fetch_ths_constituents",
        return_value=([], [], []),
    ):
        result = get_theme_stock_candidates(
            "只梳理精确的人形机器人主题A股候选",
            local_universe={"000001": {"symbol": "000001", "name": "公司一"}},
            maintenance_result={"total": 1},
        )

    assert result["success"] is False
    assert result["items"] == []
