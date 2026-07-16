"""HTTP adapter for Agent-owned macro-indicator logic."""

from fastapi import APIRouter, Query

from src.tools.get_macro_indicator import INDICATOR_FETCHERS

router = APIRouter()


@router.get("/indicator", summary="获取宏观经济指标")
def get_macro_indicator(
    indicator: str = Query(..., description="PMI | CPI | PPI | GDP | M2 | 社融 | LPR"),
    months: int = Query(12, ge=3, le=120, description="最近发布期数"),
):
    from src.tools.get_macro_indicator import get_macro_indicator as tool_get_macro_indicator

    return tool_get_macro_indicator(
        indicator if isinstance(indicator, str) else "PMI",
        periods=months if isinstance(months, int) else 12,
    )


__all__ = ["INDICATOR_FETCHERS", "get_macro_indicator", "router"]
