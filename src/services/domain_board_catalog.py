# -*- coding: utf-8 -*-
"""Bounded live concept-board catalog for semantic domain resolution.

The catalog is context for the task planner, not a company-data source.  It
uses the same complete, cached Eastmoney board feed as the market tools so the
planner can select only board names the execution layer can later verify.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def domain_board_catalog_snapshot_id(
    boards: list[dict[str, Any]],
) -> str:
    """Fingerprint catalog identity without volatile market-flow fields."""
    identities = sorted(
        {
            (
                str(item.get("sector_code") or "").strip(),
                str(item.get("name") or "").strip(),
            )
            for item in boards
            if str(item.get("sector_code") or "").strip() and str(item.get("name") or "").strip()
        }
    )
    payload = [{"board_id": board_id, "name": name} for board_id, name in identities]
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]


def get_domain_board_catalog() -> dict[str, Any]:
    from src.tools.get_sector_flow import get_sector_flow

    result = get_sector_flow(type="concept", period="today", top_n=30)
    records = result.get("records") if isinstance(result, dict) else []
    by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for item in records or []:
        if not isinstance(item, dict):
            continue
        board_id = str(item.get("sector_code") or "").strip()
        name = str(item.get("name") or "").strip()
        if not board_id or not name:
            continue
        by_identity.setdefault(
            (board_id, name),
            {
                "sector_code": board_id,
                "name": name,
                "main_flow_rank": item.get("main_flow_rank"),
                "main_net_inflow": item.get("main_net_inflow"),
                "main_net_inflow_pct": item.get("main_net_inflow_pct"),
                "pct_chg": item.get("pct_chg"),
            },
        )
    boards = [by_identity[identity] for identity in sorted(by_identity)]
    names = sorted({item["name"] for item in boards})
    return {
        "success": bool(names),
        "boards": boards,
        "board_names": names,
        "board_count": len(boards),
        "catalog_snapshot_id": domain_board_catalog_snapshot_id(boards),
        "source": result.get("source") if isinstance(result, dict) else None,
        "data_time": result.get("data_time") if isinstance(result, dict) else None,
        "errors": list(result.get("errors") or []) if isinstance(result, dict) else ["板块目录格式异常"],
        "warnings": list(result.get("warnings") or []) if isinstance(result, dict) else [],
    }


__all__ = [
    "domain_board_catalog_snapshot_id",
    "get_domain_board_catalog",
]
