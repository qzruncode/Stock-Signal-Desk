# -*- coding: utf-8 -*-
"""批量财务同步：按报告期一次拉全市场业绩快报，写入 stock_meta。

数据源（akshare）：
- stock_lrb_em(date)        — 业绩快报-利润表（营业总收入、净利润、营业利润、利润总额）
- stock_zcfz_em(date)       — 业绩快报-资产负债表（资产负债率、资产-总资产、负债-总负债、资产-货币资金）
- stock_xjll_em(date)       — 业绩快报-现金流量表（经营性现金流-现金流量净额）

注意：
- 三个接口默认排除北交所股票（filter 显式排除 TRADE_MARKET_CODE="069001017"）
- 仅返回已披露该期业绩快报的股票，覆盖率约 80%
- 字段 `deducted_profit_latest` 在业绩快报中无对应列，置 null
- 字段 `interest_bearing_debt_ratio` / `cash_debt_ratio` 业绩快报无短借/长借明细，
  已在 stock_meta 中删除
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from src.storage import DatabaseManager, StockMeta

logger = logging.getLogger(__name__)

# 状态机 — 由 sync.py 暴露路由；本模块只负责核心实现
# 模块加载时由 sync.py 注入状态对象
_state_holder: dict | None = None
_lock_holder: threading.Lock | None = None
_set_state_fn = None
_initial_state_fn = None
_utc_now_iso_fn = None


def attach_state(
    state: dict,
    lock: threading.Lock,
    set_state,
    initial_state,
    utc_now_iso,
) -> None:
    """由 sync.py 在模块加载时调用，注入状态对象。"""
    global _state_holder, _lock_holder, _set_state_fn, _initial_state_fn, _utc_now_iso_fn
    _state_holder = state
    _lock_holder = lock
    _set_state_fn = set_state
    _initial_state_fn = initial_state
    _utc_now_iso_fn = utc_now_iso


def _set(**updates) -> None:
    if _set_state_fn:
        _set_state_fn(**updates)


# 报告期候选日（季报截止日）
_REPORT_PERIOD_CANDIDATES: list[tuple[int, int]] = [
    (12, 31),  # 年报
    (9, 30),   # 三季报
    (6, 30),   # 中报
    (3, 31),   # 一季报
]

# 业绩快报在季报后约 25-30 天披露，需要留缓冲避免空数据
_REPORT_PERIOD_BUFFER_DAYS = 25


def latest_report_period(reference: date | None = None) -> str:
    """返回当前最可能已出业绩快报的报告期（YYYYMMDD 格式）。"""
    today = reference or date.today()
    candidates: list[date] = []
    for year in (today.year, today.year - 1):
        for month, day in _REPORT_PERIOD_CANDIDATES:
            d = date(year, month, day)
            if d <= today and (today - d).days >= _REPORT_PERIOD_BUFFER_DAYS:
                candidates.append(d)
    if not candidates:
        # 没满足缓冲条件，退回到 today 之前最近的报告期
        for year in (today.year, today.year - 1):
            for month, day in _REPORT_PERIOD_CANDIDATES:
                d = date(year, month, day)
                if d <= today:
                    candidates.append(d)
    return max(candidates).strftime("%Y%m%d")


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        # 业绩快报的资产负债率可能是 "38.71" 形式（百分数字符串，无 % 号）
        value = value.strip().rstrip("%")
    try:
        parsed = pd.to_numeric(value, errors="coerce")
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    return float(parsed)


def _pick_columns(df: pd.DataFrame, *candidates: str) -> pd.Series | None:
    """从 DataFrame 中按候选列名取第一个存在的，返回该列 Series。"""
    if df is None or df.empty:
        return None
    for col in candidates:
        if col in df.columns:
            return df[col]
    return None


def _normalize_code(value: Any) -> str:
    code = str(value or "").strip()
    if code.endswith(".0"):
        code = code[:-2]
    if code.isdigit() and len(code) < 6:
        code = code.zfill(6)
    return code


def _merge_fields_by_code(
    target: dict[str, dict[str, Any]],
    *,
    code_col: pd.Series,
    field_columns: dict[str, pd.Series | None],
) -> None:
    """Merge dataframe fields into target by stock code, not by row position."""
    for idx, raw_code in code_col.items():
        code = _normalize_code(raw_code)
        if not code:
            continue
        fields = target.setdefault(code, {})
        for field_name, series in field_columns.items():
            if series is None or idx not in series.index:
                continue
            value = _safe_float(series.loc[idx])
            if value is not None:
                fields[field_name] = value


def _fetch_market_dataframe(period: str, kind: str) -> pd.DataFrame:
    """调一个 akshare 业绩快报接口。kind ∈ {lrb, zcfz, xjll}。"""
    import akshare as ak

    fn = {
        "lrb": ak.stock_lrb_em,
        "zcfz": ak.stock_zcfz_em,
        "xjll": ak.stock_xjll_em,
    }[kind]
    return fn(date=period)


def _build_updates(period: str) -> tuple[list[tuple[str, dict[str, Any]]], int]:
    """拉三个接口 + 合并 + 解析，返回 [(code, {fields}), ...]。"""
    _set(message=f"业绩快报拉取中 ({period})")

    lrb = _fetch_market_dataframe(period, "lrb")
    _set(message=f"利润表已拉取 {len(lrb)} 行")
    zcfz = _fetch_market_dataframe(period, "zcfz")
    _set(message=f"资产负债表已拉取 {len(zcfz)} 行")
    xjll = _fetch_market_dataframe(period, "xjll")
    _set(message=f"现金流量表已拉取 {len(xjll)} 行")

    # 利润表列：股票代码、营业总收入、净利润
    lrb_code = _pick_columns(lrb, "股票代码", "代码")
    if lrb_code is None:
        raise RuntimeError("利润表接口未返回 股票代码 列")
    lrb_revenue = _pick_columns(lrb, "营业总收入")
    lrb_net_profit = _pick_columns(lrb, "净利润")

    # 资产负债表列：股票代码、资产负债率
    zcfz_code = _pick_columns(zcfz, "股票代码", "代码")
    if zcfz_code is None:
        raise RuntimeError("资产负债表接口未返回 股票代码 列")
    zcfz_debt_ratio = _pick_columns(zcfz, "资产负债率")

    # 现金流量表列：股票代码、经营性现金流-现金流量净额
    xjll_code = _pick_columns(xjll, "股票代码", "代码")
    if xjll_code is None:
        raise RuntimeError("现金流量表接口未返回 股票代码 列")
    xjll_op_cf = _pick_columns(xjll, "经营性现金流-现金流量净额")

    merged: dict[str, dict[str, Any]] = {}
    _merge_fields_by_code(
        merged,
        code_col=lrb_code,
        field_columns={
            "revenue_latest": lrb_revenue,
            "net_profit_latest": lrb_net_profit,
        },
    )
    _merge_fields_by_code(
        merged,
        code_col=zcfz_code,
        field_columns={"debt_ratio": zcfz_debt_ratio},
    )
    _merge_fields_by_code(
        merged,
        code_col=xjll_code,
        field_columns={"operating_cf_latest": xjll_op_cf},
    )

    report_date_iso = f"{period[:4]}-{period[4:6]}-{period[6:8]}"
    now = datetime.now()
    updates: list[tuple[str, dict[str, Any]]] = []
    for code in sorted(merged):
        fields: dict[str, Any] = {
            "report_date": report_date_iso,
            "financial_fetched_at": now,
        }
        fields.update(merged[code])
        updates.append((code, fields))

    return updates, len(updates)


def _persist_updates(updates: list[tuple[str, dict[str, Any]]]) -> tuple[int, int]:
    """批量写库：返回 (updated_count, missing_count)。"""
    db = DatabaseManager.get_instance()
    updated = missing = 0
    # 分批 500 条 UPDATE，减少单事务压力
    BATCH = 500
    from sqlalchemy import update as sa_update

    for start in range(0, len(updates), BATCH):
        batch = updates[start:start + BATCH]
        codes = [code for code, _ in batch]
        with db.get_session() as session:
            existing_codes = {
                row[0]
                for row in session.query(StockMeta.code).filter(StockMeta.code.in_(codes)).all()
            }
        for code, fields in batch:
            if code not in existing_codes:
                missing += 1
                continue
            try:
                def _write(s, _code=code, _fields=fields):
                    s.execute(
                        sa_update(StockMeta)
                        .where(StockMeta.code == _code)
                        .values(**_fields)
                    )
                db._run_write_transaction(f"financial_sync[{code}]", _write)
                updated += 1
            except Exception as exc:
                logger.warning("[FinancialSync] 写库失败 %s: %s", code, exc)
                missing += 1
        _set(
            progress=min(start + len(batch), len(updates)),
            message=f"已写入 {updated} / {len(updates)}",
        )
    return updated, missing


def run_financial_sync(period: str | None = None) -> None:
    """主入口（由 sync.py 在 daemon thread 中调用）。"""
    if not period:
        period = latest_report_period()
    _set(
        status="running",
        progress=0,
        total=0,
        message=f"启动财务同步 (报告期 {period})",
        started_at=_utc_now_iso_fn() if _utc_now_iso_fn else None,
        finished_at=None,
        error=None,
    )
    try:
        updates, total = _build_updates(period)
        _set(total=total, message=f"待写入 {total} 只股票")
        updated, missing = _persist_updates(updates)
        _set(
            status="success",
            progress=total,
            finished_at=_utc_now_iso_fn() if _utc_now_iso_fn else None,
            message=f"财务同步完成: 已更新 {updated} 只 (报告期 {period}, 未匹配到 stock_meta {missing} 只)",
        )
        logger.info(
            "[FinancialSync] 同步完成: period=%s total=%d updated=%d missing=%d",
            period, total, updated, missing,
        )
    except Exception as exc:
        _set(
            status="failed",
            error=str(exc)[:300],
            finished_at=_utc_now_iso_fn() if _utc_now_iso_fn else None,
        )
        logger.error("[FinancialSync] 同步失败: %s", exc, exc_info=True)
