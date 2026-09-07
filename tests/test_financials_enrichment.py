# -*- coding: utf-8 -*-

from market_data_service.providers import financials


def test_fetch_financials_enriches_summary_with_statement_fields(monkeypatch):
    monkeypatch.setattr(
        financials,
        "fetch_core_indicators",
        lambda symbol, periods, **kwargs: [
            {
                "report_date": "2025-12-31",
                "revenue": 100.0,
                "revenue_yoy": 10.0,
                "net_profit": 20.0,
                "net_profit_yoy": 8.0,
                "deducted_profit": 18.0,
                "deducted_profit_yoy": 7.0,
                "gross_margin": 40.0,
                "net_margin": 20.0,
            },
            {
                "report_date": "2026-03-31",
                "revenue": 120.0,
                "revenue_yoy": 12.0,
                "net_profit": 24.0,
                "net_profit_yoy": 9.0,
                "deducted_profit": 21.0,
                "deducted_profit_yoy": 8.0,
                "gross_margin": 42.0,
                "net_margin": 20.0,
            },
        ],
    )
    monkeypatch.setattr(
        financials,
        "get_financial_bundle",
        lambda symbol, periods, **kwargs: {
            "symbol": symbol,
            "periods": periods,
            "balance_sheet": [
                {
                    "report_date": "2025-12-31",
                    "accounts_receivable": 30.0,
                    "inventory": 40.0,
                    "contract_liabilities": 15.0,
                },
                {
                    "report_date": "2026-03-31",
                    "accounts_receivable": 36.0,
                    "inventory": 45.0,
                    "contract_liabilities": 18.0,
                },
            ],
            "income_statement": [
                {
                    "report_date": "2025-12-31",
                    "parent_net_profit": 19.0,
                    "parent_net_profit_yoy": 7.5,
                    "deducted_net_profit": 18.0,
                    "deducted_net_profit_yoy": 7.0,
                    "asset_impairment_loss": 2.0,
                },
                {
                    "report_date": "2026-03-31",
                    "parent_net_profit": 23.0,
                    "parent_net_profit_yoy": 8.5,
                    "deducted_net_profit": 21.0,
                    "deducted_net_profit_yoy": 8.0,
                    "asset_impairment_loss": 3.0,
                },
            ],
            "cashflow": [
                {"report_date": "2025-12-31", "operating_cash_flow": 26.0},
                {"report_date": "2026-03-31", "operating_cash_flow": 28.0},
            ],
            "source": "fake",
        },
    )

    result = financials._build("600519", periods=2)

    assert "同花顺" in result["source"]
    assert len(result["items"]) == 2

    first = result["items"][0]
    second = result["items"][1]

    assert first["revenue_qoq"] is None
    assert second["revenue_qoq"] == 20.0
    assert second["parent_net_profit"] == 23.0
    assert second["parent_net_profit_yoy"] == 8.5
    assert second["deducted_net_profit"] == 21.0
    assert second["deducted_net_profit_yoy"] == 8.0
    assert second["operating_cash_flow"] == 28.0
    assert second["accounts_receivable"] == 36.0
    assert second["inventory"] == 45.0
    assert second["contract_liabilities"] == 18.0
    assert second["asset_impairment_loss"] == 3.0


def test_fetch_financials_keeps_summary_data_when_statement_enrichment_fails(
    monkeypatch,
):
    monkeypatch.setattr(
        financials,
        "fetch_core_indicators",
        lambda symbol, periods, **kwargs: [
            {"report_date": "2026-03-31", "revenue": 120.0}
        ],
    )
    monkeypatch.setattr(
        financials,
        "get_financial_bundle",
        lambda symbol, periods, **kwargs: (_ for _ in ()).throw(
            RuntimeError("detail unavailable")
        ),
    )

    result = financials._build("600519", periods=1)

    assert result["items"][0]["revenue"] == 120.0 and result["partial"]
    assert "东方财富财报: detail unavailable" in result["errors"]
