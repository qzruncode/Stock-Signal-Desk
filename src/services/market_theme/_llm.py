# -*- coding: utf-8 -*-
"""LLM prompt assembly and structured generation for market mainline reports."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from src.ai_caller import call_ai_structured, current_shanghai_timestamp
from src.storage import DatabaseManager

from ._context import build_report_evidence_pack

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
    allowed_boards = {
        str(item.get("name") or "").strip()
        for section in (
            "industry_flow", "concept_flow",
            "industry_sectors", "concept_sectors",
        )
        for item in evidence_pack.get(section) or []
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
            if not name or not refs or (current and not branches):
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
            rows.append(row)
        return rows

    current = validate_rows(payload.get("current_mainlines"), current=True)
    future = validate_rows(payload.get("future_mainlines"), current=False)
    if not current:
        raise ValueError("model report contains no evidence-bound current mainline")
    payload["current_mainlines"] = current
    payload["future_mainlines"] = future
    payload["validation"] = {
        "method": "runtime_evidence_reference_binding",
        "accepted_current_count": len(current),
        "accepted_future_count": len(future),
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
        "你的任务不是分类关键词，而是从政策、产业、资金、估值、景气和市场风格中直接归纳当前主线。"
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
        '    {"name":"动态归纳的主线名称","stage":"生命周期阶段","reason":"为什么它是主线","branches":["必须逐字来自证据包的板块name"],"focus":"当前该看什么","risks":["风险"],"evidence_refs":["必须来自evidence_refs的ID"]}\n'
        "  ],\n"
        '  "future_mainlines": [\n'
        '    {"name":"动态归纳的候选名称","stage_hint":"模型判断的阶段","reason":"为什么值得跟踪","triggers":["触发条件"],"evidence_refs":["必须来自evidence_refs的ID"]}\n'
        "  ],\n"
        '  "action_summary": ["交易结论1","交易结论2","交易结论3"],\n'
        '  "evidence_digest": {"policy":["..."],"industry":["..."],"market":["..."]}\n'
        "}\n\n"
        "要求：\n"
        "1. 主线数量由证据决定；没有充分证据就返回空数组，不得凑数。\n"
        "2. 名称、分组和生命周期必须动态归纳，禁止套用预设行业或主题目录。\n"
        "3. current_mainlines 的每条 branches 必须逐字来自证据包板块 name，"
        "evidence_refs 必须逐字来自 evidence_refs 字典；程序会拒绝无引用结论。\n"
        "4. future_mainlines 也必须提供有效 evidence_refs。\n"
        "5. full_report 直接回答当前主线、证据、阶段、反证与未来触发条件。\n"
        f"6. 证据包如下：{json.dumps(evidence_pack, ensure_ascii=False)}"
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


def build_llm_model_report_streaming(
    context: dict[str, Any],
    *,
    evidence_pack: Optional[dict[str, Any]] = None,
    system_prompt: Optional[str] = None,
    user_prompt: Optional[str] = None,
    on_text: Optional[Any] = None,
) -> Optional[dict[str, Any]]:
    evidence_pack = evidence_pack or build_report_evidence_pack(context)
    if system_prompt is None or user_prompt is None:
        system_prompt, user_prompt = build_model_report_prompts(evidence_pack)

    accumulated_text = ""
    last_emitted_length = 0

    def _on_stream_text(_delta_text: str, full_text: str) -> None:
        nonlocal accumulated_text, last_emitted_length
        accumulated_text = full_text
        if on_text and (
            len(full_text) - last_emitted_length >= 180
            or len(full_text) < 180
        ):
            last_emitted_length = len(full_text)
            on_text(full_text, build_streaming_report_draft(full_text))

    try:
        raw_response_text, response_text, model_used, usage = stream_market_mainline_report_via_litellm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=8192,
            on_text=_on_stream_text,
        )
        persist_llm_usage(usage, model_used, call_type="market_mainline_report")
        json_payload_text = extract_json_object_from_text(raw_response_text) or response_text
        parsed = json.loads(json_payload_text)
        if not isinstance(parsed, dict):
            return None
        parsed.setdefault("generated_at", context["generated_at"])
        parsed.setdefault("as_of_date", evidence_pack["as_of_date"])
        parsed["llm_used"] = True
        parsed["model_used"] = model_used
        parsed.setdefault("current_mainlines", [])
        parsed.setdefault("future_mainlines", [])
        parsed.setdefault("action_summary", [])
        parsed.setdefault("evidence_digest", {"policy": [], "industry": [], "market": []})
        parsed.setdefault("source_summary", evidence_pack.get("source_summary") or {})
        parsed["raw_stream_output"] = raw_response_text
        parsed["raw_response"] = raw_response_text
        parsed["debug_input"] = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "evidence_pack": evidence_pack,
        }
        if on_text and raw_response_text and len(raw_response_text) != last_emitted_length:
            on_text(raw_response_text, build_streaming_report_draft(raw_response_text))
        DatabaseManager.get_instance().save_market_mainline_report(
            report_key="market_mainline",
            as_of_date=str(parsed.get("as_of_date") or evidence_pack["as_of_date"]),
            mode="llm",
            payload=parsed,
            raw_response=raw_response_text,
            model_used=model_used,
        )
        return parsed
    except Exception:
        logger.warning(
            "market mainline direct stream failed, falling back to non-stream completion",
            exc_info=True,
        )
        if on_text and accumulated_text and len(accumulated_text) != last_emitted_length:
            on_text(accumulated_text, build_streaming_report_draft(accumulated_text))

    try:
        from src.analyzer import get_analyzer
    except Exception:
        logger.exception("market mainline report analyzer import failed")
        raise RuntimeError("模型服务初始化失败")

    analyzer = get_analyzer()
    if not getattr(analyzer, "is_available", lambda: False)():
        logger.info("market mainline model report skipped because analyzer is unavailable")
        raise RuntimeError("模型服务当前不可用")

    try:
        response_text, model_used, _usage = call_ai_structured(
            analyzer,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            call_type="market_mainline_report",
            temperature=0.2,
            max_tokens=8192,
            stream=False,
        )
        parsed = json.loads(response_text)
        if not isinstance(parsed, dict):
            return None
        parsed.setdefault("generated_at", context["generated_at"])
        parsed.setdefault("as_of_date", evidence_pack["as_of_date"])
        parsed["llm_used"] = True
        parsed["model_used"] = model_used
        parsed.setdefault("current_mainlines", [])
        parsed.setdefault("future_mainlines", [])
        parsed.setdefault("action_summary", [])
        parsed.setdefault("evidence_digest", {"policy": [], "industry": [], "market": []})
        parsed.setdefault("source_summary", evidence_pack.get("source_summary") or {})
        parsed["raw_stream_output"] = accumulated_text or response_text
        parsed["raw_response"] = accumulated_text or response_text
        parsed["debug_input"] = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "evidence_pack": evidence_pack,
        }
        if on_text and response_text and len(response_text) != last_emitted_length:
            on_text(response_text, build_streaming_report_draft(response_text))
        DatabaseManager.get_instance().save_market_mainline_report(
            report_key="market_mainline",
            as_of_date=str(parsed.get("as_of_date") or evidence_pack["as_of_date"]),
            mode="llm",
            payload=parsed,
            raw_response=accumulated_text or response_text,
            model_used=model_used,
        )
        return parsed
    except Exception as exc:
        logger.exception("market mainline model report streaming generation failed")
        if on_text and accumulated_text and len(accumulated_text) != last_emitted_length:
            on_text(accumulated_text, build_streaming_report_draft(accumulated_text))
        raise RuntimeError(f"模型连接失败: {exc}") from exc
