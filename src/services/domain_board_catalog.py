# -*- coding: utf-8 -*-
"""Bounded live concept-board catalog for semantic domain resolution.

The catalog is context for the task planner, not a company-data source.  It
uses the same complete, cached Eastmoney board feed as the market tools so the
planner can select only board names the execution layer can later verify.
"""

from __future__ import annotations

from typing import Any

import re


def get_domain_board_catalog() -> dict[str, Any]:
    from src.tools.get_sector_flow import get_sector_flow

    result = get_sector_flow(type="concept", period="today", top_n=30)
    records = result.get("records") if isinstance(result, dict) else []
    names = sorted({
        str(item.get("name") or "").strip()
        for item in records or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    })
    return {
        "success": bool(names),
        "board_names": names,
        "board_count": len(names),
        "source": result.get("source") if isinstance(result, dict) else None,
        "data_time": result.get("data_time") if isinstance(result, dict) else None,
        "errors": list(result.get("errors") or []) if isinstance(result, dict) else ["板块目录格式异常"],
        "warnings": list(result.get("warnings") or []) if isinstance(result, dict) else [],
    }


def shortlist_domain_boards(
    board_names: list[str],
    *texts: str,
    limit: int = 72,
) -> list[str]:
    """Select a compact semantic-choice catalog without fixing any industry.

    The full catalog remains the validation authority.  This function only
    reduces model context by ranking live board names against the current
    request and referenced answer using exact containment and character
    n-gram overlap.  It never decides which board represents a domain.
    """

    def compact(value: str) -> str:
        return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "")).lower()

    corpus = compact("\n".join(str(value or "") for value in texts))
    corpus_bigrams = {corpus[index:index + 2] for index in range(max(0, len(corpus) - 1))}
    ranked: list[tuple[int, int, str]] = []
    for board_name in dict.fromkeys(str(value or "").strip() for value in board_names):
        board = compact(board_name)
        if not board:
            continue
        bigrams = {board[index:index + 2] for index in range(max(0, len(board) - 1))}
        overlap = len(bigrams & corpus_bigrams)
        exact = board in corpus
        # Exact mentions dominate.  Otherwise require at least one meaningful
        # two-character overlap so unrelated catalog sections never consume
        # the semantic planner's context budget.
        if not exact and overlap == 0:
            continue
        score = (10_000 + len(board) * 10) if exact else (overlap * 100 + len(board))
        ranked.append((score, overlap, board_name))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [item[2] for item in ranked[:max(12, min(int(limit), 120))]]


__all__ = ["get_domain_board_catalog", "shortlist_domain_boards"]
