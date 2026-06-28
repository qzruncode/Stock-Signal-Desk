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
                "evidence": item.get("evidence"),
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
        "1. lifecycle_notes 只保留最重要的 3 条主线。\n"
        "2. 阶段描述尽量用更像交易语言的表达，例如 主升初期 / 主升中期 / 主升后段 / 高位分歧 / 候选观察期。\n"
        "3. 主线命名优先使用更像策略会结论的叙事名称，例如：科技成长、资源重估、电力设备与能源基础设施、军工与低空安全、创新药。\n"
        "4. 如果一个主线下包含 半导体 / AI / 新材料 等多个科技分支，优先上提为“科技成长”，不要拘泥于单个细行业名称。\n"
        "5. 如果证据不足，不要瞎拔高，明确写观察期或分歧期。\n"
        "6. future_outlook 最多 4 条。\n"
        "7. 只基于以下证据包作答：\n"
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
        '    {"name":"主线名称","rank":1,"stage":"生命周期阶段","reason":"为什么它是主线","branches":["核心分支1","核心分支2"],"focus":"当前该看什么","risks":["风险1","风险2"],"evidence":["证据1","证据2","证据3"]}\n'
        "  ],\n"
        '  "future_mainlines": [\n'
        '    {"name":"未来候选主线","stage_hint":"候选观察期/早中期/左侧观察期","reason":"为什么值得跟踪","triggers":["触发条件1","触发条件2"]}\n'
        "  ],\n"
        '  "action_summary": ["交易结论1","交易结论2","交易结论3"],\n'
        '  "evidence_digest": {"policy":["..."],"industry":["..."],"market":["..."]}\n'
        "}\n\n"
        "要求：\n"
        "1. current_mainlines 保留 2 到 4 条，不要只写一条。\n"
        "2. 主线名称由你自己归纳，不要机械照抄细行业名；允许使用 AI科技链、资源重估、出海制造、电力设备与能源基础设施、创新药、军工与低空安全 这类研究表述。\n"
        "3. 生命周期要用更像交易语言的表达，例如 主升初期 / 主升中期 / 主升后段 / 高位分歧 / 候选观察期。\n"
        "4. 如果证据不足，就明确写观察期，不要硬拔高。\n"
        "5. future_mainlines 只保留真正有跟踪价值的方向，不要凑数。\n"
        "6. full_report 要直接回答：当前主线是什么、已经走到什么阶段、未来主线可能是什么。\n"
        f"7. 证据包如下：{json.dumps(evidence_pack, ensure_ascii=False)}"
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