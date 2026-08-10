"""按活跃股票覆盖同步最近可用的财务报告摘要。

同步任务的分母是当前 ``stock_meta`` 中的 active 股票，而不是某个报告期
接口返回的行数。主源按报告期读取东方财富全市场财务主指标；如果一只股票
在批量报告期快照中仍没有可用数据，再使用现有的逐股财务聚合源补取。

这里写入的是一只股票同一报告期的一组摘要字段。不同报告期的数据不会拼接
成一条“看起来完整”的记录；缺少的字段会保留为空，并在任务结果中标记为
部分覆盖，避免把旧数据或跨期数据伪装成最新财报。
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
import threading
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import update as sa_update

from src.storage import DataMaintenanceJob, DatabaseManager, StockMeta

logger = logging.getLogger(__name__)

# 状态机由 sync.py 暴露路由；本模块只负责财务同步实现。
_state_holder: dict | None = None
_lock_holder: threading.Lock | None = None
_set_state_fn = None
_initial_state_fn = None
_utc_now_iso_fn = None

_REPORT_PERIOD_CANDIDATES: tuple[tuple[int, int], ...] = (
    (12, 31),
    (9, 30),
    (6, 30),
    (3, 31),
)
_REPORT_PERIOD_BUFFER_DAYS = 25
_MAX_REPORT_PERIODS = 12
_PERIOD_FETCH_WORKERS = 3
_STOCK_FALLBACK_WORKERS = 8
_REQUIRED_UPDATE_FIELDS = frozenset(
    {
        "revenue_latest",
        "net_profit_latest",
        "revenue_ttm",
        "parent_net_profit_ttm",
        "deducted_net_profit_ttm",
        "debt_ratio",
    }
)


def attach_state(
    state: dict,
    lock: threading.Lock,
    set_state,
    initial_state,
    utc_now_iso,
) -> None:
    """由 sync.py 注入状态对象。"""
    global _state_holder, _lock_holder, _set_state_fn, _initial_state_fn, _utc_now_iso_fn
    _state_holder = state
    _lock_holder = lock
    _set_state_fn = set_state
    _initial_state_fn = initial_state
    _utc_now_iso_fn = utc_now_iso


def _set(**updates) -> None:
    if _set_state_fn:
        _set_state_fn(**updates)


def latest_report_period(reference: date | None = None) -> str:
    """返回当前最可能已经披露的最近报告期（YYYYMMDD）。"""
    today = reference or date.today()
    candidates: list[date] = []
    for year in (today.year, today.year - 1):
        for month, day in _REPORT_PERIOD_CANDIDATES:
            report_date = date(year, month, day)
            if report_date <= today and (today - report_date).days >= _REPORT_PERIOD_BUFFER_DAYS:
                candidates.append(report_date)
    if not candidates:
        for year in (today.year, today.year - 1):
            for month, day in _REPORT_PERIOD_CANDIDATES:
                report_date = date(year, month, day)
                if report_date <= today:
                    candidates.append(report_date)
    return max(candidates).strftime("%Y%m%d")


def _report_periods(reference: date | None = None, limit: int = _MAX_REPORT_PERIODS) -> list[str]:
    """返回从最近报告期开始的回溯列表，顺序为新到旧。"""
    today = reference or date.today()
    latest = datetime.strptime(latest_report_period(today), "%Y%m%d").date()
    return _periods_from_start(latest, limit)


def _periods_from_start(latest: date, limit: int = _MAX_REPORT_PERIODS) -> list[str]:
    """从指定的报告期开始生成回溯列表。"""
    periods: list[str] = []
    for year in range(latest.year, latest.year - 8, -1):
        for month, day in _REPORT_PERIOD_CANDIDATES:
            report_date = date(year, month, day)
            if report_date > latest:
                continue
            periods.append(report_date.isoformat())
            if len(periods) >= limit:
                return periods
    return periods


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().rstrip("%")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else None


def _normalize_code(value: Any) -> str:
    code = str(value or "").strip()
    if code.endswith(".0"):
        code = code[:-2]
    if code.isdigit() and len(code) < 6:
        code = code.zfill(6)
    return code


def _fetch_period_snapshot(period: str) -> dict[str, dict[str, Any]]:
    """读取一个报告期的全市场快照。"""
    from src.tools._financial_period_snapshot import fetch_financial_period_snapshot

    return fetch_financial_period_snapshot(period)


def _has_financial_value(row: dict[str, Any]) -> bool:
    return any(
        _safe_float(row.get(field)) is not None
        for field in ("TOTALOPERATEREVE", "PARENTNETPROFIT", "KCFJCXSYJLR", "ZCFZL")
    )


def _collect_period_rows(
    active_codes: set[str],
    periods: list[str],
) -> tuple[
    dict[str, tuple[str, dict[str, Any]]],
    dict[str, dict[str, dict[str, Any]]],
    set[str],
    list[str],
]:
    """读取多个报告期并为每只股票选出最新可用的一期。"""
    if not periods:
        return {}, {}, set(), ["没有可用的报告期候选"]

    _set(message=f"正在读取 {len(periods)} 个报告期的全市场财务快照")
    rows_by_period: dict[str, dict[str, dict[str, Any]]] = {}
    source_codes: set[str] = set()
    errors: list[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(_PERIOD_FETCH_WORKERS, len(periods))) as pool:
        future_periods = {pool.submit(_fetch_period_snapshot, period): period for period in periods}
        for future in concurrent.futures.as_completed(future_periods):
            period = future_periods[future]
            try:
                rows = future.result()
            except Exception as exc:
                errors.append(f"{period}: {type(exc).__name__}: {exc}")
                _set(message=f"报告期 {period} 读取失败，继续检查其他报告期")
                continue
            normalized_rows: dict[str, dict[str, Any]] = {}
            for raw_code, raw_row in (rows or {}).items():
                code = _normalize_code(raw_code)
                if not re.fullmatch(r"\d{6}", code) or not isinstance(raw_row, dict):
                    continue
                row = dict(raw_row)
                row["SECURITY_CODE"] = code
                normalized_rows[code] = row
            rows_by_period[period] = normalized_rows
            source_codes.update(normalized_rows)
            _set(message=f"已读取报告期 {period}，收到 {len(normalized_rows)} 条，继续归并最新一期")

    selected: dict[str, tuple[str, dict[str, Any]]] = {}
    # periods 本身是新到旧；只在尚未选中时写入，保证同一股票只取最近一期。
    for period in periods:
        for code, row in rows_by_period.get(period, {}).items():
            if code in active_codes and code not in selected and _has_financial_value(row):
                selected[code] = (period, row)
    _set(message=f"批量财务源已覆盖 {len(selected)} / {len(active_codes)} 只股票")
    return selected, rows_by_period, source_codes, errors


def _report_period_parts(period: str) -> tuple[str | None, str | None]:
    """返回某个季度对应的上年年报和上年同季度。"""
    try:
        parsed = datetime.strptime(period[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None, None
    return (
        date(parsed.year - 1, 12, 31).isoformat(),
        date(parsed.year - 1, parsed.month, parsed.day).isoformat(),
    )


def _ttm_fields(
    code: str,
    period: str,
    current: dict[str, Any],
    rows_by_period: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, float]:
    """仅在同一指标的跨期数据齐全时计算 TTM。"""
    result: dict[str, float] = {}
    current_values = {
        "revenue_ttm": _safe_float(current.get("TOTALOPERATEREVE")),
        "parent_net_profit_ttm": _safe_float(current.get("PARENTNETPROFIT")),
        "deducted_net_profit_ttm": _safe_float(current.get("KCFJCXSYJLR")),
    }
    if period.endswith("-12-31"):
        return {key: value for key, value in current_values.items() if value is not None}

    annual_period, prior_same_period = _report_period_parts(period)
    if not annual_period or not prior_same_period:
        return {}
    annual = rows_by_period.get(annual_period, {}).get(code)
    prior_same = rows_by_period.get(prior_same_period, {}).get(code)
    if not annual or not prior_same:
        return {}

    fields = (
        ("revenue_ttm", "TOTALOPERATEREVE"),
        ("parent_net_profit_ttm", "PARENTNETPROFIT"),
        ("deducted_net_profit_ttm", "KCFJCXSYJLR"),
    )
    for target, source in fields:
        current_value = current_values[target]
        annual_value = _safe_float(annual.get(source))
        prior_value = _safe_float(prior_same.get(source))
        if None not in (current_value, annual_value, prior_value):
            result[target] = float(current_value + annual_value - prior_value)
    return result


def _row_to_update(
    code: str,
    period: str,
    row: dict[str, Any],
    rows_by_period: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    """把批量源的一期报告转换为 stock_meta 更新字段。"""
    report_date = str(row.get("REPORT_DATE") or period)[:10]
    values: dict[str, Any] = {
        "report_date": report_date,
        "financial_fetched_at": datetime.now(),
    }
    latest_fields = (
        ("revenue_latest", "TOTALOPERATEREVE"),
        ("net_profit_latest", "PARENTNETPROFIT"),
        ("deducted_net_profit_latest", "KCFJCXSYJLR"),
        ("debt_ratio", "ZCFZL"),
    )
    for target, source in latest_fields:
        value = _safe_float(row.get(source))
        if value is not None and target in {"revenue_latest", "net_profit_latest", "debt_ratio"}:
            values[target] = value
    values.update(_ttm_fields(code, period, row, rows_by_period))
    return values


def _is_complete_update(fields: dict[str, Any]) -> bool:
    return _REQUIRED_UPDATE_FIELDS.issubset(fields)


def _quarter_index(report_date: str) -> int | None:
    match = re.fullmatch(r"(\d{4})-(03-31|06-30|09-30|12-31)", report_date[:10])
    if not match:
        return None
    quarter = {"03-31": 1, "06-30": 2, "09-30": 3, "12-31": 4}[match.group(2)]
    return int(match.group(1)) * 4 + quarter


def _fallback_update_from_financials(code: str) -> dict[str, Any] | None:
    """用现有逐股财务聚合源补齐批量源未覆盖的股票。"""
    from src.tools.get_financials import get_financials

    result = get_financials(code, periods=8, use_cache=False)
    items = [item for item in result.get("items") or [] if isinstance(item, dict) and item.get("report_date")]
    if not items:
        return None
    items.sort(key=lambda item: str(item.get("report_date") or ""))
    latest = items[-1]
    values: dict[str, Any] = {
        "report_date": str(latest.get("report_date"))[:10],
        "financial_fetched_at": datetime.now(),
    }
    for target, source in (
        ("revenue_latest", "revenue"),
        ("net_profit_latest", "parent_net_profit"),
        ("debt_ratio", "debt_ratio"),
        ("operating_cf_latest", "operating_cash_flow"),
    ):
        value = _safe_float(latest.get(source))
        if value is not None:
            values[target] = value
    if "net_profit_latest" not in values:
        value = _safe_float(latest.get("net_profit"))
        if value is not None:
            values["net_profit_latest"] = value

    latest_index = _quarter_index(str(latest.get("report_date"))[:10])
    last_four = items[-4:]
    indexes = [_quarter_index(str(item.get("report_date"))[:10]) for item in last_four]
    consecutive = (
        len(last_four) == 4
        and latest_index is not None
        and all(index is not None for index in indexes)
        and indexes == list(range(int(indexes[0]), int(indexes[0]) + 4))
    )
    if consecutive:
        for target, source in (
            ("revenue_ttm", "revenue"),
            ("parent_net_profit_ttm", "parent_net_profit"),
            ("deducted_net_profit_ttm", "deducted_profit"),
        ):
            amounts = [_safe_float(item.get(source)) for item in last_four]
            if all(amount is not None for amount in amounts):
                values[target] = float(sum(float(amount) for amount in amounts))
    if any(key in values for key in ("revenue_latest", "net_profit_latest", "debt_ratio", "operating_cf_latest")):
        return values
    return None


def _persist_updates(
    updates: list[tuple[str, dict[str, Any]]],
    *,
    progress_offset: int = 0,
) -> tuple[int, int, int]:
    """分批写库，返回 (updated_count, missing_count, failed_count)。"""
    if not updates:
        return 0, 0, 0
    db = DatabaseManager.get_instance()
    updated = missing = failed = 0
    batch_size = 500
    for start in range(0, len(updates), batch_size):
        batch = updates[start : start + batch_size]
        codes = [code for code, _ in batch]
        present: list[tuple[str, dict[str, Any]]] = []
        try:
            with db.get_session() as session:
                existing_codes = {
                    row[0]
                    for row in session.query(StockMeta.code)
                    .filter(StockMeta.code.in_(codes), StockMeta.status == "active")
                    .all()
                }

            present = [(code, fields) for code, fields in batch if code in existing_codes]
            missing += len(batch) - len(present)

            def _write(session) -> None:
                for code, fields in present:
                    session.execute(sa_update(StockMeta).where(StockMeta.code == code).values(**fields))

            if present:
                db._run_write_transaction(f"financial_sync[{start}:{start + len(batch)}]", _write)
                updated += len(present)
        except Exception as exc:
            failed += len(present)
            logger.warning("[FinancialSync] 批量写库失败 %s: %s", start, exc, exc_info=True)
        persisted = updated + missing + failed
        _set(
            progress=progress_offset + persisted,
            updated_count=updated,
            message=f"财务数据写入中：{persisted} / {len(updates)}",
        )
    return updated, missing, failed


def _active_codes() -> list[str]:
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        rows = (
            session.query(StockMeta.code)
            .filter(StockMeta.status == "active")
            .order_by(StockMeta.code)
            .all()
        )
    return [str(row[0]) for row in rows]


def _record_terminal_job(
    *,
    period: str | None,
    status: str,
    progress: int,
    total: int,
    message: str,
    error: str | None,
    started_at: datetime,
    finished_at: datetime,
) -> None:
    """把财报同步终态写入已有维护任务表，供重启后查询。"""
    if not period:
        return
    db = DatabaseManager.get_instance()
    try:
        with db.get_session() as session:
            job = (
                session.query(DataMaintenanceJob)
                .filter(
                    DataMaintenanceJob.dataset == "financial_reports",
                    DataMaintenanceJob.scope_key == "all",
                    DataMaintenanceJob.target_data_time == period,
                )
                .one_or_none()
            )
            if job is None:
                job = DataMaintenanceJob(
                    id=str(uuid.uuid4()),
                    dataset="financial_reports",
                    scope_key="all",
                    target_data_time=period,
                    trigger="settings",
                )
                session.add(job)
            job.status = status
            job.progress = progress
            job.total = total
            job.message = message
            job.error = error
            job.started_at = started_at
            job.finished_at = finished_at
            session.commit()
    except Exception:
        logger.warning("[FinancialSync] 保存终态审计记录失败", exc_info=True)


def run_financial_sync(
    period: str | None = None,
    active_codes: list[str] | None = None,
) -> None:
    """同步每只 active 股票最近可用的一期财务摘要。"""
    codes = sorted({_normalize_code(code) for code in (active_codes or _active_codes()) if re.fullmatch(r"\d{6}", _normalize_code(code))})
    if period:
        try:
            reference = datetime.strptime(str(period)[:10], "%Y-%m-%d").date()
        except ValueError:
            try:
                reference = datetime.strptime(str(period)[:8], "%Y%m%d").date()
            except ValueError:
                reference = date.today()
    else:
        reference = date.today()
    periods = _periods_from_start(
        reference if period else datetime.strptime(latest_report_period(reference), "%Y%m%d").date()
    )
    primary_period = periods[0] if periods else None
    run_started_at = datetime.now()
    _set(
        status="running",
        progress=0,
        total=len(codes),
        updated_count=0,
        no_data_count=0,
        failed_count=0,
        incomplete_count=0,
        unmatched_count=0,
        report_period=primary_period,
        periods_checked=0,
        started_at=_utc_now_iso_fn() if _utc_now_iso_fn else None,
        finished_at=None,
        message=f"准备为 {len(codes)} 只活跃股票寻找最近可用财报",
        error=None,
    )
    if not codes:
        finished_at = datetime.now()
        _set(
            status="failed",
            message="没有可同步的 active 股票",
            error="没有可同步的 active 股票",
            finished_at=_utc_now_iso_fn() if _utc_now_iso_fn else None,
        )
        _record_terminal_job(
            period=primary_period,
            status="failed",
            progress=0,
            total=0,
            message="没有可同步的 active 股票",
            error="没有可同步的 active 股票",
            started_at=run_started_at,
            finished_at=finished_at,
        )
        return

    try:
        active_set = set(codes)
        selected, rows_by_period, source_codes, source_errors = _collect_period_rows(active_set, periods)
        successful_periods = len(rows_by_period)
        primary_partial_codes: set[str] = set()
        for code, (selected_period, row) in list(selected.items()):
            if "SECURITY_CODE" in row:
                if not _is_complete_update(_row_to_update(code, selected_period, row, rows_by_period)):
                    primary_partial_codes.add(code)
        _set(
            progress=len(selected) - len(primary_partial_codes),
            periods_checked=successful_periods,
            unmatched_count=len(source_codes - active_set),
            incomplete_count=len(primary_partial_codes),
            message=(
                f"批量财务源已覆盖 {len(selected) - len(primary_partial_codes)} / {len(codes)} 只完整股票，"
                f"另有 {len(primary_partial_codes)} 只需要补源"
            ),
        )

        fallback_failed: dict[str, str] = {}
        no_data_codes: set[str] = set()
        incomplete_codes: set[str] = set()
        fallback_codes = sorted((active_set - set(selected)) | primary_partial_codes)
        if fallback_codes:
            _set(message=f"批量源未完整覆盖 {len(fallback_codes)} 只，正在逐股尝试现有财务源")
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(_STOCK_FALLBACK_WORKERS, len(fallback_codes))
            ) as pool:
                future_codes = {
                    pool.submit(_fallback_update_from_financials, code): code for code in fallback_codes
                }
                for future in concurrent.futures.as_completed(future_codes):
                    code = future_codes[future]
                    try:
                        fallback = future.result()
                    except Exception as exc:
                        fallback_failed[code] = f"{type(exc).__name__}: {exc}"
                        fallback = None
                    if fallback and _is_complete_update(fallback):
                        selected[code] = (str(fallback.get("report_date") or ""), fallback)
                    elif fallback:
                        selected.pop(code, None)
                        incomplete_codes.add(code)
                    elif code not in fallback_failed:
                        selected.pop(code, None)
                        if code in primary_partial_codes:
                            incomplete_codes.add(code)
                        else:
                            no_data_codes.add(code)
                    processed = (
                        len(selected)
                        + len(no_data_codes)
                        + len(fallback_failed)
                        + len(incomplete_codes)
                    )
                    _set(
                        progress=processed,
                        no_data_count=len(no_data_codes),
                        failed_count=len(fallback_failed),
                        incomplete_count=len(incomplete_codes),
                        message=f"逐股补齐中：已处理 {processed} / {len(codes)} 只",
                    )

        updates: list[tuple[str, dict[str, Any]]] = []
        for code in codes:
            selected_row = selected.get(code)
            if not selected_row:
                continue
            selected_period, row = selected_row
            if "SECURITY_CODE" in row:
                fields = _row_to_update(code, selected_period, row, rows_by_period)
            else:
                fields = dict(row)
            if not _is_complete_update(fields):
                incomplete_codes.add(code)
                continue
            updates.append((code, fields))

        completed_without_write = len(no_data_codes) + len(fallback_failed) + len(incomplete_codes)
        _set(
            progress=completed_without_write,
            updated_count=0,
            no_data_count=len(no_data_codes),
            failed_count=len(fallback_failed),
            incomplete_count=len(incomplete_codes),
            unmatched_count=len(source_codes - active_set),
            message=f"已找到 {len(updates)} 只股票的财报，正在写入本地数据",
        )
        updated, missing, persist_failed = _persist_updates(
            updates,
            progress_offset=completed_without_write,
        )
        failed_count = len(fallback_failed) + persist_failed
        processed = updated + len(no_data_codes) + failed_count + len(incomplete_codes) + missing
        issues = bool(no_data_codes or failed_count or missing or incomplete_codes or source_errors)
        if updated == 0:
            status = "failed"
        elif issues:
            status = "partial"
        else:
            status = "success"
        warnings: list[str] = []
        if source_errors:
            warnings.append(f"{len(source_errors)} 个报告期读取失败")
        if incomplete_codes:
            warnings.append(f"{len(incomplete_codes)} 只股票没有拿到完整核心字段，本轮未覆盖")
        if missing:
            warnings.append(f"{missing} 只股票写入时已不再是 active")
        warning_text = "；".join(warnings)
        message = (
            f"最新财报同步完成：已更新 {updated} / {len(codes)} 只；"
            f"无可用财报 {len(no_data_codes)} 只；失败 {failed_count} 只；"
            f"报告期检查 {successful_periods} 个，首选报告期 {primary_period or '未知'}"
        )
        if warning_text:
            message += f"；{warning_text}"
        _set(
            status=status,
            progress=min(processed, len(codes)),
            total=len(codes),
            updated_count=updated,
            no_data_count=len(no_data_codes),
            failed_count=failed_count,
            incomplete_count=len(incomplete_codes),
            unmatched_count=len(source_codes - active_set),
            periods_checked=successful_periods,
            report_period=primary_period,
            finished_at=_utc_now_iso_fn() if _utc_now_iso_fn else None,
            message=message,
            error=("；".join(source_errors + list(fallback_failed.values())))[:300] if failed_count else None,
        )
        _record_terminal_job(
            period=primary_period,
            status=status,
            progress=min(processed, len(codes)),
            total=len(codes),
            message=message,
            error=("；".join(source_errors + list(fallback_failed.values())))[:300] if failed_count else None,
            started_at=run_started_at,
            finished_at=datetime.now(),
        )
        logger.info(
            "[FinancialSync] 完成: total=%d updated=%d no_data=%d failed=%d incomplete=%d periods=%d",
            len(codes),
            updated,
            len(no_data_codes),
            failed_count,
            len(incomplete_codes),
            successful_periods,
        )
    except Exception as exc:
        finished_at = datetime.now()
        _set(
            status="failed",
            failed_count=len(codes),
            finished_at=_utc_now_iso_fn() if _utc_now_iso_fn else None,
            message="最新财报同步未完成",
            error=str(exc)[:300],
        )
        _record_terminal_job(
            period=primary_period,
            status="failed",
            progress=0,
            total=len(codes),
            message="最新财报同步未完成",
            error=str(exc)[:300],
            started_at=run_started_at,
            finished_at=finished_at,
        )
        logger.error("[FinancialSync] 同步失败: %s", exc, exc_info=True)
