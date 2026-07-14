# -*- coding: utf-8 -*-
"""``get_sector_list`` tool."""

from typing import Any


def get_sector_list(type: str = "industry") -> Any:
    from api.v1.endpoints.sectors import get_sector_list as endpoint
    return endpoint(type=type)
