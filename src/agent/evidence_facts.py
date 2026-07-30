# -*- coding: utf-8 -*-
"""Semantic company-evidence binding with deterministic source validation."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Awaitable, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.agent.result_contracts import MappingSelectionContext
from src.llm.anthropic_gateway import build_litellm_kwargs
from src.tools.symbols import find_securities_in_text, resolve_symbol


class ExtractedEvidenceFact(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    company_name: str
    company_mention: str
    symbol: Optional[str] = None
    stage: Literal["L3", "L2", "L1", "boundary"]
    theme_relevance: Literal["direct", "supporting", "unrelated"]
    thesis_fit: Literal["exact", "partial", "outside"]
    commercialization_signal: Literal[
        "revenue", "order", "batch_delivery", "customer_validation",
        "commercial_application", "product_layout", "negative_boundary",
    ]
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
    thesis_fit: Literal["exact", "partial", "outside"] = "exact"
    commercialization_signal: Literal[
        "revenue", "order", "batch_delivery", "customer_validation",
        "commercial_application", "product_layout", "negative_boundary",
    ] = "batch_delivery"
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
                            "company_mention": {"type": "string"},
                            "symbol": {"type": ["string", "null"]},
                            "stage": {"type": "string", "enum": ["L3", "L2", "L1", "boundary"]},
                            "theme_relevance": {
                                "type": "string",
                                "enum": ["direct", "supporting", "unrelated"],
                            },
                            "thesis_fit": {
                                "type": "string",
                                "enum": ["exact", "partial", "outside"],
                            },
                            "commercialization_signal": {
                                "type": "string",
                                "enum": [
                                    "revenue", "order", "batch_delivery", "customer_validation",
                                    "commercial_application", "product_layout", "negative_boundary"
                                ],
                            },
                            "relationship": {"type": "string"},
                            "fact": {"type": "string"},
                            "support_quote": {"type": "string"},
                            "source_id": {"type": "string"},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "required": [
                            "company_name", "company_mention", "symbol", "stage",
                            "theme_relevance", "thesis_fit",
                            "commercialization_signal", "relationship",
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
4. thesis_fit 必须逐项检查 thesis_requirements：全部满足才是 exact；只满足部分是 partial；场景不同是 outside。
   例如命题是“消费级终端端侧 AI SoC”，只应用于机器人、边缘网关或服务器的事实不能标 exact。
5. commercialization_signal 必须按原文字面选择：
   - revenue/order/batch_delivery 才可标 L3；
   - customer_validation 才可标 L2；
   - 只有“商业化应用”、产品发布、技术布局而无订单/收入/批量交付，一律为 L1；
   - 明确否认或尚未形成收入为 boundary。
6. support_quote 必须是 source_id 对应原文中可逐字回查的短片段，包含公司名称；不得改写、拼接或补充原文没有的词。
7. company_name 填本地 A 股证券标准名；company_mention 必须逐字填写来源中使用的完整公司称谓。
   如果一个证券简称只是另一个公司称谓或普通名词的一部分，company_mention 必须保留完整原词，
   不得截取其中的证券简称。
8. source.candidate_securities 是本地证券目录按字面命中提供的身份候选，不是业务证据。
   只有原文确实把名称作为公司主体，并且 support_quote 证明其与当前主题的关系时才可抽取；
   不得把行业词、产品词等普通名词误当成同名证券。symbol 可从对应候选复制或使用原文明示值，不得猜代码。
9. 必须遍历 sources 中出现的每家 A 股公司，不得围绕同一家公司重复返回大量事实而遗漏其他公司；
   candidate_securities 不是穷尽列表，原文中的其他 A 股公司也必须判断。
10. 同一公司若同时存在不同命题匹配度或兑现阶段的事实，可返回最多 3 条候选；必须包含最强的
   量产、批量交付、订单或主题收入事实。最终去重由校验器完成，不能先用较弱事实覆盖较强事实。
11. 输入提供 candidate_to_evaluate 时，必须优先、单独核验该证券；有符合命题的原文事实就返回，
    原文只出现名称而没有相关事实才返回空 facts，不得改为分析其他公司。
12. 必须通过 bind_company_evidence 工具返回结构化结果。\
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


def collect_evidence_sources(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    total_characters = 0
    # Full crawled pages usually carry the company-specific realization
    # paragraph that RSS snippets and report indexes omit.  Give those pages
    # first access to the bounded semantic context, then use RSS and research
    # indexes for corroboration.
    tool_priority = {"websearch": 0, "search_financial_news": 1, "search_research_library": 2}
    ordered_evidence = sorted(
        evidence,
        key=lambda packet: tool_priority.get(str(packet.get("tool") or ""), 9)
        if isinstance(packet, dict) else 9,
    )
    for packet in ordered_evidence:
        if not isinstance(packet, dict) or packet.get("tool") not in {
            "search_financial_news", "search_research_library", "websearch",
        }:
            continue
        result = packet.get("result")
        if not isinstance(result, dict):
            continue
        items = result.get("items") or result.get("results") or []
        for item in items:
            if not isinstance(item, dict) or len(sources) >= 30 or total_characters >= 80_000:
                continue
            title = str(item.get("title") or "").strip()
            body = "\n".join(
                str(item.get(key) or "")
                for key in ("summary", "snippet", "content_text", "content")
                if item.get(key)
            ).strip()
            if not title and not body:
                continue
            remaining = max(0, 80_000 - total_characters)
            raw_text = title + "\n" + body
            text = raw_text[: min(6000, remaining)]
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


def _has_independent_mention(value: str, text: str) -> bool:
    """Reject a security short name found only inside a longer name/token."""
    start = 0
    while True:
        position = text.find(value, start)
        if position < 0:
            return False
        if position == 0 or not text[position - 1].isalnum():
            return True
        start = position + 1


def _same_security_display_name(left: str, right: str) -> bool:
    """Ignore exchange display markers while preserving the company identity."""
    def normalize(value: str) -> str:
        text = re.sub(r"\s+", "", str(value or "")).upper()
        text = re.sub(r"^\*?ST", "", text)
        return re.sub(r"-(?:U|W|UW|WD)$", "", text)

    normalized_left = normalize(left)
    normalized_right = normalize(right)
    return bool(
        normalized_left
        and normalized_right
        and normalized_left == normalized_right
    )


def _validate_facts(
    raw_facts: list[Any],
    sources: list[dict[str, Any]],
) -> list[BoundEvidenceFact]:
    source_map = {source["source_id"]: source for source in sources}
    best_by_company: dict[tuple[str, bool], BoundEvidenceFact] = {}
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
        mention = fact.company_mention.strip()
        title = str(source.get("title") or "")
        if not mention or (
            mention not in quote
            and mention not in title
        ):
            continue
        if not (
            _has_independent_mention(mention, source_text)
            or _has_independent_mention(mention, title)
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
        if (
            not _same_security_display_name(
                mention,
                str(entity.get("name") or fact.company_name),
            )
            and resolve_symbol(mention) != str(entity.get("symbol") or "")
        ):
            continue
        symbol = str(entity.get("symbol") or "")
        effective_stage = fact.stage
        if fact.commercialization_signal in {"commercial_application", "product_layout"}:
            effective_stage = "L1"
        elif fact.commercialization_signal == "customer_validation" and fact.stage == "L3":
            effective_stage = "L2"
        elif fact.commercialization_signal == "negative_boundary":
            effective_stage = "boundary"
        candidate = BoundEvidenceFact(
            company_name=str(entity.get("name") or fact.company_name),
            symbol=symbol,
            stage=effective_stage,
            thesis_fit=fact.thesis_fit,
            commercialization_signal=fact.commercialization_signal,
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
        )
        key = (symbol, effective_stage == "boundary")
        previous = best_by_company.get(key)
        rank = {"boundary": 4, "L3": 3, "L2": 2, "L1": 1}
        fit_rank = {"exact": 3, "partial": 2, "outside": 1}
        if previous is None or (
            fit_rank[candidate.thesis_fit], rank[candidate.stage], candidate.confidence
        ) > (
            fit_rank[previous.thesis_fit], rank[previous.stage], previous.confidence
        ):
            best_by_company[key] = candidate
    return list(best_by_company.values())


async def bind_company_evidence(
    evidence: list[dict[str, Any]],
    intent: MappingSelectionContext,
    llm_cfg: dict[str, Any],
    *,
    completion: Callable[..., Awaitable[Any]],
) -> list[BoundEvidenceFact]:
    """Extract and source-validate company facts for the resolved topic."""
    sources = collect_evidence_sources(evidence)[:12]
    if not sources or not intent.normalized_topic:
        return []
    model_sources = []
    for source in sources:
        source_with_candidates = dict(source)
        identity_text = "\n".join((
            str(source.get("title") or ""),
            str(source.get("text") or ""),
        ))
        source_with_candidates["candidate_securities"] = [
            candidate
            for candidate in find_securities_in_text(identity_text, limit=20)
            if _has_independent_mention(
                str(candidate.get("name") or ""),
                identity_text,
            )
        ]
        model_sources.append(source_with_candidates)

    # A single 80k-character extraction frequently exhausted the gateway's
    # structured-output budget and returned an empty assistant message.  Split
    # by source boundaries and run bounded semantic passes concurrently; source
    # ids remain global, so the combined facts still go through one deterministic
    # quote/company validator and one cross-batch best-fact selection.
    source_batches: list[list[dict[str, Any]]] = []
    current_batch: list[dict[str, Any]] = []
    current_characters = 0
    for source in model_sources:
        source_characters = len(str(source.get("text") or ""))
        # Give every source with a locally verified company identity its own
        # semantic pass. This avoids one company's fact being omitted because
        # another source in the same batch is more salient.
        if source.get("candidate_securities"):
            if current_batch:
                source_batches.append(current_batch)
                current_batch = []
                current_characters = 0
            source_batches.append([source])
            continue
        if current_batch and (
            len(current_batch) >= 2
            or current_characters + source_characters > 12_000
        ):
            source_batches.append(current_batch)
            current_batch = []
            current_characters = 0
        current_batch.append(source)
        current_characters += source_characters
    if current_batch:
        source_batches.append(current_batch)

    async def request_facts(request: dict[str, Any]) -> list[Any]:
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
            extra_body={
                "thinking": {"type": "disabled"},
                "reasoning_effort": "none",
            },
        )
        response = await completion(**kwargs)
        payload = _payload_from_response(response)
        facts = payload.get("facts")
        if not isinstance(facts, list):
            raise ValueError("evidence response facts is not a list")
        return facts

    async def extract_batch(batch: list[dict[str, Any]]) -> list[Any]:
        base_request = {
            "requested_topic": intent.normalized_topic,
            "research_objective": intent.objective,
            "research_dimensions": intent.research_dimensions,
            "thesis_requirements": intent.thesis_requirements,
            "sources": batch,
        }
        facts = await request_facts(base_request)
        if len(batch) != 1:
            return facts
        source = batch[0]
        topic_text = _normalized_source_text(intent.normalized_topic)
        source_text = _normalized_source_text(str(source.get("text") or ""))
        if not topic_text or topic_text not in source_text:
            return facts
        validated = _validate_facts(facts, batch)
        covered_symbols = {
            fact.symbol
            for fact in validated
            if fact.thesis_fit == "exact"
        }
        missing_candidates = [
            candidate
            for candidate in source.get("candidate_securities") or []
            if str(candidate.get("symbol") or "") not in covered_symbols
        ][:5]
        for candidate in missing_candidates:
            focused_request = {
                **base_request,
                "candidate_to_evaluate": candidate,
            }
            facts.extend(await request_facts(focused_request))
        return facts

    tasks = [asyncio.create_task(extract_batch(batch)) for batch in source_batches]
    batch_results = await asyncio.gather(*tasks, return_exceptions=True)
    raw_facts: list[Any] = []
    failures: list[BaseException] = []
    for result in batch_results:
        if isinstance(result, BaseException):
            failures.append(result)
        else:
            raw_facts.extend(result)
    if not raw_facts and failures:
        raise ValueError(f"all semantic evidence batches failed: {failures[0]}")
    return _validate_facts(raw_facts, sources)


__all__ = [
    "BoundEvidenceFact",
    "bind_company_evidence",
    "collect_evidence_sources",
]
