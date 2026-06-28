# -*- coding: utf-8 -*-
"""Industry cycle analysis service for stock detail page.

Module split into ``src.services.industry_cycle`` subpackage. This file is a thin
shell that re-exports the previous public and private surface so existing
imports (``from src.services.industry_cycle_service import IndustryCycleService``
or test-only access to ``_cache_put`` / ``_parse_llm_json_payload`` etc.)
continue to work unchanged.
"""

from __future__ import annotations

from src.services.industry_cycle.cache import (
    INDUSTRY_CYCLE_CACHE_KEY,
    INDUSTRY_CYCLE_REPORT_CACHE_KEY,
    INDUSTRY_CYCLE_STOCK_FLOW_CACHE_KEY,
    _cache_get,
    _cache_key,
    _cache_put,
    _current_report_as_of_date,
    _report_cache_get,
    _report_cache_key,
    _report_cache_put,
    _stock_flow_cache_get,
    _stock_flow_cache_key,
    _stock_flow_cache_put,
    uuid4_hex,
)
from src.services.industry_cycle.data_quality import (
    _assess_evidence_gate,
    _build_data_quality,
    _build_evidence_insufficient_report,
)
from src.services.industry_cycle.industry_data import (
    _build_stock_focus_snapshot,
    _build_trading_signals,
    _build_trading_snapshot,
    _fallback_sector_item_from_flow,
    _fetch_lhb_snapshot,
    _fetch_peer_snapshot,
    _fetch_stock_flow_snapshot,
    _fetch_ths_industry_summary,
    _find_best_name_match,
    _find_industry_board,
    _find_sector_flow,
    _industry_name_aliases,
)
from src.services.industry_cycle.llm_parse import (
    _extract_json_object_from_text,
    _extract_partial_json_number_field,
    _extract_partial_json_string_field,
    _parse_llm_json_payload,
    _pick_theme_detail,
    _strip_markdown_code_fences,
)
from src.services.industry_cycle.normalization import (
    _CATALYST_KEYWORDS,
    _DRIVER_KEYWORDS,
    _FADING_STAGE_KEYWORDS,
    _PRICE_WAR_KEYWORDS,
    _THREE_YEAR_SPACE_KEYWORDS,
    _as_dict,
    _as_list,
    _compact_text,
    _contains_any,
    _extract_competition_clues,
    _extract_driver_clues,
    _first_dict,
    _list_of_dicts,
    _normalize_symbol,
    _normalize_text,
    _prune_none,
    _safe_float,
    _safe_int,
    _summarize_text_items,
)
from src.services.industry_cycle.report import (
    _build_driver_signals,
    _build_streaming_industry_cycle_draft,
    _describe_driver_signals,
    _find_detector_item,
    _format_pct_text,
    _has_strong_driver_signals,
    _infer_beneficiary_level,
    _infer_cycle_phase,
    _is_usable_report_payload,
    _merge_detector_with_fallback,
    _pick_failed_reason,
    _report_has_conflicting_conclusion,
    _report_has_incomplete_detectors,
)
from src.services.industry_cycle.service import IndustryCycleService

__all__ = [
    "IndustryCycleService",
    "INDUSTRY_CYCLE_CACHE_KEY",
    "INDUSTRY_CYCLE_REPORT_CACHE_KEY",
    "INDUSTRY_CYCLE_STOCK_FLOW_CACHE_KEY",
]
