# -*- coding: utf-8 -*-
"""Macro data endpoints — route aggregation module.

Previously a monolithic 1236-line file, now split into sub-modules:

  - _cache.py          — shared cache & background-refresh helpers
  - _helpers.py        — shared utility functions
  - _index.py          — /index endpoint
  - _bond_yield.py     — /bond-yield endpoint
  - _indicator.py      — /indicator endpoint (also exports INDICATOR_FETCHERS)
  - _sector_flow.py    — /sector-flow endpoint
  - _market_breadth.py — /market-breadth endpoint

Backward-compatible re-exports (callers import these from the package root,
e.g. ``from api.v1.endpoints.macro import get_market_breadth``):
  - INDICATOR_FETCHERS (used by stock_info.business._fetch_macro_data)
  - get_index_data / get_bond_yield / get_macro_indicator /
    get_sector_flow / get_market_breadth (used by agent tool_registry,
    market_theme, buy_criteria, industry_cycle)
  - _fetch_sector_flow_industry (used by buy_criteria, industry_cycle)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from ._index import router as index_router, get_index_data
from ._bond_yield import router as bond_yield_router, get_bond_yield
from ._indicator import router as indicator_router, INDICATOR_FETCHERS, get_macro_indicator
from ._sector_flow import router as sector_flow_router, get_sector_flow, _fetch_sector_flow_industry
from ._market_breadth import router as market_breadth_router, get_market_breadth

logger = logging.getLogger(__name__)

router = APIRouter()

router.include_router(index_router)
router.include_router(bond_yield_router)
router.include_router(indicator_router)
router.include_router(sector_flow_router)
router.include_router(market_breadth_router)

# Backward-compatible re-exports
__all__ = [
    "router",
    "INDICATOR_FETCHERS",
    "get_index_data",
    "get_bond_yield",
    "get_macro_indicator",
    "get_sector_flow",
    "get_market_breadth",
    "_fetch_sector_flow_industry",
]
