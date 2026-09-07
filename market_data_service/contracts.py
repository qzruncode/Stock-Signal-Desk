"""Pydantic schemas generated from the existing closed provider API signatures."""

import inspect
from functools import lru_cache
from typing import Any, get_type_hints
from pydantic import create_model
from market_data_service.schemas import DateWindow, StrictModel


@lru_cache
def operation_schema(operation):
    from market_data_service.sources import registry
    from market_data_service.providers.live import LiveProvider

    _, function = registry()[operation]
    if operation in {"kline", "quotes", "financials", "news", "announcements"}:
        function = getattr(LiveProvider, operation)
    hints = get_type_hints(function, include_extras=True)
    fields = {}
    for name, parameter in inspect.signature(function).parameters.items():
        if name == "self" or parameter.kind in {
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        }:
            continue
        fields[name] = (
            hints.get(name, Any),
            ... if parameter.default is inspect.Parameter.empty else parameter.default,
        )
    return create_model(
        operation.replace(".", "_") + "_Arguments",
        __base__=DateWindow if operation == "kline" else StrictModel,
        **fields,
    )
