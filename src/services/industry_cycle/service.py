# -*- coding: utf-8 -*-
"""IndustryCycleService orchestrates evidence collection, LLM analysis, prompt building and report assembly."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Callable, Optional

from .cache import (
    _cache_get,
    _current_report_as_of_date,
    _report_cache_get,
    uuid4_hex,
)
from .data_quality import (
    _assess_evidence_gate,
    _build_data_quality,
    _build_evidence_insufficient_report,
)
from .industry_data import (
    _build_stock_focus_snapshot,
    _build_trading_signals,
    _build_trading_snapshot,
    _fallback_sector_item_from_flow,
    _fetch_lhb_snapshot,
    _fetch_peer_snapshot,
    _fetch_stock_flow_snapshot,
    _fetch_ths_industry_summary,
    _find_industry_board,
    _find_sector_flow,
)
from .llm_parse import (
    _parse_llm_json_payload,
    _pick_theme_detail,
)
from .normalization import (
    _as_dict,
    _as_list,
    _first_dict,
    _list_of_dicts,
    _normalize_symbol,
    _normalize_text,
    _prune_none,
    _safe_float,
    _safe_int,
    _summarize_text_items,
)
from .report import (
    _build_streaming_industry_cycle_draft,
    _is_usable_report_payload,
)

logger = logging.getLogger(__name__)

_MAINLINE_CRITERION_IDS = (
    "market_mainline_membership",
    "market_attention",
    "substantive_business_link",
    "actual_business_benefit",
    "structural_drivers",
    "medium_term_catalysts",
)
_INDUSTRY_BETA_CRITERION_IDS = (
    "industry_upcycle",
    "three_year_space",
    "competition_quality",
    "structural_drivers",
)


def _validated_detector(
    value: Any,
    expected_ids: tuple[str, ...],
    *,
    label: str,
) -> dict[str, Any]:
    detector = _as_dict(value)
    items = _list_of_dicts(detector.get("checklist"))
    by_id = {
        _normalize_text(item.get("criterion_id")): item for item in items if _normalize_text(item.get("criterion_id"))
    }
    missing = [criterion_id for criterion_id in expected_ids if criterion_id not in by_id]
    extra = [criterion_id for criterion_id in by_id if criterion_id not in expected_ids]
    ordered = [by_id[criterion_id] for criterion_id in expected_ids if criterion_id in by_id]
    if missing or extra or len(ordered) != len(expected_ids):
        return {
            "passed": False,
            "conclusion": "",
            "failed_reason": (f"{label}结构不完整；missing={missing} extra={extra}。" "未使用关键词或固定分数补判。"),
            "checklist": ordered,
        }
    return {
        "passed": bool(detector.get("passed")) and all(bool(item.get("passed")) for item in ordered),
        "conclusion": _normalize_text(detector.get("conclusion")),
        "failed_reason": detector.get("failed_reason"),
        "checklist": ordered,
    }


def _persist_cache(symbol: str, payload: dict[str, Any]) -> None:
    """Route cache writes through the shell module so monkeypatches in tests still take effect."""
    from src.services import industry_cycle_service as _shell

    _shell._cache_put(symbol, payload)


def _persist_report_cache(symbol: str, payload: dict[str, Any]) -> None:
    from src.services import industry_cycle_service as _shell

    _shell._report_cache_put(symbol, payload)


from ._service_methods1 import _IndustryCycleServiceMethods1
from ._service_methods2 import _IndustryCycleServiceMethods2
from ._service_methods3 import _IndustryCycleServiceMethods3
class IndustryCycleService(_IndustryCycleServiceMethods1, _IndustryCycleServiceMethods2, _IndustryCycleServiceMethods3):
        REPORT_TYPE = "industry_cycle_report"


def _bind_mixin_member(_member):
    import functools
    import types

    if isinstance(_member, staticmethod):
        return staticmethod(_bind_mixin_member(_member.__func__))
    if isinstance(_member, classmethod):
        return classmethod(_bind_mixin_member(_member.__func__))
    if not isinstance(_member, types.FunctionType):
        return _member
    _bound = types.FunctionType(_member.__code__, globals(), _member.__name__, _member.__defaults__, _member.__closure__)
    _bound.__kwdefaults__ = _member.__kwdefaults__
    functools.update_wrapper(_bound, _member)
    return _bound


for _mixin in (_IndustryCycleServiceMethods1, _IndustryCycleServiceMethods2, _IndustryCycleServiceMethods3):
    for _name, _member in _mixin.__dict__.items():
        if _name not in {"__dict__", "__weakref__"}:
            setattr(IndustryCycleService, _name, _bind_mixin_member(_member))
