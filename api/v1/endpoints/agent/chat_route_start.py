# -*- coding: utf-8 -*-
"""Primary chat route implementation."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Mapping

from assistant_stream.serialization.data_stream import DataStreamResponse
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from api.v1.endpoints.agent.conversation_lifecycle import conversation_transition
from src.agent.run_registry import RunBroadcaster, RunCapacityExceeded, active_run_registry
from src.agent.run_streaming import (
    durable_subscriber_stream,
    subscriber_stream,
    timeline_presentation_stream,
)
from src.agent.runtime_safety import AgentRequestValidationError, agent_request_rate_limiter, get_agent_runtime_limits, validate_chat_request_body
from src.auth import get_client_ip
from src.services.chat_session_service import ChatSessionService
from src.storage import DatabaseManager
from src.llm.anthropic_gateway import AnthropicGatewayConfigError as AgentModelConfigError

logger = logging.getLogger(__name__)


def _extract_edit_message_id(body: Mapping[str, Any]) -> str | None:
    """Read the source message id stamped by the assistant-ui edit composer."""
    run_config = body.get("runConfig") or body.get("run_config")
    if not isinstance(run_config, Mapping):
        return None
    custom = run_config.get("custom")
    if not isinstance(custom, Mapping):
        return None
    value = custom.get("editMessageId") or custom.get("edit_message_id")
    text = str(value or "").strip()
    return text or None


async def agent_chat_impl(
    request: Request,
    db_manager: DatabaseManager,
    *,
    background_runner: Any,
    config_loader: Any,
):
    """Chat endpoint using assistant-stream DataStream protocol.

    生成逻辑跑在独立后台 task (detach 于 HTTP 连接),首连接 attach 为第一个订阅者。
    断连不杀生成 —— 后端继续跑完落库,用户刷新后可通过 /agent/chat/resume 续流。
    """
    limits = get_agent_runtime_limits()
    max_body_bytes = limits.max_request_chars * 4
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            # JSON UTF-8 can use up to four bytes per character.  Reject a
            # clearly oversized body before parsing it into memory; the exact
            # character limit is enforced again after parsing.
            if int(content_length) > max_body_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"error": "request_too_large", "message": "请求内容过大"},
                )
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_content_length", "message": "Content-Length 格式不合法"},
            )
    try:
        body_bytes = bytearray()
        async for chunk in request.stream():
            body_bytes.extend(chunk)
            if len(body_bytes) > max_body_bytes:
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": "request_too_large",
                        "message": "请求内容过大",
                    },
                )
        body = json.loads(body_bytes or b"{}")
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "message": "请求体不是有效 JSON"},
        )
    try:
        messages, conversation_id, resume_existing = validate_chat_request_body(body)
    except AgentRequestValidationError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "message": str(exc)},
        )

    active_run_registry.configure(db_manager)
    session_service = ChatSessionService(
        db_manager,
        tenant_id=str(getattr(request.state, "tenant_id", "local")),
        owner_id=str(getattr(request.state, "owner_id", "admin")),
    )
    edit_message_id = _extract_edit_message_id(body)

    # The current chat UI renders the standard assistant-stream parts in order
    # and uses agent_stage data for the durable execution summary. Keep the
    # stream projection hook so the response contract remains explicit.
    timeline_presentation = body.get("stream_presentation") == "timeline"

    def stream_for_client(source: Any):
        return timeline_presentation_stream(source) if timeline_presentation else source

    # Resume is an attachment operation, not a new model request.  It must not
    # require the current model configuration and must never create a new blank
    # conversation when a stale/invalid id is supplied.
    if resume_existing:
        if not conversation_id:
            return JSONResponse(
                status_code=400,
                content={"error": "conversation_id_required", "message": "恢复运行必须提供 conversation_id"},
            )
        if not session_service.get_conversation(conversation_id):
            return JSONResponse(
                status_code=404,
                content={"error": "conversation_not_found", "message": "对话不存在"},
            )
        active_run = active_run_registry.get(conversation_id)
        try:
            replay_from = max(0, int(body.get("after_chunk_index") or 0))
        except (TypeError, ValueError):
            replay_from = 0
        if active_run is not None and active_run.is_running:
            durable_run = await asyncio.to_thread(
                db_manager.get_agent_run,
                run_id=active_run.run_id,
            )
            logger.info(
                "[Agent] chat attach existing run_id=%s conversation_id=%s from chunk %s status=%s",
                active_run.run_id,
                conversation_id,
                replay_from,
                active_run.status,
            )
            if durable_run is not None:
                return DataStreamResponse(
                    stream_for_client(durable_subscriber_stream(
                        db_manager,
                        durable_run,
                        replay_from=replay_from,
                    ))
                )
            return DataStreamResponse(
                stream_for_client(subscriber_stream(active_run, replay_from=replay_from))
            )
        if active_run is not None:
            # Keep a retained terminal handle authoritative over a briefly
            # stale durable row. A completed/failed local run must never be
            # attached as though it were still generating.
            logger.info(
                "[Agent] chat resume rejected terminal run_id=%s conversation_id=%s status=%s",
                active_run.run_id,
                conversation_id,
                active_run.status,
            )
            return JSONResponse(
                status_code=409,
                content={"error": "run_not_active", "conversation_id": conversation_id},
            )
        durable_run = await asyncio.to_thread(
            db_manager.get_agent_run,
            conversation_id=conversation_id,
        )
        if durable_run is not None and durable_run.get("status") in {
            "queued",
            "running",
            "recovering",
        }:
            logger.info(
                "[Agent] durable resume run_id=%s conversation_id=%s from chunk %s status=%s",
                durable_run.get("run_id"),
                conversation_id,
                replay_from,
                durable_run.get("status"),
            )
            return DataStreamResponse(
                stream_for_client(durable_subscriber_stream(
                    db_manager,
                    durable_run,
                    replay_from=replay_from,
                ))
            )
        logger.info("[Agent] chat resume requested but no durable run for %s", conversation_id)
        return JSONResponse(
            status_code=409,
            content={"error": "run_not_active", "conversation_id": conversation_id},
        )

    tenant_id = str(getattr(request.state, "tenant_id", "local"))
    owner_id = str(getattr(request.state, "owner_id", "admin"))
    retry_after = agent_request_rate_limiter.check_and_record(
        f"{tenant_id}:{owner_id}:{get_client_ip(request)}",
        limit=limits.requests_per_minute,
        database=db_manager,
    )
    if retry_after:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content={
                "error": "agent_rate_limited",
                "message": "请求过于频繁，请稍后再试",
                "retry_after_seconds": retry_after,
            },
        )

    try:
        llm_cfg = config_loader()
    except AgentModelConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    conversation = session_service.ensure_conversation(conversation_id)
    conv_id = conversation["id"]
    async with conversation_transition(db_manager, conv_id):
        if not session_service.get_conversation(conv_id):
            raise HTTPException(status_code=404, detail="对话不存在")
        if body.get("history_mode") == "server":
            prepared = await asyncio.to_thread(
                session_service.prepare_request_with_server_history,
                conv_id,
                list(messages),
                parent_message_id=body.get("history_parent_id"),
                edit_message_id=edit_message_id,
            )
            messages = prepared.messages
            if prepared.replace_checkpoint:
                body["history_mode"] = "replace"
        # 原子地「判定无活跃 run + 创建新 run」(锁内)。把判定与创建合并,消除
        # is_active(无锁)与 start_or_get(锁内)之间的竞态窗口:两个并发请求不会
        # 都通过检查、各自落库 messages 后第二个静默 attach 到第一个 run 而丢消息。
        # 拿到 None 表示已有活跃 run → 409 触发前端续流。
        try:
            run = await active_run_registry.try_claim(
                conv_id,
                max_active_runs=limits.max_active_runs,
                max_owner_active_runs=limits.max_active_runs_per_owner,
                request_payload={
                    "engine": "langgraph_agent_loop",
                    "body": body,
                    "messages": list(messages),
                    "conversation_id": conv_id,
                    "model": llm_cfg.get("model"),
                },
                tenant_id=tenant_id,
                owner_id=owner_id,
            )
        except RunCapacityExceeded:
            logger.warning(
                "[Agent] global capacity exhausted conversation_id=%s active=%s limit=%s",
                conv_id,
                active_run_registry.stats()["active_runs"],
                limits.max_active_runs,
            )
            return JSONResponse(
                status_code=503,
                headers={"Retry-After": "5"},
                content={
                    "error": "agent_busy",
                    "message": "AI 助手当前任务较多，请稍后重试",
                    "retry_after_seconds": 5,
                },
            )
        if run is None:
            logger.info("[Agent] chat rejected: run already in progress for %s", conv_id)
            return JSONResponse(
                status_code=409,
                content={"error": "run_in_progress", "conversation_id": conv_id},
            )

        logger.info(
            f"[Agent] Chat request with {len(messages)} messages, "
            f"model={llm_cfg['model']}, conversation_id={conv_id}, run_id={run.run_id}"
        )

        # 生成开始前同步落库本次完整 messages(含刚发的 user 消息)。
        # 这一步不能放在后台 task 里:用户一发送就刷新时,conversation detail 会
        # 先于后台 task 执行,如果库里还没有本次 user,前端只能恢复出空白历史。
        try:
            await asyncio.to_thread(
                session_service.save_conversation_snapshot,
                conv_id,
                list(messages),
                skip_title=True,
            )
        except Exception as exc:
            logger.exception("[Agent] failed to persist request snapshot conversation_id=%s", conv_id)
            await active_run_registry.mark_done(conv_id, "failed", error="request_snapshot_failed")
            raise HTTPException(status_code=500, detail="保存对话失败，请重试") from exc

        async def factory(broadcaster: RunBroadcaster) -> "asyncio.Task":
            return asyncio.create_task(
                background_runner(
                    controller=broadcaster,
                    run=run,
                    messages=list(messages),
                    body=body,
                    llm_cfg=llm_cfg,
                    conversation_id=conv_id,
                    db_manager=db_manager,
                    session_service=session_service,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                )
            )

        # run 已由前面的 try_claim 原子创建(判定 + 创建在同一锁内)。首连接必须先
        # subscribe 再启动后台 task,否则 task 可能在首个订阅者 subscribe 之前就
        # emit 完所有 chunk,导致首连收不到内容。
        first_queue = run.broadcaster.subscribe()
        try:
            await run.start(factory)
        except Exception as exc:
            run.broadcaster.unsubscribe(first_queue)
            await active_run_registry.mark_done(conv_id, "failed", error="run_start_failed")
            logger.exception("[Agent] failed to start run_id=%s", run.run_id)
            raise HTTPException(status_code=500, detail="AI 助手任务启动失败，请重试") from exc
        return DataStreamResponse(stream_for_client(subscriber_stream(run, first_queue)))


__all__ = ["agent_chat_impl"]
