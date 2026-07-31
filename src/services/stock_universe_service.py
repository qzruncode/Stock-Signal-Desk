"""Fetch and persist the authoritative active A-share security universe."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable

from data_provider.akshare_fetcher import AkshareFetcher
from data_provider.fetchers.market import (
    get_last_a_stock_list_error,
    reset_a_stock_list_fetch_state,
)
from src.storage import DatabaseManager, StockMeta

logger = logging.getLogger(__name__)

LIST_SYNC_BATCH_SIZE = 200
LIST_SYNC_FETCH_ATTEMPTS = 3
LIST_SYNC_FETCH_RETRY_DELAY_SECONDS = 2.0
MINIMUM_A_SHARE_UNIVERSE_SIZE = 4000
MINIMUM_EXISTING_COVERAGE_RATIO = 0.85
MAXIMUM_MISSING_ACTIVE_RATIO = 0.02
MAXIMUM_MISSING_ACTIVE_FLOOR = 50

ProgressCallback = Callable[[int, int, str], None]


def sync_stock_universe(on_progress: ProgressCallback | None = None) -> dict:
    """Synchronously refresh ``stock_meta`` and return an auditable summary."""
    db = DatabaseManager.get_instance()
    fetcher = AkshareFetcher()
    reset_a_stock_list_fetch_state()
    stocks_raw = None
    last_error = None

    for attempt in range(1, LIST_SYNC_FETCH_ATTEMPTS + 1):
        if on_progress:
            on_progress(0, 0, f"股票列表拉取中 {attempt}/{LIST_SYNC_FETCH_ATTEMPTS}")
        stocks_raw = fetcher.get_all_a_stocks()
        if stocks_raw:
            break
        last_error = get_last_a_stock_list_error()
        if attempt < LIST_SYNC_FETCH_ATTEMPTS:
            time.sleep(LIST_SYNC_FETCH_RETRY_DELAY_SECONDS * attempt)

    if not stocks_raw:
        raise RuntimeError("未能从数据源获取股票列表" + (f": {last_error}" if last_error else ""))

    unique_by_code = {
        str(item.get("code") or "").strip(): item for item in stocks_raw if str(item.get("code") or "").strip()
    }
    stocks_raw = list(unique_by_code.values())
    markets = {str(item.get("market") or "").strip().lower() for item in stocks_raw}
    with db.get_session() as session:
        known_active_codes = {
            str(row.code) for row in session.query(StockMeta.code).filter(StockMeta.status == "active").all()
        }
    minimum_safe_total = max(
        MINIMUM_A_SHARE_UNIVERSE_SIZE,
        int(len(known_active_codes) * MINIMUM_EXISTING_COVERAGE_RATIO),
    )
    missing_active_count = len(known_active_codes.difference(unique_by_code))
    maximum_missing_active = max(
        MAXIMUM_MISSING_ACTIVE_FLOOR,
        int(len(known_active_codes) * MAXIMUM_MISSING_ACTIVE_RATIO),
    )
    has_shanghai = bool(markets.intersection({"sh", "kcb"}))
    has_shenzhen = bool(markets.intersection({"sz", "cyb"}))
    if (
        len(stocks_raw) < minimum_safe_total
        or missing_active_count > maximum_missing_active
        or not (has_shanghai and has_shenzhen)
    ):
        raise RuntimeError(
            f"股票源返回 {len(stocks_raw)} 只、市场 {sorted(markets)}，"
            f"安全要求至少 {minimum_safe_total} 只、遗漏现有 active 股票不超过 "
            f"{maximum_missing_active} 只且同时覆盖沪深市场；"
            "拒绝写入，避免把数据源缺页误判为退市"
        )

    now = datetime.now()
    added = updated = delisted = 0
    delisted_daily = 0
    all_codes = [str(item["code"]) for item in stocks_raw]
    total = len(stocks_raw)

    for start in range(0, total, LIST_SYNC_BATCH_SIZE):
        batch = stocks_raw[start : start + LIST_SYNC_BATCH_SIZE]
        batch_codes = [str(item["code"]) for item in batch]
        with db.get_session() as session:
            existing = {row.code: row for row in session.query(StockMeta).filter(StockMeta.code.in_(batch_codes)).all()}
            for item in batch:
                code = str(item["code"])
                meta = existing.get(code)
                if meta is None:
                    session.add(
                        StockMeta(
                            code=code,
                            name=item["name"],
                            market=item["market"],
                            status="active",
                            sector=item.get("sector"),
                            ipo_date=item.get("ipo_date"),
                            last_sync_at=now,
                        )
                    )
                    added += 1
                    continue
                meta.name = item["name"]
                meta.market = item["market"]
                meta.status = "active"
                if item.get("sector"):
                    meta.sector = item.get("sector")
                if item.get("ipo_date"):
                    meta.ipo_date = item.get("ipo_date")
                meta.last_sync_at = now
                updated += 1
            session.commit()
        progress = min(start + len(batch), total)
        if on_progress:
            on_progress(progress, total, f"股票列表写入中 {progress}/{total}")

    with db.get_session() as session:
        delisted_codes = [
            row.code
            for row in session.query(StockMeta.code)
            .filter(StockMeta.code.notin_(set(all_codes)), StockMeta.status == "active")
            .all()
        ]
        if delisted_codes:
            delisted = len(delisted_codes)
            session.query(StockMeta).filter(StockMeta.code.in_(delisted_codes)).update(
                {"status": "delisted", "updated_at": now}, synchronize_session=False
            )
        session.commit()

    summary = {
        "total": total,
        "added": added,
        "updated": updated,
        "delisted": delisted,
        "delisted_daily": delisted_daily,
        "data_time": now.isoformat(),
    }
    logger.info("[StockUniverse] refresh complete: %s", summary)
    return summary


__all__ = ["sync_stock_universe"]
