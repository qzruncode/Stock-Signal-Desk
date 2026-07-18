# -*- coding: utf-8 -*-
"""Tool registry metadata + 执行端点。

只读反射 src.tools.registry.ToolRegistry，供前端 /setting 页展示当前接入
LLM 模型的全部工具(GET /agent/tool-registry);并提供单工具试运行端点
(POST /agent/tool-registry/execute),复用与真实 agent chat 完全一致的执行链
(registry.execute → _compact_tool_result → _maybe_attach_search_fallback),
使 setting 页「测试」结果 = LLM 实际看到的结果。不修改 ToolRegistry 自身的
注册逻辑。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from api.v1.endpoints.agent import router
from api.v1.schemas.tools_meta import (
    ToolCategory,
    ToolExecuteRequest,
    ToolExecuteResponse,
    ToolMeta,
    ToolParameterSpec,
    ToolRegistryResponse,
)
from api.v1.endpoints.agent.tools import (
    _compact_tool_result,
    _maybe_attach_search_fallback,
)
from src.tools.registry import ToolRegistry
from src.tools.process_runner import execute_tool_isolated

logger = logging.getLogger(__name__)


_DESCRIPTION_MAX_LEN = 500
_VALID_CATEGORIES = {
    "data", "market", "financials", "sentiment", "macro", "search", "analysis",
    "research", "regulatory", "events", "risk",
}
_TOOL_EXECUTION_TIMEOUT_SECONDS = 45.0
_PROFESSIONAL_TOOL_TIMEOUT_SECONDS = 90.0

_registry = ToolRegistry()


def _execution_timeout(tool_name: str, arguments: Dict[str, Any]) -> float:
    if tool_name in {"get_multi_stock_decision_evidence", "get_theme_stock_candidates"}:
        return _PROFESSIONAL_TOOL_TIMEOUT_SECONDS
    if tool_name == "websearch" and bool(arguments.get("includeContent")):
        return _PROFESSIONAL_TOOL_TIMEOUT_SECONDS
    if tool_name == "get_monetary_policy_operations" and bool(arguments.get("include_content")):
        return _PROFESSIONAL_TOOL_TIMEOUT_SECONDS
    if tool_name == "get_regulatory_updates" and bool(arguments.get("include_content")):
        return _PROFESSIONAL_TOOL_TIMEOUT_SECONDS
    return _TOOL_EXECUTION_TIMEOUT_SECONDS


def _truncate_description(text: str, limit: int = _DESCRIPTION_MAX_LEN) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _simplify_param_type(raw_type: Any) -> str:
    if isinstance(raw_type, list):
        for candidate in raw_type:
            if candidate != "null":
                return str(candidate)
        return "null"
    return str(raw_type) if raw_type is not None else "string"


def _flatten_parameters(parameters: Dict[str, Any]) -> List[ToolParameterSpec]:
    if not isinstance(parameters, dict):
        return []
    properties = parameters.get("properties") or {}
    required_set = set(parameters.get("required") or [])
    if not isinstance(properties, dict):
        return []

    specs: List[ToolParameterSpec] = []
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        specs.append(
            ToolParameterSpec(
                name=name,
                type=_simplify_param_type(prop.get("type")),
                description=prop.get("description"),
                required=name in required_set,
                enum=prop.get("enum") if isinstance(prop.get("enum"), list) else None,
                default=prop.get("default"),
            )
        )
    return specs


def _build_tool_meta(tool_def: Any) -> ToolMeta:
    category = tool_def.category if tool_def.category in _VALID_CATEGORIES else "data"
    return ToolMeta(
        name=tool_def.name,
        category=category,  # type: ignore[arg-type]
        description=_truncate_description(tool_def.description or ""),
        parameters=_flatten_parameters(tool_def.parameters or {}),
    )


@router.get("/agent/tool-registry", response_model=ToolRegistryResponse)
def list_tool_registry(
    category: Optional[ToolCategory] = None,  # type: ignore[valid-type]
) -> ToolRegistryResponse:
    """返回当前 LLM 模型接入的全部工具元数据（只读）。"""
    tool_defs = list(_registry._tools.values())

    categories: Dict[str, int] = {}
    for td in tool_defs:
        cat = td.category if td.category in _VALID_CATEGORIES else "data"
        categories[cat] = categories.get(cat, 0) + 1

    tools: List[ToolMeta] = []
    for td in tool_defs:
        meta = _build_tool_meta(td)
        if category is not None and meta.category != category:
            continue
        tools.append(meta)

    return ToolRegistryResponse(
        total=len(tool_defs),
        categories=categories,
        tools=tools,
    )


@router.post("/agent/tool-registry/execute", response_model=ToolExecuteResponse)
async def execute_tool(req: ToolExecuteRequest) -> ToolExecuteResponse:
    """单工具试运行。

    复用与真实 agent chat 一致的执行链(execute → 压缩 → 联网兜底),
    使 setting 页「测试」结果与 LLM 实际看到的相同。同步网络 IO 丢进线程池,
    避免阻塞事件循环(与 chat.py 的 _execute_one_tool 同思路)。工具不存在、
    参数错误或任意异常均以 success=False + error 返回,保持响应结构统一,
    供前端按 success 字段判断。执行使用与 chat.py 相同的超时档位和原生风险
    工具进程隔离，避免设置页试运行拖死 API worker 或把上游失败误报为成功。
    """
    tool_name = (req.tool_name or "").strip()
    args = _registry.normalize_arguments(tool_name, req.arguments or {})
    start = time.perf_counter()

    def _elapsed_ms() -> int:
        return int((time.perf_counter() - start) * 1000)

    try:
        def _sync_fetch() -> Any:
            timeout = _execution_timeout(tool_name, args)
            # Every registered tool runs out-of-process.  Several apparently
            # harmless tools can enter AKShare/libmini_racer indirectly when a
            # cache misses.  Running only a hand-maintained subset in isolation
            # leaves the FastAPI worker vulnerable to a native abort during
            # concurrent probes.
            result = execute_tool_isolated(
                tool_name,
                args,
                timeout_seconds=timeout - 3,
            )
            compacted = _compact_tool_result(tool_name, result)
            return _maybe_attach_search_fallback(tool_name, args, compacted)

        timeout = _execution_timeout(tool_name, args)
        payload = await asyncio.wait_for(asyncio.to_thread(_sync_fetch), timeout=timeout)
        succeeded = not (isinstance(payload, dict) and payload.get("success") is False)
        errors = payload.get("errors") if isinstance(payload, dict) else None
        return ToolExecuteResponse(
            tool_name=tool_name,
            arguments=args,
            success=succeeded,
            result=payload,
            error=(str(errors[0]) if not succeeded and isinstance(errors, list) and errors else None),
            duration_ms=_elapsed_ms(),
        )
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning("[tool-registry] execute timed out: %s", tool_name)
        return ToolExecuteResponse(
            tool_name=tool_name,
            arguments=args,
            success=False,
            error="工具执行超时",
            duration_ms=_elapsed_ms(),
        )
    except KeyError:
        return ToolExecuteResponse(
            tool_name=tool_name,
            arguments=args,
            success=False,
            error=f"工具不存在: {tool_name}",
            duration_ms=_elapsed_ms(),
        )
    except Exception as e:  # noqa: BLE001 — 试运行端点要把任意异常透传给前端
        logger.exception("[tool-registry] execute failed: %s", tool_name)
        return ToolExecuteResponse(
            tool_name=tool_name,
            arguments=args,
            success=False,
            error=str(e),
            duration_ms=_elapsed_ms(),
        )
