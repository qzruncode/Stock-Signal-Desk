# -*- coding: utf-8 -*-
"""Legacy API convenience view for a composed market snapshot.

This remains available to non-Agent HTTP callers.  The LangGraph registry uses
``market_snapshot_tools`` source reads instead and never exposes this composed
view to a planning model.
"""

from typing import Any


def get_market_status() -> Any:
    from src.tools._market_snapshot import get_market_snapshot, market_status_view

    return market_status_view(get_market_snapshot())
