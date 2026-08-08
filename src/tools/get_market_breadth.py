"""Legacy API convenience view for a composed market snapshot.

The Agent receives individual source observations from ``market_snapshot_tools``
and must decide whether a market-breadth conclusion is supported.
"""

from typing import Any


def get_market_breadth() -> Any:
    from src.tools._market_snapshot import get_market_snapshot, market_breadth_view

    return market_breadth_view(get_market_snapshot())
