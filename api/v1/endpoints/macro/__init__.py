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

Backward-compatible re-exports:
  - INDICATOR_FETCHERS (used by stock_info.business._fetch_macro_data)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from ._index import router as index_router
from ._bond_yield import router as bond_yield_router
from ._indicator import router as indicator_router, INDICATOR_FETCHERS
from ._sector_flow import router as sector_flow_router
from ._market_breadth import router as market_breadth_router

logger = logging.getLogger(__name__)

router = APIRouter()

router.include_router(index_router)
router.include_router(bond_yield_router)
router.include_router(indicator_router)
router.include_router(sector_flow_router)
router.include_router(market_breadth_router)

# Backward-compatible re-exports
__all__ = ["router", "INDICATOR_FETCHERS"]