# -*- coding: utf-8 -*-
"""Agent facade for shared AKShare company evidence domains."""

from __future__ import annotations

from typing import Any

from src.services.akshare_evidence import get_company_evidence
from src.tools.base import ToolSpec, object_schema

_SCOPES = {
    "all": None,
    "ownership": ("ownership",),
    "financial_events": ("financial_events",),
    "corporate_events": ("corporate_events",),
    "trading_evidence": ("trading_evidence",),
    "risk_and_catalyst": ("ownership", "financial_events", "corporate_events"),
}


def get_company_structured_evidence(
    symbol: str,
    scope: str = "all",
    days: int = 730,
    report_period_count: int = 4,
) -> dict[str, Any]:
    if scope not in _SCOPES:
        raise ValueError("scope 必须是 all/ownership/financial_events/corporate_events/trading_evidence/risk_and_catalyst")
    if not 30 <= int(days) <= 1460:
        raise ValueError("days 必须在 30 到 1460 之间")
    if not 1 <= int(report_period_count) <= 8:
        raise ValueError("report_period_count 必须在 1 到 8 之间")
    return get_company_evidence(
        symbol,
        sections=_SCOPES[scope],
        days=int(days),
        report_period_count=int(report_period_count),
    )


TOOL = ToolSpec(
    name="get_company_structured_evidence",
    description=(
        "获取 A 股公司的结构化事项与持仓证据，覆盖股权质押、限售解禁、北向个股持仓、股东增减持、"
        "控制权、业绩预告/快报/报表、财报预约、商誉减值、互动问答、机构调研、回购、停复牌、重大合同、定增、"
        "筹码分布和龙虎榜机构席位。结果只保留事实、来源、覆盖和失败边界，语义判断由模型完成。"
    ),
    parameters=object_schema(
        {
            "symbol": {"type": "string", "description": "A 股代码或公司名称"},
            "scope": {
                "type": "string",
                "enum": list(_SCOPES),
                "default": "all",
                "description": "所需证据域；风险/催化可用 risk_and_catalyst",
            },
            "days": {"type": "integer", "minimum": 30, "maximum": 1460, "default": 730},
            "report_period_count": {"type": "integer", "minimum": 1, "maximum": 8, "default": 4},
        },
        ["symbol"],
    ),
    executor=get_company_structured_evidence,
    category="company_evidence",
)


__all__ = ["TOOL", "get_company_structured_evidence"]
