# -*- coding: utf-8 -*-
"""Agent-friendly, low-token view of either the full RSSHub instance or its
finance-oriented explore subset.

The full ``/namespaces`` payload is RSSHub's raw metadata (English/markdown
descriptions, prose parameter docs, 3.3MB) — too heavy and too ambiguous for
an LLM to consume directly. This module distills route metadata into compact
descriptions and parameter hints, while preserving availability and
auto-recommendation as separate fields. Both scopes are cached for 6h.

Design choice (per plan): **lightweight extraction only**. We pull param
name/required/hint/default/options straight from RSSHub metadata. Complex
param pickers (gelonghui/nanhua/cih-index/cls/futunn) are flagged with a hint
rather than having their picker logic duplicated server-side — the model is
told "this route needs a chosen id; ask the user or pick from the example".
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from market_data_service.providers.rss_cache import _cache_get, _cache_put
from market_data_service.providers.rss_namespace import get_namespaces_flat

logger = logging.getLogger(__name__)

CATALOG_CACHE_KEY = "rss:catalog:v5:finance"
ALL_CATALOG_CACHE_KEY = "rss:catalog:v5:all"
CATALOG_TTL_SECONDS = 6 * 3600  # same as namespace blob

# Routes whose params come from a remote picker (the explore page has dedicated
# picker components for these). In the catalog we just flag them so the model
# knows the param needs a chosen id rather than a free value.
_REMOTE_PICKER_ROUTES: set[str] = {
    "/gelonghui/subject/:id",
    "/nanhua/report/:type1/:type2",
    "/cih-index/report/list/:report?",
    "/cls/subject/:id?",
    "/futunn/topic/:id",
}

# Express-style path param matcher: :name, :name?, :name{regex}?, :name{regex}
_PARAM_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)(\{[^}]+\})?(\?)?")


def _is_fresh(blob: Optional[dict], ttl: int = CATALOG_TTL_SECONDS) -> bool:
    if not isinstance(blob, dict):
        return False
    fetched = blob.get("_fetched_at")
    if not fetched:
        return True
    from datetime import datetime, timedelta

    try:
        ts = datetime.fromisoformat(str(fetched))
    except Exception:
        return True
    return (datetime.now() - ts) < timedelta(seconds=ttl)


def _path_params(route_path: str) -> List[Dict[str, Any]]:
    """Extract param descriptors declared in the route_path template.

    Returns ``[{name, required, hint}]`` — `required` is False for ``:name?``,
    True otherwise. `hint` is filled by the caller from RSSHub metadata.
    """
    params: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for name, _regex, opt_q in _PARAM_RE.findall(route_path):
        if name in seen:
            continue
        seen.add(name)
        params.append({"name": name, "required": opt_q != "?", "hint": ""})
    return params


def _merge_param_hints(
    params: List[Dict[str, Any]],
    metadata: Dict[str, Any],
    route_path: str,
) -> List[Dict[str, Any]]:
    """Fill in hint/default/options from RSSHub `parameters` metadata.

    RSSHub stores each param as either a plain string (allowed values inline in
    prose) or an object ``{description, default?, options?:[{value,label}]}``.
    We do a 1:1 name match; when the metadata key differs from the path name
    (RSSHub sometimes uses `category` as the meta key for a `:id` path param)
    and there's exactly one path param + one metadata entry, use that entry.
    """
    if not params:
        return params

    meta_items: List[tuple[str, Any]] = (
        list(metadata.items()) if isinstance(metadata, dict) else []
    )

    def _resolve(name: str) -> Any:
        # exact name match first
        for k, v in meta_items:
            if k == name:
                return v
        # 1 path param ↔ 1 metadata entry: use it regardless of key name
        if len(params) == 1 and len(meta_items) == 1:
            return meta_items[0][1]
        return None

    is_picker = route_path in _REMOTE_PICKER_ROUTES
    out: List[Dict[str, Any]] = []
    for p in params:
        entry: Dict[str, Any] = {
            "name": p["name"],
            "required": p["required"],
            "hint": "",
            "default": None,
            "options": [],
        }
        meta = _resolve(p["name"])
        if isinstance(meta, str):
            entry["hint"] = meta.strip()
        elif isinstance(meta, dict):
            entry["hint"] = str(meta.get("description") or "").strip()
            if meta.get("default") is not None:
                entry["default"] = str(meta.get("default"))
            opts = meta.get("options")
            if isinstance(opts, list):
                entry["options"] = [
                    {
                        "value": str(o.get("value")),
                        "label": str(o.get("label") or o.get("value")),
                    }
                    for o in opts
                    if isinstance(o, dict) and o.get("value") is not None
                ]
        if is_picker:
            picker_hint = "需选择具体值，可通过 inspect_financial_source 获取可选项"
            entry["hint"] = (
                f"{entry['hint']}（{picker_hint}）" if entry["hint"] else picker_hint
            )
        if entry["default"] is None and entry["hint"]:
            default_match = re.search(
                r"默认(?:为)?\s*[`'“\"]?([^`'”\"，。,；;\s]+)",
                entry["hint"],
            )
            if default_match:
                parsed_default = default_match.group(1).strip()
                if parsed_default.casefold() not in {
                    "空",
                    "无",
                    "none",
                    "null",
                }:
                    entry["default"] = parsed_default
        out.append(entry)
    return out


def _build_description(route: Dict[str, Any]) -> str:
    """A one-line Chinese purpose for the route. Prefer the route `name` (already
    Chinese), augmented by namespace name; fall back to a trimmed RSSHub
    description (strip markdown tables / ::: containers / heavy prose).
    """
    name = str(route.get("name") or "").strip()
    ns_name = str(route.get("namespace_name") or "").strip()
    desc_raw = str(route.get("description") or "").strip()
    # Strip RSSHub markdown noise: tables, ::: containers, leading bullets.
    desc = re.sub(r"```.*?```", "", desc_raw, flags=re.DOTALL)
    desc = re.sub(r"^:::.*$", "", desc, flags=re.MULTILINE)
    desc = re.sub(r"^\|.*$", "", desc, flags=re.MULTILINE)
    desc = re.sub(r"\s+", " ", desc).strip()
    if len(desc) > 120:
        desc = desc[:120].rstrip() + "…"
    if name and desc:
        return (
            f"{name}。{desc}" if ns_name and ns_name not in name else f"{name}。{desc}"
        )
    if name:
        return f"{name}（{ns_name}）" if ns_name and ns_name not in name else name
    return desc or route.get("route_path", "")


def _build_catalog_entry(route: Dict[str, Any]) -> Dict[str, Any]:
    route_path = str(route.get("route_path") or "")
    params = _merge_param_hints(
        _path_params(route_path), route.get("parameters") or {}, route_path
    )
    # The finance category is a single bucket; derive a sub-category hint from
    # the namespace for the model to filter by (e.g. wallstreetcn/cls/xueqiu).
    features = {
        str(key): bool(value)
        for key, value in (route.get("features") or {}).items()
        if value
    }
    return {
        "route_path": route_path,
        "name": str(route.get("name") or ""),
        "namespace": str(route.get("namespace") or ""),
        "namespace_name": str(route.get("namespace_name") or ""),
        "description": _build_description(route),
        "example": str(route.get("example") or ""),
        "params": params,
        "url": str(route.get("url") or ""),
        "categories": [str(value) for value in route.get("categories") or []],
        "features": features,
        "maintainers": [str(value) for value in route.get("maintainers") or []],
        "requires_configuration": bool(features.get("requireConfig")),
        "readiness": str(route.get("readiness") or "available"),
        "auto_recommended": bool(route.get("auto_recommended", True)),
        "readiness_reason": route.get("readiness_reason"),
    }


def get_rss_catalog(
    force: bool = False,
    *,
    scope: str = "finance",
) -> Dict[str, Any]:
    """Return the agent-friendly catalog ``{routes, count, _cached, _stale}``.

    ``scope="all"`` includes every route reported by the configured instance,
    including unavailable and non-finance routes. ``scope="finance"`` keeps
    the smaller explore-page subset for backward compatibility. Cached 6h.
    """
    normalized_scope = "all" if str(scope or "").strip().lower() == "all" else "finance"
    cache_key = (
        ALL_CATALOG_CACHE_KEY if normalized_scope == "all" else CATALOG_CACHE_KEY
    )
    cached = _cache_get(cache_key)
    if not force and _is_fresh(cached):
        if isinstance(cached, dict) and cached.get("routes"):
            out = dict(cached)
            out["_cached"] = True
            return out

    ns = get_namespaces_flat(
        force=force,
        finance_only=normalized_scope == "finance",
        include_hidden=normalized_scope == "all",
    )
    routes = ns.get("routes") or []
    catalog_routes = [_build_catalog_entry(r) for r in routes if isinstance(r, dict)]
    payload = {
        "routes": catalog_routes,
        "count": len(catalog_routes),
        "_fetched_at": ns.get("_fetched_at"),
        "_cached": False,
        "_stale": bool(ns.get("_stale")),
        "_error": ns.get("_error"),
        "scope": normalized_scope,
    }
    if catalog_routes:
        _cache_put(cache_key, payload)
    return payload


def list_catalog_routes(
    *,
    category: Optional[str] = None,
    keyword: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Filter the catalog by namespace (category) and/or keyword for the
    semantic finance-news selector. Returns a slimmed view for the Infos API;
    Agent runs discover and inspect sources through generic atomic RSS tools.
    """
    cat = get_rss_catalog(force=False)
    routes = cat.get("routes") or []
    kw = (keyword or "").strip().lower()
    cat_filter = (category or "").strip().lower()
    out: List[Dict[str, Any]] = []
    for r in routes:
        if (
            cat_filter
            and cat_filter not in str(r.get("namespace") or "").lower()
            and cat_filter not in str(r.get("namespace_name") or "").lower()
        ):
            continue
        if kw:
            hay = f"{r.get('route_path', '')} {r.get('name', '')} {r.get('namespace_name', '')} {r.get('description', '')}".lower()
            if kw not in hay:
                continue
        out.append(
            {
                "route_path": r.get("route_path"),
                "name": r.get("name"),
                "namespace": r.get("namespace"),
                "description": r.get("description"),
                "example": r.get("example"),
                "params": r.get("params"),
            }
        )
    return out
