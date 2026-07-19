"""Start a persistent batch-analysis run in the API process."""

from __future__ import annotations

from typing import Any

from src.config import get_config
from src.services.watchlist_service import manage_watchlist as _manage_watchlist
from src.tools._workflow import envelope, run_async
from src.tools.base import ToolSpec, object_schema


def run_batch_analysis(
    symbols: str = "",
    scope: str = "symbols",
    analysis_mode: str = "template",
    prompt_template_id: str = "",
    force_refresh: bool = False,
) -> dict[str, Any]:
    from api.v1.endpoints.analysis.trigger import _resolve_and_normalize_input
    from api.v1.endpoints.batches.run import BatchRunTriggerRequest, trigger_batch_run

    if scope == "configured":
        codes = list(get_config().stock_list or [])
    elif scope == "watchlist":
        codes = list((_manage_watchlist("list", []) or {}).get("codes") or [])
    else:
        raw = [part.strip() for part in symbols.split(",") if part.strip()]
        codes = [_resolve_and_normalize_input(part) for part in raw]
    codes = list(dict.fromkeys(codes))
    if not codes:
        raise ValueError("批量分析股票范围为空")
    if len(codes) > 50:
        raise ValueError("单次批量分析最多支持 50 只股票")
    response = run_async(trigger_batch_run(BatchRunTriggerRequest(
        stock_codes=codes,
        template_id=prompt_template_id,
        analysis_mode=analysis_mode,
        force_refresh=bool(force_refresh),
    )))
    return envelope(
        accepted=True,
        scope=scope,
        stock_codes=codes,
        analysis_mode=analysis_mode,
        **dict(response),
    )


TOOL = ToolSpec(
    name="run_batch_analysis",
    description=(
        "启动正式批量分析。只有用户明确要求批量分析且范围清晰时调用；symbols 使用逗号分隔。"
        "scope=watchlist/configured 会使用已保存范围。超过 10 只时应先向用户概述范围并获得确认。"
    ),
    parameters=object_schema({
        "symbols": {"type": "string", "description": "scope=symbols 时必填，逗号分隔"},
        "scope": {"type": "string", "enum": ["symbols", "watchlist", "configured"], "default": "symbols"},
        "analysis_mode": {"type": "string", "enum": ["template", "buy_criteria"], "default": "template"},
        "prompt_template_id": {"type": "string", "description": "template 模式必填"},
        "force_refresh": {"type": "boolean", "default": False},
    }),
    executor=run_batch_analysis,
    category="action",
)


__all__ = ["TOOL", "run_batch_analysis"]
