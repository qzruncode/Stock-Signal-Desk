# -*- coding: utf-8 -*-
"""On-demand stock metadata enrichment endpoints."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pandas as pd
from fastapi import HTTPException
from sqlalchemy import select as sa_select

from api.v1.endpoints.stocks import router
from api.v1.endpoints.stocks.filter import _fetch_and_compute_fundamentals
from src.storage import DatabaseManager, StockMeta

logger = logging.getLogger(__name__)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = pd.to_numeric(value, errors="coerce")
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    return float(parsed)


def _latest_value_row(df: pd.DataFrame) -> pd.Series | None:
    if df is None or df.empty:
        return None
    if "数据日期" in df.columns:
        work = df.copy()
        work["_parsed_date"] = pd.to_datetime(work["数据日期"], errors="coerce")
        work = work.sort_values("_parsed_date")
        return work.iloc[-1]
    return df.iloc[-1]


def _enrich_valuation(code: str, db: DatabaseManager) -> tuple[bool, str | None]:
    import akshare as ak

    try:
        df = ak.stock_value_em(symbol=code)
        row = _latest_value_row(df)
        if row is None:
            return False, "stock_value_em 返回空"

        updates = {
            "total_market_cap": _safe_float(row.get("总市值")),
            "circulating_market_cap": _safe_float(row.get("流通市值")),
            "pe_ttm": _safe_float(row.get("PE(TTM)")),
            "pb": _safe_float(row.get("市净率")),
        }
        updates = {key: value for key, value in updates.items() if value is not None}
        if not updates:
            return False, "stock_value_em 未返回可写估值字段"

        def _write(session):
            meta = session.execute(
                sa_select(StockMeta).where(StockMeta.code == code)
            ).scalars().first()
            if not meta:
                return False
            for key, value in updates.items():
                setattr(meta, key, value)
            meta.last_sync_at = datetime.now()
            return True

        ok = db._run_write_transaction(f"stock_enrich_valuation[{code}]", _write)
        return bool(ok), None if ok else "stock_meta 不存在"
    except Exception as exc:
        logger.warning("[StockEnrich] valuation failed for %s: %s", code, exc)
        return False, str(exc)[:180]


@router.post(
    "/enrich",
    summary="按需补齐单只股票详情字段",
)
def enrich_stock(body: dict):
    code = str(body.get("code") or "").strip()
    sections = body.get("sections") or ["valuation", "financial"]
    if not code:
        raise HTTPException(status_code=400, detail="code is required")
    if not isinstance(sections, list):
        sections = [str(sections)]

    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        exists = session.execute(
            sa_select(StockMeta.code).where(StockMeta.code == code)
        ).scalar_one_or_none()
    if not exists:
        raise HTTPException(status_code=404, detail="stock not found")

    updated_sections: list[str] = []
    errors: dict[str, str] = {}

    if "valuation" in sections:
        ok, error = _enrich_valuation(code, db)
        if ok:
            updated_sections.append("valuation")
        elif error:
            errors["valuation"] = error

    if "financial" in sections:
        computed = _fetch_and_compute_fundamentals(code, db)
        if computed:
            updated_sections.append("financial")
        else:
            errors["financial"] = "财务数据获取失败或关键字段为空"

    with db.get_session() as session:
        meta = session.execute(
            sa_select(StockMeta).where(StockMeta.code == code)
        ).scalars().first()

    return {
        "item": meta.to_dict() if meta else None,
        "updated_sections": updated_sections,
        "errors": errors,
    }
