# -*- coding: utf-8 -*-
"""Fundamental stock filter endpoint (Phase 3 screening)."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Query

from api.v1.endpoints.stocks import router
from src.storage import DatabaseManager, StockMeta
from sqlalchemy import select as sa_select

logger = logging.getLogger(__name__)

_FUNDAMENTAL_CACHE_TTL_DAYS = 30
_FUNDAMENTAL_BATCH_SIZE = 20
_FUNDAMENTAL_BATCH_INTERVAL = 0.2
_FUNDAMENTAL_TIMEOUT_SECONDS = 180


def _compute_ttm_value(
    statements: dict,
    field: str,
) -> float | None:
    """Compute TTM value from financial statements."""
    for section in ("income_statement", "cashflow", "balance_sheet"):
        items = statements.get(section) or []
        if not items:
            continue

        sorted_items = sorted(
            items,
            key=lambda x: x.get("report_date") or "",
            reverse=True,
        )

        latest = sorted_items[0]
        latest_date = latest.get("report_date") or ""

        if latest_date.endswith("12-31"):
            val = latest.get(field)
            if val is not None:
                return float(val)

        values = []
        for item in sorted_items[:4]:
            v = item.get(field)
            if v is not None:
                values.append(float(v))

        if len(values) >= 4:
            return sum(values)
        return None

    return None


def _safe_float(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _compute_abstract_ttm(rows, metric_name: str) -> float | None:
    metric_rows = [row for row in rows if str(row.get("metric_name") or "") == metric_name]
    if not metric_rows:
        return None
    metric_rows = sorted(metric_rows, key=lambda row: row.get("report_date") or "", reverse=True)
    latest = metric_rows[0]
    latest_date = str(latest.get("report_date") or "")
    latest_value = _safe_float(latest.get("value"))
    if latest_date.endswith("12-31"):
        return latest_value

    single_values = []
    for row in metric_rows[:4]:
        value = _safe_float(row.get("single"))
        if value is not None:
            single_values.append(value)
    if len(single_values) >= 4:
        return sum(single_values)
    return latest_value


def _fetch_abstract_fundamentals(code: str) -> dict | None:
    try:
        import akshare as ak

        df = ak.stock_financial_abstract_new_ths(symbol=code, indicator="按报告期")
    except Exception as exc:
        logger.warning(f"[fundamental-filter] THS abstract failed for {code}: {exc}")
        return None

    if df is None or df.empty:
        return None

    rows = df.to_dict("records")
    report_dates = [str(row.get("report_date") or "")[:10] for row in rows if row.get("report_date")]
    latest_report = max(report_dates) if report_dates else None

    latest_debt_ratio = None
    debt_rows = [row for row in rows if str(row.get("metric_name") or "") == "assets_debt_ratio"]
    if debt_rows:
        debt_rows = sorted(debt_rows, key=lambda row: row.get("report_date") or "", reverse=True)
        latest_debt_ratio = _safe_float(debt_rows[0].get("value"))

    return {
        "revenue_latest": _compute_abstract_ttm(rows, "operating_income_total"),
        "net_profit_latest": _compute_abstract_ttm(rows, "parent_holder_net_profit"),
        "operating_cf_latest": None,
        "debt_ratio": latest_debt_ratio,
        "report_date": latest_report,
    }


def _write_fundamentals(code: str, db: DatabaseManager, computed: dict) -> bool:
    key_fields_complete = (
        computed.get("revenue_latest") is not None
        and computed.get("net_profit_latest") is not None
        and computed.get("debt_ratio") is not None
    )
    now = datetime.now()

    def _write(session):
        existing = session.execute(sa_select(StockMeta).where(StockMeta.code == code)).scalars().first()
        if existing:
            existing.revenue_latest = computed.get("revenue_latest")
            existing.net_profit_latest = computed.get("net_profit_latest")
            existing.operating_cf_latest = computed.get("operating_cf_latest")
            existing.debt_ratio = computed.get("debt_ratio")
            existing.report_date = computed.get("report_date")
            if key_fields_complete:
                existing.financial_fetched_at = now
        else:
            logger.warning(f"[fundamental-filter] StockMeta not found for {code}, skipping")
        return True

    try:
        db._run_write_transaction(f"fundamental_filter[{code}]", _write)
        return True
    except Exception as e:
        logger.warning(f"[fundamental-filter] DB upsert failed for {code}: {e}")
        return False


def _fetch_and_compute_fundamentals(
    code: str,
    db: DatabaseManager,
) -> dict | None:
    """Fetch financial statements, compute criteria fields, upsert stock_meta.

    Returns dict with computed fields, or None if fetch failed.
    """
    try:
        from api.v1.endpoints import financials as fin_mod
    except ImportError:
        logger.warning(f"[fundamental-filter] Cannot import financials module for {code}")
        return None

    normalize_symbol = getattr(fin_mod, "_normalize_symbol", None) or (lambda s: s.strip())
    fetch_ths_triple = getattr(fin_mod, "_fetch_from_ths_triple", None)
    fetch_em_statements = getattr(fin_mod, "_fetch_financial_statements_em", None)

    if fetch_ths_triple is None:
        logger.warning(f"[fundamental-filter] _fetch_from_ths_triple not available for {code}")
        return None

    symbol = normalize_symbol(code)

    statements = None
    try:
        statements = fetch_ths_triple(symbol, periods=8)
    except Exception as e:
        logger.warning(f"[fundamental-filter] THS triple failed for {code}: {e}")
        if fetch_em_statements:
            try:
                statements = fetch_em_statements(symbol)
            except Exception as e2:
                logger.warning(f"[fundamental-filter] EM also failed for {code}: {e2}")

    if not statements:
        computed = _fetch_abstract_fundamentals(code)
        if computed and _write_fundamentals(code, db, computed):
            return computed
        return None

    bs_items = statements.get("balance_sheet") or []
    latest_bs = None
    for item in sorted(bs_items, key=lambda x: x.get("report_date") or "", reverse=True):
        if item.get("total_assets") and item.get("total_assets") > 0:
            latest_bs = item
            break

    if not latest_bs:
        computed = _fetch_abstract_fundamentals(code)
        if computed and _write_fundamentals(code, db, computed):
            return computed
        return None

    total_assets = latest_bs.get("total_assets", 0)
    total_liabilities = latest_bs.get("total_liabilities", 0)
    monetary_funds = latest_bs.get("monetary_funds") or 0
    interest_bearing_debt = sum(
        value or 0
        for value in (
            latest_bs.get("short_loan"),
            latest_bs.get("long_loan"),
            latest_bs.get("noncurrent_liab_1year"),
            latest_bs.get("lease_liab"),
        )
    )

    debt_ratio = (total_liabilities / total_assets * 100) if total_assets else None

    revenue_latest = _compute_ttm_value(statements, "revenue")
    net_profit_latest = _compute_ttm_value(statements, "net_profit")
    operating_cf_latest = _compute_ttm_value(statements, "operating_cf")

    all_dates = []
    for section in ("balance_sheet", "income_statement"):
        for item in statements.get(section) or []:
            d = item.get("report_date")
            if d:
                all_dates.append(d)
    latest_report = max(all_dates) if all_dates else None

    computed = {
        "revenue_latest": revenue_latest,
        "net_profit_latest": net_profit_latest,
        "operating_cf_latest": operating_cf_latest,
        "debt_ratio": debt_ratio,
        "report_date": latest_report,
    }
    if not _write_fundamentals(code, db, computed):
        return None

    return computed


@router.post(
    "/fundamental-filter",
    summary="基本面 2 筛：返回原始财务数据供前端过滤",
)
def fundamental_filter(body: dict):
    """对给定股票代码列表拉取财务数据。

    先查 stock_meta 缓存（30 天内且关键字段完整），未缓存的调用 akshare 拉取财务报表。
    返回每只股票的原始财务数据，由前端进行条件过滤。
    """
    import time

    codes: list[str] = body.get("codes", [])[:200]

    if not codes:
        return {"data": {}}

    db = DatabaseManager.get_instance()
    ttl_cutoff = datetime.now() - timedelta(days=_FUNDAMENTAL_CACHE_TTL_DAYS)

    data: dict[str, dict] = {}

    timeout_at = time.time() + _FUNDAMENTAL_TIMEOUT_SECONDS
    codes_to_fetch: list[str] = []

    with db.get_session() as session:
        rows = session.execute(sa_select(StockMeta).where(StockMeta.code.in_(codes))).scalars().all()

        meta_map: dict[str, StockMeta] = {r.code: r for r in rows}

    for code in codes:
        meta = meta_map.get(code)
        if meta and meta.financial_fetched_at and meta.financial_fetched_at >= ttl_cutoff:
            data[code] = {
                "revenue_latest": meta.revenue_latest,
                "net_profit_latest": meta.net_profit_latest,
                "operating_cf_latest": meta.operating_cf_latest,
                "debt_ratio": meta.debt_ratio,
                "report_date": meta.report_date,
            }
        else:
            codes_to_fetch.append(code)

    logger.info(f"[fundamental-filter] {len(codes) - len(codes_to_fetch)} cached, {len(codes_to_fetch)} to fetch")

    idx = 0
    timed_out = False
    while idx < len(codes_to_fetch):
        if time.time() >= timeout_at:
            timed_out = True
            for code in codes_to_fetch[idx:]:
                data[code] = {}
            break

        batch = codes_to_fetch[idx : idx + _FUNDAMENTAL_BATCH_SIZE]
        idx += _FUNDAMENTAL_BATCH_SIZE

        for code in batch:
            computed = _fetch_and_compute_fundamentals(code, db)
            if computed:
                data[code] = computed
            else:
                data[code] = {}

        if idx < len(codes_to_fetch):
            time.sleep(_FUNDAMENTAL_BATCH_INTERVAL)

    if timed_out:
        logger.warning(f"[fundamental-filter] Timeout after processing {len(data)} codes")

    logger.info(f"[fundamental-filter] done: {len(data)} stocks returned")

    return {"data": data}
