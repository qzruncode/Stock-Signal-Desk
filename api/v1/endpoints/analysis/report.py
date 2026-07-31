# -*- coding: utf-8 -*-
"""Analysis report endpoints and helpers — push to WeChat and report builders."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Any, Dict

from fastapi import APIRouter, HTTPException

from api.v1.endpoints.analysis import router
from api.v1.schemas.analysis import AnalysisResultResponse
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.history import (
    AnalysisReport,
    ReportMeta,
    ReportSummary,
    ReportStrategy,
    ReportDetails,
)
from src.config import Config
from src.report_language import get_localized_stock_name, normalize_report_language
from src.services.task_queue import get_task_queue, TaskStatus as TaskStatusEnum
from src.utils.data_processing import (
    normalize_model_used,
    parse_json_field,
    extract_fundamental_detail_fields,
    extract_board_detail_fields,
)

logger = logging.getLogger(__name__)


@router.post(
    "/status/{task_id}/push",
    responses={
        200: {"description": "推送成功"},
        404: {"description": "任务不存在", "model": ErrorResponse},
        400: {"description": "任务未完成或无可推送内容", "model": ErrorResponse},
        500: {"description": "推送失败", "model": ErrorResponse},
    },
    summary="手动推送分析结果到企业微信",
    description="将已完成的分析任务结果推送到企业微信",
)
def push_analysis_to_wechat(task_id: str):
    """手动推送单股分析结果到企业微信。"""
    from src.notification import get_notification_service

    task_queue = get_task_queue()
    task = task_queue.get_task(task_id)

    stock_code = task_id
    stock_name: Optional[str] = None
    report_data: Optional[dict] = None

    if task:
        stock_code = task.stock_code or stock_code
        stock_name = task.stock_name
        if task.status != TaskStatusEnum.COMPLETED:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "task_not_completed",
                    "message": f"任务尚未完成，当前状态: {task.status.value}",
                },
            )
        if isinstance(task.result, dict):
            report_data = task.result
    else:
        try:
            from src.storage import DatabaseManager

            db = DatabaseManager.get_instance()
            records = db.get_analysis_history(query_id=task_id, limit=1)
            if records:
                record = records[0]
                stock_code = record.code
                raw_result = parse_json_field(record.raw_result)
                report_language = normalize_report_language(
                    (raw_result or {}).get("report_language") if isinstance(raw_result, dict) else None
                )
                stock_name = get_localized_stock_name(record.name, record.code, report_language)

                is_conversation = getattr(record, "report_type", None) == "conversation"
                if is_conversation:
                    conversation = (raw_result or {}).get("conversation") if isinstance(raw_result, dict) else {}
                    report_data = {
                        "stock_code": record.code,
                        "stock_name": stock_name,
                        "conversation": conversation,
                    }
                else:
                    report_data = {
                        "stock_code": record.code,
                        "stock_name": stock_name,
                        "report": {
                            "meta": {
                                "stock_code": record.code,
                                "stock_name": stock_name,
                                "report_language": report_language,
                                "model_used": normalize_model_used(
                                    (raw_result or {}).get("model_used") if isinstance(raw_result, dict) else None
                                ),
                                "created_at": record.created_at.isoformat() if record.created_at else None,
                            },
                            "summary": {
                                "sentiment_score": record.sentiment_score,
                                "operation_advice": record.operation_advice,
                                "trend_prediction": record.trend_prediction,
                                "analysis_summary": record.analysis_summary,
                            },
                            "strategy": {
                                "ideal_buy": getattr(record, "ideal_buy", None),
                                "secondary_buy": getattr(record, "secondary_buy", None),
                                "stop_loss": getattr(record, "stop_loss", None),
                                "take_profit": getattr(record, "take_profit", None),
                            },
                        },
                    }
        except Exception as e:
            logger.error(f"查询任务记录失败: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"error": "internal_error", "message": f"查询失败: {str(e)}"},
            )

    if not report_data:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"任务 {task_id} 不存在"},
        )

    content = _build_single_stock_push_content(report_data, stock_code, stock_name)
    if not content:
        raise HTTPException(
            status_code=400,
            detail={"error": "no_content", "message": "无可推送的分析内容"},
        )

    service = get_notification_service()
    success = service.send(content)
    if not success:
        raise HTTPException(
            status_code=500,
            detail={"error": "push_failed", "message": "企业微信推送失败"},
        )

    return {"message": "推送成功", "stock_code": stock_code, "stock_name": stock_name}


def _build_single_stock_push_content(
    report_data: dict,
    stock_code: str,
    stock_name: Optional[str],
) -> str:
    """Build WeChat push content from task result data."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    display_name = stock_name or stock_code

    conversation = report_data.get("conversation")
    if conversation:
        response_text = (conversation or {}).get("response", "")
        model = (conversation or {}).get("model_used", "")
        lines = [
            f"## {display_name} ({stock_code})",
            "",
            f"> 分析时间: {now}",
            "",
        ]
        if response_text:
            max_len = 3500
            text = response_text[:max_len] + ("..." if len(response_text) > max_len else "")
            lines.append(text)
            lines.append("")
        if model:
            lines.append(f"*模型: {model}*")
        return "\n".join(lines)

    report = report_data.get("report", {})
    meta = report.get("meta", {})
    summary = report.get("summary", {})
    strategy = report.get("strategy", {})

    sentiment_score = summary.get("sentiment_score")
    operation_advice = summary.get("operation_advice") or ""
    trend_prediction = summary.get("trend_prediction") or ""
    analysis_summary = summary.get("analysis_summary") or ""

    score_str = f"**{sentiment_score}**" if sentiment_score is not None else "N/A"

    lines = [
        f"## {display_name} ({stock_code})",
        "",
        f"> {now} | 评分: {score_str} | {trend_prediction}",
        "",
    ]

    if operation_advice:
        lines.append(f"**操作建议**: {operation_advice}")
        lines.append("")

    if analysis_summary:
        lines.append(f"**分析摘要**: {analysis_summary[:200]}")
        lines.append("")

    has_strategy = False
    strategy_lines = []
    if strategy.get("ideal_buy"):
        strategy_lines.append(f"| 理想买入 | {strategy['ideal_buy']} |")
        has_strategy = True
    if strategy.get("stop_loss"):
        strategy_lines.append(f"| 止损 | {strategy['stop_loss']} |")
        has_strategy = True
    if strategy.get("take_profit"):
        strategy_lines.append(f"| 止盈 | {strategy['take_profit']} |")
        has_strategy = True

    if has_strategy:
        lines.append("### 关键点位")
        lines.append("")
        lines.append("| 类型 | 价格 |")
        lines.append("|------|------|")
        lines.extend(strategy_lines)
        lines.append("")

    model = meta.get("model_used")
    if model:
        lines.append(f"*模型: {model}*")
    lines.append(f"*推送时间: {now}*")

    return "\n".join(lines)


def _build_conversation_report(
    conversation: Dict[str, Any],
    query_id: str,
    stock_code: str,
    stock_name: Optional[str] = None,
) -> AnalysisReport:
    """Build an AnalysisReport from conversation-format result."""
    now = datetime.now().isoformat()
    response_text = conversation.get("response", "")

    meta = ReportMeta(
        query_id=query_id,
        stock_code=stock_code,
        stock_name=conversation.get("stock_name", stock_name),
        report_type="conversation",
        report_language="zh",
        created_at=now,
        model_used=conversation.get("model_used"),
    )

    lines = [l for l in response_text.strip().split("\n") if l.strip()]
    brief = "\n".join(lines[:3]) if lines else ""

    summary = ReportSummary(
        analysis_summary=brief[:500] if brief else None,
        operation_advice=None,
        trend_prediction=None,
        sentiment_score=None,
        sentiment_label=None,
    )

    details = ReportDetails(
        news_content=response_text,
        raw_result={"conversation": conversation},
    )

    return AnalysisReport(
        meta=meta,
        summary=summary,
        strategy=None,
        details=details,
        conversation=conversation,
    )


def _load_sync_fundamental_sources(
    query_id: str,
    stock_code: str,
) -> tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """Load context_snapshot and fallback fundamental snapshot for sync analyze response."""
    try:
        from src.storage import DatabaseManager

        db = DatabaseManager.get_instance()
        records = db.get_analysis_history(query_id=query_id, code=stock_code, limit=1)
        context_snapshot = None
        if records:
            context_snapshot = parse_json_field(getattr(records[0], "context_snapshot", None))

        fallback_fundamental = db.get_latest_fundamental_snapshot(
            query_id=query_id,
            code=stock_code,
        )
        return context_snapshot, fallback_fundamental
    except Exception as e:
        logger.debug(
            "load sync fundamental sources failed (fail-open): query_id=%s stock_code=%s err=%s",
            query_id,
            stock_code,
            e,
        )
        return None, None


def _stringify_report_strategy_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _build_analysis_report(
    report_data: Dict[str, Any],
    query_id: str,
    stock_code: str,
    stock_name: Optional[str] = None,
    context_snapshot: Optional[Any] = None,
    fallback_fundamental_payload: Optional[Dict[str, Any]] = None,
) -> AnalysisReport:
    """构建符合 API 规范的分析报告。"""
    meta_data = report_data.get("meta", {})
    summary_data = report_data.get("summary", {})
    strategy_data = report_data.get("strategy", {})
    details_data = report_data.get("details", {})
    report_language = normalize_report_language(
        meta_data.get("report_language")
        or (context_snapshot or {}).get("report_language")
        or getattr(Config.get_instance(), "report_language", "zh")
    )
    localized_stock_name = get_localized_stock_name(
        meta_data.get("stock_name", stock_name),
        meta_data.get("stock_code", stock_code),
        report_language,
    )

    meta = ReportMeta(
        query_id=meta_data.get("query_id", query_id),
        stock_code=meta_data.get("stock_code", stock_code),
        stock_name=localized_stock_name,
        report_type=meta_data.get("report_type", "detailed"),
        report_language=report_language,
        created_at=meta_data.get("created_at", datetime.now().isoformat()),
        current_price=meta_data.get("current_price"),
        change_pct=meta_data.get("change_pct"),
        model_used=normalize_model_used(meta_data.get("model_used")),
    )

    summary = ReportSummary(
        analysis_summary=summary_data.get("analysis_summary"),
        operation_advice=summary_data.get("operation_advice"),
        trend_prediction=summary_data.get("trend_prediction"),
        sentiment_score=summary_data.get("sentiment_score"),
        sentiment_label=summary_data.get("sentiment_label"),
    )

    strategy = None
    if strategy_data:
        strategy = ReportStrategy(
            ideal_buy=_stringify_report_strategy_value(strategy_data.get("ideal_buy")),
            secondary_buy=_stringify_report_strategy_value(strategy_data.get("secondary_buy")),
            stop_loss=_stringify_report_strategy_value(strategy_data.get("stop_loss")),
            take_profit=_stringify_report_strategy_value(strategy_data.get("take_profit")),
        )

    extracted_fundamental = extract_fundamental_detail_fields(
        context_snapshot=context_snapshot,
        fallback_fundamental_payload=fallback_fundamental_payload,
    )
    extracted_boards = extract_board_detail_fields(
        context_snapshot=context_snapshot,
        fallback_fundamental_payload=fallback_fundamental_payload,
    )
    details = None
    has_board_details = (
        bool(extracted_boards.get("belong_boards")) or extracted_boards.get("sector_rankings") is not None
    )
    if details_data or any(extracted_fundamental.values()) or has_board_details or context_snapshot is not None:
        details = ReportDetails(
            news_content=details_data.get("news_summary") or details_data.get("news_content"),
            raw_result=details_data,
            context_snapshot=context_snapshot,
            financial_report=extracted_fundamental.get("financial_report"),
            dividend_metrics=extracted_fundamental.get("dividend_metrics"),
            belong_boards=extracted_boards.get("belong_boards"),
            sector_rankings=extracted_boards.get("sector_rankings"),
        )

    return AnalysisReport(meta=meta, summary=summary, strategy=strategy, details=details)
