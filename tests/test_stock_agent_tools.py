# -*- coding: utf-8 -*-
"""Offline contract tests for the Stock Agent's semantic and fallback tools."""

from __future__ import annotations

import os
import inspect
from datetime import datetime
from unittest.mock import Mock, patch

import pandas as pd
import pytest

import src.tools.search_financial_news as financial_news_module
import src.tools.search_research_library as research_library_module

from src.tools.get_consensus_estimates import get_consensus_estimates
from src.tools.get_peer_comparison import get_peer_comparison
from src.tools.get_sector_flow import _fetch_all as fetch_all_sector_flow, get_sector_flow
from src.tools.get_stock_capital_flow import _market_for, get_stock_capital_flow
from src.tools.get_monetary_policy_operations import _operation_item
from src.tools.rss_sources import RSS_ROUTE_CAPABILITIES
from src.tools.search_financial_news import (
    _select_specs,
    _subject_terms,
    search_financial_news,
)
from src.tools.webfetch import (
    MAX_RESPONSE_SIZE,
    _accept_header_for,
    _challenge_reason,
    _extract_html,
    _http_fetch,
    fetch_url,
)
from src.tools.websearch import (
    _engine_query,
    _exa_search,
    _firecrawl_search,
    _mcp_text,
    _provider_order,
    websearch,
)



"""Shared fixtures for the focused test slices."""

def _catalog_route(
    route_path: str,
    name: str,
    *,
    namespace: str = "test",
    params: list[dict] | None = None,
) -> dict:
    return {
        "route_path": route_path,
        "name": name,
        "namespace": namespace,
        "namespace_name": name,
        "description": name,
        "params": params or [],
    }
