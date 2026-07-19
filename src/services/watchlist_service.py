"""Shared watchlist mutations for HTTP routes and Agent tools."""

from __future__ import annotations

import re
from typing import Any

from src.services.system_config_service import SystemConfigService


def _load(service: SystemConfigService) -> tuple[list[str], str, str]:
    config = service.get_config(include_schema=False, mask_token="")
    version = str(config.get("config_version") or "")
    mask_token = str(config.get("mask_token") or "")
    for item in config.get("items") or []:
        if item.get("key") == "STOCK_LIST":
            codes = [part.strip() for part in str(item.get("value") or "").split(",") if part.strip()]
            return codes, version, mask_token
    return [], version, mask_token


def _normalize_codes(codes: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in codes:
        code = str(value or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            raise ValueError(f"无效的 A 股代码: {value}")
        if code not in normalized:
            normalized.append(code)
    return normalized


def manage_watchlist(action: str, codes: list[str] | None = None) -> dict[str, Any]:
    """List, add or remove codes using the same persistent config as the UI."""
    service = SystemConfigService()
    current, version, mask_token = _load(service)
    if action == "list":
        return {"codes": current, "count": len(current), "changed": []}
    normalized = _normalize_codes(codes or [])
    if action == "add":
        changed = [code for code in normalized if code not in current]
        updated = [*current, *changed]
    elif action == "remove":
        removal = set(normalized)
        changed = [code for code in current if code in removal]
        updated = [code for code in current if code not in removal]
    else:
        raise ValueError("action 必须是 list、add 或 remove")
    if changed:
        service.update(
            config_version=version,
            items=[{"key": "STOCK_LIST", "value": ",".join(updated)}],
            mask_token=mask_token,
            reload_now=True,
        )
    return {"codes": updated, "count": len(updated), "changed": changed}


__all__ = ["manage_watchlist"]
