#!/usr/bin/env python3
"""Fail when the removed fixed Agent architecture becomes executable again."""

from __future__ import annotations

import ast
import json
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
)

BANNED_REGISTERED_TOOLS = frozenset(
    {
        "filter_watchlist_by_theme",
        "run_stock_analysis",
        "run_batch_analysis",
        "get_multi_stock_snapshot",
        "get_multi_stock_decision_evidence",
        "prepare_market_mainline_snapshot",
        "evaluate_market_mainline_gate",
        "evaluate_multi_stock_buy_criteria",
        "analyze_stock_catalysts",
        "get_domain_stock_candidates",
        "get_company_theme_evidence",
        "get_risk_events",
        "get_company_structured_evidence",
        "screen_atr_volatility_stocks",
        "get_market_regime",
        "get_industry_index_context",
        "search_financial_news",
        "search_research_library",
        "get_regulatory_updates",
        "get_monetary_policy_operations",
        "get_social_sentiment",
        "get_sector_list",
        "list_financial_sources",
        "inspect_financial_source",
        "read_financial_feed",
        "read_financial_article",
        "export_financial_feed",
    }
)

BANNED_CONTROL_SYMBOLS = (
    "Capability",
    "GoalContract",
    "WorkflowExecutor",
    "WORKFLOW_REGISTRY",
    "_run_standard_task_pipeline",
    "orchestrator_v2",
)

EXPECTED_GRAPH_NODES = frozenset(
    {
        "begin",
        "understand",
        "clarify",
        "discover",
        "plan",
        "policy",
        "execute_read_action",
        "approval",
        "execute_approved_action",
        "reflect",
        "draft",
        "verify",
        "publish",
    }
)


def _graph_nodes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes: set[str] = set()
    for item in ast.walk(tree):
        if not isinstance(item, ast.Call):
            continue
        function = item.func
        if not (
            isinstance(function, ast.Attribute)
            and function.attr == "add_node"
            and item.args
            and isinstance(item.args[0], ast.Constant)
            and isinstance(item.args[0].value, str)
        ):
            continue
        nodes.add(item.args[0].value)
    return nodes


def _defines_tool_export(path: Path) -> bool:
    """Return whether a module can still be loaded as a registry tool."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for item in tree.body:
        if isinstance(item, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "TOOL"
            for target in item.targets
        ):
            return True
        if (
            isinstance(item, ast.AnnAssign)
            and isinstance(item.target, ast.Name)
            and item.target.id == "TOOL"
        ):
            return True
    return False


def _registered_tool_dependencies(path: Path, registered: set[str]) -> set[str]:
    """Find public imports from another model-callable tool module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    dependencies: set[str] = set()
    own_name = path.stem
    for item in ast.walk(tree):
        if isinstance(item, ast.ImportFrom):
            module = str(item.module or "")
            if module == "src.tools":
                for alias in item.names:
                    if alias.name in registered and alias.name != own_name:
                        dependencies.add(alias.name)
                continue
            prefix = "src.tools."
            if not module.startswith(prefix):
                continue
            dependency = module[len(prefix) :].split(".", 1)[0]
            if dependency not in registered or dependency == own_name:
                continue
            if any(not alias.name.startswith("_") for alias in item.names):
                dependencies.add(dependency)
        elif isinstance(item, ast.Import):
            for alias in item.names:
                prefix = "src.tools."
                if not alias.name.startswith(prefix):
                    continue
                dependency = alias.name[len(prefix) :].split(".", 1)[0]
                if dependency in registered and dependency != own_name:
                    dependencies.add(dependency)
    return dependencies


def audit_architecture() -> dict[str, Any]:
    from src.tools.registry import TOOL_MODULES, ToolRegistry

    issues: list[str] = []
    for relative in BANNED_FILES:
        if (PROJECT_ROOT / relative).is_file():
            issues.append(f"removed control-plane file exists: {relative}")
    legacy_python = sorted(
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / "src/agent/orchestrator_v2").glob("*.py")
    )
    if legacy_python:
        issues.append("legacy orchestrator modules exist: " + ", ".join(legacy_python))

    registry = ToolRegistry()
    registered = set(registry.get_tool_names())
    banned_registered = sorted(registered & BANNED_REGISTERED_TOOLS)
    if banned_registered:
        issues.append("composite tools are registered: " + ", ".join(banned_registered))
    if registered != set(TOOL_MODULES):
        issues.append("tool registry names do not exactly match TOOL_MODULES")

    for name in sorted(BANNED_REGISTERED_TOOLS):
        module_path = PROJECT_ROOT / "src/tools" / f"{name}.py"
        if module_path.is_file() and _defines_tool_export(module_path):
            issues.append(f"banned composite/alias module still exports TOOL: {name}")

    for name in sorted(registered):
        module_path = PROJECT_ROOT / "src/tools" / f"{name}.py"
        dependencies = _registered_tool_dependencies(module_path, registered)
        if dependencies:
            issues.append(
                f"registered tool depends on another registered tool: {name} -> "
                + ", ".join(sorted(dependencies))
            )
        spec = registry.get_tool(name)
        if spec is None:
            issues.append(f"registered tool cannot be loaded: {name}")
            continue
        if spec.effect not in {"read", "side_effect"}:
            issues.append(f"tool has invalid effect metadata: {name}")
        if spec.approval_policy != "required_for_side_effect":
            issues.append(f"tool has invalid approval policy: {name}")
        if spec.timeout_seconds is None or spec.timeout_seconds <= 0:
            issues.append(f"tool has no positive timeout: {name}")
        if spec.max_attempts < 1:
            issues.append(f"tool has invalid retry metadata: {name}")
        schema = spec.model_parameters()
        visible = set((schema.get("properties") or {}).keys())
        leaked = sorted(visible & set(spec.server_controlled_fields))
        if leaked:
            issues.append(f"tool exposes server-controlled fields: {name}: {', '.join(leaked)}")

    active_files = (
        PROJECT_ROOT / "src/agent/langgraph_runtime",
        PROJECT_ROOT / "api/v1/endpoints/agent/chat.py",
        PROJECT_ROOT / "api/v1/endpoints/agent/chat_background_runner.py",
        PROJECT_ROOT / "src/tools/registry.py",
    )
    for target in active_files:
        paths = sorted(target.rglob("*.py")) if target.is_dir() else [target]
        for path in paths:
            source = path.read_text(encoding="utf-8")
            for symbol in BANNED_CONTROL_SYMBOLS:
                if symbol in source:
                    issues.append(
                        f"banned legacy symbol {symbol!r} in "
                        f"{path.relative_to(PROJECT_ROOT).as_posix()}"
                    )

    graph_path = PROJECT_ROOT / "src/agent/langgraph_runtime/graph.py"
    graph_nodes = _graph_nodes(graph_path)
    if graph_nodes != EXPECTED_GRAPH_NODES:
        issues.append(
            "graph nodes differ from the generic control loop: "
            f"actual={sorted(graph_nodes)}"
        )

    registry_endpoint = (
        PROJECT_ROOT / "api/v1/endpoints/agent/tool_registry_meta.py"
    ).read_text(encoding="utf-8")
    if "@router.post" in registry_endpoint or "registry.execute(" in registry_endpoint:
        issues.append("tool registry metadata endpoint exposes direct execution")

    executor_source = (
        PROJECT_ROOT / "src/agent/langgraph_runtime/executor.py"
    ).read_text(encoding="utf-8")
    if "requires an approved interrupt" not in executor_source:
        issues.append("side-effect executor approval guard is missing")
    approval_source = (
        PROJECT_ROOT / "src/agent/langgraph_runtime/graph.py"
    ).read_text(encoding="utf-8")
    if "interrupt(" not in approval_source or "execute_approved_action" not in approval_source:
        issues.append("LangGraph interrupt approval path is missing")

    return {
        "ok": not issues,
        "engine": "langgraph",
        "registered_tools": len(registered),
        "graph_nodes": sorted(graph_nodes),
        "issues": issues,
    }


def main() -> int:
    result = audit_architecture()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
