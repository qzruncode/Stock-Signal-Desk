# -*- coding: utf-8 -*-
"""Market evidence and validated-model response assembly."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from typing import Any, Optional

from src.ai_caller import current_shanghai_timestamp

from ._context import _summarize_sources, build_report_evidence_pack
from ._market import build_deep_summary, build_trade_action, empty_market_stage

logger = logging.getLogger(__name__)

_JSON_MARKER = "__MARKET_THEME_JSON__="


def _model_report_view(report: dict[str, Any]) -> dict[str, Any]:
    current = [
        item for item in report.get("current_mainlines") or []
        if isinstance(item, dict)
    ]
    future = [
        item for item in report.get("future_mainlines") or []
        if isinstance(item, dict)
    ]
    return {
        "generated_at": report.get("generated_at") or current_shanghai_timestamp(),
        "headline": str(report.get("overview") or "市场主线动态研判已完成。"),
        "market_regime": str(
            (report.get("market_stage") or {}).get("label") or "动态语义研判"
        ),
        "market_stage": report.get("market_stage") or empty_market_stage(),
        "primary_judgement": str(report.get("full_report") or report.get("overview") or ""),
        "investment_takeaway": "；".join(
            str(item) for item in report.get("action_summary") or [] if str(item).strip()
        ),
        "policy_watchlist": [
            str(item) for item in report.get("policy_watchlist") or [] if str(item).strip()
        ],
        "current_themes": current,
        "next_themes": future,
        "current_mainlines": current,
        "future_mainlines": future,
        "llm_used": bool(report.get("llm_used")),
        "model_used": report.get("model_used"),
        "semantic_status": "completed",
    }


def build_summary_response(
    context: dict[str, Any],
    *,
    model_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = context["source_snapshot"]
    if model_report and model_report.get("current_mainlines"):
        view = _model_report_view(model_report)
        view["source_snapshot"] = {
            "market_status": snapshot.get("market_status") or {},
            "market_breadth": snapshot.get("market_breadth") or {},
        }
        return view
    return {
        "generated_at": context["generated_at"],
        "headline": "原始市场证据已更新，动态主线研判尚未完成。",
        "market_regime": "未研判",
        "market_stage": empty_market_stage(),
        "current_themes": [],
        "next_theme_pool": [],
        "investment_takeaway": "模型完成基于证据的动态归纳前，不输出主线名称或阶段。",
        "source_snapshot": {
            "market_status": snapshot.get("market_status") or {},
            "market_breadth": snapshot.get("market_breadth") or {},
        },
        "llm_used": False,
        "model_used": None,
        "semantic_status": "pending",
    }


def build_response(
    context: dict[str, Any],
    *,
    model_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = context["source_snapshot"]
    if model_report and model_report.get("current_mainlines"):
        result = _model_report_view(model_report)
    else:
        result = {
            "generated_at": context["generated_at"],
            "headline": "已收集市场、板块、资金和公开信息，动态主线研判暂不可用。",
            "market_regime": "未研判",
            "market_stage": empty_market_stage(),
            "primary_judgement": (
                "系统不再使用关键词目录、固定叙事或阈值公式代替策略研判。"
                "模型未完成时只返回原始证据。"
            ),
            "investment_takeaway": "本轮不生成未经语义研判的主题结论。",
            "policy_watchlist": [],
            "current_themes": [],
            "next_themes": [],
            "current_mainlines": [],
            "future_mainlines": [],
            "llm_used": False,
            "model_used": None,
            "semantic_status": "unavailable",
        }
    result.update({
        "source_notes": [
            "公开市场状态、市场宽度、行业与概念板块、板块资金流",
            "交易所与官方公开信息",
            "公共资讯与公开研究资料",
        ],
        "source_summary": _summarize_sources(snapshot),
        "source_snapshot": snapshot,
    })
    return result


def build_evidence_response(context: dict[str, Any]) -> dict[str, Any]:
    """Expose raw observations only; no rule-based theme synthesis."""
    snapshot = context["source_snapshot"]
    return {
        "generated_at": context["generated_at"],
        "market_stage": empty_market_stage(),
        "current_themes": [],
        "next_themes": [],
        "policy_watchlist": [],
        "semantic_status": "model_required",
        "evidence_pack": build_report_evidence_pack(context),
        "source_notes": [
            "该层只提供原始结构化证据，不判断主题、生命周期或主线排名。",
        ],
        "source_summary": _summarize_sources(snapshot),
        "source_snapshot": snapshot,
    }


def build_insight_response(evidence: dict[str, Any]) -> dict[str, Any]:
    current = [
        item for item in (
            evidence.get("current_mainlines") or evidence.get("current_themes") or []
        )
        if isinstance(item, dict)
    ]
    future = [
        item for item in (
            evidence.get("future_mainlines") or evidence.get("next_themes") or []
        )
        if isinstance(item, dict)
    ]
    market_stage = evidence.get("market_stage") or empty_market_stage()
    lifecycle_notes = [
        {
            "theme": item.get("name"),
            "stage": item.get("stage"),
            "judgement": item.get("reason") or item.get("thesis") or "",
            "reason": item.get("reason") or item.get("stage_reason") or "",
            "action": build_trade_action(item),
        }
        for item in current
    ]
    future_outlook = [
        {
            "name": item.get("name"),
            "why_now": item.get("reason") or item.get("why_now") or "",
            "stage_hint": item.get("stage_hint") or "",
        }
        for item in future
    ]
    return {
        "generated_at": evidence.get("generated_at") or current_shanghai_timestamp(),
        "overview": str(
            evidence.get("overview")
            or (
                "动态主线研判已完成。"
                if current
                else "原始证据已收集，动态主线研判尚未完成。"
            )
        ),
        "market_stage": market_stage,
        "lifecycle_notes": lifecycle_notes,
        "future_outlook": future_outlook,
        "deep_summary": build_deep_summary(
            market_stage, lifecycle_notes, future_outlook,
        ),
        "llm_used": bool(evidence.get("llm_used")),
        "model_used": evidence.get("model_used"),
        "semantic_status": "completed" if current else "pending",
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
    payload_line = next(
        (line for line in stdout.splitlines() if line.startswith(_JSON_MARKER)),
        None,
    )
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
        return payload if isinstance(payload, dict) else None
    except Exception:
        logger.exception("market theme isolated runner returned invalid json")
        return None
