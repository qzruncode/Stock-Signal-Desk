"""Function group 1 extracted from data_provider/fundamental_adapter.py."""

from __future__ import annotations

from data_provider.fundamental_adapter import (
    logging,
    re,
    datetime,
    timedelta,
    Any,
    Dict,
    List,
    Optional,
    Tuple,
    pd,
    logger,
    _DIVIDEND_KEYWORD_MAP,
    AkshareFundamentalAdapter,
 )

__all__ = ['_safe_float', '_safe_str', '_safe_datetime', '_normalize_code', '_market_prefix', '_to_em_prefixed_symbol', '_recent_report_periods', '_pick_by_keywords', '_parse_dividend_plan_to_per_share', '_extract_cash_dividend_per_share', '_filter_rows_by_code', '_normalize_report_date', '_build_dividend_payload', '_extract_latest_row']

def _safe_float(value: Any) -> Optional[float]:
    """Best-effort float conversion."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    s = str(value).strip().replace(",", "").replace("%", "")
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None

def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()

def _safe_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        parsed = pd.to_datetime(value)
    except (ValueError, TypeError):
        logger.warning("[Fundamental] _safe_datetime parse failed for value type=%s", type(value).__name__)
        return None
    if pd.isna(parsed):
        return None
    try:
        return parsed.to_pydatetime()
    except (ValueError, TypeError):
        logger.warning("[Fundamental] _safe_datetime to_pydatetime failed for value=%s", value)
        return None

def _normalize_code(raw: Any) -> str:
    s = _safe_str(raw).upper()
    if "." in s:
        s = s.split(".", 1)[0]
    s = re.sub(r"^(SH|SZ|BJ)", "", s)
    return s

def _market_prefix(code: str) -> str:
    """A 股代码 → 东财市场前缀 sh/sz/bj。"""
    c = _normalize_code(code)
    if c.startswith(("6", "5", "90")):
        return "sh"
    if c.startswith(("8", "4", "9")):
        return "bj"
    return "sz"

def _to_em_prefixed_symbol(code: str) -> str:
    """A 股代码 → 东财带前缀 symbol，如 sh688686 / sz000001。"""
    return f"{_market_prefix(code)}{_normalize_code(code)}"

def _recent_report_periods(limit: int = 4) -> List[str]:
    """最近的 A 股报告期（YYYYMMDD），按季倒序。

    用于 stock_yjyg_em/yjbb_em/yjkb_em(date=)、stock_gdfx_top_10_em(date=) 等
    需要指定报告期的接口。以当前月份推断已披露的最近报告期。
    """
    now = datetime.now()
    # 报告期月份：3/4 月底(Q1)、6 月底(Q2)、9 月底(Q3)、12 月底(Q4)
    quarters = []
    y, m = now.year, now.month
    for _ in range(limit * 2):  # 多取几期以防披露窗口
        if m >= 10:
            period = (datetime(y, 9, 30),)
        elif m >= 7:
            period = (datetime(y, 6, 30),)
        elif m >= 4:
            period = (datetime(y, 3, 31),)
        else:
            period = (datetime(y - 1, 12, 31),)
        quarters.append(period[0])
        # 回退一季度
        if m <= 3:
            m = 12
            y -= 1
        else:
            m -= 3
    # 去重并保留最近 limit 期
    seen = set()
    ordered: List[datetime] = []
    for d in quarters:
        if d not in seen:
            seen.add(d)
            ordered.append(d)
    ordered.sort(reverse=True)
    return [d.strftime("%Y%m%d") for d in ordered[:limit]]

def _pick_by_keywords(row: pd.Series, keywords: List[str]) -> Optional[Any]:
    """
    Return first non-empty row value whose column name contains any keyword.
    """
    for col in row.index:
        col_s = str(col)
        if any(k in col_s for k in keywords):
            val = row.get(col)
            if val is not None and str(val).strip() not in ("", "-", "nan", "None"):
                return val
    return None

def _parse_dividend_plan_to_per_share(plan_text: str) -> Optional[float]:
    """Parse per-share cash dividend from Chinese plan text."""
    text = _safe_str(plan_text)
    if not text:
        return None

    for pattern in (
        r"(?:每)?\s*10\s*股?\s*派(?:发)?\s*([0-9]+(?:\.[0-9]+)?)\s*元",
        r"10\s*派\s*([0-9]+(?:\.[0-9]+)?)\s*元",
    ):
        match = re.search(pattern, text)
        if match:
            parsed = _safe_float(match.group(1))
            if parsed is not None and parsed > 0:
                return parsed / 10.0

    match_per_share = re.search(r"每\s*股\s*派(?:发)?\s*([0-9]+(?:\.[0-9]+)?)\s*元", text)
    if match_per_share:
        parsed = _safe_float(match_per_share.group(1))
        if parsed is not None and parsed > 0:
            return parsed
    return None

def _extract_cash_dividend_per_share(row: pd.Series) -> Optional[float]:
    """Extract pre-tax cash dividend per share from a row."""
    plan_text = _safe_str(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["plan_text"]))
    # Keep pre-tax semantics; skip explicit after-tax plans unless pre-tax marker exists.
    if "税后" in plan_text and "税前" not in plan_text and "含税" not in plan_text:
        return None

    direct = _safe_float(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["per_share"]))
    if direct is not None and direct > 0:
        return direct
    return _parse_dividend_plan_to_per_share(plan_text)

def _filter_rows_by_code(df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    code_cols = [
        c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码", "symbol", "ts_code"))
    ]
    if not code_cols:
        return df

    target = _normalize_code(stock_code)
    for col in code_cols:
        try:
            series = df[col].astype(str).map(_normalize_code)
            filtered = df[series == target]
            if not filtered.empty:
                return filtered
        except Exception:
            continue
    return pd.DataFrame()

def _normalize_report_date(value: Any) -> Optional[str]:
    parsed = _safe_datetime(value)
    return parsed.date().isoformat() if parsed else None

def _build_dividend_payload(
    dividend_df: pd.DataFrame,
    stock_code: str,
    max_events: int = 5,
) -> Dict[str, Any]:
    work_df = _filter_rows_by_code(dividend_df, stock_code)
    if work_df.empty:
        return {}

    now_date = datetime.now().date()
    ttm_start_date = now_date - timedelta(days=365)
    dedupe_keys = set()
    events: List[Dict[str, Any]] = []

    for _, row in work_df.iterrows():
        if not isinstance(row, pd.Series):
            continue
        ex_dt = _safe_datetime(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["ex_dividend_date"]))
        record_dt = _safe_datetime(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["record_date"]))
        announce_dt = _safe_datetime(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["announce_date"]))
        event_dt = ex_dt or record_dt or announce_dt
        if event_dt is None:
            continue
        event_date = event_dt.date()
        if event_date > now_date:
            continue

        per_share = _extract_cash_dividend_per_share(row)
        if per_share is None or per_share <= 0:
            continue

        dedupe_key = (event_date.isoformat(), round(per_share, 6))
        if dedupe_key in dedupe_keys:
            continue
        dedupe_keys.add(dedupe_key)

        events.append(
            {
                "event_date": event_date.isoformat(),
                "ex_dividend_date": ex_dt.date().isoformat() if ex_dt else None,
                "record_date": record_dt.date().isoformat() if record_dt else None,
                "announcement_date": announce_dt.date().isoformat() if announce_dt else None,
                "cash_dividend_per_share": round(per_share, 6),
                "is_pre_tax": True,
            }
        )

    if not events:
        return {}

    events.sort(key=lambda item: item.get("event_date") or "", reverse=True)
    ttm_events: List[Dict[str, Any]] = []
    for item in events:
        event_dt = _safe_datetime(item.get("event_date"))
        if event_dt is None:
            continue
        event_date = event_dt.date()
        if ttm_start_date <= event_date <= now_date:
            ttm_events.append(item)

    return {
        "events": events[: max(1, max_events)],
        "ttm_event_count": len(ttm_events),
        "ttm_cash_dividend_per_share": (
            round(sum(float(item.get("cash_dividend_per_share") or 0.0) for item in ttm_events), 6)
            if ttm_events
            else None
        ),
        "coverage": "cash_dividend_pre_tax",
        "as_of": now_date.isoformat(),
    }

def _extract_latest_row(df: pd.DataFrame, stock_code: str) -> Optional[pd.Series]:
    """
    Select the most relevant row for the given stock.
    """
    if df is None or df.empty:
        return None

    code_cols = [
        c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码", "ts_code", "symbol"))
    ]
    target = _normalize_code(stock_code)
    if code_cols:
        for col in code_cols:
            try:
                series = df[col].astype(str).map(_normalize_code)
                matched = df[series == target]
                if not matched.empty:
                    return matched.iloc[0]
            except Exception:
                continue
        return None

    # Fallback: use latest row
    return df.iloc[0]
