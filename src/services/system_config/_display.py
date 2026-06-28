# -*- coding: utf-8 -*-
"""Display-side helpers for system config items."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


class DisplayMixin:
    """Map raw env values into user-facing display values."""

    _DISPLAY_KEY_ALIASES: Dict[str, Tuple[str, ...]] = {
        "AGENT_SKILL_DIR": ("AGENT_SKILL_DIR", "AGENT_STRATEGY_DIR"),
        "AGENT_SKILL_AUTOWEIGHT": ("AGENT_SKILL_AUTOWEIGHT", "AGENT_STRATEGY_AUTOWEIGHT"),
        "AGENT_SKILL_ROUTING": ("AGENT_SKILL_ROUTING", "AGENT_STRATEGY_ROUTING"),
    }
    _DISPLAY_VALUE_ALIASES: Dict[str, Dict[str, str]] = {
        "AGENT_ORCHESTRATOR_MODE": {
            "strategy": "specialist",
            "skill": "specialist",
        }
    }

    @classmethod
    def _normalize_display_value(cls, key: str, value: str) -> str:
        alias_map = cls._DISPLAY_VALUE_ALIASES.get(key.upper())
        if not alias_map:
            return value
        return alias_map.get(value.strip().lower(), value)

    @classmethod
    def _build_display_config_map(cls, raw_config_map: Dict[str, str]) -> Dict[str, str]:
        raw_upper = {key.upper(): value for key, value in raw_config_map.items()}
        aliased_keys = {
            alias
            for candidates in cls._DISPLAY_KEY_ALIASES.values()
            for alias in candidates
        }
        display_map: Dict[str, str] = {}

        for key, value in raw_upper.items():
            if key in aliased_keys:
                continue
            display_map[key] = cls._normalize_display_value(key, value)

        for canonical_key, candidates in cls._DISPLAY_KEY_ALIASES.items():
            canonical_env_key = candidates[0]
            if canonical_env_key in raw_upper:
                display_map[canonical_key] = cls._normalize_display_value(
                    canonical_key,
                    raw_upper[canonical_env_key],
                )
                continue

            selected_value: Optional[str] = None
            candidate_seen = False
            for candidate_key in candidates[1:]:
                if candidate_key not in raw_upper:
                    continue
                candidate_seen = True
                candidate_value = raw_upper[candidate_key]
                if candidate_value:
                    selected_value = candidate_value
                    break
            if candidate_seen:
                if selected_value is None:
                    for candidate_key in candidates[1:]:
                        if candidate_key in raw_upper:
                            selected_value = raw_upper[candidate_key]
                            break
                if selected_value is None:
                    selected_value = ""
                display_map[canonical_key] = cls._normalize_display_value(
                    canonical_key,
                    selected_value,
                )

        return display_map

    @staticmethod
    def _resolve_display_value(raw_value: str, field_schema: Dict[str, Any], raw_value_exists: bool) -> str:
        if raw_value_exists:
            return raw_value

        if field_schema.get("ui_control") == "switch":
            default_value = field_schema.get("default_value")
            if isinstance(default_value, str) and default_value:
                return default_value

        return raw_value
