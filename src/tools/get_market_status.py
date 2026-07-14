# -*- coding: utf-8 -*-
"""``get_market_status`` tool."""

from typing import Any


def get_market_status() -> Any:
    from api.v1.endpoints.market_status import get_market_status as endpoint
    return endpoint(force=False)
