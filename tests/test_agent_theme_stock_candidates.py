from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from src.tools.get_theme_stock_candidates import (
    _canonical_theme,
    _fetch_eastmoney_constituents,
    _fetch_ths_constituents,
    get_theme_stock_candidates,
)


def test_product_level_robot_board_is_not_widened_to_generic_robot_theme() -> None:
    assert _canonical_theme("机器人执行器") == "机器人执行器"
    assert _canonical_theme("机器人减速器") == "机器人减速器"
    assert _canonical_theme("只梳理精确的人形机器人主题A股候选") == "人形机器人"


@pytest.fixture(autouse=True)
def _skip_real_universe_maintenance():
    with patch(
        "src.services.data_maintenance.ensure_stock_universe",
        return_value={
            "total": 12,
            "data_time": "2026-07-19T00:00:00",
            "is_stale": False,
            "refreshed": False,
            "maintenance_status": "ready",
        },
    ):
        yield


def test_eastmoney_theme_candidates_fetch_every_constituent_page() -> None:
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
            return Response({"data": {"total": 1, "diff": [{"f12": "BK1184", "f14": "人形机器人"}]}})
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
    assert boards[0]["coverage"] == "full"
    assert boards[0]["primary_theme"] is True


def test_ths_theme_candidates_fetch_every_constituent_page() -> None:
    requested: list[str] = []

    class Response:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str, **_kwargs):
        requested.append(url)
        page = 1
        if "/page/" in url:
            page = int(url.split("/page/", 1)[1].split("/", 1)[0])
        return Response(f"<span class='page_info'>1/3</span><!--PAGE{page}-->")

    def fake_read_html(source):
        text = source.getvalue()
        page = int(text.split("PAGE", 1)[1].split("-->", 1)[0])
        return [pd.DataFrame([{"代码": f"00000{page}", "名称": f"公司{page}"}])]

    with patch(
        "src.tools.get_theme_stock_candidates._ths_board_map",
        return_value={"人形机器人": "309119", "机器人概念": "300816"},
    ), patch("requests.get", side_effect=fake_get), patch("pandas.read_html", side_effect=fake_read_html):
        items, boards, errors = _fetch_ths_constituents("人形机器人")

    assert errors == []
    assert [item["symbol"] for item in items] == ["000001", "000002", "000003"]
    assert boards[0]["coverage"] == "full"
    assert boards[0]["constituent_count"] == 3
    assert boards[0]["fetched_page_count"] == 3
    assert any("/page/2/" in url for url in requested)
    assert any("/page/3/" in url for url in requested)


def test_theme_candidates_intersect_board_constituents_with_full_local_universe() -> None:
    local = {
        f"0000{index:02d}": {
            "symbol": f"0000{index:02d}",
            "name": f"公司{index}",
            "sector": "专用设备制造业",
            "revenue_latest": index * 100,
            "net_profit_latest": index,
            "report_date": "2026-03-31",
        }
        for index in range(1, 13)
    }
    sina_items = [
        {
            "symbol": f"0000{index:02d}",
            "source_name": f"公司{index}",
            "board": "机器人概念",
            "board_score": 98,
            "source": "新浪概念板块",
            "source_url": "https://example.com/sina",
        }
        for index in range(1, 11)
    ]
    # This code is outside stock_meta and must never leak into the candidate pool.
    sina_items.append({
        "symbol": "999999", "source_name": "不存在公司", "board": "机器人概念",
        "board_score": 98, "source": "新浪概念板块", "source_url": "https://example.com/sina",
    })
    ths_items = [
        {
            "symbol": f"0000{index:02d}",
            "source_name": f"公司{index}",
            "board": "人形机器人",
            "board_score": 100,
            "primary_theme": True,
            "source": "同花顺概念板块",
            "source_url": "https://example.com/ths",
        }
        for index in range(1, 4)
    ]

    with patch(
        "src.tools.get_theme_stock_candidates._load_local_universe", return_value=local,
    ), patch(
        "src.tools.get_theme_stock_candidates._fetch_eastmoney_constituents",
        return_value=([], [], []),
    ), patch(
        "src.tools.get_theme_stock_candidates._fetch_sina_constituents",
        return_value=(sina_items, [{"name": "机器人概念"}], []),
    ), patch(
        "src.tools.get_theme_stock_candidates._fetch_ths_constituents",
        return_value=(ths_items, [{"name": "人形机器人"}], []),
    ):
        result = get_theme_stock_candidates("人形机器人", limit=40)

    assert result["success"] is True
    assert result["local_universe_count"] == 12
    assert result["candidate_count"] == 10
    assert result["returned_count"] == 10
    assert all(item["symbol"] != "999999" for item in result["items"])
    assert all(item["evidence_level"] == "L1" for item in result["items"])
    assert all(item["company_evidence_required"] is True for item in result["items"])
    assert result["items"][0]["primary_theme_membership"] is True
    assert result["items"][0]["board_count"] == 2


def test_theme_candidates_keep_successful_source_when_another_source_fails() -> None:
    local = {
        "300024": {
            "symbol": "300024", "name": "机器人", "sector": "专用设备制造业",
            "revenue_latest": 1, "net_profit_latest": 1, "report_date": "2026-03-31",
        },
    }
    raw = [{
        "symbol": "300024", "source_name": "机器人", "board": "机器人概念",
        "board_score": 98, "source": "新浪概念板块", "source_url": "https://example.com/sina",
    }]

    with patch(
        "src.tools.get_theme_stock_candidates._load_local_universe", return_value=local,
    ), patch(
        "src.tools.get_theme_stock_candidates._fetch_eastmoney_constituents",
        return_value=([], [], []),
    ), patch(
        "src.tools.get_theme_stock_candidates._fetch_sina_constituents",
        return_value=(raw, [{"name": "机器人概念"}], []),
    ), patch(
        "src.tools.get_theme_stock_candidates._fetch_ths_constituents",
        side_effect=RuntimeError("upstream blocked"),
    ):
        result = get_theme_stock_candidates("人形机器人")

    assert result["success"] is True
    assert result["partial"] is True
    assert result["candidate_count"] == 1
    assert "upstream blocked" in result["warnings"][0]


def test_exact_humanoid_board_excludes_broad_robot_alias_when_fully_covered() -> None:
    local = {
        "300024": {
            "symbol": "300024", "name": "机器人", "sector": "专用设备",
            "revenue_latest": 1, "net_profit_latest": 1, "report_date": "2026-03-31",
        },
        "300580": {
            "symbol": "300580", "name": "贝斯特", "sector": "汽车零部件",
            "revenue_latest": 1, "net_profit_latest": 1, "report_date": "2026-03-31",
        },
    }
    exact = [{
        "symbol": "300580", "source_name": "贝斯特", "board": "人形机器人",
        "board_score": 100, "primary_theme": True,
        "source": "东方财富概念板块", "source_url": "https://example.com/humanoid",
    }]
    broad = [{
        "symbol": "300024", "source_name": "机器人", "board": "机器人概念",
        "board_score": 99, "primary_theme": False,
        "source": "新浪概念板块", "source_url": "https://example.com/robot",
    }]

    with patch(
        "src.tools.get_theme_stock_candidates._load_local_universe", return_value=local,
    ), patch(
        "src.tools.get_theme_stock_candidates._fetch_eastmoney_constituents",
        return_value=(exact, [{
            "name": "人形机器人", "source": "东方财富概念板块",
            "coverage": "full", "primary_theme": True,
        }], []),
    ), patch(
        "src.tools.get_theme_stock_candidates._fetch_sina_constituents",
        return_value=(broad, [{
            "name": "机器人概念", "source": "新浪概念板块",
            "coverage": "full", "primary_theme": False,
        }], []),
    ), patch(
        "src.tools.get_theme_stock_candidates._fetch_ths_constituents",
        return_value=([], [], []),
    ):
        result = get_theme_stock_candidates("人形机器人")

    assert [item["symbol"] for item in result["items"]] == ["300580"]
    assert all(board["primary_theme"] is True for board in result["matched_boards"])


def test_conversational_theme_argument_keeps_exact_board_boundary() -> None:
    local = {
        "300580": {
            "symbol": "300580", "name": "贝斯特", "sector": "汽车零部件",
            "revenue_latest": 1, "net_profit_latest": 1, "report_date": "2026-03-31",
        },
        "300024": {
            "symbol": "300024", "name": "机器人", "sector": "专用设备",
            "revenue_latest": 1, "net_profit_latest": 1, "report_date": "2026-03-31",
        },
    }
    exact = [{
        "symbol": "300580", "source_name": "贝斯特", "board": "人形机器人",
        "board_score": 100, "primary_theme": True,
        "source": "东方财富概念板块", "source_url": "https://example.com/humanoid",
    }]
    broad = [{
        "symbol": "300024", "source_name": "机器人", "board": "机器人概念",
        "board_score": 99, "primary_theme": False,
        "source": "新浪概念板块", "source_url": "https://example.com/robot",
    }]

    with patch(
        "src.tools.get_theme_stock_candidates._load_local_universe", return_value=local,
    ), patch(
        "src.tools.get_theme_stock_candidates._fetch_eastmoney_constituents",
        return_value=(exact, [{
            "name": "人形机器人", "source": "东方财富概念板块",
            "coverage": "full", "primary_theme": True,
        }], []),
    ), patch(
        "src.tools.get_theme_stock_candidates._fetch_sina_constituents",
        return_value=(broad, [{
            "name": "机器人概念", "source": "新浪概念板块",
            "coverage": "full", "primary_theme": False,
        }], []),
    ), patch(
        "src.tools.get_theme_stock_candidates._fetch_ths_constituents",
        return_value=([], [], []),
    ):
        result = get_theme_stock_candidates("只梳理精确的人形机器人主题A股候选")

    assert result["theme"] == "人形机器人"
    assert [item["symbol"] for item in result["items"]] == ["300580"]
    assert all(board["primary_theme"] is True for board in result["matched_boards"])
