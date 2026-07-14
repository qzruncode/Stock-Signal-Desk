# -*- coding: utf-8 -*-
"""``get_market_mainline_report`` tool."""

from typing import Any


def get_market_mainline_report(include_debug_input: bool = False) -> Any:
    from src.services.market_theme_service import MarketThemeService
    return MarketThemeService().get_model_report_for_tool(include_debug_input=include_debug_input)
