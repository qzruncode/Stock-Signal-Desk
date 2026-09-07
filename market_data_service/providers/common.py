"""Shared source normalization and durable cache using standard library APIs."""

from datetime import date, datetime
import math
import pickle
import re
from typing import Any
from contextvars import ContextVar

import pandas as pd
from sqlalchemy import select
from tenacity import Retrying, stop_after_attempt, wait_exponential

from market_data_service.database import get_database
from market_data_service.models import StockMeta
from market_data_service.control_models import utcnow

force_source_read = ContextVar("market_data_force_source_read", default=False)


def bare_symbol(value: str) -> str:
    raw = str(value).strip()
    code = re.sub(r"^(sh|sz|bj)", "", raw, flags=re.I).split(".")[0]
    if code.isdigit():
        return code.zfill(6)
    with get_database().get_session() as session:
        matches = list(
            session.scalars(
                select(StockMeta.code).where(
                    StockMeta.name == raw, StockMeta.status == "active"
                )
            )
        )
    if len(matches) != 1:
        raise ValueError(f"证券名称无法唯一确认: {raw}")
    return matches[0]


bare_local_symbol = bare_symbol


def exchange_prefix(symbol, *, upper=True, suffix=False):
    code = bare_symbol(symbol)
    market = (
        "BJ"
        if code.startswith(("4", "8", "92"))
        else "SH"
        if code.startswith(("6", "5", "90"))
        else "SZ"
    )
    return (
        f"{code}.{market}" if suffix else f"{market if upper else market.lower()}{code}"
    )


def json_value(value: Any):
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def frame_records(frame):
    return [] if frame is None or frame.empty else json_value(frame.to_dict("records"))


def cached_call(key, call, *, ttl_seconds=1800, attempts=2, **_):
    database = get_database()
    cached = database.get_tool_cache("provider:" + key)
    if (
        not force_source_read.get()
        and cached
        and cached["updated_at"]
        and 0 <= (utcnow() - cached["updated_at"]).total_seconds() < ttl_seconds
    ):
        return pickle.loads(cached["payload"]), True
    from market_data_service.data_provider.rate_limiter import akshare_rate_limiter

    def limited_call():
        akshare_rate_limiter.wait()
        return call()

    result = Retrying(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(min=0.5, max=3),
        reraise=True,
    )(limited_call)
    # Only locally generated provider values enter this cache; never deserialize
    # any binary supplied by an HTTP caller or imported legacy cache.
    database.save_tool_cache("provider:" + key, pickle.dumps(result, protocol=5))
    return result, False


def source_meta(source, *, cached=False, available=True, **extra):
    return {
        "source": source,
        "_cached": cached,
        "success": available,
        "_fetched_at": utcnow().isoformat() + "Z",
        **extra,
    }
