# -*- coding: utf-8 -*-
"""Human-in-the-Loop 审批 endpoint。

POST /api/v1/agent/approve
Body: { "tool_call_id": str, "approved": bool }

工具执行前若发了 approval-request(见 chat.py 的 GATED_TOOLS),前端收到后
渲染确认按钮,用户决定后调本 endpoint。后端据此 set 对应 asyncio.Event,
唤醒正在 _execute_one_tool 中阻塞等待的协程。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from api.v1.endpoints.agent import router
from api.v1.endpoints.agent.chat import _pending_approvals

logger = logging.getLogger(__name__)


class ApprovalRequest(BaseModel):
    tool_call_id: str
    approved: bool


@router.post("/agent/approve")
async def approve_tool_call(req: ApprovalRequest):
    state = _pending_approvals.get(req.tool_call_id)
    if state is None:
        # 审批状态已过期(超时/已处理)或不存在,幂等返回
        logger.info("[Agent] approve: no pending approval for %s", req.tool_call_id)
        return {"ok": False, "reason": "no_pending_approval"}

    state.approved = req.approved
    state.event.set()
    logger.info(
        "[Agent] approve: %s -> %s (symbol=%s)",
        req.tool_call_id, "approved" if req.approved else "rejected", state.symbol,
    )
    return {"ok": True, "approved": req.approved}
