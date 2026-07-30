# -*- coding: utf-8 -*-
"""LLM prompt assembly and structured generation for market mainline reports."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from src.ai_caller import call_ai_structured, current_shanghai_timestamp
from src.storage import DatabaseManager

from ._context import (
    MARKET_MAINLINE_REPORT_CONTRACT,
    build_report_evidence_pack,
)

logger = logging.getLogger(__name__)


def _validated_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        str(item).strip() for item in value if str(item).strip()
    ))


def _validate_model_report(
    payload: dict[str, Any],
    evidence_pack: dict[str, Any],
) -> dict[str, Any]:
    """Bind every accepted narrative to exact runtime evidence identifiers."""
    evidence_index = evidence_pack.get("evidence_refs") or {}
    if not isinstance(evidence_index, dict):
        evidence_index = {}
    allowed_refs = set(evidence_index)
    board_catalog = evidence_pack.get("board_catalog") or {}
    allowed_boards = {
        str(item.get("name") or "").strip()
        for section in ("industry", "concept")
        for item in board_catalog.get(section) or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }

    def validate_rows(value: Any, *, current: bool) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for raw in value or []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            refs = [
                ref for ref in _validated_text_list(raw.get("evidence_refs"))
                if ref in allowed_refs
            ]
            branches = [
                branch for branch in _validated_text_list(raw.get("branches"))
                if branch in allowed_boards
            ]
            if not name or not refs or not branches:
                continue
            row = {
                **raw,
                "name": name,
                "evidence_refs": refs,
                "evidence": [
                    f"{ref} {str((evidence_index.get(ref) or {}).get('name') or '').strip()}".strip()
                    for ref in refs
                ],
            }
            if current:
                row["branches"] = branches
                row["rank"] = len(rows) + 1
                row["rank_label"] = f"主线{len(rows) + 1}"
                row["components"] = branches
                row["thesis"] = str(row.get("reason") or "").strip()
                row["stage_reason"] = str(row.get("reason") or "").strip()
            else:
                evidence_axes: list[dict[str, Any]] = []
                seen_axes: set[str] = set()
                for axis_item in raw.get("evidence_axes") or []:
                    if not isinstance(axis_item, dict):
                        continue
                    axis = str(axis_item.get("axis") or "").strip()
                    axis_refs = [
                        ref
                        for ref in _validated_text_list(
                            axis_item.get("evidence_refs")
                        )
                        if ref in allowed_refs
                    ]
                    if not axis or not axis_refs or axis in seen_axes:
                        continue
                    seen_axes.add(axis)
                    evidence_axes.append({
                        "axis": axis,
                        "evidence_refs": axis_refs,
                    })
                trigger_assessments: list[dict[str, Any]] = []
                for trigger in raw.get("trigger_assessments") or []:
                    if not isinstance(trigger, dict):
                        continue
                    description = str(
                        trigger.get("description") or ""
                    ).strip()
                    trigger_refs = [
                        ref
                        for ref in _validated_text_list(
                            trigger.get("evidence_refs")
                        )
                        if ref in allowed_refs
                    ]
                    status = str(trigger.get("status") or "unknown")
                    if not description or status not in {
                        "met",
                        "partial",
                        "unmet",
                        "unknown",
                    }:
                        continue
                    if status in {"met", "partial"} and not trigger_refs:
                        status = "unknown"
                    trigger_assessments.append({
                        "description": description,
                        "status": status,
                        "evidence_refs": trigger_refs,
                    })
                row["branches"] = branches
                row["evidence_axes"] = evidence_axes
                row["trigger_assessments"] = trigger_assessments
                row["triggers"] = [
                    item["description"] for item in trigger_assessments
                ]
            rows.append(row)
        return rows

    current = validate_rows(payload.get("current_mainlines"), current=True)
    candidates = validate_rows(
        payload.get("candidate_mainlines")
        or payload.get("future_mainlines"),
        current=False,
    )
    if not current and not candidates:
        raise ValueError(
            "model report contains no evidence-bound current or candidate mainline"
        )
    payload["current_mainlines"] = current
    payload["candidate_mainlines"] = candidates
    # Read-only compatibility for existing market API consumers.  New
    # orchestration code reads candidate_mainlines.
    payload["future_mainlines"] = candidates
    payload["contract_version"] = MARKET_MAINLINE_REPORT_CONTRACT
    payload["validation"] = {
        "method": "runtime_evidence_reference_binding",
        "accepted_current_count": len(current),
        "accepted_candidate_count": len(candidates),
        "accepted_future_count": len(candidates),
        "allowed_board_count": len(allowed_boards),
        "available_evidence_count": len(allowed_refs),
    }
    return payload


def build_llm_insight_response(evidence: dict[str, Any]) -> Optional[dict[str, Any]]:
    try:
        from src.analyzer import get_analyzer
    except Exception:
        logger.exception("market insight LLM analyzer import failed")
        return None

    analyzer = get_analyzer()
    if not getattr(analyzer, "is_available", lambda: False)():
        logger.info("market insight LLM skipped because analyzer is unavailable")
        return None

    payload = {
        "generated_at": evidence.get("generated_at"),
        "market_stage": evidence.get("market_stage"),
        "current_themes": [
            {
                "name": item.get("name"),
                "rank_label": item.get("rank_label"),
                "stage": item.get("stage"),
                "components": item.get("components"),
                "thesis": item.get("thesis"),
                "stage_reason": item.get("stage_reason"),
                "policy_signal": item.get("policy_signal"),
                "industry_trend": item.get("industry_trend"),
                "valuation_view": item.get("valuation_view"),
                "expectation_view": item.get("expectation_view"),
                "risks": item.get("risks"),
                "evidence_refs": item.get("evidence_refs"),
            }
            for item in (evidence.get("current_themes") or [])[:3]
        ],
        "next_themes": (evidence.get("next_themes") or [])[:4],
        "policy_watchlist": evidence.get("policy_watchlist") or [],
    }
    system_prompt = (
        "你是A股策略分析师。你不能编造数据，也不能引入材料包之外的新事实。"
        "你的任务是基于给定证据包，把零散证据整理成像资深研究员写的市场主线判断。"
        "输出必须是严格 JSON，不要包含 markdown，不要包含代码块。"
        "请使用简洁、专业、带交易含义的中文。"
    )
    user_prompt = (
        "请根据下面的证据包，输出 JSON，字段必须完全匹配：\n"
        "{\n"
        '  "overview": "一句话总判断，说明当前不是单一主线还是多主线并行，以及最强主线是谁",\n'
        '  "market_stage": {"label": "整个市场所处阶段", "description": "1-2句说明"},\n'
        '  "lifecycle_notes": [\n'
        '    {"theme":"主线名","stage":"所处阶段","judgement":"这条线是什么","reason":"为什么这么判断","action":"交易含义"}\n'
        "  ],\n"
        '  "future_outlook": [\n'
        '    {"name":"候选方向","why_now":"为什么值得观察","stage_hint":"候选观察期/早中期/左侧观察期"}\n'
        "  ],\n"
        '  "deep_summary": "最后一句收束，告诉用户当前最值得深挖和未来更有预期差的方向"\n'
        "}\n\n"
        "要求：\n"
        "1. 只保留证据足够的主线，不规定数量，不为凑数添加方向。\n"
        "2. 阶段、叙事名称和交易含义必须根据证据动态归纳，不套用预设行业目录。\n"
        "3. 如果证据不足，明确写未研判，不得补造。\n"
        "4. 只基于以下证据包作答：\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )

    try:
        response_text, model_used, _usage = call_ai_structured(
            analyzer,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            call_type="market_mainline_insight",
            temperature=0.2,
            max_tokens=4096,
        )
        parsed = json.loads(response_text)
        if not isinstance(parsed, dict):
            return None
        parsed["generated_at"] = evidence.get("generated_at") or current_shanghai_timestamp()
        parsed["llm_used"] = True
        parsed["model_used"] = model_used
        parsed.setdefault("market_stage", evidence.get("market_stage") or {})
        parsed.setdefault("lifecycle_notes", [])
        parsed.setdefault("future_outlook", [])
        parsed.setdefault("deep_summary", "")
        return parsed
    except Exception:
        logger.exception("market insight LLM generation failed")
        return None


def build_llm_model_report(context: dict[str, Any]) -> Optional[dict[str, Any]]:
    try:
        from src.analyzer import get_analyzer
    except Exception:
        logger.exception("market mainline report analyzer import failed")
        return None

    analyzer = get_analyzer()
    if not getattr(analyzer, "is_available", lambda: False)():
        logger.info("market mainline model report skipped because analyzer is unavailable")
        return None

    evidence_pack = build_report_evidence_pack(context)
    system_prompt, user_prompt = build_model_report_prompts(evidence_pack)

    try:
        response_text, model_used, _usage = call_ai_structured(
            analyzer,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            call_type="market_mainline_report",
            temperature=0.2,
            max_tokens=8192,
        )
        parsed = json.loads(response_text)
        if not isinstance(parsed, dict):
            return None
        parsed = _validate_model_report(parsed, evidence_pack)
        parsed.setdefault("generated_at", context["generated_at"])
        parsed.setdefault("as_of_date", evidence_pack["as_of_date"])
        parsed["llm_used"] = True
        parsed["model_used"] = model_used
        parsed.setdefault("current_mainlines", [])
        parsed.setdefault("candidate_mainlines", [])
        parsed.setdefault("future_mainlines", [])
        parsed.setdefault("action_summary", [])
        parsed.setdefault("evidence_digest", {"policy": [], "industry": [], "market": []})
        parsed.setdefault("source_summary", evidence_pack.get("source_summary") or {})
        parsed["debug_input"] = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "evidence_pack": evidence_pack,
        }
        DatabaseManager.get_instance().save_market_mainline_report(
            report_key="market_mainline",
            as_of_date=str(parsed.get("as_of_date") or evidence_pack["as_of_date"]),
            mode="llm",
            payload=parsed,
            raw_response=response_text,
            model_used=model_used,
        )
        return parsed
    except Exception:
        logger.exception("market mainline model report generation failed")
        return None


def build_model_report_prompts(evidence_pack: dict[str, Any]) -> tuple[str, str]:
    system_prompt = (
        "你是A股市场策略研究员。你只能基于给定证据包分析，不能编造材料包之外的事实。"
        "你的任务不是分类关键词，而是从政策方向、产业供需、技术路线、资本开支、"
        "景气趋势和机构策略共识中归纳未来1—6个月的A股主导叙事。"
        "资金流、涨跌幅、成交排名、技术指标和个股走势不属于市场主线证据，禁止据此建立或否定主线。"
        "输出必须是严格 JSON。不要使用 markdown 代码块。"
    )
    user_prompt = (
        "请基于以下证据包，直接完成A股市场主线分析，并输出严格 JSON。\n"
        "字段必须完全匹配：\n"
        "{\n"
        '  "generated_at": "生成时间",\n'
        '  "as_of_date": "YYYY-MM-DD",\n'
        '  "overview": "一句话总判断，直接回答当前主线是什么",\n'
        '  "full_report": "3到6段完整中文研判，像策略会观点，不要列表式流水账",\n'
        '  "market_stage": {"label": "整个市场所处阶段", "description": "1到3句解释"},\n'
        '  "current_mainlines": [\n'
        '    {"name":"动态归纳的主线名称","lifecycle":"confirmed/expanding/fading","stage":"中文生命周期说明","reason":"为什么它是主线","branches":["必须逐字来自证据包的板块name"],"focus":"当前该看什么","risks":["风险"],"evidence_refs":["必须来自evidence_refs的ID"]}\n'
        "  ],\n"
        '  "candidate_mainlines": [\n'
        '    {"name":"动态归纳的候选名称","lifecycle":"emerging/validating","stage_hint":"中文阶段说明","reason":"为什么值得跟踪","branches":["必须逐字来自证据包的板块name"],"expected_horizon":"one_to_six_months/six_to_twelve_months/over_twelve_months","evidence_axes":[{"axis":"institution_consensus/policy/supply_demand/technology/capital_expenditure/continuous_prosperity","evidence_refs":["支持该证据维度的有效ID"]}],"trigger_assessments":[{"description":"可验证触发条件","status":"met/partial/unmet/unknown","evidence_refs":["状态为met/partial时必须提供有效证据ID"]}],"evidence_refs":["必须来自evidence_refs的ID"]}\n'
        "  ],\n"
        '  "action_summary": ["交易结论1","交易结论2","交易结论3"],\n'
        '  "evidence_digest": {"policy":["..."],"industry":["..."],"market":["..."]}\n'
        "}\n\n"
        "要求：\n"
        "1. 主线数量由证据决定；没有充分证据就返回空数组，不得凑数。\n"
        "2. 当前主线专指未来1—6个月的主导产业叙事；长期趋势只能作为背景，"
        "单日交易热度不能升级为主线。\n"
        "3. 名称、分组和生命周期必须动态归纳，禁止套用预设行业或主题目录。\n"
        "4. board_catalog 只用于把已经由叙事证据建立的主线映射到标准板块名称，"
        "目录存在本身不是主线证据。\n"
        "5. current_mainlines 的每条 branches 必须逐字来自 board_catalog 的板块 name，"
        "evidence_refs 必须逐字来自 evidence_refs 字典；程序会拒绝无引用结论。\n"
        "6. candidate_mainlines 是候选而不是已确认的未来赢家；必须提供标准 branches、"
        "预期时间窗、逐类绑定 evidence_refs 的证据维度和结构化触发进度。"
        "met/partial 触发必须绑定有效 evidence_refs，"
        "没有证据时只能写 unknown 或 unmet。\n"
        "7. full_report 直接回答当前主线、证据、阶段、反证与未来触发条件。\n"
        "8. 禁止引入或推断资金流、涨跌幅、成交排名、均线和技术形态。\n"
        f"9. 证据包如下：{json.dumps(evidence_pack, ensure_ascii=False)}"
    )
    return system_prompt, user_prompt


def extract_partial_json_string_field(raw_text: str, field_name: str) -> Optional[str]:
    marker = f'"{field_name}"'
    field_pos = raw_text.find(marker)
    if field_pos < 0:
        return None
    colon_pos = raw_text.find(":", field_pos + len(marker))
    if colon_pos < 0:
        return None

    quote_pos = None
    for index in range(colon_pos + 1, len(raw_text)):
        if raw_text[index] == '"':
            quote_pos = index
            break
        if not raw_text[index].isspace():
            return None
    if quote_pos is None:
        return None

    chars: list[str] = []
    escaping = False
    closed = False
    for index in range(quote_pos + 1, len(raw_text)):
        ch = raw_text[index]
        if escaping:
            chars.append("\\" + ch)
            escaping = False
            continue
        if ch == "\\":
            escaping = True
            continue
        if ch == '"':
            closed = True
            break
        chars.append(ch)

    fragment = "".join(chars)
    if not fragment and not closed:
        return None

    try:
        if closed:
            return json.loads(f'"{fragment}"')
        repaired = (
            fragment
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )
        return repaired.strip() or None
    except Exception:
        logger.error("[MarketTheme] _repair_malformed_json failed", exc_info=True)
        return None


def build_streaming_report_draft(raw_text: str) -> dict[str, Any]:
    draft: dict[str, Any] = {}
    for field_name in ("overview", "full_report", "as_of_date"):
        value = extract_partial_json_string_field(raw_text, field_name)
        if value:
            draft[field_name] = value

    stage_label = extract_partial_json_string_field(raw_text, "label")
    stage_description = extract_partial_json_string_field(raw_text, "description")
    if stage_label or stage_description:
        draft["market_stage"] = {
            "label": stage_label or "",
            "description": stage_description or "",
        }

    return draft


def build_report_context(*, force: bool, include_rss: bool = True) -> dict[str, Any]:
    from ._context import collect_context
    return collect_context(force=force, include_rss=include_rss)
