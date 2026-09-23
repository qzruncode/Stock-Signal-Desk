"""Runtime-owned product mode dispatch.

This module is intentionally outside the Plan and Team packages.  Auto may
select Goal, but the selected mode owns its graph after dispatch completes.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from .state import GraphContext


ProductMode = Literal["auto", "direct", "plan", "team", "goal"]


def goal_auto_routing_enabled() -> bool:
    """Return the server-side rollout switch for Auto -> Goal routing."""

    return str(os.getenv("AGENT_GOAL_AUTO_ROUTING_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class ProductModeRoute(BaseModel):
    """Top-level product route selected before a graph owns the run."""

    model_config = ConfigDict(extra="forbid")

    progress_text: str = Field(default="", max_length=1_200)
    mode: Literal["direct", "plan", "team", "goal"]
    reason: str = Field(min_length=1, max_length=600)
    execution_strategy: Literal["single_agent", "team", "goal"] = "single_agent"


def normalize_product_mode(requested: str | None = None) -> str:
    normalized = str(requested or "auto").strip().lower()
    if normalized not in {"auto", "direct", "plan", "team", "goal"}:
        raise ValueError(f"unknown agent mode: {normalized}")
    return normalized


def _route_messages(user_text: str, *, allow_goal: bool) -> list[Any]:
    goal_instruction = (
        "goal 适合用户给出高层目标、需要多步执行、持续观察、可验证完成条件和必要时调整动作。"
        if allow_goal
        else "goal 当前只允许用户显式选择，Auto 不要选择 goal。"
    )
    return [
        SystemMessage(
            content=(
                "你是产品层 Auto 路由器。可选模式有 direct、plan、team、goal。"
                "direct 适合单步解释或单一事实；plan 适合需要显式步骤和依赖的多步任务；"
                "team 只适合需要多个领域并行协作的任务；"
                + goal_instruction
                + "goal 是独立产品模式，不要把它描述成 plan 或 team 的子流程。"
                "不要执行工具，不要输出最终答案。只能返回 ProductModeRoute；"
                "progress_text 用一句自然语言说明实际路由依据。"
            )
        ),
        HumanMessage(content=str(user_text or "")[:8_000]),
    ]


async def resolve_product_mode(
    state: Mapping[str, Any],
    context: GraphContext,
    *,
    requested: str | None = None,
    route_id: str | None = None,
) -> dict[str, Any]:
    """Resolve Auto at the Runtime boundary, before graph selection."""

    requested_mode = normalize_product_mode(requested or state.get("agent_mode") or "auto")
    action_id = str(route_id or state.get("run_id") or "mode-dispatch").strip()
    context.events.stage(
        "routing",
        "started",
        "正在判断本轮产品模式",
        action_id=f"{action_id}:route",
        user_message="我先判断这项任务适合哪种独立执行模式。",
        details={
            "requested_mode": requested_mode,
            "route_scope": "product",
        },
    )
    calls = 0
    auto_goal_enabled = goal_auto_routing_enabled()
    if requested_mode in {"direct", "plan", "team", "goal"}:
        mode = requested_mode
        strategy = "team" if mode == "team" else "goal" if mode == "goal" else "single_agent"
        reason = "由运行请求显式指定"
    else:
        try:
            raw = await context.model.with_structured_output(
                ProductModeRoute,
                include_raw=True,
            ).ainvoke(
                _route_messages(
                    str(state.get("user_text") or ""),
                    allow_goal=auto_goal_enabled,
                )
            )
            calls = 1
            parsed = raw.get("parsed") if isinstance(raw, Mapping) else raw
            if not isinstance(parsed, ProductModeRoute):
                raise ValueError("ProductModeRoute structured output missing")
            mode = parsed.mode
            strategy = parsed.execution_strategy
            if mode == "team":
                strategy = "team"
            elif mode == "goal":
                strategy = "goal"
            else:
                strategy = "single_agent"
            reason = parsed.reason
            if parsed.progress_text:
                context.events.progress(parsed.progress_text)
            if mode == "goal" and not auto_goal_enabled:
                detail = "Goal 当前只允许显式选择，Auto -> Goal 尚未开放。"
                context.events.stage(
                    "routing",
                    "blocked",
                    detail,
                    action_id=f"{action_id}:route",
                    error_code="goal_auto_routing_disabled",
                    details={
                        "error": detail,
                        "route_scope": "product",
                        "feature_flag": "AGENT_GOAL_AUTO_ROUTING_ENABLED",
                    },
                )
                return {
                    "orchestrator_route": "failed",
                    "orchestrator_route_reason": detail,
                    "orchestrator_execution_strategy": "",
                    "resolved_agent_mode": "",
                    "team_status": "not_started",
                    "team_contract_call_count": calls,
                    "status": "failed",
                    "error_code": "goal_auto_routing_disabled",
                    "terminal_detail": detail,
                }
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"[:600]
            context.events.stage(
                "routing",
                "failed",
                "产品模式路由未能完成，已停止本轮执行",
                action_id=f"{action_id}:route",
                error_code="orchestrator_route_failed",
                details={"error": detail, "route_scope": "product"},
            )
            return {
                "orchestrator_route": "failed",
                "orchestrator_route_reason": detail,
                "orchestrator_execution_strategy": "",
                "resolved_agent_mode": "",
                "team_status": "not_started",
                "team_contract_call_count": max(1, calls),
                "status": "failed",
                "error_code": "orchestrator_route_failed",
                "terminal_detail": "产品模式路由未能完成，本轮未执行 Agent。",
            }

    resolved = "team" if strategy == "team" else "goal" if strategy == "goal" else mode
    context.events.stage(
        "routing",
        "completed",
        reason,
        action_id=f"{action_id}:route",
        user_message=f"已确定执行模式：{resolved}。",
        details={
            "requested_mode": requested_mode,
            "resolved_agent_mode": resolved,
            "execution_strategy": strategy,
            "route_scope": "product",
            "contract": ProductModeRoute.__name__ if calls else "server_override",
        },
    )
    return {
        "resolved_agent_mode": resolved,
        "orchestrator_route": resolved,
        "orchestrator_route_reason": reason,
        "orchestrator_execution_strategy": strategy,
        "team_status": "routed" if strategy == "team" else "not_started",
        "team_contract_call_count": calls,
    }


__all__ = [
    "ProductMode",
    "ProductModeRoute",
    "goal_auto_routing_enabled",
    "normalize_product_mode",
    "resolve_product_mode",
]
