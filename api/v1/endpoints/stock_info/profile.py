# -*- coding: utf-8 -*-
"""HTTP adapter for the module-owned company-profile tool."""

from __future__ import annotations

from fastapi import HTTPException, Query

from api.v1.endpoints.stock_info import router
from src.tools.get_stock_info import get_stock_info as _tool_get_stock_info


def _normalize_symbol(symbol: str) -> str:
    text = str(symbol).strip()
    lower = text.lower()
    return text[2:] if lower.startswith(("sh", "sz", "bj")) else text


def _fetch_from_cninfo(symbol: str) -> dict:
    """Compatibility export for legacy in-project callers."""
    from src.tools.get_stock_info import _fetch_cninfo

    try:
        return _fetch_cninfo(_normalize_symbol(symbol))
    except Exception:
        return {}


def _fetch_from_em(symbol: str) -> dict:
    """Compatibility export with normalized Eastmoney capital fields."""
    from src.tools.get_stock_info import _fetch_eastmoney_capital

    try:
        return _fetch_eastmoney_capital(_normalize_symbol(symbol))
    except Exception:
        return {}


@router.get("/info", summary="获取个股基本资料")
def get_stock_info(
    symbol: str = Query(..., description="股票代码，如 000001、600519"),
    force: bool = Query(False, description="强制实时拉取，跳过缓存"),
):
    try:
        result = _tool_get_stock_info(symbol, use_cache=not force)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_symbol", "message": str(exc)}) from exc
    if not result.get("success"):
        raise HTTPException(
            status_code=502,
            detail={
                "error": "no_data",
                "message": "; ".join(result.get("errors") or ["无法获取公司资料"]),
            },
        )
    return result
