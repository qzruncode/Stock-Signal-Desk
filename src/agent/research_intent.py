# -*- coding: utf-8 -*-
"""Semantic research-intent resolution for the stock Agent.

The runtime used to infer the active topic and workflow by scanning user text
with regular expressions.  That made a harmless wording change capable of
switching the whole evidence plan.  This module asks the configured model for
one small, typed decision and validates the result before any research tool is
scheduled.  String matching remains outside this module as an availability
fallback only.
"""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.llm.anthropic_gateway import build_litellm_kwargs


IntentKind = Literal[
    "casual",
    "general_question",
    "market_snapshot",
    "industry_chain",
    "theme_company_mapping",
    "stock_research",
    "investment_decision",
    "comparison",
    "risk_check",
]
EntityScope = Literal[
    "none",
    "current_message",
    "previous_answer",
    "conversation",
]


class ResearchIntent(BaseModel):
    """Validated semantic state for one conversation branch head."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    kind: IntentKind
    topic: Optional[str] = None
    discovery_theme: Optional[str] = None
    entity_scope: EntityScope = "none"
    entities: list[str] = Field(default_factory=list)
    objective: str
    research_dimensions: list[str] = Field(default_factory=list)
    output_requirements: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: Optional[str] = None
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    source: Literal["semantic", "fallback"] = "semantic"

    @field_validator("topic", "discovery_theme", "clarification_question", mode="before")
    @classmethod
    def _normalize_nullable_text(cls, value: Any) -> Any:
        """Tolerate gateways that serialize JSON null as the string ``"null"``."""
        if value is None:
            return None
        if isinstance(value, str) and value.strip().lower() in {"", "null", "none", "nil"}:
            return None
        return value

    @property
    def normalized_topic(self) -> str:
        return str(self.topic or "").strip()

    @property
    def normalized_discovery_theme(self) -> str:
        return str(self.discovery_theme or self.topic or "").strip()


_INTENT_TOOL = {
    "type": "function",
    "function": {
        "name": "resolve_research_intent",
        "description": "Resolve the latest user turn into a typed stock-research intent.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "casual", "general_question", "market_snapshot", "industry_chain",
                        "theme_company_mapping", "stock_research", "investment_decision",
                        "comparison", "risk_check",
                    ],
                },
                "topic": {"type": ["string", "null"]},
                "discovery_theme": {"type": ["string", "null"]},
                "entity_scope": {
                    "type": "string",
                    "enum": ["none", "current_message", "previous_answer", "conversation"],
                },
                "entities": {"type": "array", "items": {"type": "string"}},
                "objective": {"type": "string"},
                "research_dimensions": {"type": "array", "items": {"type": "string"}},
                "output_requirements": {"type": "array", "items": {"type": "string"}},
                "needs_clarification": {"type": "boolean"},
                "clarification_question": {"type": ["string", "null"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": [
                "kind", "topic", "discovery_theme", "entity_scope", "entities", "objective",
                "research_dimensions", "output_requirements", "needs_clarification",
                "clarification_question", "confidence",
            ],
        },
    },
}


_INTENT_SYSTEM_PROMPT = """\
你是股票研究助手的语义路由器。只理解用户当前真正要研究什么，不回答问题，也不选择具体工具。

规则：
1. 以最后一条用户消息为当前任务。当前消息明确出现的新主题，必须覆盖更早的旧主题。
2. 只有当前消息是纯指代（例如“这些公司呢”“它们能买吗”）时，才继承紧邻上一条回答的主题或公司范围。
3. “上面说的 AI 芯片”同时包含指代和明确主题，topic 必须是“AI芯片”，不能退回更早的“AI产业链”。
4. entity_scope 表示本轮公司范围来自哪里；不要把更早回答里举例、反证或背景公司扩进范围。
   如果本轮没有具体公司对象，entity_scope 必须为 none；主题本身不属于公司范围。
5. topic 是用户真正研究的详细主题；discovery_theme 是候选池数据源使用的最短标准 A 股板块名，二者不得混用：
   - “用于训练和推理的 AI 计算芯片”可写 topic="AI计算芯片（训练与推理）"，discovery_theme="AI芯片"；
   - “低空经济产业链”可写 topic="低空经济产业链"，discovery_theme="低空经济"；
   - 只有 theme_company_mapping 需要 discovery_theme，其他 kind 填 null。
6. kind 表示用户要的研究产物，而不是消息里碰巧出现的词：
   - industry_chain：研究产业环节、价值量、竞争格局或受益顺序；
   - theme_company_mapping：把主题映射为上市公司、完整名单或核心受益公司；
   - investment_decision：问能否买入、持有、卖出、仓位或入场条件；
   - stock_research：完整研究一家或多家公司；
   - comparison/risk_check/market_snapshot/general_question/casual 按字面语义选择。
7. research_dimensions 写本轮真正需要研究的业务维度，例如 GPU、训练芯片、推理芯片、订单、收入；
   不要套用其他行业的零部件词。
8. 只有缺少对象且无法从紧邻上下文唯一确定时，needs_clarification 才为 true。
9. 必须通过 resolve_research_intent 工具返回结构化结果。\
"""


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ).strip()
    return ""


def _conversation_for_resolution(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Keep branch semantics while excluding old tool payloads and UI metadata."""
    compact: list[dict[str, str]] = []
    for message in messages[-10:]:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        text = _message_text(message)
        if not text:
            continue
        compact.append({"role": role, "content": text[:5000]})
    return compact


def _json_object(value: str) -> dict[str, Any]:
    stripped = value.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", stripped)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("intent response is not an object")
    return parsed


def _intent_payload_from_response(response: Any) -> dict[str, Any]:
    for choice in getattr(response, "choices", []) or []:
        message = getattr(choice, "message", None)
        if message is None:
            continue
        for tool_call in getattr(message, "tool_calls", None) or []:
            function = getattr(tool_call, "function", None)
            name = getattr(function, "name", None)
            arguments = getattr(function, "arguments", None)
            if name == "resolve_research_intent" and arguments:
                return _json_object(str(arguments))
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return _json_object(content)
    raise ValueError("semantic intent model returned no structured payload")


async def resolve_research_intent(
    messages: list[dict[str, Any]],
    llm_cfg: dict[str, Any],
    *,
    completion: Callable[..., Awaitable[Any]],
    current_entities: Optional[list[dict[str, str]]] = None,
    previous_answer_entities: Optional[list[dict[str, str]]] = None,
) -> ResearchIntent:
    """Resolve the current branch head through one forced structured model call."""
    conversation = _conversation_for_resolution(messages)
    if not conversation:
        raise ValueError("conversation has no user text")
    context = {
        "conversation": conversation,
        "verified_entities_in_current_message": current_entities or [],
        "verified_entities_in_previous_answer": previous_answer_entities or [],
    }
    kwargs = build_litellm_kwargs(
        llm_cfg,
        stream=False,
        messages=[
            {"role": "system", "content": _INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        tools=[_INTENT_TOOL],
        tool_choice={"type": "function", "function": {"name": "resolve_research_intent"}},
        temperature=0,
        max_tokens=1200,
    )
    response = await completion(**kwargs)
    try:
        intent = ResearchIntent.model_validate(_intent_payload_from_response(response))
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid semantic research intent: {exc}") from exc
    if intent.needs_clarification and not intent.clarification_question:
        raise ValueError("clarification intent is missing clarification_question")
    # Entity scope is a company-set contract, not a topic-inheritance signal.
    # A model may label an explicit topic as current_message even though no
    # company was mentioned.  Normalize that harmless ambiguity before routing.
    if intent.entity_scope == "current_message" and not current_entities and not intent.entities:
        intent = intent.model_copy(update={"entity_scope": "none"})
    return intent


__all__ = ["ResearchIntent", "resolve_research_intent"]
