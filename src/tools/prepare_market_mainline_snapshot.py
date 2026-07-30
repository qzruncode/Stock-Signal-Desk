"""Prepare one evidence-bound market-mainline snapshot for research workflows."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.services.market_theme_service import MarketThemeService
from src.tools.base import ToolSpec, TypedToolResult, report_tool_progress

DESCRIPTION = (
    "生成或复用一次证据绑定的 A 股市场主线快照，供未来主线研判或"
    "逐股八维买入判断共享。它区分当前已确认主线与未来候选主线，"
    "并保留政策、产业、技术、资本开支、机构共识及触发条件证据；"
    "不判断任何个股，也不把候选方向写成确定结果。"
)


class PrepareMarketMainlineSnapshotArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False


class PrepareMarketMainlineSnapshotResult(TypedToolResult):
    available: bool
    generation_pending: bool
    report_pending: bool
    snapshot_id: str | None = None
    generation_task: dict[str, Any] | None = None
    contract_version: str | None = None
    generated_at: str | None = None
    as_of_date: str | None = None
    overview: Any = None
    market_stage: Any = None
    current_mainlines: list[Any] = Field(default_factory=list)
    candidate_mainlines: list[Any] = Field(default_factory=list)
    future_mainlines: list[Any] = Field(default_factory=list)
    action_summary: Any = None
    evidence_digest: Any = None
    source_summary: Any = None
    validation: Any = None
    llm_used: Any = None
    model_used: Any = None


def _compact_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: report.get(key)
        for key in (
            "contract_version",
            "generated_at",
            "as_of_date",
            "overview",
            "market_stage",
            "current_mainlines",
            "candidate_mainlines",
            "future_mainlines",
            "action_summary",
            "evidence_digest",
            "source_summary",
            "validation",
            "llm_used",
            "model_used",
        )
        if report.get(key) is not None
    }


def prepare_market_mainline_snapshot(
    force: bool = False,
) -> dict[str, Any]:
    service = MarketThemeService()
    try:
        report = service.ensure_model_report_inline(
            force=force,
            on_progress=lambda progress, message, reasoning_delta: (
                report_tool_progress(
                    message,
                    progress=progress,
                    reasoning_delta=reasoning_delta,
                )
            ),
        )
    except Exception as exc:
        message = (
            "市场主线快照生成失败："
            f"{type(exc).__name__}: {str(exc)[:220]}"
        )
        return {
            "success": False,
            "partial": False,
            "available": False,
            "generation_pending": False,
            "report_pending": True,
            "errors": [message],
            "warnings": [],
            "data_time": None,
            "is_stale": None,
        }
    available = bool(
        not report.get("report_pending")
        and report.get("as_of_date")
        and (
            report.get("current_mainlines")
            or report.get("candidate_mainlines")
            or report.get("future_mainlines")
        )
    )
    if not available:
        task = (
            report.get("generation_task")
            if isinstance(report.get("generation_task"), dict)
            else {}
        )
        if report.get("generation_failed"):
            reason = str(
                report.get("generation_error")
                or task.get("error")
                or "市场主线生成任务失败"
            )
        else:
            reason = "当日市场主线报告没有形成可用主线"
        return {
            "success": False,
            "partial": False,
            "available": False,
            "generation_pending": str(task.get("status") or "") in {
                "pending",
                "processing",
            },
            "generation_task": task or None,
            "as_of_date": None,
            "overview": None,
            "current_mainlines": [],
            "report_pending": True,
            "errors": [reason],
            "warnings": [],
            "data_time": None,
            "is_stale": None,
        }
    snapshot = _compact_report(report)
    generated_at = str(snapshot.get("generated_at") or "")
    as_of_date = str(snapshot.get("as_of_date") or "")
    contract_version = str(snapshot.get("contract_version") or "")
    return {
        "success": True,
        "partial": False,
        "available": True,
        "generation_pending": False,
        "snapshot_id": ":".join(
            value for value in (contract_version, as_of_date, generated_at)
            if value
        ),
        **snapshot,
        "report_pending": False,
        "errors": [],
        "warnings": [],
        "data_time": generated_at or as_of_date,
        "is_stale": False,
    }


TOOL = ToolSpec(
    name="prepare_market_mainline_snapshot",
    description=DESCRIPTION,
    parameters=None,
    executor=prepare_market_mainline_snapshot,
    category="market",
    args_model=PrepareMarketMainlineSnapshotArgs,
    result_model=PrepareMarketMainlineSnapshotResult,
)


__all__ = ["TOOL", "prepare_market_mainline_snapshot"]
