# -*- coding: utf-8 -*-
"""Strongly typed contracts for the unified Agent orchestration control plane.

This module deliberately contains no provider, tool-registry, or chat-endpoint
logic.  It is the stable boundary shared by planning, compilation, execution,
state persistence, and observability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Awaitable, Callable, Generic, Literal, Mapping, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator



class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )

class AgentErrorCode(str, Enum):
    PLANNER_TIMEOUT = "planner_timeout"
    PLANNER_SCHEMA_INVALID = "planner_schema_invalid"
    CLARIFICATION_REQUIRED = "clarification_required"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    POLICY_BLOCKED = "policy_blocked"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    CIRCUIT_OPEN = "circuit_open"
    BUDGET_EXCEEDED = "budget_exceeded"
    TOOL_FAILED = "tool_failed"
    COVERAGE_INCOMPLETE = "coverage_incomplete"
    SYNTHESIS_FAILED = "synthesis_failed"

class AgentStage(str, Enum):
    OUTLINE = "outline"
    PARAMETERIZATION = "parameterization"
    NORMALIZATION = "normalization"
    RESOURCE_BINDING = "resource_binding"
    COMPILATION = "compilation"
    POLICY = "policy"
    EXECUTION = "execution"
    BENEFIT_OUTLINE = "benefit_outline"
    CATALOG_LOADING = "catalog_loading"
    CATALOG_MAPPING = "catalog_mapping"
    RESULT_VALIDATION = "result_validation"
    RESOURCE_PUBLISHED = "resource_published"
    SYNTHESIS = "synthesis"
    COMPLETED = "completed"

class StageStatus(str, Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"

class OutcomeStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"

class ResourceType(str, Enum):
    SECURITY_COLLECTION = "security_collection"
    DOMAIN_COLLECTION = "domain_collection"
    EVIDENCE_COLLECTION = "evidence_collection"
    MARKET_MAINLINE_SNAPSHOT = "market_mainline_snapshot"
    GENERIC_RESULT = "generic_result"

class EffectLevel(str, Enum):
    READ = "read"
    MUTATION = "mutation"
    DESTRUCTIVE = "destructive"
    EXTERNAL = "external"
    TRADE = "trade"

class CacheReuseScope(str, Enum):
    RUN_ONLY = "run_only"
    CROSS_RUN = "cross_run"

class RendererMode(str, Enum):
    DETERMINISTIC = "deterministic"
    EVIDENCE_SYNTHESIS = "evidence_synthesis"

class QuestionType(str, Enum):
    """User-visible answer mode, independent from tools and workflows."""

    DIRECT = "direct"
    FACTUAL = "factual"
    EXPLANATION = "explanation"
    DIAGNOSIS = "diagnosis"
    COMPARISON = "comparison"
    RESEARCH = "research"
    FORECAST = "forecast"
    DECISION = "decision"
    OPERATION = "operation"

class UncertaintyMode(str, Enum):
    """How an answer may communicate uncertainty without becoming a refusal."""

    EXACT = "exact"
    BOUNDED = "bounded"
    SCENARIO = "scenario"
    NOT_APPLICABLE = "not_applicable"

class EvidenceDimension(str, Enum):
    """Stable semantic evidence axes consumed by Goal evaluation."""

    GENERAL_KNOWLEDGE = "general_knowledge"
    SECURITY_IDENTITY = "security_identity"
    REALTIME_MARKET = "realtime_market"
    PRICE_HISTORY = "price_history"
    TECHNICAL_SIGNALS = "technical_signals"
    COMPANY_BUSINESS = "company_business"
    COMPANY_FINANCIALS = "company_financials"
    VALUATION = "valuation"
    FINANCIAL_STATEMENTS = "financial_statements"
    NEWS = "news"
    ANNOUNCEMENTS = "announcements"
    RISK_EVENTS = "risk_events"
    REGULATORY = "regulatory"
    RESEARCH_CONSENSUS = "research_consensus"
    CATALYSTS = "catalysts"
    SOCIAL_SENTIMENT = "social_sentiment"
    COMPARATIVE_SNAPSHOT = "comparative_snapshot"
    MARKET_REGIME = "market_regime"
    MARKET_MAINLINE = "market_mainline"
    SECTOR_STRUCTURE = "sector_structure"
    CAPITAL_FLOW = "capital_flow"
    MACRO_POLICY = "macro_policy"
    INDUSTRY_STRUCTURE = "industry_structure"
    DOMAIN_CANDIDATES = "domain_candidates"
    THEME_BUSINESS = "theme_business"
    SCREENING = "screening"
    WATCHLIST_STATE = "watchlist_state"
    DATA_QUALITY = "data_quality"
    ANALYSIS_OPERATIONS = "analysis_operations"
    FEED_CONTENT = "feed_content"
    PUBLIC_WEB = "public_web"
    TRADE_STATE = "trade_state"

class Capability(str, Enum):
    """Complete standard-capability surface of the unified control plane."""

    GENERAL_RESPONSE = "general_response"
    SECURITY_LOOKUP = "security_lookup"
    REALTIME_QUOTE = "realtime_quote"
    PRICE_HISTORY = "price_history"
    TECHNICAL_ANALYSIS = "technical_analysis"
    FUNDAMENTAL_ANALYSIS = "fundamental_analysis"
    VALUATION_ANALYSIS = "valuation_analysis"
    FINANCIAL_STATEMENT_ANALYSIS = "financial_statement_analysis"
    NEWS_ANALYSIS = "news_analysis"
    ANNOUNCEMENT_ANALYSIS = "announcement_analysis"
    RISK_ANALYSIS = "risk_analysis"
    REGULATORY_ANALYSIS = "regulatory_analysis"
    RESEARCH_REPORT_ANALYSIS = "research_report_analysis"
    CATALYST_ANALYSIS = "catalyst_analysis"
    SOCIAL_SENTIMENT_ANALYSIS = "social_sentiment_analysis"
    STOCK_COMPARISON = "stock_comparison"
    STOCK_DEEP_RESEARCH = "stock_deep_research"
    INVESTMENT_DECISION = "investment_decision"
    MARKET_OVERVIEW = "market_overview"
    MARKET_MAINLINE_RESEARCH = "market_mainline_research"
    SECTOR_ANALYSIS = "sector_analysis"
    CAPITAL_FLOW_ANALYSIS = "capital_flow_analysis"
    MACRO_ANALYSIS = "macro_analysis"
    INDUSTRY_RESEARCH = "industry_research"
    THEME_STOCK_DISCOVERY = "theme_stock_discovery"
    THEME_BUSINESS_EVIDENCE = "theme_business_evidence"
    STOCK_SCREENING = "stock_screening"
    COLLECTION_FINANCIAL_FILTER = "collection_financial_filter"
    WATCHLIST_QUERY = "watchlist_query"
    WATCHLIST_MUTATION = "watchlist_mutation"
    WATCHLIST_GROUP_MANAGEMENT = "watchlist_group_management"
    DATA_HEALTH = "data_health"
    FORMAL_ANALYSIS = "formal_analysis"
    ANALYSIS_HISTORY = "analysis_history"
    ANALYSIS_TEMPLATE_MANAGEMENT = "analysis_template_management"
    BATCH_ANALYSIS = "batch_analysis"
    BATCH_RUN_MANAGEMENT = "batch_run_management"
    ANALYSIS_SCHEDULE_MANAGEMENT = "analysis_schedule_management"
    NOTIFICATION = "notification"
    FINANCIAL_SOURCE_DISCOVERY = "financial_source_discovery"
    FINANCIAL_FEED_READ = "financial_feed_read"
    FINANCIAL_ARTICLE_READ = "financial_article_read"
    WEBPAGE_FEED_TRANSFORM = "webpage_feed_transform"
    FINANCIAL_FEED_EXPORT = "financial_feed_export"
    PUBLIC_WEB_RESEARCH = "public_web_research"
    TRADE_EXECUTION = "trade_execution"


IntentT = TypeVar("IntentT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)
ArgsT = TypeVar("ArgsT", bound=BaseModel)

from . import _contracts_models1 as _contracts_models1
for _name in _contracts_models1.__all__:
    globals()[_name] = getattr(_contracts_models1, _name)

from . import _contracts_models2 as _contracts_models2
for _name in _contracts_models2.__all__:
    globals()[_name] = getattr(_contracts_models2, _name)


Compiler = Callable[
    [str, str, IntentT, tuple[InputReferenceV2, ...], ResultSelectionV2 | None, int],
    NormalizedIntent[IntentT],
]
StageObserver = Callable[[AgentStageEventV2], Awaitable[None] | None]

def stable_fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()
