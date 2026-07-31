# -*- coding: utf-8 -*-
"""Stock basic info endpoints — route registration."""

from fastapi import APIRouter

router = APIRouter()

# Register route handlers from submodules
from api.v1.endpoints.stock_info import profile  # noqa: E402, F401
from api.v1.endpoints.stock_info import business  # noqa: E402, F401

# Re-export for lazy imports in tool_registry, services, and tests
from api.v1.endpoints.stock_info.profile import get_stock_info  # noqa: E402, F401
from api.v1.endpoints.stock_info.business import get_stock_business  # noqa: E402, F401
