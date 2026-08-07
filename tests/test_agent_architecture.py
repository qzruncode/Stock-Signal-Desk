from __future__ import annotations

from pathlib import Path

from scripts.check_agent_architecture import (
    BANNED_FILES,
    BANNED_REGISTERED_TOOLS,
    PROJECT_ROOT,
    audit_architecture,
)
from src.tools.registry import ToolRegistry


def test_only_generic_langgraph_control_plane_is_executable() -> None:
    result = audit_architecture()
    assert result["ok"] is True, result["issues"]
    assert result["engine"] == "langgraph"


def test_removed_control_plane_files_do_not_return() -> None:
    assert not [relative for relative in BANNED_FILES if (PROJECT_ROOT / relative).exists()]
    legacy_root = Path(PROJECT_ROOT) / "src/agent/orchestrator_v2"
    assert not list(legacy_root.glob("*.py"))


def test_composite_sop_tools_are_not_registered() -> None:
    registered = set(ToolRegistry().get_tool_names())
    assert registered.isdisjoint(BANNED_REGISTERED_TOOLS)
