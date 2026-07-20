# -*- coding: utf-8 -*-
"""Semantic research-intent resolution for the stock Agent.

The runtime used to infer the active topic and workflow by scanning user text
with regular expressions.  That made a harmless wording change capable of
switching the whole evidence plan.  This module asks the configured model for
one typed decision and validates the result before any research tool is
scheduled.  The validated object is the only workflow-routing authority.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src.llm.anthropic_gateway import build_litellm_kwargs
from src.services.stock_screening.screen_spec import (
    QuantitativeScreenSpec,
    quantitative_screen_spec_schema,
)


IntentKind = Literal[
    "casual",
    "general_question",
    "watchlist_query",
    "news_search",
    "article_read",
    "market_snapshot",
    "industry_chain",
    "theme_company_mapping",
    "stock_research",
    "investment_decision",
    "comparison",
    "risk_check",
    "collection_financial_filter",
    "quantitative_screening",
]
EntityScope = Literal[
    "none",
    "current_message",
    "previous_answer",
    "conversation",
]
SelectionMode = Literal["none", "complete_inventory", "ranked_shortlist"]
CompanyMappingMode = Literal["none", "structured_candidates", "business_evidence"]
FinancialMetric = Literal["debt_ratio"]
ComparisonOperator = Literal["gt", "gte", "lt", "lte", "eq"]
FilterAction = Literal["exclude_matching", "keep_matching"]

logger = logging.getLogger(__name__)


# The configured gateway occasionally accepts a request but does not return
# response headers before the chat-level deadline.  Keep timeout recovery
# inside the semantic resolver so the validated second attempt can still run.
INTENT_PRIMARY_TIMEOUT_SECONDS = 40.0
INTENT_RECOVERY_TIMEOUT_SECONDS = 32.0
INTENT_CACHE_TTL = timedelta(days=7)
INTENT_CACHE_VERSION = "v1"


class SemanticIntentUnavailableError(RuntimeError):
    """Both semantic gateway attempts timed out before returning a payload."""


class CollectionFinancialFilterSpec(BaseModel):
    """Executable financial predicate for a previously established company set."""

    model_config = ConfigDict(extra="forbid")

    metric: FinancialMetric
    operator: ComparisonOperator
    threshold: float = Field(ge=0.0)
    action: FilterAction


class ResearchIntent(BaseModel):
    """Validated semantic state for one conversation branch head."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    kind: IntentKind
    topic: Optional[str] = None
    discovery_theme: Optional[str] = None
    selection_mode: SelectionMode = "none"
    company_mapping_mode: CompanyMappingMode = "none"
    resolved_domains: list[str] = Field(default_factory=list)
    thesis_requirements: list[str] = Field(default_factory=list)
    entity_scope: EntityScope = "none"
    entities: list[str] = Field(default_factory=list)
    objective: str
    research_dimensions: list[str] = Field(default_factory=list)
    output_requirements: list[str] = Field(default_factory=list)
    quantitative_screen_spec: Optional[QuantitativeScreenSpec] = None
    collection_financial_filter_spec: Optional[CollectionFinancialFilterSpec] = None
    unsupported_requirements: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: Optional[str] = None
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    source: Literal["semantic", "semantic_cache"] = "semantic"

    @field_validator("topic", "discovery_theme", "clarification_question", mode="before")
    @classmethod
    def _normalize_nullable_text(cls, value: Any) -> Any:
        """Tolerate gateways that serialize JSON null as the string ``"null"``."""
        if value is None:
            return None
        if isinstance(value, str) and value.strip().lower() in {"", "null", "none", "nil"}:
            return None
        return value

    @field_validator(
        "resolved_domains", "thesis_requirements", "entities", "research_dimensions",
        "output_requirements", "unsupported_requirements", mode="before",
    )
    @classmethod
    def _normalize_unique_text_list(cls, value: Any) -> Any:
        """Normalize model-produced lists without interpreting their wording."""
        if not isinstance(value, list):
            return value
        cleaned: list[str] = []
        for item in value:
            requirement = str(item or "").strip(" ，,；;。")
            if not requirement:
                continue
            if requirement not in cleaned:
                cleaned.append(requirement)
        return cleaned

    @model_validator(mode="after")
    def _validate_execution_contract(self) -> "ResearchIntent":
        if self.needs_clarification and not self.clarification_question:
            raise ValueError("clarification intent is missing clarification_question")
        if self.kind == "quantitative_screening":
            if self.unsupported_requirements and not self.needs_clarification:
                raise ValueError("量化筛选包含不支持条件时必须请求澄清，不能静默丢弃")
            if (
                not self.needs_clarification
                and self.quantitative_screen_spec is None
            ):
                raise ValueError("量化筛选必须提供完整 quantitative_screen_spec")
        elif self.quantitative_screen_spec is not None or self.unsupported_requirements:
            raise ValueError("非量化筛选不得携带量化执行规格或不支持条件")

        if self.kind == "theme_company_mapping":
            if self.company_mapping_mode == "none" and not self.needs_clarification:
                raise ValueError("主题公司映射必须声明 company_mapping_mode")
            if not self.resolved_domains and not self.needs_clarification:
                raise ValueError("主题公司映射必须提供 resolved_domains")
        elif self.company_mapping_mode != "none" or self.resolved_domains:
            raise ValueError("非主题公司映射不得携带公司映射模式或领域列表")

        if self.kind == "collection_financial_filter":
            if self.collection_financial_filter_spec is None and not self.needs_clarification:
                raise ValueError("集合财务筛选必须提供 collection_financial_filter_spec")
            if self.entity_scope != "previous_answer" and not self.needs_clarification:
                raise ValueError("集合财务筛选的实体范围必须是 previous_answer")
        elif self.collection_financial_filter_spec is not None:
            raise ValueError("非集合财务筛选不得携带集合财务筛选规格")
        return self

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
                        "watchlist_query", "news_search", "article_read",
                        "theme_company_mapping", "stock_research", "investment_decision",
                        "comparison", "risk_check",
                        "collection_financial_filter",
                        "quantitative_screening",
                    ],
                },
                "topic": {"type": ["string", "null"]},
                "discovery_theme": {"type": ["string", "null"]},
                "selection_mode": {
                    "type": "string",
                    "enum": ["none", "complete_inventory", "ranked_shortlist"],
                },
                "company_mapping_mode": {
                    "type": "string",
                    "enum": ["none", "structured_candidates", "business_evidence"],
                },
                "resolved_domains": {"type": "array", "items": {"type": "string"}},
                "thesis_requirements": {"type": "array", "items": {"type": "string"}},
                "entity_scope": {
                    "type": "string",
                    "enum": ["none", "current_message", "previous_answer", "conversation"],
                },
                "entities": {"type": "array", "items": {"type": "string"}},
                "objective": {"type": "string"},
                "research_dimensions": {"type": "array", "items": {"type": "string"}},
                "output_requirements": {"type": "array", "items": {"type": "string"}},
                "quantitative_screen_spec": quantitative_screen_spec_schema(nullable=True),
                "collection_financial_filter_spec": {
                    "anyOf": [
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "metric": {"type": "string", "enum": ["debt_ratio"]},
                                "operator": {"type": "string", "enum": ["gt", "gte", "lt", "lte", "eq"]},
                                "threshold": {"type": "number", "minimum": 0},
                                "action": {"type": "string", "enum": ["exclude_matching", "keep_matching"]},
                            },
                            "required": ["metric", "operator", "threshold", "action"],
                        },
                        {"type": "null"},
                    ],
                },
                "unsupported_requirements": {"type": "array", "items": {"type": "string"}},
                "needs_clarification": {"type": "boolean"},
                "clarification_question": {"type": ["string", "null"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": [
                "kind", "topic", "discovery_theme", "selection_mode", "company_mapping_mode",
                "resolved_domains", "thesis_requirements",
                "entity_scope", "entities", "objective",
                "research_dimensions", "output_requirements", "quantitative_screen_spec",
                "collection_financial_filter_spec",
                "unsupported_requirements", "needs_clarification",
                "clarification_question", "confidence",
            ],
        },
    },
}


_INTENT_SYSTEM_PROMPT = """\
你是通用 AI 助手的语义路由器；股票研究只是它擅长的一类任务。只理解用户当前真正要完成什么，
不回答问题，也不选择具体工具。不得因为消息里出现股票名称、行业词或“哪些”就自动升级为完整股票研究。

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
6. theme_company_mapping 必须给出两个独立的结构化决定：
   - resolved_domains：用户本轮要求映射公司的全部业务领域。用户引用紧邻回答里的排名、梯队、首位、
     最受益方向或其中一组内容时，你必须理解引用关系并把对应领域直接解析到这个数组；不得要求运行时
     再从原文匹配词语。若上一回答的首位方向是“关节执行器（电机+减速器+丝杠）”，应按实际可查询的
     产品领域展开为类似“无框力矩电机、减速器、行星滚柱丝杠”，而不是只返回大主题“人形机器人”。
   - company_mapping_mode：structured_candidates 表示从本地完整股票池和结构化板块按领域找候选；
     business_evidence 表示用户明确要求以订单、收入、量产、客户验证等公司级事实作为纳入或剔除标准，
     才需要进一步做公司业务举证。用户只是要求找相关、布局、受益、核心或大力发展的公司时，使用
     structured_candidates，不能擅自升级成资讯、研报或网络举证流程。
   非 theme_company_mapping 必须返回 company_mapping_mode=none、resolved_domains=[]。
7. selection_mode 表示最终公司结论的形态：
   - complete_inventory：用户要按领域找公司、完整名单或全部候选；
   - ranked_shortlist：用户明确要求对公司横向比较、排序或只保留最符合的一部分；
   - none：非公司映射任务。
8. thesis_requirements 只写公司进入结论必须同时满足的业务事实条件；展示方式放入
   output_requirements。不得因为表达语气强烈，就自行增加订单、收入、量产或客户验证门槛。
9. kind 表示用户要的研究产物，而不是消息里碰巧出现的词：
   - watchlist_query：查看、筛选或解释用户自己的自选股/自选分组；范围必须锁定在该集合，
     “我的自选里哪些与某主题有关”不是从全市场发现主题公司；
   - news_search：用户只要求搜索、列出或浏览资讯，尤其是“先搜索、我指定一条后再读”的分阶段任务；
     这一轮只返回候选资讯，不能提前升级为个股研究或投资决策；
   - article_read：用户已指定一条资讯或 URL，要求读取正文；这是内容读取，不是投资分析；
   - industry_chain：研究产业环节、价值量、竞争格局或受益顺序；
   - theme_company_mapping：把主题映射为上市公司、完整名单或核心受益公司；
   - investment_decision：问能否买入、持有、卖出、仓位或入场条件；
   - stock_research：完整研究一家或多家公司；
   - collection_financial_filter：用户要求按财务阈值筛选上一条回答中的公司集合；必须把财务字段、
     比较符、数值阈值和筛除/保留动作完整写入 collection_financial_filter_spec。这是集合内筛选，
     entity_scope 必须为 previous_answer，不得扩展到全市场，也不得附带行情或技术指标；
   - comparison/risk_check/market_snapshot/general_question/casual 按字面语义选择。日常非股票问题属于
     general_question 或 casual，绝不能硬套股票流程。
   - quantitative_screening：用户给出技术/财务公式、阈值或排序条件，要求从全市场自动选股；
     这类任务必须交给确定性筛选工具，不能让回答模型自行拉数据或计算。
10. research_dimensions 写本轮真正需要研究的业务维度，例如 GPU、训练芯片、推理芯片、订单、收入；
   不要套用其他行业的零部件词。
11. quantitative_screening 必须同时遵守以下合同：
   - quantitative_screen_spec 必须是完整的执行规格，不能只给本轮修改的字段。
   - 当前支持的技术策略只有 atr_relative_frequency；支持ATR的SMA/EMA/Wilder平滑、长期基线
     SMA/EMA、动态线乘法或除法、gt/gte/lt/lte/eq比较、回看天数、达标天数与比例。
   - 财务字段只支持 revenue_ttm、deducted_net_profit_ttm、debt_ratio；金额统一换算为人民币元，
     百分比保留百分数，例如5亿元=500000000、70%=70。
   - 用户没有指定ST范围时 include_st=true；“全部A股”markets=[sh,sz,bj]；前复权为qfq。
     这些是范围标准化，不得为用户未说明的技术周期、阈值、财务条件或排序擅自补默认值。
   - 用户只说“按ATR选股”但没有给出执行所需周期、动态线、窗口或达标条件，必须澄清，
     quantitative_screen_spec=null。
   - 多轮追问如“营收改成10亿”“改为20日ATR”必须从紧邻上一轮完整规格继承其他条件，
     仅修改用户明确改变的字段，再返回一份完整新规格。
   - 用户要求RSI、MACD、PE或任何不在上述字段/策略中的条件时，逐项写入
     unsupported_requirements，needs_clarification=true，不能删除这些条件后继续筛选。
   - output_fields 只列用户要求展示的指标；始终无需列code/name，工具会自动添加。用户未指定
     展示列时，使用技术判定字段、所有参与财务过滤/排序的字段、财务报告期/来源和行情日期；
     preview_limit 未指定时仅可使用界面标准值10。这两项不改变入选结论。
   - 非 quantitative_screening 必须返回 quantitative_screen_spec=null、unsupported_requirements=[]。
12. 非 collection_financial_filter 必须返回 collection_financial_filter_spec=null。
13. 只有缺少对象、量化条件不完整或存在不支持条件且无法从紧邻上下文唯一确定时，
    needs_clarification 才为 true。澄清问题必须指出具体缺失或不支持的条件。
14. 必须通过 resolve_research_intent 工具返回结构化结果。\
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
    for message in messages[-8:]:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        text = _message_text(message)
        if not text:
            continue
        # Intent resolution needs the prior conclusion boundary, not the full
        # report body.  Keeping giant assistant answers here made a semantic
        # routing call time out and silently changed a ranked-shortlist request
        # back into the legacy broad-inventory path.
        if role == "assistant" and len(text) > 3600:
            text = text[:2800] + "\n...[中间正文省略]...\n" + text[-600:]
        elif role == "user":
            text = text[:1600]
        compact.append({"role": role, "content": text})
    return compact


def _conversation_for_recovery(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Build a smaller adjacent-turn context for a timed-out semantic retry.

    This is still resolved by the semantic model; it does not inspect wording
    or infer a workflow locally.  The compact form lowers gateway prefill cost
    while retaining the beginning and conclusion boundary of the prior answer.
    """
    compact = _conversation_for_resolution(messages)[-4:]
    recovered: list[dict[str, str]] = []
    for message in compact:
        text = message["content"]
        if message["role"] == "assistant" and len(text) > 1900:
            text = text[:1550] + "\n...[正文省略]...\n" + text[-250:]
        elif message["role"] == "user":
            text = text[:1000]
        recovered.append({"role": message["role"], "content": text})
    return recovered


def _semantic_cache_key(
    messages: list[dict[str, Any]],
    llm_cfg: dict[str, Any],
    current_entities: list[dict[str, str]],
    previous_answer_entities: list[dict[str, str]],
) -> str | None:
    """Fingerprint only semantic inputs; an edited user turn gets a new key."""
    if not llm_cfg.get("api_base"):
        return None
    payload = {
        "version": INTENT_CACHE_VERSION,
        "model": str(llm_cfg.get("model") or ""),
        "conversation": _conversation_for_recovery(messages),
        "current_entities": current_entities,
        "previous_answer_entities": previous_answer_entities,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return f"semantic_intent:{INTENT_CACHE_VERSION}:{hashlib.sha256(encoded).hexdigest()}"


def _load_cached_intent(cache_key: str | None) -> ResearchIntent | None:
    if not cache_key:
        return None
    try:
        from src.storage.manager import DatabaseManager

        cached = DatabaseManager.get_instance().get_tool_cache(cache_key)
        if not cached:
            return None
        updated_at = cached.get("updated_at")
        if not isinstance(updated_at, datetime) or datetime.now() - updated_at > INTENT_CACHE_TTL:
            return None
        payload = json.loads(bytes(cached["payload"]).decode("utf-8"))
        intent = ResearchIntent.model_validate(payload)
        return intent.model_copy(update={"source": "semantic_cache"})
    except Exception:
        logger.warning("[AgentIntent] ignored invalid persistent semantic cache", exc_info=True)
        return None


def _save_cached_intent(cache_key: str | None, intent: ResearchIntent) -> None:
    if not cache_key:
        return
    try:
        from src.storage.manager import DatabaseManager

        payload = intent.model_copy(update={"source": "semantic"}).model_dump_json().encode("utf-8")
        DatabaseManager.get_instance().save_tool_cache(cache_key, payload)
    except Exception:
        logger.warning("[AgentIntent] failed to persist semantic cache", exc_info=True)


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
    def field(value: Any, name: str) -> Any:
        return value.get(name) if isinstance(value, dict) else getattr(value, name, None)

    for choice in field(response, "choices") or []:
        message = field(choice, "message")
        if message is None:
            continue
        for tool_call in field(message, "tool_calls") or []:
            function = field(tool_call, "function")
            name = field(function, "name")
            arguments = field(function, "arguments")
            if name == "resolve_research_intent" and arguments:
                if isinstance(arguments, dict):
                    return arguments
                return _json_object(str(arguments))
        content = field(message, "content")
        if isinstance(content, str) and content.strip():
            return _json_object(content)
        if isinstance(content, list):
            text = "".join(
                str(field(part, "text") or "")
                for part in content
                if field(part, "type") in {None, "text"}
            ).strip()
            if text:
                return _json_object(text)
    raise ValueError("semantic intent model returned no structured payload")


async def resolve_research_intent(
    messages: list[dict[str, Any]],
    llm_cfg: dict[str, Any],
    *,
    completion: Callable[..., Awaitable[Any]],
    current_entities: Optional[list[dict[str, str]]] = None,
    previous_answer_entities: Optional[list[dict[str, str]]] = None,
) -> ResearchIntent:
    """Resolve the current branch head through a bounded structured model call."""
    conversation = _conversation_for_resolution(messages)
    if not conversation:
        raise ValueError("conversation has no user text")
    context = {
        "conversation": conversation,
        "verified_entities_in_current_message": current_entities or [],
        "verified_entities_in_previous_answer": previous_answer_entities or [],
    }
    cache_key = _semantic_cache_key(
        messages,
        llm_cfg,
        current_entities or [],
        previous_answer_entities or [],
    )
    cached_intent = _load_cached_intent(cache_key)
    if cached_intent is not None:
        logger.info("[AgentIntent] reused validated persistent semantic result")
        return cached_intent
    validation_error = ""
    intent: Optional[ResearchIntent] = None
    for attempt in range(2):
        request_context = dict(context)
        if attempt == 1:
            request_context["conversation"] = _conversation_for_recovery(messages)
        if validation_error:
            request_context["previous_validation_error"] = validation_error
            request_context["instruction"] = (
                "上一次语义调用超时或结构化结果无效。请只依据当前精简上下文重新理解，"
                "返回完整结构化对象，不改变用户语义。"
            )
        kwargs = build_litellm_kwargs(
            llm_cfg,
            stream=False,
            messages=[
                {"role": "system", "content": _INTENT_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(request_context, ensure_ascii=False)},
            ],
            tools=[_INTENT_TOOL],
            tool_choice={"type": "function", "function": {"name": "resolve_research_intent"}},
            temperature=0,
            max_tokens=1600,
        )
        try:
            timeout_seconds = (
                INTENT_PRIMARY_TIMEOUT_SECONDS
                if attempt == 0
                else INTENT_RECOVERY_TIMEOUT_SECONDS
            )
            async with asyncio.timeout(timeout_seconds):
                response = await completion(**kwargs)
            intent = ResearchIntent.model_validate(_intent_payload_from_response(response))
            break
        except (TimeoutError, ValidationError, ValueError, json.JSONDecodeError) as exc:
            validation_error = str(exc)
            logger.warning(
                "[AgentIntent] semantic attempt %d failed (%s); recovery=%s",
                attempt + 1,
                type(exc).__name__,
                attempt == 0,
            )
            if attempt == 1:
                if isinstance(exc, TimeoutError):
                    raise SemanticIntentUnavailableError(
                        "semantic intent gateway timed out after recovery"
                    ) from exc
                raise ValueError(f"invalid semantic research intent after retry: {exc}") from exc
    if intent is None:
        raise ValueError("semantic research intent was not resolved")
    # Entity scope is a company-set contract, not a topic-inheritance signal.
    # A model may label an explicit topic as current_message even though no
    # company was mentioned.  Normalize that harmless ambiguity before routing.
    if intent.entity_scope == "current_message" and not current_entities and not intent.entities:
        intent = intent.model_copy(update={"entity_scope": "none"})
    if (
        intent.entity_scope == "previous_answer"
        and not previous_answer_entities
        and not intent.entities
    ):
        intent = intent.model_copy(update={"entity_scope": "none"})
    _save_cached_intent(cache_key, intent)
    return intent


__all__ = [
    "CollectionFinancialFilterSpec",
    "ResearchIntent",
    "SemanticIntentUnavailableError",
    "resolve_research_intent",
]
