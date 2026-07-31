"""Program-owned semantic selection and parameter binding for RSSHub routes."""

from __future__ import annotations

import re
from typing import Any, Iterable

from src.tools.rss_sources import RSS_ROUTE_CAPABILITIES


_WORD_RE = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]", re.I)

def semantic_tokens(value: str) -> set[str]:
    normalized = str(value or "").casefold()
    tokens = set(_WORD_RE.findall(normalized))
    cjk = "".join(
        character
        for character in normalized
        if "\u3400" <= character <= "\u9fff"
    )
    tokens.update(
        cjk[index : index + 2]
        for index in range(max(0, len(cjk) - 1))
    )
    return {token for token in tokens if token}


def _parameter_metadata(route: dict[str, Any]) -> list[dict[str, Any]]:
    raw = route.get("params") or route.get("parameters") or []
    return [dict(value) for value in raw if isinstance(value, dict)]


def _first_option(parameter: dict[str, Any]) -> str | None:
    options = parameter.get("options") or []
    if not isinstance(options, list):
        return None
    for option in options:
        if isinstance(option, dict) and option.get("value") is not None:
            return str(option["value"])
    return None


def bind_route_parameters(
    route: dict[str, Any],
    *,
    query: str,
    subjects: Iterable[str] = (),
) -> dict[str, str] | None:
    """Fill only values justified by metadata or the structured information need."""
    narrowed_query = " ".join(
        dict.fromkeys(
            value
            for raw in subjects
            if (value := str(raw or "").strip())
        )
    ) or str(query or "").strip()
    symbols = re.findall(r"(?<!\d)\d{6}(?!\d)", narrowed_query)
    bound: dict[str, str] = {}
    parameters = _parameter_metadata(route)
    query_parameter_names = {
        "q",
        "query",
        "keyword",
        "keywords",
        "search",
        "searchword",
        "search_word",
    }
    symbol_parameter_names = {
        "code",
        "symbol",
        "stock",
        "stockcode",
        "ticker",
    }
    for parameter in parameters:
        name = str(parameter.get("name") or "").strip()
        lowered = name.casefold()
        value: str | None = None
        if lowered in query_parameter_names and narrowed_query:
            value = narrowed_query
        elif lowered in symbol_parameter_names and symbols:
            value = symbols[0]
        elif lowered in {"lang", "language"}:
            options = [
                str(option.get("value"))
                for option in parameter.get("options") or []
                if isinstance(option, dict)
                and option.get("value") is not None
            ]
            value = next(
                (
                    option
                    for option in options
                    if option.casefold()
                    in {"zh", "zh-cn", "zh-hans", "cn", "chinese", "mandarin"}
                ),
                None,
            )
        if value is None and parameter.get("default") is not None:
            value = str(parameter["default"])
        if value is None and parameter.get("required"):
            value = _first_option(parameter)
        if value is None and parameter.get("required"):
            return None
        if value is not None and value.strip():
            bound[name] = value.strip()
    # Optional path parameters are positional. A later value cannot be placed
    # safely when an earlier segment has no metadata-backed value, because
    # collapsing slashes would move it into the wrong parameter slot.
    last_bound = max(
        (
            index
            for index, parameter in enumerate(parameters)
            if str(parameter.get("name") or "") in bound
        ),
        default=-1,
    )
    for index in range(last_bound):
        name = str(parameters[index].get("name") or "")
        if name not in bound:
            for trailing in parameters[index + 1 :]:
                bound.pop(str(trailing.get("name") or ""), None)
            break
    return bound


def _route_score(
    route: dict[str, Any],
    *,
    information_need: str,
    query: str,
    params: dict[str, str],
    audited_capabilities: frozenset[str] | None,
) -> float:
    searchable = " ".join(
        [
            str(route.get("name") or ""),
            str(route.get("namespace_name") or ""),
            str(route.get("namespace") or ""),
            str(route.get("description") or ""),
            " ".join(str(value) for value in route.get("categories") or []),
            str(route.get("route_path") or ""),
        ]
    )
    if audited_capabilities is not None:
        # Existing routes use the user's audited catalog policy. Query wording
        # cannot switch a route into another business purpose.
        score = 20.0
    else:
        # Future routes have no code entry yet, so compare their live metadata
        # through one generic index without request-specific phrases.
        need_text = " ".join(
            value
            for value in (information_need, query)
            if str(value or "").strip()
        )
        need_tokens = semantic_tokens(need_text)
        route_tokens = semantic_tokens(searchable)
        score = (
            8.0 * len(need_tokens & route_tokens) / max(1, len(need_tokens))
        )
    if any(
        name.casefold()
        in {
            "q",
            "query",
            "keyword",
            "keywords",
            "search",
            "searchword",
            "search_word",
        }
        for name in params
    ):
        score += 6.0
    readiness = str(route.get("readiness") or "available")
    if readiness == "unavailable":
        score -= 10
    elif readiness in {"degraded", "requires_configuration"}:
        score -= 3
    if route.get("auto_recommended"):
        score += 1
    return score


def resolve_rss_source_specs(
    routes: Iterable[dict[str, Any]],
    *,
    information_need: str,
    query: str,
    subjects: Iterable[str] = (),
    max_sources: int = 4,
    allowed_paths: frozenset[str] | None = None,
) -> list[tuple[str, dict[str, str], str]]:
    """Resolve any current-instance route without a route whitelist."""
    ranked: list[tuple[float, str, dict[str, str], str]] = []
    for route in routes:
        path = str(route.get("route_path") or route.get("path") or "").strip()
        if not path or (allowed_paths is not None and path not in allowed_paths):
            continue
        audited_capabilities = RSS_ROUTE_CAPABILITIES.get(path)
        if (
            audited_capabilities is not None
            and information_need
            and information_need not in audited_capabilities
        ):
            continue
        params = bind_route_parameters(
            route,
            query=query,
            subjects=subjects,
        )
        if params is None:
            continue
        score = _route_score(
            route,
            information_need=information_need,
            query=query,
            params=params,
            audited_capabilities=audited_capabilities,
        )
        if score <= 0:
            continue
        ranked.append(
            (
                score,
                path,
                params,
                str(route.get("name") or path),
            )
        )
    ranked.sort(key=lambda value: (-value[0], value[1]))
    bounded = max(1, min(int(max_sources or 4), 12))
    return [
        (path, params, name)
        for _, path, params, name in ranked[:bounded]
    ]


__all__ = [
    "bind_route_parameters",
    "resolve_rss_source_specs",
    "semantic_tokens",
]
