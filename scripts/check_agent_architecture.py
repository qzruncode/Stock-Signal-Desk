#!/usr/bin/env python3
"""Static guardrails for the generic message-and-tool Agent architecture."""

from __future__ import annotations

import ast
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


BANNED_FILES = (
    "src/agent/task_planner.py",
    "src/agent/task_executor.py",
    "src/agent/task_workflows.py",
    "src/agent/workflow_registry.py",
    "src/agent/pipeline_planning.py",
    "src/agent/pipeline_execution.py",
    "src/agent/pipeline_finalization.py",
    "api/v1/endpoints/agent/governance.py",
    "api/v1/endpoints/agent/chat_streaming.py",
    "api/v1/endpoints/agent/revalidation.py",
)

BANNED_DIRECTORIES = (
    "src/agent/orchestrator_v2",
)

BANNED_FRONTEND_PATHS = (
    "apps/dsa-web/src/hooks/useAssistantTools.tsx",
    "apps/dsa-web/src/components/assistant-ui/tool-ui",
    "apps/dsa-web/src/utils/toolLabels.ts",
    "apps/dsa-web/src/utils/toolResults.ts",
)

# These were either composite SOP tools or the old per-provider generated tool
# surface.  Source implementations may remain as internal adapters, but none
# of these names may be model-callable through ToolRegistry.
BANNED_REGISTERED_TOOLS = frozenset(
    {
        "filter_watchlist_by_theme",
        "run_stock_analysis",
        "run_batch_analysis",
        "get_multi_stock_snapshot",
        "get_multi_stock_financials",
        "get_multi_stock_decision_evidence",
        "evaluate_multi_stock_buy_criteria",
        "analyze_stock_catalysts",
        "search_financial_news",
        "search_research_library",
        "get_realtime_quotes",
        "get_kline",
        "get_history_data",
        "get_technical_indicators",
        "websearch",
        "webfetch",
        "search_news",
        "read_rss_feed",
        "discover_rss_sources",
        "inspect_rss_source",
        "read_realtime_quote_eastmoney_push",
        "read_realtime_quote_sina",
        "read_realtime_quote_tencent",
        "read_realtime_quote_xueqiu",
        "read_recent_kline_eastmoney",
        "read_recent_kline_sina",
        "read_recent_kline_tencent",
        "read_kline_range_eastmoney",
        "read_kline_range_sina",
        "read_kline_range_tencent",
        "search_web_firecrawl_searxng",
        "search_web_exa",
        "search_web_parallel",
        "read_web_http",
        "read_web_scrapling",
        "read_web_patchright",
        "read_web_firecrawl",
    }
)

REQUIRED_SOURCE_OPERATIONS = frozenset(
    {
        "read_rss_source",
        "list_rss_source_catalog",
        "read_realtime_quote",
        "read_recent_kline",
        "read_kline_range",
        "calculate_technical_indicator",
        "search_web_source",
        "read_web_source",
    }
)

# Source adapters are intentionally implementation-only.  The generic
# ``source_operations`` module is the one model registration surface; these
# files must never grow a second per-provider ToolSpec catalogue.
INTERNAL_SOURCE_ADAPTERS = (
    "src/tools/realtime_quote_source_tools.py",
    "src/tools/kline_source_tools.py",
    "src/tools/technical_indicator_source_tools.py",
    "src/tools/rss_route_tools.py",
    "src/tools/web_source_tools.py",
)

_BANNED_CONTROL_SYMBOLS = frozenset(
    {
        "Capability",
        "GoalContract",
        "WorkflowExecutor",
        "WORKFLOW_REGISTRY",
        "_run_standard_task_pipeline",
    }
)


def _banned_control_symbols_for(path: Path) -> frozenset[str]:
    relative = path.relative_to(PROJECT_ROOT)
    if relative.parts[:4] == ("src", "agent", "langgraph_runtime", "goal"):
        return _BANNED_CONTROL_SYMBOLS - {"GoalContract"}
    return _BANNED_CONTROL_SYMBOLS


def _names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for item in ast.walk(tree):
        if isinstance(item, ast.Name):
            names.add(item.id)
        elif isinstance(item, ast.Attribute):
            names.add(item.attr)
    return names


def _class_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {item.name for item in ast.walk(tree) if isinstance(item, ast.ClassDef)}


def _schema_enum_values(schema: Any) -> set[str]:
    """Read enum values from direct or nullable Pydantic JSON-schema forms."""
    if not isinstance(schema, dict):
        return set()
    values = schema.get("enum")
    if isinstance(values, list):
        return {str(value) for value in values}
    for key in ("anyOf", "oneOf"):
        choices = schema.get(key)
        if isinstance(choices, list):
            for choice in choices:
                values = _schema_enum_values(choice)
                if values:
                    return values
    return set()


def audit_architecture() -> dict[str, Any]:
    from src.agent.langgraph_runtime.catalog import ToolCatalog
    from src.tools.registry import TOOL_MODULES, ToolRegistry
    from src.tools.source_operations import RSS_SOURCE_CATALOG

    issues: list[str] = []
    for relative in BANNED_FILES:
        if (PROJECT_ROOT / relative).is_file():
            issues.append(f"removed control-plane file exists: {relative}")
    for relative in BANNED_DIRECTORIES:
        if (PROJECT_ROOT / relative).exists():
            issues.append(f"removed control-plane directory exists: {relative}")
    for relative in BANNED_FRONTEND_PATHS:
        if (PROJECT_ROOT / relative).exists():
            issues.append(f"removed fixed frontend renderer exists: {relative}")

    graph_path = PROJECT_ROOT / "src/agent/langgraph_runtime/graph.py"
    graph_text = graph_path.read_text(encoding="utf-8")
    if "create_agent(" not in graph_text:
        issues.append("runtime graph does not use langchain.agents.create_agent")
    if "StateGraph(" in graph_text or ".add_node(" in graph_text:
        issues.append("runtime graph still hand-compiles a semantic business DAG")

    for relative in INTERNAL_SOURCE_ADAPTERS:
        names = _names(PROJECT_ROOT / relative)
        leaked = sorted(names & {"ToolSpec", "TOOLS"})
        if leaked:
            issues.append(
                "internal source adapter still exports a model tool surface: "
                + relative
                + ": "
                + ", ".join(leaked)
            )

    for path in (PROJECT_ROOT / "src/agent").rglob("*.py"):
        names = _names(path)
        leaked = sorted(names & _banned_control_symbols_for(path))
        if leaked:
            issues.append(f"removed fixed-control symbol in {path.relative_to(PROJECT_ROOT)}: {', '.join(leaked)}")

    governance_models = PROJECT_ROOT / "src/storage/_models_agent_governance.py"
    legacy_governance = _class_names(governance_models) & {
        "AgentCapabilityGrant",
        "AgentCapabilityRelease",
    }
    if legacy_governance:
        issues.append(
            "legacy capability registry models exist: " + ", ".join(sorted(legacy_governance))
        )

    registry = ToolRegistry()
    registered = set(registry.get_tool_names())
    banned = sorted(registered & BANNED_REGISTERED_TOOLS)
    if banned:
        issues.append("composite or per-source tools are registered: " + ", ".join(banned))
    missing = sorted(REQUIRED_SOURCE_OPERATIONS - registered)
    if missing:
        issues.append("generic source operations are missing: " + ", ".join(missing))

    owners = {name: registry.get_tool_owner_module(name) for name in registry.get_tool_names()}
    if any(owner is None for owner in owners.values()):
        issues.append("a registered tool has no owner module")
    unloaded = sorted(set(TOOL_MODULES) - {str(owner) for owner in owners.values()})
    if unloaded:
        issues.append("tool module contributed no operation: " + ", ".join(unloaded))

    rss = registry.get_tool("read_rss_source")
    if rss is None:
        issues.append("read_rss_source is unavailable")
    else:
        source_ids = {str(item.get("id") or "") for item in rss.source_catalog}
        schema_ids = _schema_enum_values(
            rss.model_parameters().get("properties", {}).get("source_id", {})
        )
        if len(source_ids) != len(RSS_SOURCE_CATALOG):
            issues.append(f"RSS source directory count differs: {len(source_ids)}")
        if source_ids != schema_ids:
            issues.append("read_rss_source schema and source directory diverge")

    for name in registry.get_tool_names():
        spec = registry.get_tool(name)
        if spec is None:
            issues.append(f"registered operation cannot be loaded: {name}")
            continue
        if spec.effect not in {"read", "side_effect"}:
            issues.append(f"operation has invalid effect metadata: {name}")
        if spec.approval_policy != "required_for_side_effect":
            issues.append(f"operation has invalid approval policy: {name}")
        if spec.max_attempts < 1:
            issues.append(f"operation has invalid retry metadata: {name}")
        fields = set((spec.model_parameters().get("properties") or {}).keys())
        if "action" in fields or "workflow" in fields or "capability" in fields:
            issues.append(f"operation exposes a workflow selector: {name}")
        if "source_id" in fields and not spec.source_catalog:
            issues.append(f"operation exposes undeclared source_id: {name}")
        if spec.source_catalog:
            ids = {str(item.get("id") or "") for item in spec.source_catalog}
            enum = _schema_enum_values(
                spec.model_parameters().get("properties", {}).get("source_id", {})
            )
            if not ids or ids != enum:
                issues.append(f"operation source catalog/schema mismatch: {name}")
        leaked_controls = fields & set(spec.server_controlled_fields)
        if leaked_controls:
            issues.append(f"operation exposes server controls: {name}: {', '.join(sorted(leaked_controls))}")

    catalog = ToolCatalog(registry)
    model_visible = {str(item.get("operation") or "") for item in catalog.compact_catalog()}
    if model_visible != registered:
        issues.append("model operation directory does not expose every registered operation")
    if hasattr(catalog, "load_schemas") or callable(getattr(catalog, "search", None)):
        issues.append("model operation directory still exposes a pre-ranking/schema gate")

    from src.agent.langgraph_runtime.agent_tools import build_langchain_tools

    bound = {str(tool.name) for tool in build_langchain_tools(registry)}
    if bound != registered:
        issues.append("LangChain tool binding does not expose every registered operation")

    return {
        "ok": not issues,
        "issues": issues,
        "engine": "langgraph_agent_loop",
        "operation_count": len(registered),
        "rss_source_count": len(RSS_SOURCE_CATALOG),
    }


def main() -> int:
    report = audit_architecture()
    if report["ok"]:
        print(f"architecture check passed ({report['operation_count']} operations, {report['rss_source_count']} RSS sources)")
        return 0
    for issue in report["issues"]:
        print(f"architecture check failed: {issue}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
