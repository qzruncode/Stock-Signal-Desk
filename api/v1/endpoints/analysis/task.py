# -*- coding: utf-8 -*-
"""Analysis task query endpoints — list tasks, stream SSE, query single status."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.v1.endpoints.analysis import router
from api.v1.schemas.analysis import (
    AnalysisResultResponse,
    TaskStatus,
    TaskInfo,
    TaskListResponse,
)
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.history import (
    AnalysisReport,
    ReportMeta,
    ReportSummary,
    ReportStrategy,
)
from src.report_language import get_localized_stock_name, normalize_report_language
from src.services.task_queue import get_task_queue
from src.utils.data_processing import normalize_model_used, parse_json_field

logger = logging.getLogger(__name__)


@router.get(
    "/tasks",
    response_model=TaskListResponse,
    responses={
        200: {"description": "任务列表"},
    },
    summary="获取分析任务列表",
    description="获取当前所有分析任务，可按状态筛选"
)
def get_task_list(
    status: Optional[str] = Query(
        None,
        description="筛选状态：pending, processing, completed, failed（支持逗号分隔多个）"
    ),
    limit: int = Query(20, description="返回数量限制", ge=1, le=100),
) -> TaskListResponse:
    """获取分析任务列表。"""
    task_queue = get_task_queue()

    all_tasks = task_queue.list_all_tasks(limit=limit)

    if status:
        status_list = [s.strip().lower() for s in status.split(",")]
        all_tasks = [t for t in all_tasks if t.status.value in status_list]

    stats = task_queue.get_task_stats()

    task_infos = [
        TaskInfo(
            task_id=t.task_id,
            stock_code=t.stock_code,
            stock_name=t.stock_name,
            status=t.status.value,
            progress=t.progress,
            message=t.message,
            report_type=t.report_type,
            created_at=t.created_at.isoformat(),
            started_at=t.started_at.isoformat() if t.started_at else None,
            completed_at=t.completed_at.isoformat() if t.completed_at else None,
            error=t.error,
            original_query=t.original_query,
            selection_source=t.selection_source,
            prompt_template_id=t.prompt_template_id,
            prompt_template_name=t.prompt_template_name,
            conversation=t.conversation,
        )
        for t in all_tasks
    ]

    return TaskListResponse(
        total=stats["total"],
        pending=stats["pending"],
        processing=stats["processing"],
        tasks=task_infos,
    )


@router.get(
    "/tasks/stream",
    responses={
        200: {"description": "SSE 事件流", "content": {"text/event-stream": {}}},
    },
    summary="任务状态 SSE 流",
    description="通过 Server-Sent Events 实时推送任务状态变化"
)
async def task_stream():
    """SSE 任务状态流。"""
    async def event_generator():
        task_queue = get_task_queue()
        event_queue: asyncio.Queue = asyncio.Queue()

        yield _format_sse_event("connected", {"message": "Connected to task stream"})

        pending_tasks = task_queue.list_pending_tasks()
        for task in pending_tasks:
            yield _format_sse_event("task_created", task.to_dict())

        task_queue.subscribe(event_queue)

        try:
            while True:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=30)
                    yield _format_sse_event(event["type"], event["data"])
                except asyncio.TimeoutError:
                    yield _format_sse_event("heartbeat", {
                        "timestamp": datetime.now().isoformat()
                    })
        except asyncio.CancelledError:
            logger.debug("SSE client disconnected, cancelling event generator")
            raise
        finally:
            task_queue.unsubscribe(event_queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


def _format_sse_event(event_type: str, data: Dict[str, Any]) -> str:
    """格式化 SSE 事件。"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get(
    "/status/{task_id}",
    response_model=TaskStatus,
    responses={
        200: {"description": "任务状态"},
        404: {"description": "任务不存在", "model": ErrorResponse},
    },
    summary="查询分析任务状态",
    description="根据 task_id 查询单个任务的状态"
)
def get_analysis_status(task_id: str) -> TaskStatus:
    """查询分析任务状态。优先从任务队列查询，否则从数据库查询历史记录。"""
    from api.v1.endpoints.analysis.report import (
        _build_conversation_report,
        _stringify_report_strategy_value,
    )

    task_queue = get_task_queue()
    task = task_queue.get_task(task_id)

    if task:
        result: Optional[AnalysisResultResponse] = None

        if isinstance(task.result, dict):
            try:
                result = AnalysisResultResponse.model_validate(task.result)
            except Exception:
                report_payload = task.result
                result = AnalysisResultResponse(
                    query_id=task.task_id,
                    stock_code=task.stock_code,
                    stock_name=task.stock_name,
                    report=report_payload,
                    created_at=(
                        task.completed_at.isoformat()
                        if task.completed_at
                        else task.created_at.isoformat()
                    ),
                )
                logger.info(
                    "任务结果采用通用包装返回: task_id=%s stock_code=%s",
                    task.task_id,
                    task.stock_code,
                )

        return TaskStatus(
            task_id=task.task_id,
            status=task.status.value,
            progress=task.progress,
            result=result,
            error=task.error,
            message=task.message,
            stock_name=task.stock_name,
            original_query=task.original_query,
            selection_source=task.selection_source,
            prompt_template_id=task.prompt_template_id,
            prompt_template_name=task.prompt_template_name,
            conversation=task.conversation,
        )

    try:
        from src.storage import DatabaseManager
        db = DatabaseManager.get_instance()
        records = db.get_analysis_history(query_id=task_id, limit=1)

        if records:
            record = records[0]
            raw_result = parse_json_field(record.raw_result)
            model_used = normalize_model_used(
                (raw_result or {}).get("model_used") if isinstance(raw_result, dict) else None
            )
            report_language = normalize_report_language(
                (raw_result or {}).get("report_language") if isinstance(raw_result, dict) else None
            )
            stock_name = get_localized_stock_name(record.name, record.code, report_language)

            current_price = None
            change_pct = None
            context_snapshot = parse_json_field(getattr(record, 'context_snapshot', None))
            if context_snapshot and isinstance(context_snapshot, dict):
                enhanced_context = context_snapshot.get('enhanced_context') or {}
                realtime = enhanced_context.get('realtime') or {}
                current_price = realtime.get('price')
                change_pct = realtime.get('change_pct')
                realtime_quote_raw = context_snapshot.get('realtime_quote_raw') or {}
                if current_price is None:
                    current_price = realtime_quote_raw.get('price')
                if change_pct is None:
                    change_pct = realtime_quote_raw.get('change_pct')
                if change_pct is None:
                    change_pct = realtime_quote_raw.get('pct_chg')

            is_conversation = getattr(record, 'report_type', None) == 'conversation'

            if is_conversation:
                conversation = None
                if isinstance(raw_result, dict):
                    conversation = raw_result.get("conversation")
                response_text = record.news_content or ""
                report_dict = _build_conversation_report(
                    conversation=conversation or {},
                    query_id=task_id,
                    stock_code=record.code,
                    stock_name=stock_name,
                ).model_dump()
                if report_dict.get("meta"):
                    report_dict["meta"]["id"] = record.id
                    report_dict["meta"]["created_at"] = record.created_at.isoformat() if record.created_at else None
            else:
                report_dict = AnalysisReport(
                    meta=ReportMeta(
                        id=record.id,
                        query_id=task_id,
                        stock_code=record.code,
                        stock_name=stock_name,
                        report_type=getattr(record, 'report_type', None),
                        report_language=report_language,
                        created_at=record.created_at.isoformat() if record.created_at else None,
                        model_used=model_used,
                        current_price=current_price,
                        change_pct=change_pct,
                    ),
                    summary=ReportSummary(
                        sentiment_score=record.sentiment_score,
                        operation_advice=record.operation_advice,
                        trend_prediction=record.trend_prediction,
                        analysis_summary=record.analysis_summary,
                    ),
                    strategy=ReportStrategy(
                        ideal_buy=_stringify_report_strategy_value(getattr(record, 'ideal_buy', None)),
                        secondary_buy=_stringify_report_strategy_value(getattr(record, 'secondary_buy', None)),
                        stop_loss=_stringify_report_strategy_value(getattr(record, 'stop_loss', None)),
                        take_profit=_stringify_report_strategy_value(getattr(record, 'take_profit', None)),
                    ),
                ).model_dump()
            return TaskStatus(
                task_id=task_id,
                status="completed",
                progress=100,
                result=AnalysisResultResponse(
                    query_id=task_id,
                    stock_code=record.code,
                    stock_name=stock_name,
                    report=report_dict,
                    created_at=record.created_at.isoformat() if record.created_at else datetime.now().isoformat()
                ),
                error=None,
                message=None,
            )

    except Exception as e:
        logger.error(f"查询任务状态失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": f"查询任务状态失败: {str(e)}"
            }
        )

    raise HTTPException(
        status_code=404,
        detail={
            "error": "not_found",
            "message": f"任务 {task_id} 不存在或已过期"
        }
    )