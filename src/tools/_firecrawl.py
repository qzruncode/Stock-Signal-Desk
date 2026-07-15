# -*- coding: utf-8 -*-
"""Shared self-hosted Firecrawl REST configuration for Agent web tools."""

from __future__ import annotations

import os

DEFAULT_FIRECRAWL_BASE_URL = "http://127.0.0.1:3002"


def firecrawl_base_url() -> str:
    return (os.getenv("FIRECRAWL_BASE_URL") or DEFAULT_FIRECRAWL_BASE_URL).strip().rstrip("/")


def firecrawl_rest_config() -> tuple[str, dict[str, str], str] | None:
    """Return the project-managed keyless self-hosted REST endpoint."""
    base_url = firecrawl_base_url()
    if not base_url:
        return None
    return base_url, {"Content-Type": "application/json"}, "self_hosted_keyless"


__all__ = [
    "DEFAULT_FIRECRAWL_BASE_URL",
    "firecrawl_base_url",
    "firecrawl_rest_config",
]
