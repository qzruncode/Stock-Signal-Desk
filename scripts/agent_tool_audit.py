#!/usr/bin/env python3
"""Static audit of the model-visible atomic tool catalog.

The old audit executed tools through a direct HTTP endpoint. That endpoint no
longer exists because every invocation must pass through LangGraph policy and,
for side effects, an interrupt approval.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.check_agent_architecture import audit_architecture  # noqa: E402
from src.tools.registry import ToolRegistry  # noqa: E402


def main() -> int:
    architecture = audit_architecture()
    registry = ToolRegistry()
    tools = []
    for name in registry.get_tool_names():
        spec = registry.get_tool(name)
        assert spec is not None
        tools.append(
            {
                "name": name,
                "effect": spec.effect,
                "dynamic_effect": spec.effect_resolver is not None,
                "approval_policy": spec.approval_policy,
                "timeout_seconds": spec.timeout_seconds,
                "max_attempts": spec.max_attempts,
                "idempotent": spec.idempotent,
                "server_fields_hidden": not bool(
                    set(spec.model_parameters().get("properties") or {})
                    & set(spec.server_controlled_fields)
                ),
            }
        )
    result = {**architecture, "tools": tools}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
