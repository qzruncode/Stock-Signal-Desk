# -*- coding: utf-8 -*-
"""Persistent read-only cache keyed by the complete typed execution contract."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any, Mapping

from src.agent.orchestrator_v2.contracts import (
    CacheReuseScope,
    FreshnessPolicy,
    stable_fingerprint,
)
from src.agent.orchestrator_v2.runtime import CompiledTaskV2
from src.agent.task_workflows import WorkflowCall


_SENSITIVE_MODEL_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "password",
        "secret",
        "token",
    }
)
_RSS_TEXT_PARSER_VERSION = "rss-text-parser-2026.07.31.1"
_RSS_TEXT_TOOLS = frozenset(
    {
        "discover_rss_sources",
        "inspect_rss_source",
        "read_rss_feed",
        "read_rss_item",
        "read_text_document",
        "search_financial_news",
        "search_research_library",
    }
)
_CONVERSATION_RESOURCE_TOOLS = frozenset(
    {"read_rss_item", "read_text_document"}
)


def _cache_safe_model_config(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _cache_safe_model_config(item)
            for key, item in value.items()
            if (
                str(key).replace("-", "_").lower() not in _SENSITIVE_MODEL_KEYS
                and not any(
                    str(key).replace("-", "_").lower().endswith(f"_{suffix}") for suffix in _SENSITIVE_MODEL_KEYS
                )
            )
        }
    if isinstance(value, (list, tuple)):
        return [_cache_safe_model_config(item) for item in value]
    return value


def execution_cache_key_v2(
    task: CompiledTaskV2,
    call: WorkflowCall,
    arguments: Mapping[str, Any],
    *,
    model_config: Mapping[str, Any],
    conversation_id: str | None = None,
) -> str | None:
    policy = task.freshness_policy
    if policy.reuse_scope != CacheReuseScope.CROSS_RUN or policy.max_age_seconds is None:
        return None
    fingerprint = stable_fingerprint(
        {
            "cache_contract": "agent-orchestrator-v4-canonical",
            "capability": task.capability.value,
            "capability_version": task.capability_version,
            "intent_schema_version": task.intent_schema_version,
            "resource_fingerprint": task.resource_fingerprint,
            "step_id": call.step_id,
            "arguments": dict(arguments),
            "model_config_fingerprint": stable_fingerprint(_cache_safe_model_config(model_config)),
            "freshness_policy": policy.model_dump(mode="json"),
            "rss_text_parser_version": (
                _RSS_TEXT_PARSER_VERSION
                if call.tool_name in _RSS_TEXT_TOOLS
                else None
            ),
            "conversation_resource_scope": (
                str(conversation_id or "")
                if call.tool_name in _CONVERSATION_RESOURCE_TOOLS
                else None
            ),
        }
    )
    return f"agent_v4:{fingerprint}"


def load_execution_cache_v2(
    db_manager: Any,
    cache_key: str,
    *,
    ttl_seconds: int,
) -> dict[str, Any] | None:
    cached = db_manager.get_tool_cache(cache_key)
    if not isinstance(cached, Mapping):
        return None
    updated_at = cached.get("updated_at")
    if not isinstance(updated_at, datetime):
        return None
    age = (datetime.now() - updated_at.replace(tzinfo=None)).total_seconds()
    if age < 0 or age > ttl_seconds:
        return None
    try:
        payload = json.loads(bytes(cached["payload"]).decode("utf-8"))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def save_execution_cache_v2(
    db_manager: Any,
    cache_key: str,
    result: Mapping[str, Any],
    *,
    freshness_policy: FreshnessPolicy,
) -> None:
    if result.get("success") is not True or result.get("partial") is True:
        return
    if result.get("coverage_complete") is False:
        return
    if freshness_policy.require_observed_at and not any(
        result.get(key)
        for key in (
            "observed_at",
            "data_time",
            "as_of",
            "timestamp",
            "trade_time",
        )
    ):
        # A volatile capability without an authoritative observation time
        # cannot safely cross a run boundary.
        return
    db_manager.save_tool_cache(
        cache_key,
        json.dumps(
            dict(result),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8"),
    )


__all__ = [
    "execution_cache_key_v2",
    "load_execution_cache_v2",
    "save_execution_cache_v2",
]
