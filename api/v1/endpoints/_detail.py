# -*- coding: utf-8 -*-
"""History detail helpers — price extraction, response model assembly, conversation payload."""

from __future__ import annotations

from typing import Any, Dict, Optional

from api.v1.schemas.history import (
    AnalysisReport,
    ReportMeta,
    ReportSummary,
    ReportStrategy,
    ReportDetails,
)
from src.report_language import (
    get_sentiment_label,
    get_localized_stock_name,
    localize_operation_advice,
    localize_trend_prediction,
    normalize_report_language,
)
from src.utils.data_processing import (
    normalize_model_used,
    extract_fundamental_detail_fields,
    extract_board_detail_fields,
)
from src.storage import DatabaseManager


def _extract_conversation_payload(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    raw_result = result.get("raw_result")
    conversation = raw_result.get("conversation") if isinstance(raw_result, dict) else None

    if isinstance(conversation, dict):
        return conversation

    if result.get("report_type") != "conversation":
        return None

    response_text = result.get("news_content") or result.get("analysis_summary") or ""
    if not response_text:
        return None

    return {
        "prompt": "",
        "response": response_text,
        "model_used": result.get("model_used"),
        "stock_name": result.get("stock_name"),
    }


def _extract_current_price(result: dict) -> tuple:
    """Extract current_price and change_pct from context_snapshot."""
    current_price = None
    change_pct = None
    context_snapshot = result.get("context_snapshot")
    if context_snapshot and isinstance(context_snapshot, dict):
        enhanced_context = context_snapshot.get("enhanced_context") or {}
        realtime = enhanced_context.get("realtime") or {}
        current_price = realtime.get("price")
        change_pct = realtime.get("change_pct")

        realtime_quote_raw = context_snapshot.get("realtime_quote_raw")
        if not isinstance(realtime_quote_raw, dict):
            realtime_quote_raw = {}
        if current_price is None:
            current_price = realtime_quote_raw.get("price")
        if change_pct is None:
            change_pct = realtime_quote_raw.get("change_pct")
        if change_pct is None:
            change_pct = realtime_quote_raw.get("pct_chg")
    return current_price, change_pct


def _build_analysis_report(result: dict, db_manager: DatabaseManager) -> AnalysisReport:
    current_price, change_pct = _extract_current_price(result)

    raw_result = result.get("raw_result")
    if not isinstance(raw_result, dict):
        raw_result = {}

    context_snapshot = result.get("context_snapshot")
    if not isinstance(context_snapshot, dict):
        context_snapshot = {}

    report_language = normalize_report_language(
        result.get("report_language") or raw_result.get("report_language") or context_snapshot.get("report_language")
    )

    stock_name = get_localized_stock_name(
        result.get("stock_name"),
        result.get("stock_code", ""),
        report_language,
    )

    meta = ReportMeta(
        id=result.get("id"),
        query_id=result.get("query_id", ""),
        stock_code=result.get("stock_code", ""),
        stock_name=stock_name,
        report_type=result.get("report_type"),
        report_language=report_language,
        created_at=result.get("created_at"),
        current_price=current_price,
        change_pct=change_pct,
        model_used=normalize_model_used(result.get("model_used")),
    )

    summary = ReportSummary(
        analysis_summary=result.get("analysis_summary"),
        operation_advice=localize_operation_advice(
            result.get("operation_advice"),
            report_language,
        ),
        trend_prediction=localize_trend_prediction(
            result.get("trend_prediction"),
            report_language,
        ),
        sentiment_score=result.get("sentiment_score"),
        sentiment_label=(
            result.get("sentiment_label")
            or get_sentiment_label(
                result.get("sentiment_score"),
                report_language,
            )
        ),
    )

    strategy = ReportStrategy(
        ideal_buy=result.get("ideal_buy"),
        secondary_buy=result.get("secondary_buy"),
        stop_loss=result.get("stop_loss"),
        take_profit=result.get("take_profit"),
    )

    fallback_fundamental = db_manager.get_latest_fundamental_snapshot(
        query_id=result.get("query_id", ""),
        code=result.get("stock_code", ""),
    )
    extracted_fundamental = extract_fundamental_detail_fields(
        context_snapshot=result.get("context_snapshot"),
        fallback_fundamental_payload=fallback_fundamental,
    )
    extracted_boards = extract_board_detail_fields(
        context_snapshot=result.get("context_snapshot"),
        fallback_fundamental_payload=fallback_fundamental,
    )

    details = ReportDetails(
        news_content=result.get("news_content"),
        raw_result=result.get("raw_result"),
        context_snapshot=result.get("context_snapshot"),
        financial_report=extracted_fundamental.get("financial_report"),
        dividend_metrics=extracted_fundamental.get("dividend_metrics"),
        belong_boards=extracted_boards.get("belong_boards"),
        sector_rankings=extracted_boards.get("sector_rankings"),
    )

    return AnalysisReport(
        meta=meta,
        summary=summary,
        strategy=strategy,
        details=details,
        conversation=_extract_conversation_payload(result),
    )
