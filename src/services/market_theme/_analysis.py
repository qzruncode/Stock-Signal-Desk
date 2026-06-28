# -*- coding: utf-8 -*-
"""Rule-based analysis and response assembly for summary/analysis/evidence/insight layers."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime
from typing import Any, Optional

from src.ai_caller import current_shanghai_timestamp

from ._context import (
    collect_context,
    build_minimal_evidence_fallback,
    build_minimal_fallback,
    build_minimal_model_report,
    _summarize_sources,
)
from ._market import (
    build_deep_summary,
    build_market_stage,
    build_next_themes,
    build_policy_watchlist,
    build_rule_themes,
    build_trade_action,
    collect_headlines,
    summarize_market_regime,
)
from ._utils import cache_get, cache_put

logger = logging.getLogger(__name__)

_JSON_MARKER = "__MARKET_THEME_JSON__="


def build_summary_response(context: dict[str, Any]) -> dict[str, Any]:
    snapshot = context["source_snapshot"]
    themes = build_rule_themes(snapshot, [])
    next_themes = build_next_themes(snapshot, [])
    market_regime = summarize_market_regime(snapshot["market_status"], snapshot["market_breadth"])
    leading = themes[0] if themes else None
    return {
        "generated_at": context["generated_at"],
        "headline": (
            f"当前A股主线不是单线，更像 { ' / '.join(theme['name'] for theme in themes[:3]) } 并行。"
            if themes else "当前市场主线仍偏轮动，尚未形成特别稳定的单一方向。"
        ),
        "market_regime": market_regime,
        "market_stage": build_market_stage(snapshot["market_status"], snapshot["market_breadth"], themes),
        "current_themes": [
            {
                "name": theme["name"],
                "stage": theme["stage"],
                "rank_label": theme.get("rank_label"),
                "components": theme.get("components", []),
                "stage_reason": theme.get("stage_reason"),
            }
            for theme in themes[:3]
        ],
        "next_theme_pool": [candidate["name"] for candidate in next_themes[:4]],
        "investment_takeaway": (
            f"当前更该围绕 {leading['name']} 这类仍有产业和资金共振的方向做取舍，"
            "而不是追逐纯情绪题材。"
            if leading else "当前更适合等待更清晰的主线聚焦。"
        ),
        "source_snapshot": {
            "market_status": snapshot["market_status"],
            "market_breadth": snapshot["market_breadth"],
        },
    }


def build_response(context: dict[str, Any]) -> dict[str, Any]:
    snapshot = context["source_snapshot"]
    headlines = collect_headlines(snapshot["rss"])
    themes = build_rule_themes(snapshot, headlines)
    next_themes = build_next_themes(snapshot, headlines)

    market_status = snapshot["market_status"]
    breadth = snapshot["market_breadth"]
    market_regime = summarize_market_regime(market_status, breadth)

    headline = (
        f"当前市场不是单一主线，而是由“{themes[0]['name']}”领衔、"
        f"“{themes[1]['name']}”与“{themes[2]['name']}”共同构成主线梯队。"
        if len(themes) >= 3
        else (f"当前更像“{themes[0]['name']}”领衔的结构性主线。" if themes else "当前市场更像多方向轮动，尚未形成特别清晰且稳定的单一主线。")
    )
    primary = (
        "这版结果不是在猜下一个概念名，而是把板块涨跌、资金流、官方公开信息、行业研报和政策线索"
        "收敛成几个能被用户直接理解的叙事级主线，再判断它们分别处在预热、发酵、加速还是分歧阶段。"
    )
    takeaway = (
        "优先关注具备政策催化、产业趋势验证和估值性价比三者共振的方向；"
        "对只有短线热度、缺少中期逻辑支撑的题材保持克制。"
    )
    return {
        "generated_at": context["generated_at"],
        "headline": headline,
        "market_regime": market_regime,
        "primary_judgement": primary,
        "investment_takeaway": takeaway,
        "policy_watchlist": build_policy_watchlist(headlines),
        "current_themes": themes,
        "next_themes": next_themes,
        "source_notes": [
            "公开市场数据：市场状态、市场宽度、行业/概念板块、板块资金流向",
            "官方/交易所信息：上交所问询、上交所披露、中国外汇交易中心公开信息",
            "公共资讯与研报：财联社电报、华尔街见闻日历、东方财富策略/宏观/行业研报",
        ],
        "source_summary": _summarize_sources(snapshot),
        "source_snapshot": snapshot,
    }


def build_evidence_response(context: dict[str, Any]) -> dict[str, Any]:
    full = build_response(context)
    snapshot = context["source_snapshot"]
    return {
        "generated_at": context["generated_at"],
        "market_stage": build_market_stage(snapshot["market_status"], snapshot["market_breadth"], full["current_themes"]),
        "current_themes": full["current_themes"],
        "next_themes": full["next_themes"],
        "policy_watchlist": full["policy_watchlist"],
        "source_notes": full["source_notes"],
        "source_summary": full["source_summary"],
        "source_snapshot": full["source_snapshot"],
    }


def build_insight_response(evidence: dict[str, Any]) -> dict[str, Any]:
    current_themes = evidence.get("current_themes") or []
    next_themes = evidence.get("next_themes") or []
    market_stage = evidence.get("market_stage") or {"label": "结构轮动", "description": "当前市场仍在多方向轮动。"}
    leading = current_themes[0] if current_themes else None
    second = current_themes[1] if len(current_themes) > 1 else None
    third = current_themes[2] if len(current_themes) > 2 else None

    overview = (
        f"我的判断是：当前A股不是单一主线，而是“{leading['name']} + {second['name']} + {third['name']}”三条线并行，"
        f"其中最强主线是 {leading['name']}，市场整体处在{market_stage.get('label')}。"
        if leading and second and third
        else "我的判断是：当前市场仍以结构轮动为主，尚未形成完全单一的主线。"
    )
    lifecycle_notes = []
    for theme in current_themes[:3]:
        lifecycle_notes.append({
            "theme": theme["name"],
            "stage": theme["stage"],
            "judgement": theme.get("thesis") or "",
            "reason": theme.get("stage_reason") or "",
            "action": build_trade_action(theme),
        })
    future_outlook = [
        {
            "name": item["name"],
            "why_now": item["why_now"],
            "stage_hint": "候选观察期",
        }
        for item in next_themes[:4]
    ]
    return {
        "generated_at": evidence.get("generated_at") or current_shanghai_timestamp(),
        "overview": overview,
        "market_stage": market_stage,
        "lifecycle_notes": lifecycle_notes,
        "future_outlook": future_outlook,
        "deep_summary": build_deep_summary(market_stage, lifecycle_notes, future_outlook),
        "llm_used": False,
        "model_used": None,
    }


def run_isolated(*, force: bool, layer: str, timeout: int = 35) -> Optional[dict]:
    cmd = [sys.executable, "-m", "src.services.market_theme_service"]
    if force:
        cmd.append("--force")
    cmd.extend(["--layer", layer])
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(__import__("pathlib").Path(__file__).resolve().parents[3]),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.error("market theme isolated runner timed out: layer=%s timeout=%ss", layer, timeout)
        return None
    except Exception:
        logger.exception("market theme isolated runner failed to start")
        return None

    stdout = completed.stdout or ""
    payload_line = next((line for line in stdout.splitlines() if line.startswith(_JSON_MARKER)), None)
    if completed.returncode != 0 or not payload_line:
        logger.error(
            "market theme isolated runner failed: code=%s stderr=%s stdout_tail=%s",
            completed.returncode,
            (completed.stderr or "").strip()[-1200:],
            stdout.strip()[-1200:],
        )
        return None

    try:
        payload = json.loads(payload_line[len(_JSON_MARKER):])
        if isinstance(payload, dict):
            return payload
    except Exception:
        logger.exception("market theme isolated runner returned invalid json")
    return None