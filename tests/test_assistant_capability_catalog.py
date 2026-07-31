from __future__ import annotations

import re
from pathlib import Path

from src.tools.registry import TOOL_MODULES
from scripts.agent_tool_audit import schema_only_tools, tool_cases


def test_frontend_capability_catalog_covers_every_registered_agent_tool() -> None:
    catalog_path = Path(__file__).parents[1] / "apps" / "dsa-web" / "src" / "utils" / "assistantQuickActions.ts"
    catalog = catalog_path.read_text(encoding="utf-8")
    mapped_tools = re.findall(r"toolName: '([^']+)'", catalog)

    assert len(mapped_tools) == len(set(mapped_tools))
    assert set(mapped_tools) == set(TOOL_MODULES)


def test_production_tool_audit_covers_every_registered_agent_tool() -> None:
    executable = set(tool_cases())
    schema_only = schema_only_tools()

    assert not executable & schema_only
    assert executable | schema_only == set(TOOL_MODULES)
