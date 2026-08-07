#!/usr/bin/env python3
"""Fail-closed production preflight for the LangGraph Agent runtime."""

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

from scripts.check_agent_architecture import audit_architecture  # noqa: E402
from src.agent.runtime_safety import agent_production_issues, is_production_environment  # noqa: E402
from src.storage import DatabaseManager  # noqa: E402


_ASSET_REF_PATTERN = re.compile(r"""(?:src|href)\s*=\s*["'](/assets/[^"']+)["']""", re.I)


def _missing_frontend_assets(static_dir: Path) -> list[str]:
    index_path = static_dir / "index.html"
    if not index_path.is_file():
        return []
    html = index_path.read_text(encoding="utf-8", errors="replace")
    return sorted(
        {
            ref
            for ref in _ASSET_REF_PATTERN.findall(html)
            if not (static_dir / ref.lstrip("/")).is_file()
        }
    )


def main() -> int:
    static_dir = PROJECT_ROOT / "static"
    issues = agent_production_issues(static_dir)
    architecture = audit_architecture()
    issues.extend(str(item) for item in architecture["issues"])
    if not is_production_environment():
        issues.append("APP_ENV=production or DSA_PRODUCTION=true is required for production preflight")
    issues.extend(
        f"frontend bundle references a missing asset: {item}"
        for item in _missing_frontend_assets(static_dir)
    )
    try:
        db_manager = DatabaseManager.get_instance()
        with db_manager.get_session() as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        issues.append(f"database is unavailable: {type(exc).__name__}: {exc}")

    result = {
        "ok": not issues,
        "engine": "langgraph",
        "static_dir": str(static_dir),
        "registered_tools": architecture["registered_tools"],
        "graph_nodes": architecture["graph_nodes"],
        "issues": list(dict.fromkeys(issues)),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
