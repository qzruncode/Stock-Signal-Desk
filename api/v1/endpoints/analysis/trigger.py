# -*- coding: utf-8 -*-
"""Analysis trigger endpoints — POST /analyze and sync/async handlers."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Optional, Union, Any, Dict

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse

from api.deps import get_config_dep
from api.v1.endpoints.analysis import router
from api.v1.schemas.analysis import (
    AnalyzeRequest,
    AnalysisResultResponse,
    TaskAccepted,
    BatchTaskAcceptedResponse,
    BatchTaskAcceptedItem,
    BatchDuplicateTaskItem,
    DuplicateTaskErrorResponse,
)
from api.v1.schemas.common import ErrorResponse
from data_provider.utils import canonical_stock_code, normalize_stock_code
from src.config import Config
from src.services.name_to_code_resolver import resolve_name_to_code
from src.services.stock_code_utils import is_code_like
from src.services.task_queue import get_task_queue
from src.utils.data_processing import normalize_model_used, parse_json_field

logger = logging.getLogger(__name__)

_SUPPORTED_FREE_TEXT_RE = re.compile(r"^[A-Za-z0-9.*\-+㐀-鿿\s]+$")


def _invalid_analysis_input_error() -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "error": "validation_error",
            "message": "请输入有效的股票代码或股票名称",
        },
    )


def _is_obviously_invalid_analysis_input(text: str) -> bool:
    if not text or is_code_like(text):
        return False

    if not _SUPPORTED_FREE_TEXT_RE.fullmatch(text):
        return True

    has_letters = any(ch.isalpha() and ch.isascii() for ch in text)
    has_digits = any(ch.isdigit() for ch in text)
    return has_letters and has_digits


def _resolve_and_normalize_input(raw_value: str) -> str:
    """Resolve and normalize a stock input for analysis requests."""
    text = (raw_value or "").strip()
    if not text:
        return ""

    if is_code_like(text):
        return canonical_stock_code(text)

    if _is_obviously_invalid_analysis_input(text):
        raise _invalid_analysis_input_error()

    resolved = resolve_name_to_code(text)
    if resolved:
        return canonical_stock_code(resolved)

    raise _invalid_analysis_input_error()


@router.post(
    "/analyze",
    response_model=AnalysisResultResponse,
    responses={
        200: {"description": "分析完成（同步模式）", "model": AnalysisResultResponse},
        202: {
            "description": "分析任务已接受（异步模式）",
            "model": Union[TaskAccepted, BatchTaskAcceptedResponse],
        },
        400: {"description": "请求参数错误", "model": ErrorResponse},
        409: {"description": "股票正在分析中，拒绝重复提交", "model": DuplicateTaskErrorResponse},
        500: {"description": "分析失败", "model": ErrorResponse},
    },
    summary="触发股票分析",
    description="启动 AI 智能分析任务，支持同步和异步模式。异步模式下相同股票代码不允许重复提交。",
)
def trigger_analysis(
    request: AnalyzeRequest, config: Config = Depends(get_config_dep)
) -> Union[AnalysisResultResponse, JSONResponse]:
    """触发股票分析。

    启动 AI 智能分析任务，支持单只或多只股票批量分析。
    """
    stock_codes = []
    if request.stock_code:
        stock_codes.append(request.stock_code)
    if request.stock_codes:
        stock_codes.extend(request.stock_codes)

    if not stock_codes:
        raise HTTPException(
            status_code=400, detail={"error": "validation_error", "message": "必须提供 stock_code 或 stock_codes 参数"}
        )

    resolved = [_resolve_and_normalize_input(c) for c in stock_codes]

    seen = set()
    unique_codes = []
    for code in resolved:
        if not code:
            continue
        norm = normalize_stock_code(code)
        if norm not in seen:
            seen.add(norm)
            unique_codes.append(code)

    stock_codes = unique_codes

    MAX_BATCH_SIZE = 50
    if len(stock_codes) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": f"单次分析请求最多支持 {MAX_BATCH_SIZE} 只股票"},
        )

    if not stock_codes:
        raise HTTPException(
            status_code=400, detail={"error": "validation_error", "message": "股票代码不能为空或仅包含空白字符"}
        )

    if not request.async_mode:
        if len(stock_codes) > 1:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "validation_error",
                    "message": "同步模式仅支持单只股票分析，请使用 async_mode=true 进行批量分析",
                },
            )
        return _handle_sync_analysis(stock_codes[0], request)

    return _handle_async_analysis_batch(stock_codes, request)


def _handle_async_analysis_batch(stock_codes: list, request: AnalyzeRequest) -> JSONResponse:
    """Handle asynchronous analysis requests, including batch submission."""
    task_queue = get_task_queue()

    is_single = len(stock_codes) == 1
    preserve_batch_metadata = request.selection_source == "import"

    stock_name = request.stock_name if is_single else None
    original_query = request.original_query if (is_single or preserve_batch_metadata) else None
    selection_source = request.selection_source if (is_single or preserve_batch_metadata) else None
    notify = getattr(request, "notify", True)

    submit_kwargs = dict(
        stock_codes=stock_codes,
        stock_name=stock_name,
        original_query=original_query,
        selection_source=selection_source,
        report_type=request.report_type,
        force_refresh=request.force_refresh,
        notify=notify,
        prompt_template_id=request.prompt_template_id,
    )

    accepted_tasks, duplicate_errors = task_queue.submit_tasks_batch(**submit_kwargs)

    accepted = [
        BatchTaskAcceptedItem(
            task_id=task.task_id,
            stock_code=task.stock_code,
            status="pending",
            message=f"分析任务已加入队列: {task.stock_code}",
        )
        for task in accepted_tasks
    ]
    duplicates = [
        BatchDuplicateTaskItem(
            stock_code=dup.stock_code,
            existing_task_id=dup.existing_task_id,
            message=str(dup),
        )
        for dup in duplicate_errors
    ]

    if len(stock_codes) == 1 and duplicates:
        dup = duplicates[0]
        error_response = DuplicateTaskErrorResponse(
            error="duplicate_task",
            message=dup.message,
            stock_code=dup.stock_code,
            existing_task_id=dup.existing_task_id,
        )
        return JSONResponse(status_code=409, content=error_response.model_dump())

    if len(stock_codes) == 1 and accepted:
        task_accepted = TaskAccepted(
            task_id=accepted[0].task_id,
            status="pending",
            message=accepted[0].message,
        )
        return JSONResponse(status_code=202, content=task_accepted.model_dump())

    batch_response = BatchTaskAcceptedResponse(
        accepted=accepted,
        duplicates=duplicates,
        message=f"已提交 {len(accepted)} 个任务，{len(duplicates)} 个重复跳过",
    )
    return JSONResponse(status_code=202, content=batch_response.model_dump())


def _handle_sync_analysis(stock_code: str, request: AnalyzeRequest) -> AnalysisResultResponse:
    """处理同步分析请求，直接执行分析并返回结果。"""
    from src.services.analysis_service import AnalysisService
    from api.v1.endpoints.analysis.report import _build_conversation_report

    query_id = uuid.uuid4().hex

    try:
        service = AnalysisService()
        result = service.analyze_stock(
            stock_code=stock_code,
            report_type=request.report_type,
            force_refresh=request.force_refresh,
            query_id=query_id,
            send_notification=getattr(request, "notify", True),
            prompt_template_id=getattr(request, "prompt_template_id", None),
        )

        if result is None:
            error_message = service.last_error or f"分析股票 {stock_code} 失败"
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "analysis_failed",
                    "message": error_message,
                },
            )

        conversation = result.get("conversation", {})

        report = _build_conversation_report(
            conversation=conversation,
            query_id=query_id,
            stock_code=result.get("stock_code", stock_code),
            stock_name=result.get("stock_name"),
        )

        return AnalysisResultResponse(
            query_id=query_id,
            stock_code=result.get("stock_code", stock_code),
            stock_name=result.get("stock_name"),
            report=report.model_dump() if report else None,
            created_at=datetime.now().isoformat(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"分析失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": "internal_error", "message": f"分析过程发生错误: {str(e)}"}
        )
