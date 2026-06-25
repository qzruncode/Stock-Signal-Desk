# -*- coding: utf-8 -*-
"""A-share stock list sync and query endpoints."""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_system_config_service
from api.v1.schemas.common import ErrorResponse
from data_provider.akshare_fetcher import AkshareFetcher
from src.services.system_config_service import SystemConfigService
from src.storage import DatabaseManager, StockMeta, StockDaily
from sqlalchemy import delete, func, select

logger = logging.getLogger(__name__)

import chinese_calendar


def _get_latest_trading_day(reference: date | None = None) -> date:
    """计算最近一个 A 股交易日，自动跳过周末和中国法定节假日。"""
    d = reference or date.today()
    for _ in range(30):
        if chinese_calendar.is_workday(d):
            return d
        d -= timedelta(days=1)
    return reference or date.today()


router = APIRouter()

# Global sync state (in-process, restart resets it)
_sync_lock = threading.Lock()
_sync_state: dict = {
    "status": "idle",  # idle | running | syncing_kline | success | failed
    "progress": 0,
    "total": 0,
    "kline_progress": 0,
    "kline_total": 0,
    "started_at": None,
    "finished_at": None,
    "message": "",
    "error": None,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_sync_state(**updates) -> None:
    with _sync_lock:
        _sync_state.update(updates)


def _get_sync_state_copy() -> dict:
    with _sync_lock:
        return dict(_sync_state)


def _mark_sync_started() -> bool:
    with _sync_lock:
        if _sync_state["status"] in ("running", "syncing_kline"):
            return False
        _sync_state.update({
            "status": "running",
            "progress": 0,
            "total": 0,
            "kline_progress": 0,
            "kline_total": 0,
            "started_at": _utc_now_iso(),
            "finished_at": None,
            "message": "",
            "error": None,
        })
        return True


def _run_sync():
    """Execute full A-share stock sync in a background thread."""
    try:
        db = DatabaseManager.get_instance()
        today_start = datetime.combine(date.today(), datetime.min.time())

        # Check if stock list was already synced today
        with db.get_session() as session:
            latest_sync = session.query(func.max(StockMeta.last_sync_at)).scalar()
        already_synced_today = latest_sync is not None and latest_sync >= today_start

        delisted = 0
        delisted_codes_count = 0
        added, updated = 0, 0
        skip_phase_one = False

        if already_synced_today:
            skip_phase_one = True
            # Check if ALL active stocks have K-line data
            with db.get_session() as session:
                active_count = session.query(func.count(StockMeta.id)).filter(StockMeta.status == "active").scalar()
                stock_with_data = session.query(func.count(func.distinct(StockDaily.code))).scalar()
            if stock_with_data < active_count:
                logger.info("[StocksSync] K 线数据不完整 (%d/%d)，强制执行 K 线同步", stock_with_data, active_count)
            else:
                logger.info("[StocksSync] 今日已同步，跳过 Phase 1 和 K 线阶段")
                with db.get_session() as session:
                    count = session.query(func.count(StockMeta.id)).filter(StockMeta.status == "active").scalar()
                _set_sync_state(
                    status="success",
                    progress=count,
                    kline_progress=count,
                    kline_total=count,
                    finished_at=_utc_now_iso(),
                    message=f"同步完成: 今日已同步，跳过列表和 K 线更新",
                )
                logger.info("[StocksSync] 同步完成（跳过）")
                return

        # At this point, either we still need to run Phase 1, OR we forced through
        # from already_synced_today (today's list sync done, but K-line incomplete).
        # In the forced-through case, load existing stocks from DB and skip Phase 1.
        if skip_phase_one:
            with db.get_session() as session:
                stocks = [
                    {"code": row.code, "name": row.name}
                    for row in session.query(StockMeta.code, StockMeta.name)
                    .filter(StockMeta.status == "active")
                    .order_by(StockMeta.code)
                    .all()
                ]
        else:
            # Phase 1: Fetch stock list + delisted cleanup
            fetcher = AkshareFetcher()
            stocks_raw = fetcher.get_all_a_stocks()

            if not stocks_raw:
                _set_sync_state(
                    status="failed",
                    message="未能从数据源获取股票列表",
                    finished_at=_utc_now_iso(),
                )
                return

            _set_sync_state(total=len(stocks_raw))
            now = datetime.now()

            with db.get_session() as session:
                all_codes = [s["code"] for s in stocks_raw]
                existing = {
                    row.code: row
                    for row in session.query(StockMeta).filter(StockMeta.code.in_(all_codes)).all()
                }

                for i, item in enumerate(stocks_raw):
                    code = item["code"]
                    meta = existing.get(code)

                    if meta:
                        meta.name = item["name"]
                        meta.market = item["market"]
                        meta.pe_ttm = item.get("pe_ttm")
                        meta.pb = item.get("pb")
                        meta.total_market_cap = item.get("total_market_cap")
                        meta.circulating_market_cap = item.get("circulating_market_cap")
                        meta.last_sync_at = now
                        updated += 1
                    else:
                        meta = StockMeta(
                            code=code,
                            name=item["name"],
                            market=item["market"],
                            status="active",
                            pe_ttm=item.get("pe_ttm"),
                            pb=item.get("pb"),
                            total_market_cap=item.get("total_market_cap"),
                            circulating_market_cap=item.get("circulating_market_cap"),
                            last_sync_at=now,
                        )
                        session.add(meta)
                        added += 1

                    if (i + 1) % 100 == 0:
                        _set_sync_state(progress=i + 1)

                # Mark delisted stocks and clean up daily data
                current_codes = set(all_codes)
                delisted_codes = [
                    row.code
                    for row in session.query(StockMeta.code)
                    .filter(StockMeta.code.notin_(current_codes), StockMeta.status == "active")
                    .all()
                ]

                if delisted_codes:
                    session.execute(delete(StockDaily).where(StockDaily.code.in_(delisted_codes)))
                    delisted_codes_count = len(delisted_codes)

                delisted = (
                    session.query(StockMeta)
                    .filter(StockMeta.code.notin_(current_codes), StockMeta.status == "active")
                    .update({"status": "delisted", "updated_at": now}, synchronize_session=False)
                )

                session.commit()

            stocks = stocks_raw

        # Phase 2: Incremental K-line sync — batch concurrent for speed
        kline_total = len(stocks)
        kline_incremental = 0
        kline_skipped = kline_total
        kline_failed = 0

        active_codes = [s["code"] for s in stocks]
        today = _get_latest_trading_day()
        _set_sync_state(
            status="syncing_kline",
            kline_progress=0,
            kline_total=kline_total,
        )

        # Batch query latest dates for all stocks at once
        with db.get_session() as session:
            from sqlalchemy import select
            latest_dates = {
                row[0]: row[1]
                for row in session.execute(
                    select(StockDaily.code, func.max(StockDaily.date))
                    .where(StockDaily.code.in_(active_codes))
                    .group_by(StockDaily.code)
                ).all()
            }

        # Categorize in memory (no per-stock DB calls)
        full_fetch_codes: List[str] = []
        incremental_codes: List[str] = []
        skip_count = 0

        for code in active_codes:
            latest_date = latest_dates.get(code)
            if latest_date is None:
                full_fetch_codes.append(code)
            elif latest_date >= today - timedelta(days=1):
                skip_count += 1
            else:
                incremental_codes.append(code)

        kline_skipped = skip_count
        progress_offset = skip_count  # Skipped stocks count as progress
        _set_sync_state(kline_progress=progress_offset)

        from data_provider.akshare_fetcher import AkshareFetcher as AF
        batch_fetcher = AF(sleep_min=0.3, sleep_max=0.5)
        inc_fetcher = AF(sleep_min=0.3, sleep_max=0.5)

        # Batch full fetch with concurrent threads + real-time progress
        if full_fetch_codes:
            import concurrent.futures
            logger.info("[StocksSync] K线全量并发拉取 %d 只股票", len(full_fetch_codes))

            def _fetch_one(code: str) -> tuple:
                df = batch_fetcher.fetch_stock_kline_history(code, days=365)
                return code, df

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                futures = {pool.submit(_fetch_one, code): code for code in full_fetch_codes}
                for future in concurrent.futures.as_completed(futures):
                    code, df = future.result()
                    try:
                        if df is not None and not df.empty:
                            db.save_daily_data(df, code, "akshare")
                            kline_incremental += 1
                        else:
                            kline_failed += 1
                    except Exception as e:
                        logger.warning("[StocksSync] K线保存失败 %s: %s", code, str(e)[:120])
                        kline_failed += 1
                    progress_offset += 1
                    _set_sync_state(kline_progress=progress_offset)

        # Sequential incremental fetch (usually just a few days of data)
        for code in incremental_codes:
            try:
                latest_date = db.get_latest_daily_date(code)
                if latest_date is None or latest_date >= today - timedelta(days=1):
                    kline_skipped += 1
                    continue

                start_date_str = (latest_date + timedelta(days=1)).strftime("%Y%m%d")
                end_date_str = today.strftime("%Y%m%d")

                inc_fetcher._enforce_rate_limit()
                df = inc_fetcher.fetch_stock_kline_history(
                    code, days=max(60, (today - latest_date).days + 5),
                )
                if df is not None and not df.empty:
                    db.save_daily_data(df, code, "akshare")
                    kline_incremental += 1
                else:
                    kline_failed += 1
            except Exception as e:
                logger.warning("[StocksSync] K线增量同步失败 %s: %s", code, str(e)[:120])
                kline_failed += 1
            progress_offset += 1
            _set_sync_state(kline_progress=progress_offset)

        msg_parts = []
        if already_synced_today:
            msg_parts.append("今日已同步，跳过列表更新")
        else:
            msg_parts.append(f"新增 {added}, 更新 {updated}, 退市 {delisted}(清理日线 {delisted_codes_count})")
        msg_parts.append(f"K线增量 {kline_incremental}, 跳过 {kline_skipped}, 失败 {kline_failed}")

        _set_sync_state(
            status="success",
            progress=len(stocks),
            kline_progress=kline_total,
            finished_at=_utc_now_iso(),
            message=f"同步完成: {', '.join(msg_parts)}",
        )

        logger.info(
            "[StocksSync] 同步完成: 总数=%d 新增=%d 更新=%d 退市=%d",
            len(stocks), added, updated, delisted,
        )

    except Exception as e:
        _set_sync_state(status="failed", error=str(e), finished_at=_utc_now_iso())
        logger.error("[StocksSync] 同步失败: %s", e, exc_info=True)


@router.post(
    "/sync",
    summary="Sync all A-share stocks",
    responses={409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def sync_stocks(
    service: SystemConfigService = Depends(get_system_config_service),
):
    """Trigger a full sync of all A-share stock metadata from East Money via akshare."""
    if not _mark_sync_started():
        raise HTTPException(
            status_code=409,
            detail={"error": "sync_in_progress", "message": "同步正在进行中，请稍后再试"},
        )

    thread = threading.Thread(target=_run_sync, daemon=True)
    thread.start()

    return {
        "success": True,
        "message": "同步已启动",
        "status": "running",
    }


@router.get(
    "/sync/status",
    summary="Get sync status",
)
def get_sync_status():
    """Return current sync progress.

    When the in-memory state is idle/empty (e.g. after a restart), fall back
    to the database count so the frontend doesn't show "not synced yet".
    """
    state = _get_sync_state_copy()
    if state["total"] == 0:
        try:
            db = DatabaseManager.get_instance()
            with db.get_session() as session:
                total = session.query(StockMeta).filter(StockMeta.status == "active").count()
            if total > 0:
                return {
                    **state,
                    "status": "success" if state["status"] == "idle" else state["status"],
                    "total": total,
                    "message": state["message"] or "数据已存在（来自数据库）",
                }
        except Exception:
            pass
    return state


@router.get(
    "",
    summary="List all A-share stocks",
)
def list_stocks(
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=50, ge=10, le=500, description="每页数量"),
    search: Optional[str] = Query(default=None, description="搜索代码或名称"),
    market: Optional[str] = Query(default=None, description="市场筛选 (sh/sz/cyb/kcb/bj)"),
):
    """Paginate and search the synced A-share stock list."""
    db = DatabaseManager.get_instance()

    with db.get_session() as session:
        query = session.query(StockMeta).filter(StockMeta.status == "active")

        if market:
            query = query.filter(StockMeta.market == market)

        if search:
            search_term = f"%{search.strip()}%"
            from sqlalchemy import or_
            query = query.filter(
                or_(
                    StockMeta.code.like(search_term),
                    StockMeta.name.like(search_term),
                )
            )

        total = query.count()
        items = (
            query.order_by(StockMeta.code)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        return {
            "items": [item.to_dict() for item in items],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
        }


@router.get(
    "/count",
    summary="Get total stock count",
)
def get_stock_count():
    """Return total count of active A-share stocks in database."""
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        total = session.query(StockMeta).filter(StockMeta.status == "active").count()
    return {"total": total}


@router.get(
    "/kline-status",
    summary="验证 K 线数据完整性",
)
def get_kline_status():
    """返回股票总数、已有 K 线数据的股票数、缺失数、最近交易日。"""
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        total_stocks = session.query(func.count(StockMeta.id)).filter(StockMeta.status == "active").scalar()
        stocks_with_kline = session.query(func.count(func.distinct(StockDaily.code))).scalar()
        latest_trading_day = session.execute(select(func.max(StockDaily.date))).scalar()

    return {
        "total_stocks": total_stocks or 0,
        "stocks_with_kline": stocks_with_kline or 0,
        "missing": (total_stocks or 0) - (stocks_with_kline or 0),
        "latest_trading_day": str(latest_trading_day) if latest_trading_day else None,
    }


@router.post(
    "/kline/batch",
    summary="Get batch kline data for specified stock codes",
)
def get_kline_batch(body: dict):
    """Return daily OHLCV data from local DB for specified stock codes.

    Request body: { "codes": ["000001", "000002", ...], "count": 250 }
    Returns: { "results": { "000001": [[date, o, h, l, c], ...], ... } }
    Only returns data for codes that exist in DB; missing codes get empty arrays.
    """
    import time
    from sqlalchemy import select

    codes: list[str] = body.get("codes", [])
    count: int = body.get("count", 250)

    if not codes:
        return {"results": {}}

    # Cap batch size to prevent abuse
    codes = codes[:100]

    t0 = time.time()
    db = DatabaseManager.get_instance()

    with db.get_session() as session:
        rows = session.execute(
            select(StockDaily.code, StockDaily.date, StockDaily.open, StockDaily.high,
                   StockDaily.low, StockDaily.close)
            .where(StockDaily.code.in_(codes))
            .order_by(StockDaily.code, StockDaily.date)
        ).all()

    # Group by code, convert to compact arrays [date_str, o, h, l, c]
    klines: dict[str, list] = {c: [] for c in codes}
    for row in rows:
        date_str = row[1].isoformat() if hasattr(row[1], 'isoformat') else str(row[1])[:10]
        klines[row[0]].append([date_str, row[2], row[3], row[4], row[5]])

    # Trim to most recent `count` bars per stock
    results = {}
    for code in codes:
        data = klines[code]
        if len(data) > count:
            data = data[-count:]
        results[code] = data

    elapsed = int((time.time() - t0) * 1000)
    logger.info(f"[kline/batch] {len(codes)} codes, {elapsed}ms")

    return {"results": results}


# ========================================================================
# Fundamental filter endpoint (Phase 3 screening)
# ========================================================================

_FUNDAMENTAL_CACHE_TTL_DAYS = 30
_FUNDAMENTAL_BATCH_SIZE = 20
_FUNDAMENTAL_BATCH_INTERVAL = 0.2
_FUNDAMENTAL_TIMEOUT_SECONDS = 180  # 3 minutes


def _compute_ttm_value(
    statements: dict,
    field: str,
) -> float | None:
    """Compute TTM value from financial statements.

    Strategy:
    - If latest report is annual (12-31), use that value directly.
    - Otherwise, sum the last 4 quarters.
    - Returns None if data is insufficient.
    """
    # Try income_statement first, then cashflow, then balance_sheet
    for section in ('income_statement', 'cashflow', 'balance_sheet'):
        items = statements.get(section) or []
        if not items:
            continue

        # Sort by report_date descending
        sorted_items = sorted(
            items,
            key=lambda x: x.get('report_date') or '',
            reverse=True,
        )

        latest = sorted_items[0]
        latest_date = latest.get('report_date') or ''

        # If latest is annual (ends with 12-31), use it directly
        if latest_date.endswith('12-31'):
            val = latest.get(field)
            if val is not None:
                return float(val)

        # Otherwise sum last 4 quarters
        values = []
        for item in sorted_items[:4]:
            v = item.get(field)
            if v is not None:
                values.append(float(v))

        if len(values) >= 4:
            return sum(values)
        # Insufficient data
        return None

    return None


def _fetch_and_compute_fundamentals(
    code: str,
    db: DatabaseManager,
) -> dict | None:
    """Fetch financial statements, compute criteria fields, upsert stock_meta.

    Returns dict with computed fields, or None if fetch failed.
    """
    # Import financials functions (they're private in financials.py module)
    try:
        from api.v1.endpoints import financials as fin_mod
    except ImportError:
        logger.warning(f"[fundamental-filter] Cannot import financials module for {code}")
        return None

    normalize_symbol = getattr(fin_mod, '_normalize_symbol', None) or (lambda s: s.strip())
    fetch_ths_triple = getattr(fin_mod, '_fetch_from_ths_triple', None)
    fetch_em_statements = getattr(fin_mod, '_fetch_financial_statements_em', None)

    if fetch_ths_triple is None:
        logger.warning(f"[fundamental-filter] _fetch_from_ths_triple not available for {code}")
        return None

    symbol = normalize_symbol(code)

    # Try THS triple first
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
        return None

    # Extract latest balance sheet item for ratio fields
    bs_items = (statements.get('balance_sheet') or [])
    latest_bs = None
    for item in sorted(bs_items, key=lambda x: x.get('report_date') or '', reverse=True):
        if item.get('total_assets') and item.get('total_assets') > 0:
            latest_bs = item
            break

    if not latest_bs:
        return None

    total_assets = latest_bs.get('total_assets', 0)
    total_liabilities = latest_bs.get('total_liabilities', 0)

    # Compute debt_ratio only
    debt_ratio = (total_liabilities / total_assets * 100) if total_assets else None

    # Compute TTM values: revenue and deducted_profit only
    revenue_ttm = _compute_ttm_value(statements, 'revenue')
    deducted_profit_ttm = _compute_ttm_value(statements, 'deducted_net_profit')

    # Get latest report date
    all_dates = []
    for section in ('balance_sheet', 'income_statement'):
        for item in (statements.get(section) or []):
            d = item.get('report_date')
            if d:
                all_dates.append(d)
    latest_report = max(all_dates) if all_dates else None

    now = datetime.now()

    # Cache valid when 3 key fields are present
    key_fields_complete = (
        revenue_ttm is not None
        and deducted_profit_ttm is not None
        and debt_ratio is not None
    )

    # Upsert into stock_meta — only 3 fields
    def _write(session):
        from sqlalchemy import select as sa_select
        existing = session.execute(
            sa_select(StockMeta).where(StockMeta.code == code)
        ).scalars().first()
        if existing:
            existing.revenue_ttm = revenue_ttm
            existing.deducted_profit_ttm = deducted_profit_ttm
            existing.debt_ratio = debt_ratio
            existing.report_date = latest_report
            if key_fields_complete:
                existing.financial_fetched_at = now
        else:
            logger.warning(f"[fundamental-filter] StockMeta not found for {code}, skipping")
        return True

    try:
        db._run_write_transaction(f"fundamental_filter[{code}]", _write)
    except Exception as e:
        logger.warning(f"[fundamental-filter] DB upsert failed for {code}: {e}")
        return None

    return {
        'revenue_ttm': revenue_ttm,
        'deducted_profit_ttm': deducted_profit_ttm,
        'debt_ratio': debt_ratio,
        'report_date': latest_report,
    }


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
    from sqlalchemy import select as sa_select

    codes: list[str] = body.get("codes", [])[:200]  # cap at 200

    if not codes:
        return {"data": {}}

    db = DatabaseManager.get_instance()
    ttl_cutoff = datetime.now() - timedelta(days=_FUNDAMENTAL_CACHE_TTL_DAYS)

    data: dict[str, dict] = {}

    timeout_at = time.time() + _FUNDAMENTAL_TIMEOUT_SECONDS
    codes_to_fetch: list[str] = []

    # Step 1: Load stock_meta for all codes (batch read)
    with db.get_session() as session:
        rows = session.execute(
            sa_select(StockMeta).where(StockMeta.code.in_(codes))
        ).scalars().all()

        meta_map: dict[str, StockMeta] = {r.code: r for r in rows}

    # Step 2: Return cached data if valid
    for code in codes:
        meta = meta_map.get(code)
        if meta and meta.financial_fetched_at and meta.financial_fetched_at >= ttl_cutoff:
            data[code] = {
                'revenue_ttm': meta.revenue_ttm,
                'deducted_profit_ttm': meta.deducted_profit_ttm,
                'operating_cf_ttm': meta.operating_cf_ttm,
                'net_profit_ttm': meta.net_profit_ttm,
                'debt_ratio': meta.debt_ratio,
                'interest_bearing_debt_ratio': meta.interest_bearing_debt_ratio,
                'cash_debt_ratio': meta.cash_debt_ratio,
                'report_date': meta.report_date,
            }
        else:
            codes_to_fetch.append(code)

    logger.info(f"[fundamental-filter] {len(codes) - len(codes_to_fetch)} cached, {len(codes_to_fetch)} to fetch")

    # Step 3: Fetch uncached codes in batches (5/batch, 0.5s interval)
    idx = 0
    timed_out = False
    while idx < len(codes_to_fetch):
        if time.time() >= timeout_at:
            timed_out = True
            for code in codes_to_fetch[idx:]:
                data[code] = {}
            break

        batch = codes_to_fetch[idx:idx + _FUNDAMENTAL_BATCH_SIZE]
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
