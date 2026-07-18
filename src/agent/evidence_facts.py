# -*- coding: utf-8 -*-
"""Semantic company-evidence binding with deterministic source validation."""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.agent.research_intent import ResearchIntent
from src.llm.anthropic_gateway import build_litellm_kwargs
from src.tools.symbols import find_securities_in_text


class ExtractedEvidenceFact(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    company_name: str
    symbol: Optional[str] = None
    stage: Literal["L3", "L2", "L1", "boundary"]
    theme_relevance: Literal["direct", "supporting", "unrelated"]
    relationship: str
    fact: str
    support_quote: str
    source_id: str
    confidence: float = Field(ge=0.0, le=1.0)


class BoundEvidenceFact(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    company_name: str
    symbol: str
    stage: Literal["L3", "L2", "L1", "boundary"]
    relationship: str
    fact: str
    support_quote: str
    source_id: str
    source_name: str
    source_url: str
    source_date: str
    source_date_is_retrieval: bool = False
    confidence: float


_FACT_TOOL = {
    "type": "function",
    "function": {
        "name": "bind_company_evidence",
        "description": "Extract topic-relevant, company-bound facts from supplied source texts.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "facts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "company_name": {"type": "string"},
                            "symbol": {"type": ["string", "null"]},
                            "stage": {"type": "string", "enum": ["L3", "L2", "L1", "boundary"]},
                            "theme_relevance": {
                                "type": "string",
                                "enum": ["direct", "supporting", "unrelated"],
                            },
                            "relationship": {"type": "string"},
                            "fact": {"type": "string"},
                            "support_quote": {"type": "string"},
                            "source_id": {"type": "string"},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "required": [
                            "company_name", "symbol", "stage", "theme_relevance", "relationship",
                            "fact", "support_quote", "source_id", "confidence",
                        ],
                    },
                },
            },
            "required": ["facts"],
        },
    },
}


_FACT_SYSTEM_PROMPT = """\
你是股票研究证据绑定器。只能从输入 sources 的原文中抽取与 requested_topic 相关的公司事实，不写研究结论。

证据阶段：
- L3：原文明确把当前主题产品/业务与已实现收入、收入占比、金额订单、实现量产、批量交付或批量落地绑定；
- L2：原文明确把当前主题产品/业务与送样、定点、客户测试/认证、供应链切入或放量阶段绑定，但尚无收入/规模兑现；
- L1：只证明技术储备、产品布局或概念关系；
- boundary：原文明确否认、尚未形成收入/订单、收入占比较小或计划取消。

约束：
1. 必须逐句绑定公司。混合文章中其他公司的订单、收入或批量供货不能转移给当前主题公司。
2. 公司总营收不能当成主题业务收入；概念板块成员不能升级为 L2/L3。
3. theme_relevance=direct 只用于原文事实本身与 requested_topic 直接相关；supporting 用于上游/配套关系；无关事实标 unrelated。
4. support_quote 必须是 source_id 对应原文中可逐字回查的短片段，包含公司名称；不得改写、拼接或补充原文没有的词。
5. symbol 只在原文明示时填写；不得猜代码。
6. 同一公司保留最能证明阶段的一条正向事实和必要的 boundary；没有合格事实就返回空数组。
7. 必须通过 bind_company_evidence 工具返回结构化结果。\
"""


def _source_date(source_item: dict[str, Any], result: dict[str, Any]) -> tuple[str, bool]:
    raw = str(
        source_item.get("published")
        or source_item.get("publish_date")
        or source_item.get("published_date")
        or source_item.get("content_time")
        or ""
    )
    match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", raw)
    if match:
        return "-".join((match.group(1), match.group(2).zfill(2), match.group(3).zfill(2))), False
    text = "\n".join(
        str(source_item.get(key) or "")
        for key in ("title", "snippet", "summary", "content_text", "content")
    )
    match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text[:1200])
    if match:
        return "-".join((match.group(1), match.group(2).zfill(2), match.group(3).zfill(2))), False
    retrieved = str(result.get("retrieved_at") or "")
    match = re.search(r"(20\d{2})-(\d{2})-(\d{2})", retrieved)
    if match:
        return "-".join(match.groups()), True
    return "", False


def _collect_sources(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    total_characters = 0
    for packet in evidence:
        if not isinstance(packet, dict) or packet.get("tool") not in {
            "search_financial_news", "search_research_library", "websearch",
        }:
            continue
        result = packet.get("result")
        if not isinstance(result, dict):
            continue
        items = result.get("items") or result.get("results") or []
        for item in items:
            if not isinstance(item, dict) or len(sources) >= 36 or total_characters >= 140_000:
                continue
            title = str(item.get("title") or "").strip()
            body = "\n".join(
                str(item.get(key) or "")
                for key in ("summary", "snippet", "content_text", "content")
                if item.get(key)
            ).strip()
            if not title and not body:
                continue
            remaining = max(0, 140_000 - total_characters)
            text = (title + "\n" + body)[: min(9000, remaining)]
            if not text:
                continue
            source_date, retrieval_date = _source_date(item, result)
            source = {
                "source_id": f"s{len(sources) + 1}",
                "title": title,
                "text": text,
                "source_name": str(item.get("source") or item.get("author") or "公开资料"),
                "source_url": str(item.get("url") or item.get("link") or ""),
                "source_date": source_date,
                "source_date_is_retrieval": retrieval_date,
            }
            sources.append(source)
            total_characters += len(text)
    return sources


def _payload_from_response(response: Any) -> dict[str, Any]:
    for choice in getattr(response, "choices", []) or []:
        message = getattr(choice, "message", None)
        if message is None:
            continue
        for tool_call in getattr(message, "tool_calls", None) or []:
            function = getattr(tool_call, "function", None)
            if getattr(function, "name", None) != "bind_company_evidence":
                continue
            arguments = getattr(function, "arguments", None)
            if arguments:
                payload = json.loads(str(arguments))
                if isinstance(payload, dict):
                    return payload
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            payload = json.loads(content)
            if isinstance(payload, dict):
                return payload
    raise ValueError("evidence model returned no structured payload")


def _normalized_source_text(value: str) -> str:
    # Markdown emphasis and layout whitespace do not change whether a quote is
    # grounded in the supplied source.
    return re.sub(r"[\s*_`#>]+", "", value).replace("／", "/").lower()


def _validate_facts(
    raw_facts: list[Any],
    sources: list[dict[str, Any]],
) -> list[BoundEvidenceFact]:
    source_map = {source["source_id"]: source for source in sources}
    bound: list[BoundEvidenceFact] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in raw_facts:
        try:
            fact = ExtractedEvidenceFact.model_validate(raw)
        except ValidationError:
            continue
        if fact.theme_relevance == "unrelated" or fact.confidence < 0.65:
            continue
        source = source_map.get(fact.source_id)
        if source is None or not source.get("source_url") or not source.get("source_date"):
            continue
        quote = fact.support_quote.strip()
        source_text = str(source.get("text") or "")
        if not quote or _normalized_source_text(quote) not in _normalized_source_text(source_text):
            continue
        company_token = _normalized_source_text(fact.company_name)
        if company_token not in _normalized_source_text(quote) and company_token not in _normalized_source_text(
            str(source.get("title") or "")
        ):
            continue
        resolution_text = f"{fact.company_name} {fact.symbol or ''}".strip()
        resolved = find_securities_in_text(resolution_text, limit=5)
        if not resolved:
            continue
        entity = next(
            (
                item for item in resolved
                if not fact.symbol or str(item.get("symbol") or "") == str(fact.symbol).zfill(6)
            ),
            None,
        )
        if entity is None:
            continue
        symbol = str(entity.get("symbol") or "")
        key = (symbol, fact.stage, fact.source_id)
        if key in seen:
            continue
        seen.add(key)
        bound.append(BoundEvidenceFact(
            company_name=str(entity.get("name") or fact.company_name),
            symbol=symbol,
            stage=fact.stage,
            relationship=fact.relationship,
            # Render the checked quote, not an unverified model paraphrase.
            fact=quote,
            support_quote=quote,
            source_id=fact.source_id,
            source_name=str(source.get("source_name") or "公开资料"),
            source_url=str(source.get("source_url") or ""),
            source_date=str(source.get("source_date") or ""),
            source_date_is_retrieval=bool(source.get("source_date_is_retrieval")),
            confidence=fact.confidence,
        ))
    return bound


async def bind_company_evidence(
    evidence: list[dict[str, Any]],
    intent: ResearchIntent,
    llm_cfg: dict[str, Any],
    *,
    completion: Callable[..., Awaitable[Any]],
) -> list[BoundEvidenceFact]:
    """Extract and source-validate company facts for the resolved topic."""
    sources = _collect_sources(evidence)
    if not sources or not intent.normalized_topic:
        return []
    request = {
        "requested_topic": intent.normalized_topic,
        "research_objective": intent.objective,
        "research_dimensions": intent.research_dimensions,
        "sources": sources,
    }
    kwargs = build_litellm_kwargs(
        llm_cfg,
        stream=False,
        messages=[
            {"role": "system", "content": _FACT_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
        ],
        tools=[_FACT_TOOL],
        tool_choice={"type": "function", "function": {"name": "bind_company_evidence"}},
        temperature=0,
        max_tokens=4000,
    )
    response = await completion(**kwargs)
    payload = _payload_from_response(response)
    facts = payload.get("facts")
    if not isinstance(facts, list):
        raise ValueError("evidence response facts is not a list")
    return _validate_facts(facts, sources)


__all__ = ["BoundEvidenceFact", "bind_company_evidence"]
