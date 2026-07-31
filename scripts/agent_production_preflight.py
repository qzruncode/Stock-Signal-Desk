#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed production preflight for the interactive AI Assistant."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import setup_env  # noqa: E402

setup_env()

from sqlalchemy import text  # noqa: E402

from src.agent.runtime_safety import (  # noqa: E402
    agent_production_issues,
    is_production_environment,
)
from src.storage import DatabaseManager  # noqa: E402
from src.agent.task_workflows import (  # noqa: E402
    StandardTaskKind,
    WORKFLOW_REGISTRY,
    registered_workflow_tools,
)
from src.tools.registry import ToolRegistry  # noqa: E402


_ASSET_REF_PATTERN = re.compile(r"""(?:src|href)\s*=\s*["'](/assets/[^"']+)["']""", re.I)


def _missing_frontend_assets(static_dir: Path) -> list[str]:
    index_path = static_dir / "index.html"
    if not index_path.is_file():
        return []
    html = index_path.read_text(encoding="utf-8", errors="replace")
    return sorted({ref for ref in _ASSET_REF_PATTERN.findall(html) if not (static_dir / ref.lstrip("/")).is_file()})


def main() -> int:
    static_dir = PROJECT_ROOT / "static"
    issues = agent_production_issues(static_dir)
    if not is_production_environment():
        issues.append("APP_ENV=production or DSA_PRODUCTION=true is required for production preflight")
    for missing_asset in _missing_frontend_assets(static_dir):
        issues.append(f"frontend bundle references a missing asset: {missing_asset}")

    try:
        db_manager = DatabaseManager.get_instance()
        with db_manager.get_session() as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - preflight must aggregate failures
        issues.append(f"database is unavailable: {type(exc).__name__}: {exc}")

    try:
        tool_names = set(ToolRegistry().get_tool_names())
        registered_tools = len(tool_names)
        if registered_tools <= 0:
            issues.append("tool registry is empty")
        workflow_tools = set(registered_workflow_tools())
        missing_workflow_tools = sorted(tool_names - workflow_tools)
        stale_workflow_tools = sorted(workflow_tools - tool_names)
        if missing_workflow_tools:
            issues.append("registered tools missing from fixed workflows: " + ", ".join(missing_workflow_tools))
        if stale_workflow_tools:
            issues.append("fixed workflows reference unregistered tools: " + ", ".join(stale_workflow_tools))
        missing_task_kinds = sorted(kind.value for kind in set(StandardTaskKind) - set(WORKFLOW_REGISTRY))
        if missing_task_kinds:
            issues.append("standard task kinds missing from workflow registry: " + ", ".join(missing_task_kinds))
        oversized_workflows = sorted(
            kind.value
            for kind, spec in WORKFLOW_REGISTRY.items()
            if len(spec.tool_whitelist) > 8 or spec.max_tool_calls > 8
        )
        if oversized_workflows:
            issues.append("fixed workflows exceed the 8-tool/call policy: " + ", ".join(oversized_workflows))
    except Exception as exc:  # noqa: BLE001
        registered_tools = 0
        issues.append(f"tool registry initialization failed: {type(exc).__name__}: {exc}")

    result = {
        "ok": not issues,
        "static_dir": str(static_dir),
        "registered_tools": registered_tools,
        "standard_tasks": len(StandardTaskKind),
        "fixed_workflows": len(WORKFLOW_REGISTRY),
        "issues": issues,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
