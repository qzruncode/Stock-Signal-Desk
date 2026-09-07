# -*- coding: utf-8 -*-
"""Re-exports — backward compatibility shim.

All symbols were moved to ``_symbol``, ``_content``, ``_analysis``.
Keep this file until all internal importers are migrated.
"""

from market_data_service.providers.sentiment_support._symbol import (  # noqa: F401
    _to_em_symbol,
    _normalize_symbol,
    _safe_float,
    _safe_amount,
    _safe_str,
    _safe_pct,
    _safe_int_like,
    _clamp,
    _pick_col,
    _row_pick,
    _parse_date,
    _latest_quarter_dates,
    _parse_chinese_share_amount,
    _to_ts_code,
    _to_top_holder_symbol,
)
from market_data_service.providers.sentiment_support._content import (  # noqa: F401
    _rss_stock_keywords,
    _rss_stock_industry_keywords,
    _normalize_rss_text,
    _rss_entry_matches_keywords,
    _rss_entry_is_recent,
    _rss_stock_feed_specs,
    _rsshub_is_slow_spec,
    _rsshub_enough_entries,
    _fetch_rsshub_entries,
    _rss_entry_text,
    _rss_entry_summary,
    _rss_entry_date,
    _dedupe_rss_entries,
)
from market_data_service.providers.sentiment_support._analysis import (  # noqa: F401
    _latest_content_time,
    _content_is_stale,
    _resolve_post_publish_time,
)
