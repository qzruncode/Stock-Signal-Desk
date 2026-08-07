# -*- coding: utf-8 -*-
"""Shenwan industry-index catalog and constituent evidence."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from src.services.akshare_evidence.common import fetch_frame


def _normalized(value: Any) -> str:
    return re.sub(r"[\s行业指数板块概念产业]", "", str(value or "")).casefold()


def get_industry_index_context(
    query: str = "",
    index_type: str = "一级行业",
    index_code: str = "",
    include_components: bool = True,
    max_matches: int = 5,
    history_points: int = 120,
) -> dict[str, Any]:
    import akshare as ak

    allowed = {"市场表征", "一级行业", "二级行业", "风格指数", "大类风格指数", "金创指数"}
    if index_type not in allowed:
        raise ValueError("index_type 不在申万公开指数类别中")
    max_matches = max(1, min(int(max_matches), 20))
    history_points = max(20, min(int(history_points), 500))
    catalog = fetch_frame(
        "index_realtime_sw",
        f"industry:sw_realtime:{index_type}",
        lambda: ak.index_realtime_sw(symbol=index_type),
        ttl_seconds=1800,
    )
    catalog_items = catalog.get("items") or []
    code_text = str(index_code or "").strip()
    query_text = _normalized(query)
    matches: list[dict[str, Any]] = []
    for item in catalog_items:
        code = str(item.get("指数代码") or item.get("代码") or "").strip()
        name = str(item.get("指数名称") or item.get("名称") or "").strip()
        if code_text and code == code_text:
            score = 3
        elif query_text and _normalized(name) == query_text:
            score = 2
        elif query_text and (query_text in _normalized(name) or _normalized(name) in query_text):
            score = 1
        elif not code_text and not query_text:
            score = 0
        else:
            continue
        matches.append({"match_score": score, **item})
    matches.sort(key=lambda item: (-int(item.get("match_score") or 0), str(item.get("指数代码") or "")))
    matches = matches[:max_matches]

    components: dict[str, dict[str, Any]] = {}
    histories: dict[str, dict[str, Any]] = {}
    for match in matches:
        code = str(match.get("指数代码") or match.get("代码") or "").strip()
        if not code:
            continue
        history = fetch_frame(
            "index_hist_sw",
            f"industry:sw_history:{code}:day",
            lambda code=code: ak.index_hist_sw(symbol=code, period="day"),
            ttl_seconds=6 * 3600,
        )
        history["items"] = (history.get("items") or [])[-history_points:]
        history["item_count"] = len(history["items"])
        histories[code] = history
    if include_components:
        for match in matches:
            code = str(match.get("指数代码") or match.get("代码") or "").strip()
            if not code:
                continue
            components[code] = fetch_frame(
                "index_component_sw",
                f"industry:sw_components:{code}",
                lambda code=code: ak.index_component_sw(symbol=code),
                ttl_seconds=12 * 3600,
            )
    errors = []
    if catalog.get("error"):
        errors.append(f"catalog: {catalog['error']}")
    errors.extend(
        f"histories.{code}: {result['error']}"
        for code, result in histories.items()
        if result.get("error")
    )
    errors.extend(
        f"components.{code}: {result['error']}"
        for code, result in components.items()
        if result.get("error")
    )
    success = catalog.get("success") is True
    observed_at = datetime.now().astimezone().isoformat()
    return {
        "query": query.strip() or None,
        "index_type": index_type,
        "requested_index_code": code_text or None,
        "matches": matches,
        "match_count": len(matches),
        "histories": histories,
        "components": components,
        "catalog": catalog if not (query_text or code_text) else {key: value for key, value in catalog.items() if key != "items"},
        "retrieval_only": True,
        "membership_boundary": "指数成分关系只证明申万分类归属，不证明订单、收入或投资价值。",
        "source": "AKShare/申万宏源研究指数",
        "coverage_complete": success and not errors,
        "success": success,
        "partial": success and bool(errors),
        "errors": errors,
        "warnings": ([] if matches else ["未找到匹配指数；可先读取同类别目录后再指定指数代码。"]),
        "data_time": observed_at if success else None,
        "retrieved_at": observed_at,
        "is_stale": False if success else None,
        "freshness_unknown": not success,
    }


__all__ = ["get_industry_index_context"]
